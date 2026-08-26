"""
Управление окнами через wmctrl.
Безопасно: только белый список команд.
"""
import subprocess
import shutil
import logging

log = logging.getLogger("secretary.window_control")


def _check_wmctrl():
    """Проверяет установлен ли wmctrl"""
    if shutil.which("wmctrl") is None:
        return False, "wmctrl не установлен. Установи: sudo pacman -S wmctrl"
    return True, ""


def _run_wmctrl(args, timeout=5):
    """Безопасный запуск wmctrl"""
    ok, err = _check_wmctrl()
    if not ok:
        return False, err
    
    try:
        result = subprocess.run(
            ["wmctrl"] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Команда не ответила вовремя"
    except Exception as e:
        return False, str(e)


def show_desktop():
    """Показать рабочий стол (свернуть все окна)"""
    # Используем xdotool для имитации Super+D (стандартный хоткей)
    try:
        subprocess.run(
            ["xdotool", "key", "super+d"],
            timeout=5
        )
        return True, "Показываю рабочий стол"
    except Exception:
        # Fallback: сворачиваем все окна через wmctrl
        ok, output = _run_wmctrl(["-l"])
        if not ok:
            return False, output
        
        lines = output.split("\n")
        for line in lines:
            if line.strip():
                window_id = line.split()[0]
                _run_wmctrl(["-i", "-r", window_id, "-b", "add,hidden"])
        
        return True, "Сворачиваю все окна"


def minimize_window():
    """Свернуть текущее окно"""
    try:
        subprocess.run(
            ["xdotool", "key", "super+h"],
            timeout=5
        )
        return True, "Сворачиваю окно"
    except Exception:
        return False, "Не получилось свернуть окно"


def maximize_window():
    """Развернуть текущее окно"""
    ok, output = _run_wmctrl(["-r",":ACTIVE:", "-b", "toggle,maximized_vert,maximized_horz"])
    if ok:
        return True, "Разворачиваю окно"
    return False, output


def close_window():
    """Закрыть текущее окно"""
    ok, output = _run_wmctrl(["-c",":ACTIVE:"])
    if ok:
        return True, "Закрываю окно"
    return False, output


def list_windows():
    """Показать список открытых окон"""
    ok, output = _run_wmctrl(["-l"])
    if not ok:
        return False, output
    
    lines = output.split("\n")
    if not lines or not lines[0]:
        return True, "Нет открытых окон"
    
    result = "Открытые окна:\n"
    for i, line in enumerate(lines[:10], 1):  # максимум 10 окон
        parts = line.split(None, 3)
        if len(parts) >= 4:
            title = parts[3]
            result += f"{i}. {title}\n"
    
    return True, result


def switch_window():
    """Переключиться на следующее окно"""
    try:
        subprocess.run(
            ["xdotool", "key", "alt+Tab"],
            timeout=5
        )
        return True, "Переключаю окно"
    except Exception:
        return False, "Не получилось переключить окно"


def focus_window_by_number(number):
    """Фокус на окно по номеру из списка"""
    ok, output = _run_wmctrl(["-l"])
    if not ok:
        return False, output
    
    lines = output.split("\n")
    if number < 1 or number > len(lines):
        return False, f"Окно номер {number} не найдено"
    
    window_id = lines[number - 1].split()[0]
    ok, output = _run_wmctrl(["-i", "-a", window_id])
    
    if ok:
        return True, f"Переключаюсь на окно {number}"
    return False, output


def window_command(text):
    """Главная функция обработки команд окон"""
    text_lower = text.lower()
    
    # 1. Показать рабочий стол / свернуть все
    if any(phrase in text_lower for phrase in ["покажи рабочий стол", "сверни все окна", "сверни всё"]):
        return show_desktop()
    
    # 2. Свернуть текущее окно
    if "сверни окно" in text_lower:
        return minimize_window()
    
    # 3. Развернуть окно
    if "разверни окно" in text_lower or "разверни на весь экран" in text_lower:
        return maximize_window()
    
    # 4. Закрыть окно
    if "закрой окно" in text_lower or "закрой это окно" in text_lower:
        return close_window()
    
    # 5. Список окон
    if "какие окна открыты" in text_lower or "список окон" in text_lower:
        return list_windows()
    
    # 6. Переключить окно
    if "переключи окно" in text_lower or "следующее окно" in text_lower:
        return switch_window()
    
    # 7. Фокус на окно по номеру: "окно 2" или "второе окно"
    if "окно" in text_lower:
        words = text_lower.split()
        for i, word in enumerate(words):
            if word.isdigit():
                return focus_window_by_number(int(word))
        
        # Числа словами
        numbers = {"первое": 1, "второе": 2, "третье": 3, "четвёртое": 4, "пятое": 5}
        for word, num in numbers.items():
            if word in text_lower:
                return focus_window_by_number(num)
    
    return False, ""
