"""
Быстрые команды без обращения к LLM.

ВАЖНО: здесь остаются ТОЛЬКО детерминированные, безопасные операции без
побочных эффектов на систему — время, дата, приветствия, простые списки.
Всё, что раньше дублировалось здесь через text-парсинг (управление ПК,
окнами, установка приложений, память, персонализация) — убрано, потому что
теперь это делает LLM через нативный tool calling (core/pc_tools.py,
core/memory_tools.py и т.д. в ALL_TOOLS).

Причина: если один и тот же функционал доступен и через жёсткий regex
здесь, и через tool calling в llm.py — непредсказуемо, какой путь сработает
первым, и это как раз то дублирование, ради устранения которого делался
переход на нативный tool calling.
"""
import logging
from pathlib import Path
from datetime import datetime

from core.entertainment import entertainment_command

log = logging.getLogger("secretary.commands")

# Путь к файлу со списком задач — оставлен как был, это отдельный
# "быстрый" список от того что в core/lists.py + memory_tools.
# TODO: со временем стоит объединить с core/lists.py, чтобы не было
# двух разных механизмов хранения задач.
TODO_FILE = Path.home() / "secretary" / "tasks.json"


def fast_command(text, llm=None, memory=None, personality=None):
    """Обрабатывает быстрые команды без LLM.

    Возвращает (True, ответ) если распознала команду, (False, "") если нет —
    тогда main.py передаст запрос дальше в llm.ask(), где решение о вызове
    инструментов принимает сама модель.

    memory/personality — опциональные, для разрушительных операций
    ('очисти память') и прямых команд персонализации ('меня зовут'),
    которые надёжнее ловить явной фразой, чем полагаться на модель.
    """
    text_lower = text.lower().strip()

    # === 1. ВРЕМЯ ===
    if any(word in text_lower for word in ["время", "который час", "сколько времени"]):
        now = datetime.now()
        return True, f"Сейчас {now.hour} часов {now.minute} минут"

    # === 2. ДАТА ===
    if any(word in text_lower for word in ["дата", "какое число", "какой день", "какой сегодня"]):
        now = datetime.now()
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        return True, f"Сегодня {now.day} {months[now.month - 1]}, {days[now.weekday()]} {now.year} года"

    # === 3. ПРИВЕТСТВИЯ ===
    if any(word in text_lower for word in ["привет", "здравствуй", "добрый день", "доброе утро", "добрый вечер"]):
        return True, "Привет! Чем могу помочь?"

    # === 4. БЛАГОДАРНОСТИ ===
    if any(word in text_lower for word in ["спасибо", "благодарю"]):
        return True, "Пожалуйста! Рада помочь."

    # === 5. ИМЯ ===
    if "как тебя зовут" in text_lower or "твоё имя" in text_lower:
        return True, "Меня зовут Луна. Я твой голосовой ассистент."

    # === АНЕКДОТЫ И ФАКТЫ ===
    success, answer = entertainment_command(text)
    if success or answer:
        return success, answer

    # === ПАМЯТЬ (только разрушительная операция — прямая фраза надёжнее) ===
    if memory is not None:
        success, answer = memory.memory_command(text)
        if success or answer:
            return success, answer

    # === ПЕРСОНАЛИЗАЦИЯ (прямые команды типа 'меня зовут') ===
    if personality is not None:
        success, answer = personality.personality_command(text)
        if success or answer:
            return success, answer

    return False, ""
