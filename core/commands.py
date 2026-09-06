"""
Быстрые детерминированные команды — без LLM.
Отвечает мгновенно на простые запросы: время, дата, шутки, приветствия.
Добавляет мгновенности на слабом железе.
"""
import re
import time
import random
import json
import logging
from pathlib import Path

log = logging.getLogger("secretary.commands")

# Простые шутки/ответы для мгновенного ответа
JOKES = [
    "Почему программисты путают Хэллоуин и Рождество? Потому что OCT 31 == DEC 25.",
    "Заходит как-то нейросеть в бар... А бармен ей: 'У нас тут нет CUDA'.",
    "Сколько нужно программистов, чтобы поменять лампочку? Ни одного, это аппаратная проблема.",
    "Почему ИИ не пьёт кофе? Боится, что его взбодрят до переобучения.",
    "Мой код работает, и я не знаю почему. Мой код не работает, и я тоже не знаю почему.",
]

FUN_FACTS = [
    "Первый компьютерный баг был настоящим насекомым — молью, застрявшей в реле компьютера Марк-2 в 1947 году.",
    "Первый жёсткий диск весил около тонны и имел объём всего 5 мегабайт.",
    "Слово 'робот' придумал чешский писатель Карел Чапек в 1920 году.",
    "Первая компьютерная игра 'Теннис для двоих' была создана в 1958 году.",
    "Имя 'Питон' произошло не от змеи, а от шоу 'Летающий цирк Монти Пайтона'.",
]

GREETINGS = [
    "Привет! Чем могу помочь?",
    "Здравствуй! Что тебе нужно?",
    "Приветик! Слушаю тебя.",
    "Здравствуй! Рада тебя слышать.",
]

GOODBYES = [
    "Пока! Обращайся если что.",
    "До встречи!",
    "До свидания! Хорошего дня!",
]


def calculate(text):
    """Извлекает математическое выражение и вычисляет его."""
    expr = text
    expr = expr.replace("плюс", "+")
    expr = expr.replace("минус", "-")
    expr = expr.replace("умножить на", "*")
    expr = expr.replace("умножить", "*")
    expr = expr.replace("разделить на", "/")
    expr = expr.replace("разделить", "/")
    expr = expr.replace("делить на", "/")
    expr = expr.replace("делить", "/")
    match = re.search(r'[\d\s\+\-\*/\.\(\)]+', expr)
    if not match:
        return None
    expr = match.group().strip()
    if not expr or not any(c.isdigit() for c in expr):
        return None
    try:
        allowed = set("0123456789+-*/.() ")
        if not all(c in allowed for c in expr):
            return None
        result = eval(expr, {"__builtins__": {}}, {})
        return result
    except Exception:
        return None


def fast_command(text):
    """
    Пытается обработать запрос без LLM.
    Возвращает (handled: bool, response: str).
    Если handled == False, запрос уходит в LLM.
    """
    text_lower = text.lower().strip()

    # === ВРЕМЯ ===
    if any(kw in text_lower for kw in ["который час", "сколько времени", "сколько сейчас времени", "текущее время", "время сейчас"]):
        t = time.strftime("%H:%M")
        return True, f"Сейчас {t}."

    # === ДАТА ===
    if any(kw in text_lower for kw in ["какое число", "какая дата", "какой сегодня день", "сегодняшнее число"]):
        months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        t = time.localtime()
        day = days[t.tm_wday]
        return True, f"Сегодня {t.tm_mday} {months[t.tm_mon - 1]} {t.tm_year} года, {day}."

    # === ПРИВЕТСТВИЯ ===
    if any(kw in text_lower for kw in ["привет", "здравствуй", "здравствуйте", "хай", "добрый день", "добрый вечер", "доброе утро"]):
        return True, random.choice(GREETINGS)

    # === ПРОЩАНИЯ ===
    if any(kw in text_lower for kw in ["пока", "до свидания", "прощай", "до встречи", "выход"]):
        return True, random.choice(GOODBYES)

    # === ШУТКИ ===
    if any(kw in text_lower for kw in ["расскажи шутку", "рассмеши", "шутку", "анекдот"]):
        return True, random.choice(JOKES)

    # === ИНТЕРЕСНЫЕ ФАКТЫ ===
    if any(kw in text_lower for kw in ["интересный факт", "расскажи факт", "что нибудь интересное", "удиви меня"]):
        return True, random.choice(FUN_FACTS)

    # === КАЛЬКУЛЯТОР ===
    if any(kw in text_lower for kw in ["посчитай", "сколько будет", "вычисли"]):
        result = calculate(text_lower)
        if result is not None:
            return True, f"Получается {result}."
        return False, ""

    # === КАК ДЕЛА ===
    if any(kw in text_lower for kw in ["как дела", "как ты", "как настроение", "как жизнь"]):
        responses = [
            "Отлично! Работаю на полную мощность.",
            "Хорошо! Все системы в норме.",
            "Нормально! Процессор не перегревается.",
            "Прекрасно! Готов помочь.",
        ]
        return True, random.choice(responses)

    # === КТО ТЫ ===
    if any(kw in text_lower for kw in ["кто ты", "как тебя зовут", "ты кто", "что ты такое"]):
        return True, "Я Луна — твой локальный голосовой секретарь. Работаю полностью на твоём компьютере, без интернета для основных задач."

    # === СПАСИБО ===
    if any(kw in text_lower for kw in ["спасибо", "благодарю", "спс", "пасиб"]):
        return True, "Всегда пожалуйста!"

    # === НЕ ПОНИМАЮ ===
    return False, ""
