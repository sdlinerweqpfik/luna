"""
Тесты Memory System v2.
Запуск: python tests/test_memory_v2.py
"""
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Подменяем MEMORY_FILE на временный перед импортом Memory
_test_dir = tempfile.mkdtemp(prefix="memory_test_")
_test_file = Path(_test_dir) / "memory.json"

import core.memory as mem_module
mem_module.MEMORY_FILE = _test_file

from core.memory import Memory, Fact, _normalize, _extract_keywords, MAX_FACTS


def _fresh_memory():
    """Чистая память для каждого теста."""
    if _test_file.exists():
        _test_file.unlink()
    return Memory(speaker_id="test")


# === M1: Создание факта ===
def test_m1_create_fact():
    m = _fresh_memory()
    result = m.add_fact("Проект называется Luna", category="project", importance=5)
    assert "Запомнила" in result
    facts = m.list_facts()
    assert len(facts) == 1
    assert facts[0]["text"] == "Проект называется Luna"
    assert facts[0]["category"] == "project"
    assert facts[0]["importance"] == 5
    assert "id" in facts[0]
    assert "created_at" in facts[0]
    print("✅ test_m1_create_fact")


# === M2: Чтение ===
def test_m2_read_fact():
    m = _fresh_memory()
    m.add_fact("Люблю Portal", category="preference", importance=4)
    facts = m.list_facts()
    assert len(facts) == 1
    assert facts[0]["text"] == "Люблю Portal"
    print("✅ test_m2_read_fact")


# === M3: Duplicate / Update ===
def test_m3_duplicate_update():
    m = _fresh_memory()
    r1 = m.add_fact("Люблю Portal")
    assert "Запомнила" in r1
    r2 = m.add_fact("Люблю Portal")
    assert "Уже знаю" in r2
    facts = m.list_facts()
    assert len(facts) == 1  # не создался дубликат
    # Обновление того же текста — тоже дедупликация
    fid = facts[0]["id"]
    r3 = m.update_fact(fid, "Люблю Portal")
    assert "уже существует" in r3 or "Обновила" in r3
    print("✅ test_m3_duplicate_update")


# === M4: Удаление ===
def test_m4_delete_fact():
    m = _fresh_memory()
    m.add_fact("Временный факт")
    facts = m.list_facts()
    fid = facts[0]["id"]
    result = m.delete_fact(fid)
    assert "Удалила" in result
    assert len(m.list_facts()) == 0
    # Удаление несуществующего
    result2 = m.delete_fact("nonexistent_id")
    assert "не найден" in result2
    print("✅ test_m4_delete_fact")


# === M5: Importance ===
def test_m5_importance():
    m = _fresh_memory()
    m.add_fact("Низкий", importance=1)
    m.add_fact("Высокий", importance=5)
    m.add_fact("Средний", importance=3)
    results = m.recall("")
    assert results[0]["importance"] == 5
    assert results[-1]["importance"] == 1
    print("✅ test_m5_importance")


# === M6: Категории ===
def test_m6_categories():
    m = _fresh_memory()
    m.add_fact("Pref", category="preference")
    m.add_fact("Tech", category="technical")
    m.add_fact("Temp", category="temporary")
    facts = m.list_facts()
    cats = {f["category"] for f in facts}
    assert cats == {"preference", "technical", "temporary"}
    # Невалидная категория → personal
    m.add_fact("Bad cat", category="invalid_category")
    bad = [f for f in m.list_facts() if f["text"] == "Bad cat"]
    assert bad[0]["category"] == "personal"
    print("✅ test_m6_categories")


# === M7: Релевантный recall ===
def test_m7_relevant_recall():
    m = _fresh_memory()
    m.add_fact("Люблю Minecraft", category="preference", importance=4)
    m.add_fact("Проект Luna на Python", category="project", importance=5)
    m.add_fact("Живу в Чайковском", category="personal", importance=3)
    # Запрос про Minecraft
    results = m.recall("Minecraft")
    assert any("Minecraft" in f["text"] for f in results)
    # Запрос про проект
    results2 = m.recall("проект Python")
    assert any("Luna" in f["text"] for f in results2)
    print("✅ test_m7_relevant_recall")


# === M8: Ограничение количества ===
def test_m8_recall_limit():
    m = _fresh_memory()
    for i in range(20):
        m.add_fact(f"Факт номер {i}", importance=3)
    results = m.recall("")
    assert len(results) <= 10  # MAX_RECALL_RESULTS
    ctx = m.get_context_facts("")
    assert len(ctx) <= 8  # MAX_CONTEXT_FACTS
    print("✅ test_m8_recall_limit")


# === M9: Сохранение после перезапуска ===
def test_m9_persistence():
    m1 = _fresh_memory()
    m1.add_fact("Персистентный факт", category="personal", importance=4)
    del m1
    m2 = Memory(speaker_id="test")
    facts = m2.list_facts()
    assert len(facts) == 1
    assert facts[0]["text"] == "Персистентный факт"
    print("✅ test_m9_persistence")


# === M10: Корректная работа с пустой памятью ===
def test_m10_empty_memory():
    m = _fresh_memory()
    assert m.list_facts() == []
    assert m.recall("") == []
    assert m.get_context_facts("") == []
    assert m.get_facts() == []
    result = m.delete_fact("nonexistent")
    assert "не найден" in result
    print("✅ test_m10_empty_memory")


# === M11: Повреждённый файл ===
def test_m11_corrupted_file():
    _test_file.write_text("{broken json!!!", encoding="utf-8")
    m = Memory(speaker_id="test")
    assert m.list_facts() == []
    # После этого память должна работать нормально
    m.add_fact("После восстановления")
    assert len(m.list_facts()) == 1
    print("✅ test_m11_corrupted_file")


# === M12: Временный мусор не сохраняется автоматически ===
def test_m12_no_auto_trash():
    """Память сама не решает что сохранять — это ответственность LLM.
    Но temporary категория имеет низкий приоритет в recall."""
    m = _fresh_memory()
    m.add_fact("Сегодня пью чай", category="temporary", importance=1)
    m.add_fact("Проект Luna", category="project", importance=5)
    results = m.recall("")
    # Temporary факт должен быть последним (низкий score)
    assert results[-1]["category"] == "temporary"
    assert results[0]["category"] == "project"
    print("✅ test_m12_no_auto_trash")


# === M13: Миграция старого формата ===
def test_m13_migration():
    old_data = {"test": ["Старый факт 1", "Старый факт 2"]}
    _test_file.write_text(json.dumps(old_data), encoding="utf-8")
    m = Memory(speaker_id="test")
    facts = m.list_facts()
    assert len(facts) == 2
    assert facts[0]["text"] == "Старый факт 1"
    assert "id" in facts[0]
    assert "category" in facts[0]
    print("✅ test_m13_migration")


# === M14: update_fact проверяет ID и дедупликацию ===
def test_m14_update_validation():
    m = _fresh_memory()
    m.add_fact("Факт А")
    m.add_fact("Факт Б")
    facts = m.list_facts()
    id_a = facts[0]["id"]
    id_b = facts[1]["id"]
    # Обновление несуществующего ID
    r1 = m.update_fact("nonexistent", "Новый текст")
    assert "не найден" in r1
    # Обновление на текст другого факта → дедупликация
    r2 = m.update_fact(id_a, "Факт Б")
    assert "уже существует" in r2
    # Нормальное обновление
    r3 = m.update_fact(id_a, "Обновлённый факт А")
    assert "Обновила" in r3
    updated = m.get_fact_by_id(id_a)
    assert updated["text"] == "Обновлённый факт А"
    assert updated["updated_at"] >= updated["created_at"]
    print("✅ test_m14_update_validation")


# === M15: Обратная совместимость get_facts() ===
def test_m15_backward_compat():
    m = _fresh_memory()
    m.add_fact("Факт для совместимости")
    old_api = m.get_facts()
    assert isinstance(old_api, list)
    assert old_api[0] == "Факт для совместимости"
    print("✅ test_m15_backward_compat")


if __name__ == "__main__":
    tests = [
        test_m1_create_fact,
        test_m2_read_fact,
        test_m3_duplicate_update,
        test_m4_delete_fact,
        test_m5_importance,
        test_m6_categories,
        test_m7_relevant_recall,
        test_m8_recall_limit,
        test_m9_persistence,
        test_m10_empty_memory,
        test_m11_corrupted_file,
        test_m12_no_auto_trash,
        test_m13_migration,
        test_m14_update_validation,
        test_m15_backward_compat,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"❌ {t.__name__}: {e}")
    print(f"\nMemory v2 тестов пройдено: {len(tests) - failed}/{len(tests)}")

    # Cleanup
    import shutil
    shutil.rmtree(_test_dir, ignore_errors=True)
