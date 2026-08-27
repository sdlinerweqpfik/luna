"""
Диагностический тест ПОЛНОГО цикла интеграции Confirmation Manager v3
в голосовой цикл: create -> claim -> TTS -> mark -> voice confirm.

deliver_answer() и voice_input() — точные копии протоколов, встроенных
в main.py (handle_request и conversation_mode), поэтому тест проверяет
ту же последовательность, что выполняется в бою.

Запуск: python tests/test_voice_cycle_protocol.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.confirmation import ConfirmationManager


class FakeTTS:
    """Заглушка TTS: запоминает озвученное, возвращает заданный результат."""

    def __init__(self, delivered=True):
        self.delivered = delivered
        self.spoken = []

    def speak_interruptible(self, text, on_interrupted=None):
        self.spoken.append(text)
        return self.delivered


def deliver_answer(m, tts, answer, pending_before=None):
    """Копия протокола из main.py::handle_request (хвост функции)."""
    pending_after = m.get_active()
    if (pending_after is not None
            and pending_after is not pending_before
            and pending_after.status == "pending"):
        m.claim_for_prompt(pending_after.id)
        if tts.speak_interruptible(answer):
            m.mark_prompted(pending_after.id)
        else:
            m.abort_prompting(pending_after.id)
    else:
        tts.speak_interruptible(answer)


def voice_input(m, text):
    """Копия протокола из main.py::conversation_mode (блок подтверждения)."""
    decision, action = m.match_user_input(text)
    if decision == "confirm":
        return m.confirm(action.id, source="voice")
    if decision == "reject":
        return True, m.reject(action.id)
    if decision == "ambiguous":
        return False, "ambiguous"
    return None, None


def _make_pending(m, runs):
    """Имитация вызова опасного инструмента: create_pending + executor."""
    def executor(payload):
        runs.append(1)
        return "выполнено"
    return m.create_pending("test", "Тестовое действие", {}, executor)


# ------------------------------------------------------------------ S1
def test_s1_full_happy_cycle():
    """Полный цикл: create -> claim -> озвучка -> mark -> 'да' -> выполнено 1 раз."""
    m = ConfirmationManager()
    runs = []
    tts = FakeTTS(delivered=True)

    a = _make_pending(m, runs)
    assert a is not None and a.status == "pending"

    deliver_answer(m, tts, "Выключить компьютер? Скажи да или нет.")
    assert a.status == "prompted"

    ok, _ = voice_input(m, "да")
    assert ok is True
    assert len(runs) == 1
    assert m.get_active() is None


# ------------------------------------------------------------------ S2
def test_s2_tts_failure_aborts():
    """Сбой TTS/прерывание: abort освобождает слот, 'да' ничего не делает."""
    m = ConfirmationManager()
    runs = []
    tts = FakeTTS(delivered=False)

    a = _make_pending(m, runs)
    deliver_answer(m, tts, "Выключить компьютер?")
    assert a.status == "aborted"
    assert m.get_active() is None

    ok, _ = voice_input(m, "да")
    assert ok is None and not runs

    b = _make_pending(m, runs)   # слот свободен — новый create разрешён
    assert b is not None


# ------------------------------------------------------------------ S3
def test_s3_ambiguous_does_not_confirm():
    """'да нет' -> ambiguous, действие живо; чистое 'нет' отменяет."""
    m = ConfirmationManager()
    runs = []
    tts = FakeTTS()

    _make_pending(m, runs)
    deliver_answer(m, tts, "Выключить компьютер?")

    ok, res = voice_input(m, "да нет")
    assert ok is False and res == "ambiguous"
    assert not runs
    assert m.get_active() is not None

    ok, _ = voice_input(m, "нет")
    assert ok is True
    assert not runs
    assert m.get_active() is None


# ------------------------------------------------------------------ S4
def test_s4_llm_cannot_confirm():
    """Модель не может подтвердить действие сама (source-gate)."""
    m = ConfirmationManager()
    runs = []
    tts = FakeTTS()

    a = _make_pending(m, runs)
    deliver_answer(m, tts, "Выключить компьютер?")

    ok, _ = m.confirm(a.id, source="llm")
    assert ok is False
    assert not runs
    assert m.get_active() is not None


# ------------------------------------------------------------------ S5
def test_s5_second_create_blocked_while_prompted():
    """Второй create при активном prompted pending блокирован."""
    m = ConfirmationManager()
    runs = []
    tts = FakeTTS()

    _make_pending(m, runs)
    deliver_answer(m, tts, "Выключить компьютер?")

    blocked = m.create_pending("test", "Второе действие", {}, lambda p: "x")
    assert blocked is None
    assert m.get_active() is not None


if __name__ == "__main__":
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"\nВсе {len(tests)} интеграционных теста пройдены.")
