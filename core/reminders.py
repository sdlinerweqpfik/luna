"""
Проактивные напоминания о невыполненных задачах.

Логика: через REMINDER_INTERVAL_HOURS после добавления задачи (и затем
повторно каждые REMINDER_INTERVAL_HOURS, пока задача не выполнена), Луна
сама озвучивает напоминание — без wake word, как и core.tools.set_timer.

Переживает перезапуск процесса: schedule_all_pending() читает список задач
из core.lists (с полем "added") и заново расставляет таймеры при старте —
без этого все запланированные напоминания терялись бы молча при каждом
перезапуске, потому что threading.Timer существует только в памяти процесса.

Не напоминает бесконечно долго: MAX_REMINDERS ограничивает число повторов
на одну задачу, чтобы не превратиться в назойливый будильник, если задача
зависла надолго.
"""
import logging
import threading
from datetime import datetime, timedelta

from core.lists import get_tasks_raw, mark_task_reminded

log = logging.getLogger("secretary.reminders")

REMINDER_INTERVAL_HOURS = 3
MAX_REMINDERS = 5  # после 5 напоминаний (15 часов) перестаёт напоминать про эту задачу

# Заполняется в main.py при старте, как TTS_INSTANCE в tools.py
TTS_INSTANCE = None

_scheduled_timers = {}  # task_text -> threading.Timer, чтобы не задваивать таймеры на одну задачу
_lock = threading.Lock()


def _remind_task(task_text):
    """Срабатывает по таймеру. Проверяет что задача всё ещё существует
    (не выполнена и не удалена) перед тем как озвучивать — иначе Луна
    напомнит про то, что ты уже сделал час назад."""
    with _lock:
        _scheduled_timers.pop(task_text, None)

    current_tasks = get_tasks_raw()
    match = next((t for t in current_tasks if t["text"] == task_text), None)

    if match is None:
        # Задача выполнена или удалена с момента постановки таймера — молчим
        log.info(f"Напоминание про '{task_text}' отменено — задачи больше нет в списке")
        return

    reminded_count = match.get("reminded_count", 0)
    if reminded_count >= MAX_REMINDERS:
        log.info(f"Достигнут лимит напоминаний для '{task_text}' — больше не напоминаю")
        return

    print(f"\n🔔 НАПОМИНАНИЕ О ЗАДАЧЕ: {task_text}")
    if TTS_INSTANCE is not None:
        try:
            TTS_INSTANCE.speak(f"Напоминаю, у тебя не выполнена задача: {task_text}")
        except Exception as e:
            log.error(f"Не удалось озвучить напоминание о задаче: {e}")

    mark_task_reminded(task_text)
    # Планируем следующее напоминание через тот же интервал, если лимит не исчерпан
    if reminded_count + 1 < MAX_REMINDERS:
        _schedule_timer(task_text, REMINDER_INTERVAL_HOURS * 3600)


def _schedule_timer(task_text, delay_seconds):
    """Ставит (или переставляет) таймер напоминания для конкретной задачи."""
    with _lock:
        existing = _scheduled_timers.get(task_text)
        if existing is not None:
            existing.cancel()

        timer = threading.Timer(delay_seconds, _remind_task, args=(task_text,))
        timer.daemon = True  # не блокирует завершение процесса
        timer.start()
        _scheduled_timers[task_text] = timer


def schedule_reminder_for_new_task(task_text):
    """Вызывается сразу после добавления новой задачи (из core/lists.py или
    tools.py add_task), чтобы поставить первый таймер напоминания."""
    _schedule_timer(task_text, REMINDER_INTERVAL_HOURS * 3600)


def cancel_reminder(task_text):
    """Отменяет запланированное напоминание — вызывается при выполнении
    или удалении задачи, чтобы не напоминать про то, чего больше нет."""
    with _lock:
        existing = _scheduled_timers.pop(task_text, None)
        if existing is not None:
            existing.cancel()


def schedule_all_pending():
    """Вызывается один раз при старте main.py. Читает текущий список задач
    и для каждой невыполненной считает, сколько времени осталось до
    следующего напоминания (на основе 'added' + REMINDER_INTERVAL_HOURS *
    (reminded_count + 1)), и ставит таймер на оставшееся время.

    Без этого шага все напоминания, запланированные до перезапуска
    процесса, были бы потеряны молча — threading.Timer не переживает
    перезапуск сам по себе."""
    tasks = get_tasks_raw()
    now = datetime.now()
    scheduled = 0

    for task in tasks:
        reminded_count = task.get("reminded_count", 0)
        if reminded_count >= MAX_REMINDERS:
            continue

        try:
            added = datetime.fromisoformat(task["added"])
        except (KeyError, ValueError):
            # Задача без метки времени (старый формат, не успевший
            # мигрировать) — считаем что добавлена только что
            added = now

        next_reminder_at = added + timedelta(
            hours=REMINDER_INTERVAL_HOURS * (reminded_count + 1)
        )
        delay = (next_reminder_at - now).total_seconds()

        if delay <= 0:
            # Уже пора — напоминаем почти сразу (небольшая задержка, чтобы
            # не наваливаться на пользователя напоминаниями в первую же
            # секунду после запуска программы)
            delay = 5

        _schedule_timer(task["text"], delay)
        scheduled += 1

    if scheduled:
        log.info(f"Восстановлено {scheduled} запланированных напоминаний о задачах")
