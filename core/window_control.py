"""
Управление окнами через wmctrl/xdotool.
Безопасно: только белый список команд.

v2.2: удалён window_command() — старый текстовый парсер, дублировал
tool calling (core/pc_tools.py). Здесь только низкоуровневые функции.
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
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Команда не ответила вовремя"
    except Exception as e:
        return False, str(e)


def show_desktop():
    """Показать рабочий стол (свернуть все окна)"""
    try:
        subprocess.run(["xdotool", "key", "super+d"], timeout=5)
        return True, "Показываю рабочий стол"
    except Exception:
        ok, output = _run_wmctrl(["-l"])
        if not ok:
            return False, output
        for line in output.split("\n"):
            if line.strip():
                window_id = line.split()[0]
                _run_wmctrl(["-i", "-r", window_id, "-b", "add,hidden"])
        return True, "Сворачиваю все окна"


def minimize_window():
    """Свернуть текущее окно"""
    try:
        subprocess.run(["xdotool", "key", "super+h"], timeout=5)
        return True, "Сворачиваю окно"
    except Exception:
        return False, "Не получилось свернуть окно"


def maximize_window():
    """Развернуть текущее окно"""
    ok, output = _run_wmctrl(["-r", ":ACTIVE:", "-b", "toggle,maximized_vert,maximized_horz"])
    if ok:
        return True, "Разворачиваю окно"
    return False, output


def close_window():
    """Закрыть текущее окно"""
    ok, output = _run_wmctrl(["-c", ":ACTIVE:"])
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
    for i, line in enumerate(lines[:10], 1):
        parts = line.split(None, 3)
        if len(parts) >= 4:
            result += f"{i}. {parts[3]}\n"
    return True, result


def switch_window():
    """Переключиться на следующее окно"""
    try:
        subprocess.run(["xdotool", "key", "alt+Tab"], timeout=5)
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
