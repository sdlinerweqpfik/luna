"""
Списки покупок и задач.

Формат хранения задач — JSON, не plain-text: каждой задаче нужна временная
метка добавления, чтобы проактивные напоминания (core/reminders.py) знали,
когда её добавили и через сколько часов пора напомнить, если она всё ещё
не выполнена. Список покупок остался текстовым — временная метка там не
нужна, покупкам не нужны напоминания.
"""
import json
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("secretary.lists")

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SHOPPING_FILE = DATA_DIR / "shopping.txt"
TASKS_FILE = DATA_DIR / "tasks.json"


def _read_list(filepath):
    """Читает плоский текстовый список (для покупок)."""
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        items = [line.strip() for line in f if line.strip()]
    return items


def _write_list(filepath, items):
    """Записывает плоский текстовый список (для покупок)."""
    with open(filepath, "w", encoding="utf-8") as f:
        for item in items:
            f.write(item + "\n")


def _pluralize(count, one, few, many):
    """Правильные склонения: 1 пункт, 2 пункта, 5 пунктов"""
    n = abs(count) % 100
    n1 = n % 10
    if 10 < n < 20:
        return many
    if n1 > 1 and n1 < 5:
        return few
    if n1 == 1:
        return one
    return many


# ============================================
# СПИСОК ПОКУПОК (plain text)
# ============================================

def shopping_add(item):
    """Добавить в список покупок"""
    item = item.strip().lower()
    if not item:
        return "Не поняла, что добавить."

    items = _read_list(SHOPPING_FILE)

    if item in items:
        return f"'{item.capitalize()}' уже есть в списке покупок."

    items.append(item)
    _write_list(SHOPPING_FILE, items)

    count = len(items)
    word = _pluralize(count, "пункт", "пункта", "пунктов")
    return f"Добавила {item} в список покупок. Всего {count} {word}."


def shopping_show():
    """Показать список покупок"""
    items = _read_list(SHOPPING_FILE)

    if not items:
        return "Список покупок пуст."

    count = len(items)
    word = _pluralize(count, "пункт", "пункта", "пунктов")

    if count <= 5:
        items_str = ", ".join(items)
        return f"В списке покупок {count} {word}: {items_str}."
    else:
        items_str = ", ".join(items[:5])
        return f"В списке покупок {count} {word}. Первые пять: {items_str}. И ещё {count - 5}."


def shopping_remove(item):
    """Удалить из списка покупок"""
    item = item.strip().lower()
    items = _read_list(SHOPPING_FILE)

    if not items:
        return "Список покупок пуст."

    found = None
    for existing in items:
        if existing == item or item in existing or existing in item:
            found = existing
            break

    if not found:
        return f"'{item}' нет в списке покупок."

    items.remove(found)
    _write_list(SHOPPING_FILE, items)

    count = len(items)
    word = _pluralize(count, "пункт", "пункта", "пунктов")
    return f"Удалила {found}. Осталось {count} {word}."


def shopping_clear():
    """Очистить список покупок"""
    items = _read_list(SHOPPING_FILE)
    if not items:
        return "Список покупок уже пуст."

    count = len(items)
    word = _pluralize(count, "пункт", "пункта", "пунктов")
    _write_list(SHOPPING_FILE, [])
    return f"Очистила список покупок. Было {count} {word}."


# ============================================
# СПИСОК ЗАДАЧ (JSON с временными метками)
# ============================================

def _read_tasks():
    """Читает задачи из JSON. Мигрирует старый tasks.txt при первом запуске,
    если json ещё не существует, но старый текстовый файл есть."""
    if TASKS_FILE.exists():
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Ошибка чтения задач: {e}")
            return []

    old_txt = DATA_DIR / "tasks.txt"
    if old_txt.exists():
        log.info("Обнаружен старый tasks.txt — мигрирую в tasks.json")
        old_items = _read_list(old_txt)
        now = datetime.now().isoformat()
        migrated = [{"text": t, "added": now, "reminded_count": 0} for t in old_items]
        _write_tasks(migrated)
        return migrated

    return []


def _write_tasks(tasks):
    """Записывает задачи в JSON."""
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def tasks_add(task):
    """Добавить задачу"""
    task = task.strip()
    if not task:
        return "Не поняла, какую задачу добавить."

    tasks = _read_tasks()

    if task.lower() in [t["text"].lower() for t in tasks]:
        return f"Задача '{task}' уже есть в списке."

    tasks.append({
        "text": task,
        "added": datetime.now().isoformat(),
        "reminded_count": 0,
    })
    _write_tasks(tasks)

    count = len(tasks)
    word = _pluralize(count, "задача", "задачи", "задач")

    # Ставим напоминание для новой задачи, если модуль напоминаний подключён
    try:
        from core.reminders import schedule_reminder_for_new_task
        schedule_reminder_for_new_task(task)
    except ImportError:
        pass  # reminders.py ещё не подключён — не критично, просто не будет проактивного напоминания

    return f"Добавила задачу: {task}. Всего {count} {word}."


def tasks_show():
    """Показать задачи"""
    tasks = _read_tasks()

    if not tasks:
        return "Список задач пуст. Отдыхай!"

    count = len(tasks)
    word = _pluralize(count, "задача", "задачи", "задач")

    if count <= 5:
        numbered = [f"{i}. {t['text']}" for i, t in enumerate(tasks, 1)]
        tasks_str = " ".join(numbered)
        return f"У тебя {count} {word}. {tasks_str}"
    else:
        numbered = [f"{i}. {t['text']}" for i, t in enumerate(tasks[:5], 1)]
        tasks_str = " ".join(numbered)
        return f"У тебя {count} {word}. Первые пять: {tasks_str}. И ещё {count - 5}."


def tasks_done(task):
    """Отметить задачу выполненной (удалить)"""
    task = task.strip().lower()
    tasks = _read_tasks()

    if not tasks:
        return "Список задач пуст."

    found_idx = None
    for i, existing in enumerate(tasks):
        text_lower = existing["text"].lower()
        if text_lower == task or task in text_lower or text_lower in task:
            found_idx = i
            break

    if found_idx is None:
        return f"Задача '{task}' не найдена."

    found_text = tasks[found_idx]["text"]
    tasks.pop(found_idx)
    _write_tasks(tasks)

    # Отменяем запланированное напоминание — задачи больше нет
    try:
        from core.reminders import cancel_reminder
        cancel_reminder(found_text)
    except ImportError:
        pass

    count = len(tasks)
    word = _pluralize(count, "задача", "задачи", "задач")
    return f"Молодец! Задача '{found_text}' выполнена. Осталось {count} {word}."


def tasks_clear():
    """Очистить все задачи"""
    tasks = _read_tasks()
    if not tasks:
        return "Список задач уже пуст."

    try:
        from core.reminders import cancel_reminder
        for t in tasks:
            cancel_reminder(t["text"])
    except ImportError:
        pass

    count = len(tasks)
    word = _pluralize(count, "задача", "задачи", "задач")
    _write_tasks([])
    return f"Очистила все задачи. Было {count} {word}."


def get_tasks_raw():
    """Возвращает задачи как есть (с added/reminded_count) — для
    core/reminders.py, которому нужны метаданные, а не только текст."""
    return _read_tasks()


def mark_task_reminded(task_text):
    """Увеличивает счётчик напоминаний для задачи — вызывается из
    core/reminders.py после того как напоминание озвучено."""
    tasks = _read_tasks()
    for t in tasks:
        if t["text"] == task_text:
            t["reminded_count"] = t.get("reminded_count", 0) + 1
            _write_tasks(tasks)
            return
