import logging
import re
import threading
from ddgs import DDGS
from datetime import datetime
import requests

from core.lists import (
    shopping_add, shopping_show, shopping_remove, shopping_clear,
    tasks_add, tasks_show, tasks_done, tasks_clear
)
from core.pc_tools import PC_TOOLS
from core.memory_tools import MEMORY_TOOLS

log = logging.getLogger("secretary.tools")


# === ПОИСК В ИНТЕРНЕТЕ ===
def search_web(query: str, num_results: int = 3) -> str:
    """Поиск в интернете для новостей, фактов и любой актуальной информации.

    Args:
        query: поисковый запрос
        num_results: сколько результатов вернуть (по умолчанию 3)
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
        if not results:
            return "Ничего не найдено."
        answer_parts = []
        for i, r in enumerate(results, 1):
            answer_parts.append(f"{i}. {r['title']}: {r['body']}")
        return "\n".join(answer_parts)
    except Exception as e:
        log.error(f"Ошибка поиска: {e}")
        return "Ошибка при поиске."


# === ВРЕМЯ И ДАТА ===
def get_current_time() -> str:
    """Текущее время и дата."""
    now = datetime.now()
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    day_name = days[now.weekday()]
    month_name = months[now.month - 1]
    return f"Сейчас {now.hour} часов {now.minute} минут, {day_name}, {now.day} {month_name} {now.year} года."


# === ПОГОДА ===
def get_weather(city: str = "") -> str:
    """Погода в указанном городе.
    Args:
        city: название города (если пусто — город из профиля пользователя)
    """
    if not city and PERSONALITY is not None:
        city = PERSONALITY.profile.get("city", "")
    city = city or "Москва"
    try:
        url = f"https://wttr.in/{city}?format=%C,%t,ветер %w&lang=ru"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return f"Погода в городе {city}: {response.text}"
        return "Не удалось получить погоду."
    except Exception:
        return "Ошибка получения погоды."


# === КАЛЬКУЛЯТОР ===
def calculate(expression: str) -> str:
    """Математический калькулятор для точных вычислений.

    Args:
        expression: выражение для вычисления, можно словами ('два плюс два') или знаками
    """
    try:
        expr = expression.lower()
        expr = expr.replace("плюс", "+").replace("минус", "-")
        expr = expr.replace("умножить на", "*").replace("умножить", "*")
        expr = expr.replace("разделить на", "/").replace("разделить", "/")
        expr = expr.replace("в степени", "**").replace("степень", "**")
        expr = expr.replace("процент от", "*0.01*").replace("процентов от", "*0.01*")
        expr = expr.replace("процент", "*0.01").replace("процентов", "*0.01")
        expr = expr.replace("корень из", "sqrt").replace("корень", "sqrt")
        expr = expr.replace(",", ".").replace("х", "*").replace("×", "*").replace("÷", "/")

        expr = re.sub(r'[^0-9+\-*/().sqrt ]', '', expr)

        if not expr.strip():
            return "Не понял выражение."

        import math
        allowed_names = {"sqrt": math.sqrt, "abs": abs, "round": round}
        result = eval(expr, {"__builtins__": {}}, allowed_names)

        if isinstance(result, float) and result == int(result):
            result = int(result)

        return f"Результат: {result}"
    except Exception as e:
        log.error(f"Ошибка калькулятора: {e}")
        return "Не смог посчитать. Попробуй переформулировать."


# === КУРС ВАЛЮТ ЦБ РФ ===
def get_currency_rate(currency: str = "USD") -> str:
    """Курс валют ЦБ РФ.

    Args:
        currency: код или название валюты, например USD, EUR
    """
    try:
        response = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=5)
        data = response.json()

        currency_up = currency.upper()
        if currency_up in data["Valute"]:
            valute = data["Valute"][currency_up]
            return f"Курс {valute['Name']}: {valute['Value']} рублей за {valute['Nominal']} {valute['CharCode']}."

        for code, valute in data["Valute"].items():
            if currency.lower() in valute["Name"].lower():
                return f"Курс {valute['Name']}: {valute['Value']} рублей за {valute['Nominal']} {valute['CharCode']}."

        return f"Валюта {currency} не найдена."
    except Exception as e:
        log.error(f"Ошибка курса валют: {e}")
        return "Не удалось получить курс."


# === ВИКИПЕДИЯ ===
def get_wikipedia(query: str) -> str:
    """Краткая информация из Википедии по запросу.

    Args:
        query: тема или название статьи для поиска
    """
    try:
        url = "https://ru.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": True,
            "explaintext": True,
            "titles": query,
            "redirects": 1
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()

        pages = data["query"]["pages"]
        for page_id, page in pages.items():
            if page_id != "-1":
                extract = page.get("extract", "Статья не найдена.")
                sentences = extract.split(". ")[:3]
                return ". ".join(sentences) + "."

        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": 1
        }
        search_response = requests.get(url, params=search_params, timeout=5)
        search_data = search_response.json()

        if search_data["query"]["search"]:
            title = search_data["query"]["search"][0]["title"]
            return get_wikipedia(title)

        return f"Не нашёл информацию про '{query}' в Википедии."
    except Exception as e:
        log.error(f"Ошибка Википедии: {e}")
        return "Ошибка при поиске в Википедии."


# === ТАЙМЕР / НАПОМИНАНИЕ ===
_active_timers = []

# Заполняется в main.py при старте: tools.TTS_INSTANCE = tts
# Нужен, чтобы сработавший таймер мог реально проговорить напоминание
# голосом, а не только пикнуть. Тот же паттерн, что и MEMORY в memory_tools.py.
TTS_INSTANCE = None
PERSONALITY = None


def set_timer(minutes: float, message: str = "Таймер сработал") -> str:
    """Ставит таймер или напоминание через указанное количество минут.
    Когда сработает — Луна САМА проговорит сообщение голосом, без
    необходимости говорить wake word заново.

    Args:
        minutes: через сколько минут сработает таймер
        message: что сказать, когда сработает
    """
    try:
        minutes = float(minutes)
        seconds = minutes * 60

        def timer_callback():
            print(f"\n🔔 ТАЙМЕР: {message}")
            if TTS_INSTANCE is not None:
                try:
                    TTS_INSTANCE.speak(f"Напоминаю: {message}")
                    return
                except Exception as e:
                    log.error(f"Не удалось озвучить напоминание: {e}")
            try:
                import subprocess
                subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                             timeout=5, capture_output=True)
            except Exception:
                print("\a" * 5)

        timer = threading.Timer(seconds, timer_callback)
        timer.start()
        _active_timers.append(timer)

        if minutes < 1:
            time_str = f"{int(seconds)} секунд"
        elif minutes == int(minutes):
            time_str = f"{int(minutes)} минут"
        else:
            time_str = f"{minutes} минут"

        return f"Таймер на {time_str} установлен. Я напомню: {message}"
    except Exception as e:
        log.error(f"Ошибка таймера: {e}")
        return "Не смог установить таймер."


# === СПИСКИ ПОКУПОК И ЗАДАЧ ===

def add_shopping_item(item: str) -> str:
    """Добавить товар в список покупок.

    Args:
        item: название товара
    """
    return shopping_add(item)


def show_shopping_list() -> str:
    """Показать текущий список покупок."""
    return shopping_show()


def remove_shopping_item(item: str) -> str:
    """Удалить товар из списка покупок.

    Args:
        item: название товара
    """
    return shopping_remove(item)


def clear_shopping_list() -> str:
    """Полностью очистить список покупок."""
    return shopping_clear()


def add_task(task: str) -> str:
    """Добавить задачу в список дел.

    Args:
        task: текст задачи
    """
    return tasks_add(task)


def show_tasks() -> str:
    """Показать список задач."""
    return tasks_show()


def complete_task(task: str) -> str:
    """Отметить задачу выполненной.

    Args:
        task: текст задачи (как в списке)
    """
    return tasks_done(task)


def clear_tasks() -> str:
    """Полностью очистить список задач."""
    return tasks_clear()


# === РЕЕСТР ИНСТРУМЕНТОВ ===
ALL_TOOLS = [
    search_web,
    get_current_time,
    get_weather,
    calculate,
    get_currency_rate,
    get_wikipedia,
    set_timer,
    add_shopping_item,
    show_shopping_list,
    remove_shopping_item,
    clear_shopping_list,
    add_task,
    show_tasks,
    complete_task,
    clear_tasks,
] + PC_TOOLS + MEMORY_TOOLS

TOOLS_BY_NAME = {fn.__name__: fn for fn in ALL_TOOLS}
