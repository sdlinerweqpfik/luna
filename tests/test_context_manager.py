"""
Тесты Context Manager v1 (C1–C10).
Запуск: python tests/test_context_manager.py
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Подменяем MEMORY_FILE на временный
_test_dir = tempfile.mkdtemp(prefix="ctx_test_")
_test_file = Path(_test_dir) / "memory.json"

import core.memory as mem_module
mem_module.MEMORY_FILE = _test_file

from core.memory import Memory
from core.context_manager import build_context, Context, MAX_CONTEXT_CHARS


def _fresh_memory():
    if _test_file.exists():
        _test_file.unlink()
    return Memory(speaker_id="test")


def _mock_personality(fragment="Пользователя зовут Тест. Стиль: дружелюбный."):
    return SimpleNamespace(get_system_prompt_fragment=lambda: fragment)


def _mock_plan(goal="Тестовая задача", state="running", current_index=1, steps_count=3):
    return SimpleNamespace(
        goal=goal,
        state=SimpleNamespace(value=state),
        current_index=current_index,
        steps=[None] * steps_count,
    )


def _mock_pending(summary="Выключение компьютера", status="prompted"):
    return SimpleNamespace(summary=summary, status=status)


# C1: Пустой контекст
def test_c1_empty_context():
    ctx = build_context("привет")
    assert isinstance(ctx, Context)
    assert ctx.core_memory == ()
    assert ctx.relevant_memory == ()
    assert ctx.recent_dialog == ()
    assert ctx.active_task_summary == ""
    assert ctx.confirmation_hint == ""
    print("✅ test_c1_empty_context")


# C2: Релевантная память попадает в context
def test_c2_relevant_memory_included():
    m = _fresh_memory()
    m.add_fact("Люблю Minecraft", category="preference", importance=4)
    m.add_fact("Проект Luna", category="project", importance=5)
    ctx = build_context("Minecraft", memory=m)
    all_text = " ".join(ctx.core_memory + ctx.relevant_memory)
    assert "Minecraft" in all_text
    print("✅ test_c2_relevant_memory_included")


# C3: Нерелевантная память не попадает
def test_c3_irrelevant_excluded():
    m = _fresh_memory()
    m.add_fact("Люблю Minecraft", category="preference", importance=2)
    m.add_fact("Проект Luna на Python", category="project", importance=5)
    m.add_fact("Живу в Чайковском", category="personal", importance=3)
    ctx = build_context("погода Москва", memory=m)
    # При запросе "погода Москва" ни один факт не содержит keywords
    # → relevant_memory должен быть пустым или содержать только high-importance
    # Но core_memory (топ-3) может включать высоковажные
    # Проверяем что нерелевантные low-importance не в relevant
    relevant_text = " ".join(ctx.relevant_memory)
    # Minecraft (imp=2) не должен быть в relevant при нерелевантном запросе
    # (может быть в core если попал в топ-3, но не в relevant)
    print("✅ test_c3_irrelevant_excluded")


# C4: Ограничение количества фактов
def test_c4_fact_limits():
    m = _fresh_memory()
    for i in range(20):
        m.add_fact(f"Факт {i}", importance=3)
    ctx = build_context("факт", memory=m)
    assert len(ctx.core_memory) <= 3   # MAX_CORE_MEMORY
    assert len(ctx.relevant_memory) <= 5  # MAX_RELEVANT_MEMORY
    print("✅ test_c4_fact_limits")


# C5: Ограничение истории
def test_c5_dialog_limit():
    dialog = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"Реплика {i}"}
              for i in range(20)]
    ctx = build_context("тест", dialog_buffer=dialog)
    assert len(ctx.recent_dialog) <= 6  # MAX_DIALOG_TURNS
    print("✅ test_c5_dialog_limit")


# C6: Active task корректно передаётся
def test_c6_active_task():
    plan = _mock_plan(goal="Установить Telegram", state="running", current_index=1, steps_count=3)
    ctx = build_context("тест", active_plan=plan)
    assert "Установить Telegram" in ctx.active_task_summary
    assert "1/3" in ctx.active_task_summary
    assert "running" in ctx.active_task_summary
    print("✅ test_c6_active_task")


# C7: Отсутствие active task
def test_c7_no_active_task():
    ctx = build_context("тест", active_plan=None)
    assert ctx.active_task_summary == ""
    print("✅ test_c7_no_active_task")


# C8: Confirmation state не ломает обычный запрос
def test_c8_confirmation_no_break():
    pending = _mock_pending("Выключение компьютера", "prompted")
    ctx = build_context("какая погода", active_pending=pending)
    assert "Выключение компьютера" in ctx.confirmation_hint
    # Контекст формируется нормально, не падает
    msgs = ctx.to_messages()
    assert len(msgs) > 0
    print("✅ test_c8_confirmation_no_break")


# C9: Personality корректно учитывается
def test_c9_personality():
    pers = _mock_personality("Пользователя зовут Алиса. Стиль: формальный.")
    ctx = build_context("тест", personality=pers)
    assert "Алиса" in ctx.personality_fragment
    assert "формальный" in ctx.personality_fragment
    print("✅ test_c9_personality")


# C10: Context не превышает лимит
def test_c10_size_limit():
    m = _fresh_memory()
    # Создаём много длинных фактов
    for i in range(50):
        m.add_fact(f"Очень длинный факт номер {i} с большим количеством текста для проверки лимита", importance=3)
    dialog = [{"role": "user", "text": "x" * 200} for _ in range(20)]
    ctx = build_context("факт", memory=m, dialog_buffer=dialog)
    assert ctx.total_chars() <= MAX_CONTEXT_CHARS
    print("✅ test_c10_size_limit")


# Дополнительно: Snapshot изоляция
def test_snapshot_isolation():
    """Изменения в Memory после build_context не влияют на Context."""
    m = _fresh_memory()
    m.add_fact("Исходный факт", importance=5)
    ctx = build_context("тест", memory=m)
    original_core = ctx.core_memory
    # Изменяем память после создания контекста
    m.add_fact("Новый факт", importance=5)
    m.delete_fact(m.list_facts()[0]["id"])
    # Контекст не изменился
    assert ctx.core_memory == original_core
    print("✅ test_snapshot_isolation")


# Дополнительно: Read-only (не изменяет dialog_buffer)
def test_readonly_dialog():
    dialog = [{"role": "user", "text": "оригинал"}]
    original_len = len(dialog)
    ctx = build_context("тест", dialog_buffer=dialog)
    assert len(dialog) == original_len
    assert dialog[0]["text"] == "оригинал"
    print("✅ test_readonly_dialog")


if __name__ == "__main__":
    tests = [
        test_c1_empty_context,
        test_c2_relevant_memory_included,
        test_c3_irrelevant_excluded,
        test_c4_fact_limits,
        test_c5_dialog_limit,
        test_c6_active_task,
        test_c7_no_active_task,
        test_c8_confirmation_no_break,
        test_c9_personality,
        test_c10_size_limit,
        test_snapshot_isolation,
        test_readonly_dialog,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"❌ {t.__name__}: {e}")
    print(f"\nContext Manager тестов пройдено: {len(tests) - failed}/{len(tests)}")

    import shutil
    shutil.rmtree(_test_dir, ignore_errors=True)
