"""
Безопасное управление ПК.
Принципы: белый список команд, запрет системных папок.

v2.2: удалён pc_command() — старый текстовый парсер с параллельным
текстовым подтверждением («скажи подтверждаю выключение»), который
обходил Confirmation Manager v3. Весь ввод теперь идёт через LLM
tool calling (core/pc_tools.py) и шлюз. Здесь остаются только
низкоуровневые функции, которые pc_tools использует.
"""
import subprocess
import os
import logging

log = logging.getLogger("secretary.pc_control")

PROTECTED_PATHS = [
    "/etc", "/usr", "/var", "/boot", "/proc", "/sys", "/dev",
    "/root", "/lib", "/lib64", "/bin", "/sbin",
]

APPS = {
    "браузер": ("firefox", "Firefox"),
    "firefox": ("firefox", "Firefox"),
    "файлы": ("thunar", "Файловый менеджер"),
    "проводник": ("thunar", "Файловый менеджер"),
    "папки": ("thunar", "Файловый менеджер"),
    "терминал": ("xfce4-terminal", "Терминал"),
    "консоль": ("xfce4-terminal", "Терминал"),
    "редактор": ("mousepad", "Текстовый редактор"),
    "блокнот": ("mousepad", "Текстовый редактор"),
    "калькулятор": ("gnome-calculator", "Калькулятор"),
    "музыка": ("rhythmbox", "Музыка"),
    "видео": ("vlc", "VLC"),
    "плеер": ("vlc", "VLC"),
    "фото": ("gimp", "GIMP"),
    "графика": ("gimp", "GIMP"),
    "код": ("code", "VS Code"),
    "vscode": ("code", "VS Code"),
    "vs code": ("code", "VS Code"),
    "настройки": ("xfce4-settings-manager", "Настройки"),
}

SYSTEM_COMMANDS = {
    "загрузка": "Загрузка системы",
    "процессор": "Загрузка процессора",
    "память": "Использование памяти",
    "диск": "Свободное место",
    "процессы": "Топ процессов",
}


def _run_safe_command(cmd, timeout=10):
    """Безопасный запуск команды с таймаутом"""
    try:
        result = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Команда не ответила вовремя"
    except Exception as e:
        return False, "", str(e)


def open_app(text):
    """Открывает приложение из белого списка"""
    text_lower = text.lower()
    for keyword, (command, name) in APPS.items():
        if keyword in text_lower:
            try:
                subprocess.Popen([command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log.info(f"Открыто приложение: {name}")
                return True, f"Открываю {name}"
            except FileNotFoundError:
                log.warning(f"Приложение не найдено: {command}")
                return False, f"{name} не установлен на этой системе"
            except Exception as e:
                log.error(f"Ошибка запуска {name}: {e}")
                return False, f"Не получилось открыть {name}"
    return False, ""


def is_path_safe(path):
    """Проверяет что путь не в защищённой папке"""
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        for protected in PROTECTED_PATHS:
            if abs_path.startswith(protected):
                return False, f"Доступ к {protected} запрещён"
        if ".." in abs_path:
            return False, "Нельзя использовать '..' в пути"
        return True, abs_path
    except Exception as e:
        return False, f"Ошибка проверки пути: {e}"


def open_folder(path):
    """Открывает папку (с проверкой безопасности)"""
    is_safe, result = is_path_safe(path)
    if not is_safe:
        return False, f"⛔ {result}"
    if not os.path.exists(result):
        return False, f"Папка не найдена: {path}"
    try:
        subprocess.Popen(["thunar", result], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, f"Открываю папку {path}"
    except Exception as e:
        return False, f"Не получилось открыть папку: {e}"


def get_system_info(info_type):
    """Получает информацию о системе (только чтение, безопасно)"""
    commands = {
        "загрузка": ["uptime"],
        "процессор": ["sh", "-c", "top -bn1 | head -5"],
        "память": ["free", "-h"],
        "диск": ["df", "-h", "/home"],
        "процессы": ["sh", "-c", "ps aux --sort=-%cpu | head -6"],
    }
    cmd = commands.get(info_type)
    if not cmd:
        return False, "Неизвестная команда"

    success, stdout, stderr = _run_safe_command(cmd)
    if not success:
        return False, f"Ошибка: {stderr}"

    if info_type == "память":
        for line in stdout.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                return True, f"Использовано {parts[2]} из {parts[1]}"
    elif info_type == "диск":
        lines = stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                return True, f"Свободно {parts[3]} из {parts[1]} ({parts[4]} занято)"
    elif info_type == "загрузка":
        parts = stdout.split("load average:")
        if len(parts) > 1:
            loads = parts[1].strip().split(",")
            return True, f"Загрузка процессора: {loads[0].strip()}"

    return True, stdout.strip()[:200]


def cancel_shutdown():
    """Отмена выключения"""
    success, stdout, stderr = _run_safe_command(["shutdown", "-c"])
    if success:
        return True, "Выключение отменено"
    return False, f"Не получилось: {stderr}"
