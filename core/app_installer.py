"""
Установка приложений: белый список + строгая валидация безопасности.
"""
import subprocess
import shutil
import re
import logging

log = logging.getLogger("secretary.app_installer")

APPS = {
    "zen браузер": ("zen-browser-bin", "Zen Browser", "aur"),
    "zen browser": ("zen-browser-bin", "Zen Browser", "aur"),
    "зен браузер": ("zen-browser-bin", "Zen Browser", "aur"),
    "firefox": ("firefox", "Firefox", "pacman"),
    "хром": ("chromium", "Chromium", "pacman"),
    "chromium": ("chromium", "Chromium", "pacman"),
    "браузер": ("firefox", "Firefox", "pacman"),
    "телеграм": ("telegram-desktop", "Telegram", "pacman"),
    "telegram": ("telegram-desktop", "Telegram", "pacman"),
    "дискорд": ("discord", "Discord", "pacman"),
    "discord": ("discord", "Discord", "pacman"),
    "либре офис": ("libreoffice-fresh", "LibreOffice", "pacman"),
    "libreoffice": ("libreoffice-fresh", "LibreOffice", "pacman"),
    "vlc": ("vlc", "VLC плеер", "pacman"),
    "плеер": ("vlc", "VLC плеер", "pacman"),
    "gimp": ("gimp", "GIMP", "pacman"),
    "vscode": ("code", "VS Code", "pacman"),
    "vs code": ("code", "VS Code", "pacman"),
}

_pending_install = None


def _find_aur_helper():
    """Находит AUR хелпер (yay или paru)"""
    if shutil.which("yay"):
        return "yay"
    if shutil.which("paru"):
        return "paru"
    return None


def _check_installed(package_name):
    """Проверяет установлен ли пакет"""
    try:
        result = subprocess.run(["pacman", "-Q", package_name], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def find_app(text):
    """Ищет приложение в белом списке"""
    text_lower = text.lower()
    for keyword, (package, name, source) in APPS.items():
        if keyword in text_lower:
            return package, name, source
    return None, None, None


def validate_command(cmd):
    """Строгая валидация команды безопасности."""
    cmd_lower = cmd.lower().strip()
    allowed_starts = ["pacman", "sudo pacman", "yay", "paru", "sudo yay", "sudo paru"]
    if not any(cmd_lower.startswith(s) for s in allowed_starts):
        return False, "Команда должна начинаться с pacman, yay или paru"

    forbidden = ["rm", "dd", "mkfs", "fdisk", "chmod 777", ">", "<", "|", "&", ";",
                 "`", "$(", "curl", "wget", "bash", "sh -c", "eval", "sudo rm", "sudo dd"]
    for f in forbidden:
        if f in cmd_lower:
            return False, f"Запрещённая конструкция: {f}"

    flags = re.findall(r'-\w+', cmd)
    allowed_flags = {"-S", "-s", "-Q", "-Sy", "-Syu", "--noconfirm", "--needed", "-y", "-u", "-a", "--aur"}
    for flag in flags:
        if flag not in allowed_flags:
            return False, f"Запрещённый флаг: {flag}"

    if len(cmd) > 200:
        return False, "Команда слишком длинная"
    return True, "OK"


def app_installer_command(text, llm=None):
    """Главная функция для команд установки (используется через tool calling в pc_tools)."""
    return False, ""
