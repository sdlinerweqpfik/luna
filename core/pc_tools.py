"""
Инструменты управления ПК/окнами/установкой приложений под нативный tool calling.

БЕЗОПАСНОСТЬ (архитектура Confirmation Manager):
Опасные действия (выключение, перезагрузка, установка) НЕ выполняются
немедленно и НЕ могут быть подтверждены самой моделью. При вызове такого
инструмента создаётся "pending action" в core/confirmation.py, который
ожидает ЯВНОГО голосового подтверждения пользователя ('да'/'нет').
Параметр confirm в сигнатуре оставлен для обратной совместимости, но
ИГНОРИРУЕТСЯ — решение о выполнении принимает только пользователь.
"""
import logging
import subprocess

from core.pc_control import (
    open_app as _open_app,
    open_folder as _open_folder,
    get_system_info as _get_system_info,
    cancel_shutdown as _cancel_shutdown,
    _run_safe_command,
)
from core.window_control import (
    show_desktop as _show_desktop,
    minimize_window as _minimize_window,
    maximize_window as _maximize_window,
    close_window as _close_window,
    list_windows as _list_windows,
    switch_window as _switch_window,
    focus_window_by_number as _focus_window_by_number,
)
from core.app_installer import (
    find_app as _find_app,
    _check_installed,
    _find_aur_helper,
)
from core.confirmation import request_confirmation

log = logging.getLogger("secretary.pc_tools")


# ============ ПРИЛОЖЕНИЯ (безопасно) ============

def open_application(app_name: str) -> str:
    """Открыть приложение из белого списка (браузер, файлы, терминал, редактор,
    калькулятор, музыка, видео, фото, код, настройки).
    Args:
        app_name: название приложения как его назвал пользователь, например 'браузер' или 'терминал'
    """
    success, message = _open_app(app_name)
    if not success and not message:
        return f"Приложение '{app_name}' не найдено в белом списке."
    return message


def open_folder_path(path: str) -> str:
    """Открыть папку в файловом менеджере по пути.
    Args:
        path: путь к папке, например ~/Documents
    """
    success, message = _open_folder(path)
    return message


# ============ СИСТЕМНАЯ ИНФОРМАЦИЯ (безопасно, только чтение) ============

def get_system_status(info_type: str) -> str:
    """Получить информацию о системе: загрузка, процессор, память, диск, процессы.
    Args:
        info_type: тип информации — один из: загрузка, процессор, память, диск, процессы
    """
    success, message = _get_system_info(info_type)
    return message if message else "Не удалось получить информацию."


# ============ ПИТАНИЕ (опасно — только через голосовое подтверждение) ============

def shutdown_pc(confirm: bool = False) -> str:
    """Выключить компьютер. ОПАСНОЕ действие. При вызове создаётся запрос
    на подтверждение — пользователь должен подтвердить голосом, сказав 'да'.
    Параметр confirm игнорируется: подтверждение даёт только пользователь.
    """
    def _do_shutdown(payload):
        success, stdout, stderr = _run_safe_command(["shutdown", "-h", "+1"])
        if success:
            return "Выключаю систему через минуту. Скажи 'отмена выключения', чтобы отменить."
        return f"Не получилось выключить: {stderr}"

    return request_confirmation(
        action_type="shutdown_pc",
        summary="Выключение компьютера",
        payload={},
        executor=_do_shutdown,
    )


def cancel_pc_shutdown() -> str:
    """Отменить запланированное выключение компьютера. Безопасная операция."""
    success, message = _cancel_shutdown()
    return message


def reboot_pc(confirm: bool = False) -> str:
    """Перезагрузить компьютер. ОПАСНОЕ действие — создаёт запрос на
    подтверждение, пользователь подтверждает голосом, сказав 'да'.
    Параметр confirm игнорируется.
    """
    def _do_reboot(payload):
        success, stdout, stderr = _run_safe_command(["reboot"])
        if success:
            return "Перезагружаю систему."
        return f"Не получилось перезагрузить: {stderr}"

    return request_confirmation(
        action_type="reboot_pc",
        summary="Перезагрузка компьютера",
        payload={},
        executor=_do_reboot,
    )


# ============ ОКНА (безопасно) ============

def show_desktop() -> str:
    """Показать рабочий стол, свернув все окна."""
    _, message = _show_desktop()
    return message


def minimize_current_window() -> str:
    """Свернуть текущее активное окно."""
    _, message = _minimize_window()
    return message


def maximize_current_window() -> str:
    """Развернуть текущее активное окно на весь экран."""
    _, message = _maximize_window()
    return message


def close_current_window() -> str:
    """Закрыть текущее активное окно."""
    _, message = _close_window()
    return message


def list_open_windows() -> str:
    """Показать список всех открытых окон с номерами."""
    _, message = _list_windows()
    return message


def switch_to_next_window() -> str:
    """Переключиться на следующее открытое окно (аналог Alt+Tab)."""
    _, message = _switch_window()
    return message


def focus_window(window_number: int) -> str:
    """Переключить фокус на окно по номеру из списка открытых окон.
    Args:
        window_number: номер окна, например 1 для первого окна в списке
    """
    _, message = _focus_window_by_number(window_number)
    return message


# ============ УСТАНОВКА ПРИЛОЖЕНИЙ (опасно — только через голосовое подтверждение) ============

def install_application(app_name: str, confirm: bool = False) -> str:
    """Установить приложение из белого списка. ОПАСНОЕ действие — создаёт
    запрос на подтверждение, пользователь подтверждает голосом, сказав 'да'.
    Параметр confirm игнорируется.
    Args:
        app_name: название приложения, например 'телеграм' или 'discord'
    """
    package, name, source = _find_app(app_name)
    if not package:
        return (
            f"'{app_name}' нет в белом списке известных приложений. "
            "Установка вне белого списка через голос сейчас не поддерживается."
        )
    if _check_installed(package):
        return f"{name} уже установлен."

    def _do_install(payload):
        package = payload["package"]
        source = payload["source"]
        name = payload["name"]
        if source == "aur":
            helper = _find_aur_helper()
            if not helper:
                return "Для AUR-пакетов нужен yay или paru, ни один не найден."
            cmd = [helper, "-S", "--noconfirm", package]
        else:
            cmd = ["sudo", "pacman", "-S", "--noconfirm", package]
        try:
            log.info(f"Устанавливаю {name} ({package})")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                return f"{name} установлен успешно."
            log.error(f"Ошибка установки {name}: {result.stderr}")
            return f"Не получилось установить {name}."
        except subprocess.TimeoutExpired:
            return "Установка заняла слишком много времени и была прервана."
        except Exception as e:
            log.error(f"Ошибка установки: {e}")
            return f"Ошибка при установке: {e}"

    return request_confirmation(
        action_type="install_application",
        summary=f"Установка приложения {name}",
        payload={"package": package, "name": name, "source": source},
        executor=_do_install,
        ttl_seconds=120,   # установка долгая — даём больше времени на подтверждение
    )


# ============ РЕЕСТР ДЛЯ tools.py ============

PC_TOOLS = [
    open_application,
    open_folder_path,
    get_system_status,
    shutdown_pc,
    cancel_pc_shutdown,
    reboot_pc,
    show_desktop,
    minimize_current_window,
    maximize_current_window,
    close_current_window,
    list_open_windows,
    switch_to_next_window,
    focus_window,
    install_application,
]
