"""
Security Gateway v1 — единая точка политики для tool calls.

Маршрутизация:
  SAFE     -> один вызов инструмента (после валидации аргументов)
  CONFIRM  -> вызов инструмента; инструмент САМ создаёт pending
              через Confirmation Manager. Gateway не выполняет действие
              и не подтверждает его.
  ADVANCED -> вызов планировщика; каждый его шаг идёт через Gateway
  DENY     -> отказ, функция не вызывается

Принципы:
- default DENY: инструмента нет в таблице -> отказ
- Gateway не запускает внешние процессы и не выполняет код
- Gateway не подтверждает действия и не заменяет Confirmation Manager
- аргументы от LLM не доверены: доверенные ключи вырезаются
"""
import logging
from pathlib import Path

log = logging.getLogger("secretary.security")

SAFE = "SAFE"
CONFIRM = "CONFIRM"
ADVANCED = "ADVANCED"
DENY = "DENY"

# Централизованная policy table. Default = DENY.
TOOL_POLICIES = {
    # SAFE: read-only / локальные данные (core/tools.py)
    "search_web": SAFE,
    "get_current_time": SAFE,
    "get_weather": SAFE,
    "calculate": SAFE,
    "get_currency_rate": SAFE,
    "get_wikipedia": SAFE,
    "set_timer": SAFE,
    "add_shopping_item": SAFE,
    "show_shopping_list": SAFE,
    "remove_shopping_item": SAFE,
    "clear_shopping_list": SAFE,
    "add_task": SAFE,
    "show_tasks": SAFE,
    "complete_task": SAFE,
    "clear_tasks": SAFE,
    "remember_fact": SAFE,
    "recall_facts": SAFE,
    # SAFE: PC (core/pc_tools.py)
    "get_system_status": SAFE,
    "open_application": SAFE,           # whitelist внутри инструмента
    "open_folder_path": SAFE,           # + path-валидация Gateway
    "cancel_pc_shutdown": SAFE,
    "show_desktop": SAFE,
    "minimize_current_window": SAFE,
    "maximize_current_window": SAFE,
    "close_current_window": SAFE,       # UI-действие
    "list_open_windows": SAFE,
    "switch_to_next_window": SAFE,
    "focus_window": SAFE,
    # CONFIRM: изменяют систему; pending создаёт сам инструмент
    "shutdown_pc": CONFIRM,
    "reboot_pc": CONFIRM,
    "install_application": CONFIRM,
    # ADVANCED: планировщик, шаги идут через Gateway
    "advanced_task": ADVANCED,
}

# Явный DENY для попыток произвольного выполнения
EXPLICIT_DENY = {
    "run_command", "run_shell", "shell", "system", "os_command",
    "exec_code", "eval_code", "python_exec", "execute_shell",
    "subprocess_run", "run_python", "bash", "cmd",
}

# Ключи, которые LLM не может задавать как доверенные
UNTRUSTED_ARG_KEYS = {
    "confirm", "approved", "user_confirmed", "source", "authorized",
    "authenticated", "authorization_level", "admin", "sudo", "trusted",
}

PATH_ARG_KEYS = {"path", "folder", "directory", "folder_path"}

FORBIDDEN_PATH_PREFIXES = (
    "/etc", "/root", "/sys", "/proc", "/boot", "/usr", "/var",
    "/bin", "/sbin", "/lib", "/opt", "/dev", "/srv",
)

DENY_MESSAGE = "Это действие запрещено политикой безопасности."


def classify(name):
    """Классификация инструмента. Default = DENY."""
    if name in EXPLICIT_DENY:
        return DENY
    return TOOL_POLICIES.get(name, DENY)


def _sanitize_args(args):
    """Вырезает доверенные ключи из аргументов LLM."""
    return {k: v for k, v in (args or {}).items()
            if k not in UNTRUSTED_ARG_KEYS}


def _validate_path(value):
    """Путь должен вести внутрь home; обходы запрещены."""
    if not isinstance(value, str) or not value.strip():
        return False
    raw = value.strip()
    if ".." in raw.replace("\\", "/").split("/") or "\x00" in raw:
        return False
    try:
        resolved = Path(raw).expanduser().resolve()
    except Exception:
        return False
    home = Path.home().resolve()
    if not (resolved == home or home in resolved.parents):
        return False
    s = str(resolved)
    for pref in FORBIDDEN_PATH_PREFIXES:
        if s == pref or s.startswith(pref + "/"):
            return False
    return True


def _validate_args(name, args):
    """Policy-валидация аргументов.
    Пакетные/белосписочные проверки остаются в инструментах
    (install_application: APPS-whitelist — авторитетный слой)."""
    for key in PATH_ARG_KEYS:
        if key in args and not _validate_path(args[key]):
            return False, "path"
    return True, None


def execute(name, fn, args=None, context=None):
    """Единственная точка выполнения tool call из LLM и Advanced.

    context принимается, но доверенные поля (source и т.п.) НИКОГДА
    не читаются из аргументов модели и не могут быть ею заданы.
    """
    policy = classify(name)

    if fn is None or policy == DENY:
        log.warning(f"[SECURITY] DENY {name}")
        return DENY_MESSAGE

    args = _sanitize_args(args)

    if policy == SAFE:
        ok, kind = _validate_args(name, args)
        if not ok:
            log.warning(f"[SECURITY] DENY {name} (bad {kind})")
            return f"Недопустимый аргумент для {name}."
        log.info(f"[SECURITY] ALLOW {name}")
        return fn(**args)

    if policy == CONFIRM:
        # Инструмент сам создаёт pending через request_confirmation.
        # Gateway НЕ выполняет действие и НЕ подтверждает его.
        log.info(f"[SECURITY] CONFIRM {name}")
        return fn(**args)

    if policy == ADVANCED:
        log.info(f"[SECURITY] ADVANCED {name}")
        return fn(**args)

    log.warning(f"[SECURITY] DENY {name}")
    return DENY_MESSAGE
