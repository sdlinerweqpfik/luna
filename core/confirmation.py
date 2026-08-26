"""
Confirmation Manager v1.

Управляет подтверждением опасных действий. Принцип: опасное действие,
запрошенное LLM, НЕ выполняется немедленно и НЕ может быть подтверждено
самой моделью. Вместо этого создаётся "pending action" с уникальным ID,
который ожидает ЯВНОГО подтверждения от реального пользовательского ввода.

Жизненный цикл:
1. Опасный инструмент вызывает manager.create_pending(...) -> PendingAction
2. Пользователю озвучивается запрос на подтверждение
3. Следующий пользовательский ввод проверяется на согласие/отказ (ГОЛОС)
4. При согласии выполняется сохранённый executor, действие УДАЛЯЕТСЯ
5. Действие ОДНОРАЗОВОЕ — повторное выполнение невозможно
6. При превышении срока жизни (expiration) автоматически отменяется

Работает без input() — подтверждение приходит через голосовой цикл в
conversation_mode, а не через блокирующий текстовый ввод.
"""

import re
import uuid
import logging
import threading
from datetime import datetime, timedelta

log = logging.getLogger("secretary.confirmation")

# Слова согласия и отказа для голосового подтверждения
CONFIRM_WORDS = {
    "да", "подтверждаю", "подтверди", "давай", "выполняй", "выполни",
    "делай", "сделай", "ок", "хорошо", "конечно", "согласен", "согласна",
}
REJECT_WORDS = {
    "нет", "не", "отмена", "отмени", "стоп", "не надо", "не нужно",
    "передумал", "передумала", "назад", "проехали",
}

# Время жизни подтверждения по умолчанию (секунды)
DEFAULT_TTL_SECONDS = 60


class PendingAction:
    """Одно ожидающее подтверждения действие."""

    def __init__(self, action_type, summary, payload, executor, ttl_seconds):
        self.id = uuid.uuid4().hex                     # уникальный ID
        self.action_type = action_type                 # тип действия (строка)
        self.summary = summary                         # человекочитаемое описание
        self.payload = payload                         # данные действия (словарь)
        self.executor = executor                       # callable(payload) -> str
        self.created_at = datetime.now()               # timestamp
        self.expires_at = self.created_at + timedelta(seconds=ttl_seconds)
        self.status = "pending"                        # pending|confirmed|rejected|expired|executed|error

    @property
    def timestamp(self):
        return self.created_at

    @property
    def expiration(self):
        return self.expires_at

    def is_expired(self):
        return datetime.now() > self.expires_at


class ConfirmationManager:
    """Менеджер ожидающих подтверждений. Синглтон на всё приложение."""

    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()

    def create_pending(self, action_type, summary, payload, executor,
                       ttl_seconds=DEFAULT_TTL_SECONDS):
        """Создаёт и регистрирует новое ожидающее действие."""
        self._purge_expired()
        action = PendingAction(action_type, summary, payload, executor, ttl_seconds)
        with self._lock:
            self._pending[action.id] = action
        log.info(f"Создано ожидающее действие {action.id} [{action_type}]: {summary}")
        return action

    def confirm(self, action_id):
        """Подтверждает и выполняет действие.
        Возвращает (успех: bool, результат: str).
        Действие одноразовое: после выполнения/попытки оно удаляется,
        повторный вызов с тем же ID ничего не выполнит."""
        with self._lock:
            action = self._pending.pop(action_id, None)   # сразу извлекаем
        if action is None:
            return False, "Действие не найдено или уже выполнено."
        if action.is_expired():
            action.status = "expired"
            return False, "Время подтверждения истекло. Повтори запрос заново."
        if action.status != "pending":
            return False, f"Действие уже обработано (статус: {action.status})."
        try:
            result = action.executor(action.payload)
            action.status = "executed"
            log.info(f"Действие {action.id} выполнено")
            return True, result
        except Exception as e:
            action.status = "error"
            log.error(f"Ошибка выполнения действия {action.id}: {e}")
            return False, f"Ошибка при выполнении: {e}"

    def reject(self, action_id):
        """Отменяет действие."""
        with self._lock:
            action = self._pending.pop(action_id, None)
        if action is None:
            return "Нечего отменять."
        action.status = "rejected"
        log.info(f"Действие {action.id} отменено пользователем")
        return f"Отменила: {action.summary}."

    def get_active(self):
        """Возвращает список активных (непросроченных) действий, по старшинству."""
        self._purge_expired()
        with self._lock:
            active = [a for a in self._pending.values() if not a.is_expired()]
        return sorted(active, key=lambda a: a.created_at)

    def has_pending(self):
        """Есть ли активные ожидающие действия."""
        return len(self.get_active()) > 0

    def match_user_input(self, text):
        """Проверяет пользовательский ввод на согласие/отказ активного действия.
        Возвращает ('confirm'|'reject', action) или (None, None).
        Подтверждение принимается ТОЛЬКО из пользовательского ввода,
        никогда из решения модели."""
        active = self.get_active()
        if not active:
            return None, None
        # извлекаем только буквенные токены, чтобы "да," не ломало сравнение
        words = set(re.findall(r"[а-яёa-z]+", text.lower()))
        action = active[0]  # подтверждаем самое старое активное действие
        if words & CONFIRM_WORDS:
            return "confirm", action
        if words & REJECT_WORDS:
            return "reject", action
        return None, None

    def _purge_expired(self):
        """Удаляет просроченные действия (ленивая очистка)."""
        now = datetime.now()
        with self._lock:
            expired = [aid for aid, a in self._pending.items() if now > a.expires_at]
            for aid in expired:
                self._pending[aid].status = "expired"
                log.info(f"Действие {aid} просрочено и удалено")
                del self._pending[aid]


# Синглтон для всего приложения
manager = ConfirmationManager()


def request_confirmation(action_type, summary, payload, executor,
                         ttl_seconds=DEFAULT_TTL_SECONDS):
    """Удобная обёртка для опасных инструментов: создаёт pending и
    возвращает готовый ответ для модели, который она озвучит пользователю.
    Модель НЕ выполняет действие сама — только просит подтверждения."""
    action = manager.create_pending(action_type, summary, payload, executor, ttl_seconds)
    return (
        f"Опасное действие «{summary}» ожидает подтверждения пользователя. "
        f"Скажи пользователю, что именно будет сделано, и попроси подтвердить "
        f"словом «да» или отменить словом «нет». НЕ выполняй действие сам."
    )
