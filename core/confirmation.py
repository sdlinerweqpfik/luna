"""
Confirmation Manager v3 — единственный pending confirmation.

Жизненный цикл состояния:

    pending --claim_for_prompt()--> prompting --mark_prompted()--> prompted
       |                                  |                            |
       | (заменён новым create,           | abort_prompting()          | confirm(voice/user)
       |  пока не claimed)                | (ошибка TTS)               | reject() / TTL
       v                                  v                            v
    discarded                          aborted                 executed/rejected/expired

Инварианты v3:
1. create_pending() создаёт action в состоянии pending.
2. claim_for_prompt(action_id) — атомарный переход pending -> prompting;
   вызывается ПЕРЕД передачей confirmation prompt в voice/UI слой.
3. mark_prompted(action_id) — переход prompting -> prompted; вызывается
   после успешной передачи / начала фактической озвучки. Стартует TTL.
4. Пока action в PROMPTING или PROMPTED, второй create_pending() НЕ может
   его заменить (возвращает None). Заменить можно только unclaimed pending.
5. abort_prompting(action_id) — явная отмена PROMPTING action при ошибке
   TTS; после неё слот свободен для следующего create_pending().
6. Expiration атомарен: единственный переход в expired и удаление из слота
   выполняет _purge_expired_locked() под lock (нет рассинхрона состояния).
7. confirm() атомарен (claim под lock), executor ВСЕГДА вне lock,
   source-gate {"voice","user"} и monotonic-TTL — как в v2, без изменений.
8. match_user_input() строгий: подтверждают только короткие чистые фразы
   из whitelist; негация/контекст/длинные/неизвестные фразы -> ambiguous.

Соответствие тестов (tests/test_confirmation.py):
  C1 — единственный pending (replace unclaimed; block prompting/prompted)
  C2 — параллельный confirm: один победил, executor 1 раз, повторное «да» no-op
  H1 — executor вне lock (реентерабельность без deadlock)
  H2 — source-gate: только voice/user
  H3 — TTL от mark_prompted(); expiration удаляет
  M1 — «да нет» => ambiguous, не подтверждает; чистое «да» подтверждает
  M2 — строгий matcher: контекст/негация/длинные/неизвестные => ambiguous
  P1 — lifecycle prompting: claim, блокировка create, abort_prompting (TTS error)
  X1 — confirm до delivered prompt отклоняется
  A1 — гонка claim_for_prompt vs create_pending (потоки): состояние консистентно
  A2 — гонка expiration под параллельным доступом: expired ровно один раз,
       executor не запускается, слот пуст
"""
import re
import time
import uuid
import logging
import threading
from datetime import datetime

log = logging.getLogger("secretary.confirmation")

DEFAULT_TTL_SECONDS = 60

# Только эти источники могут подтверждать опасное действие.
ALLOWED_CONFIRM_SOURCES = frozenset({"voice", "user"})

# Состояния
STATUS_PENDING = "pending"        # создан, промпт ещё не уходит в voice/UI
STATUS_PROMPTING = "prompting"    # промпт передан/передаётся в voice/UI слой
STATUS_PROMPTED = "prompted"      # промпт реально озвучен, TTL идёт
STATUS_EXPIRED = "expired"

# Строгий matcher: подтверждают ТОЛЬКО эти короткие чистые фразы.
CONFIRM_PHRASES = frozenset({
    "да", "ага", "угу", "ок", "окей", "хорошо", "конечно", "разумеется",
    "подтверждаю", "подтверди", "давай", "выполняй", "выполни",
    "делай", "сделай", "согласен", "согласна",
    "да давай", "да подтверждаю", "да конечно",
})
REJECT_PHRASES = frozenset({
    "нет", "не", "не надо", "не нужно", "отмена", "отмени", "стоп",
    "отказ", "отказываюсь", "передумал", "передумала", "не делай",
    "не выполняй",
})
NEGATION_TOKENS = frozenset({
    "нет", "не", "нельзя", "запрещаю", "отмена", "отмени", "стоп",
    "отказ", "отказываюсь", "передумал", "передумала",
})


class PendingAction:
    """Одно опасное действие, ожидающее подтверждения пользователя."""

    def __init__(self, action_type, summary, payload, executor, ttl_seconds):
        self.id = uuid.uuid4().hex
        self.action_type = action_type
        self.summary = summary
        self.payload = payload
        self.executor = executor
        self.ttl_seconds = ttl_seconds
        self.created_at = time.monotonic()
        self.created_wall = datetime.now()
        self.prompted_at = None
        self.status = STATUS_PENDING

    @property
    def is_prompted(self):
        return self.status == STATUS_PROMPTED or self.prompted_at is not None

    @property
    def expiration(self):
        """Дедлайн (monotonic) или None, пока TTL не стартовал."""
        if self.prompted_at is None:
            return None
        return self.prompted_at + self.ttl_seconds

    def is_expired(self, now=None):
        if self.prompted_at is None:
            return False
        now = time.monotonic() if now is None else now
        return now >= self.prompted_at + self.ttl_seconds


class ConfirmationManager:
    """Менеджер единственного pending confirmation (v3)."""

    def __init__(self):
        self._pending = None
        self._lock = threading.Lock()

    # ================= ВНУТРЕННЕЕ =================

    def _purge_expired_locked(self):
        """ЕДИНСТВЕННЫЙ атомарный переход в expired + удаление из слота.
        Вызывается только под lock, первым делом в точках входа.
        Возвращает истёкший action или None."""
        action = self._pending
        if (action is not None
                and action.status == STATUS_PROMPTED
                and action.is_expired()):
            self._pending = None
            action.status = STATUS_EXPIRED
            log.info(f"Pending {action.id} истёк — атомарно удалён из слота")
            return action
        return None

    # ================= СОЗДАНИЕ =================

    def create_pending(self, action_type, summary, payload, executor,
                       ttl_seconds=DEFAULT_TTL_SECONDS):
        """Создаёт единственный pending action (состояние pending).

        Возвращает PendingAction или None, если слот занят action в
        PROMPTING/PROMPTED — тогда вызывающий обязан сообщить модели,
        что другое действие уже ждёт подтверждения.

        Заменить можно только unclaimed pending (промпт ещё не уходил в
        voice/UI слой — намерения пользователя к нему нет).
        """
        with self._lock:
            self._purge_expired_locked()
            old = self._pending
            if old is not None:
                if old.status in (STATUS_PROMPTING, STATUS_PROMPTED):
                    log.warning(
                        f"create_pending отклонён: действие {old.id} в "
                        f"состоянии {old.status} — замена запрещена"
                    )
                    return None
                old.status = "discarded"
                log.info(f"Pending {old.id} не был claimed — заменён")
            action = PendingAction(action_type, summary, payload,
                                   executor, ttl_seconds)
            self._pending = action
            log.info(f"Создан pending {action.id} [{action_type}]: {summary}")
            return action

    # ================= ДОСТАВКА ПРОМПТА =================

    def claim_for_prompt(self, action_id):
        """Атомарный переход pending -> prompting. Вызывается ПЕРЕД передачей
        confirmation prompt в voice/UI слой. После этого create_pending()
        не может заменить действие."""
        with self._lock:
            action = self._pending
            if action is None or action.id != action_id:
                return False
            if action.status != STATUS_PENDING:
                return False
            action.status = STATUS_PROMPTING
            log.info(f"Pending {action_id} claimed для передачи промпта")
            return True

    def mark_prompted(self, action_id):
        """Переход prompting -> prompted ПОСЛЕ успешной передачи/начала
        озвучки. Стартует TTL. Идемпотентен для уже prompted."""
        with self._lock:
            action = self._pending
            if action is None or action.id != action_id:
                return False
            if action.status == STATUS_PROMPTED:
                return True
            if action.status != STATUS_PROMPTING:
                return False
            action.prompted_at = time.monotonic()
            action.status = STATUS_PROMPTED
            log.info(f"Pending {action_id} озвучен — TTL {action.ttl_seconds}s")
            return True

    def abort_prompting(self, action_id):
        """Явная отмена PROMPTING action (ошибка TTS/доставки). Освобождает
        слот, после чего разрешён следующий create_pending().
        Работает также для unclaimed pending (сбой до claim).
        Для PROMPTED используй reject() — это решение пользователя."""
        with self._lock:
            action = self._pending
            if action is None or action.id != action_id:
                return False
            if action.status not in (STATUS_PROMPTING, STATUS_PENDING):
                return False
            self._pending = None
            action.status = "aborted"
            log.info(f"Pending {action_id} aborted (ошибка доставки промпта)")
            return True

    # ================= РАЗРЕШЕНИЕ =================

    def confirm(self, action_id, source):
        """Атомарно забирает и выполняет pending action.
        source обязан быть в ALLOWED_CONFIRM_SOURCES. executor вне lock."""
        if source not in ALLOWED_CONFIRM_SOURCES:
            log.warning(f"confirm отклонён для {action_id}: source={source!r}")
            return False, "Источник подтверждения не разрешён."

        with self._lock:
            expired = self._purge_expired_locked()
            if expired is not None and expired.id == action_id:
                return False, "Время подтверждения истекло."
            action = self._pending
            if action is None or action.id != action_id:
                return False, "Нет действия, ожидающего подтверждения."
            if action.status != STATUS_PROMPTED:
                # pending/prompting: промпт не доставлен — осознанное
                # согласие невозможно. Fail-safe.
                return False, "Промпт подтверждения ещё не передан пользователю."
            # Атомарный claim: слот пуст ДО любого выполнения.
            self._pending = None
            action.status = "confirmed"

        # Lock отпущен ЗДЕСЬ — executor никогда не выполняется под ним.
        try:
            result = action.executor(action.payload)
        except Exception as e:
            action.status = "error"
            log.error(f"Ошибка executor для {action_id}: {e}")
            return False, f"Ошибка при выполнении: {e}"
        action.status = "executed"
        log.info(f"Pending {action_id} выполнен")
        return True, result

    def reject(self, action_id):
        """Отмена пользователем. Fail-safe: источник не проверяется."""
        with self._lock:
            self._purge_expired_locked()
            action = self._pending
            if action is None or action.id != action_id:
                return "Нечего отменять."
            self._pending = None
            action.status = "rejected"
        log.info(f"Pending {action_id} отклонён пользователем")
        return f"Отменила: {action.summary}."

    # ================= ЗАПРОСЫ / MATCHER =================

    def get_active(self):
        """Живой pending action или None (expiration удаляет атомарно)."""
        with self._lock:
            self._purge_expired_locked()
            return self._pending

    def has_pending(self):
        return self.get_active() is not None

    def match_user_input(self, text):
        """Строгая классификация ВВОДА ПОЛЬЗОВАТЕЛЯ.

        Возвращает (decision, action):
          "confirm"   — только короткая чистая фраза из whitelist
          "reject"    — только чистая фраза отказа из whitelist
          "ambiguous" — всё остальное: смешанные токены, негация,
                        дополнительный контекст, длинные/неизвестные фразы.
                        НИКОГДА не подтверждает.
          (None, None) — нет активного pending.
        Ничего не выполняет — только классификация.
        """
        action = self.get_active()
        if action is None:
            return None, None
        tokens = re.findall(r"[а-яёa-z]+", text.lower())
        if not tokens:
            return "ambiguous", action
        norm = " ".join(tokens)
        has_negation = bool(set(tokens) & NEGATION_TOKENS)
        if not has_negation and norm in CONFIRM_PHRASES:
            return "confirm", action
        if norm in REJECT_PHRASES:
            return "reject", action
        return "ambiguous", action


# Синглтон для всего приложения
manager = ConfirmationManager()


def request_confirmation(action_type, summary, payload, executor,
                         ttl_seconds=DEFAULT_TTL_SECONDS):
    """Хелпер для инструментов: создаёт pending и возвращает текст для
    озвучки. Интеграционный voice/UI слой обязан далее:
      1. claim_for_prompt(action.id)  — перед передачей промпта в TTS;
      2. mark_prompted(action.id)     — после начала фактической озвучки;
      3. abort_prompting(action.id)   — при ошибке TTS/доставки.
    """
    action = manager.create_pending(action_type, summary, payload,
                                    executor, ttl_seconds)
    if action is None:
        return (
            "Другое действие уже ожидает подтверждения пользователя. "
            "Дождись его решения, прежде чем запрашивать новое."
        )
    return (
        f"Опасное действие «{summary}» ожидает подтверждения пользователя. "
        f"Скажи пользователю, что именно будет сделано, и попроси сказать "
        f"«да» для подтверждения или «нет» для отмены. НЕ выполняй действие сам."
    )
