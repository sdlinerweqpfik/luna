"""
Observability / Diagnostics v1 — временный диагностический слой.

НЕ бизнес-логика и НЕ часть security. Не меняет поведение модулей:
только структурированные события (JSON) с request_id для восстановления
полной цепочки обработки запроса.

API:
    diagnostics.init(cfg)          # из config.yaml: diagnostics.enabled
    diagnostics.start_request(source, text)
    diagnostics.end_request(error=None)
    diagnostics.log_event(event, **fields)
    diagnostics.span(event, **fields)   # context manager с duration_ms

При выключенной диагностике все вызовы — один if, overhead минимальный.
"""
import contextvars
import json
import logging
import time
import uuid
from datetime import datetime

log = logging.getLogger("diagnostics")
log.propagate = False
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

NO_REQUEST = "no_request"
_rid = contextvars.ContextVar("diag_request_id", default=NO_REQUEST)

_enabled = False
_log_content = False
_RECORDS = []
MAX_RECORDS = 2000

_SENSITIVE_KEYS = {
    "password", "token", "secret", "api_key", "apikey",
    "authorization", "cookie", "audio",
}


def init(cfg=None) -> bool:
    """Включить/выключить диагностику из config.yaml."""
    global _enabled, _log_content
    cfg = cfg or {}
    _enabled = bool(cfg.get("enabled", False))
    _log_content = bool(cfg.get("log_content", False))
    if _enabled:
        install_bridge()
    return _enabled


def is_enabled() -> bool:
    return _enabled


def current_request_id() -> str:
    return _rid.get()


def _scrub(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k.lower() in _SENSITIVE_KEYS:
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        else:
            out[k] = v
    return out


def _emit(event: str, level: str, fields: dict):
    rec = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
        "request_id": _rid.get(),
    }
    rec.update(_scrub(fields))
    _RECORDS.append(rec)
    if len(_RECORDS) > MAX_RECORDS:
        del _RECORDS[: len(_RECORDS) - MAX_RECORDS]
    log.info(json.dumps(rec, ensure_ascii=False, default=str))


def start_request(source: str = "system", text=None) -> str:
    if not _enabled:
        return NO_REQUEST
    rid = uuid.uuid4().hex[:12]
    _rid.set(rid)
    fields = {"source": source}
    if text:
        if _log_content:
            fields["text"] = str(text)[:300]
        else:
            fields["text_len"] = len(str(text))
    _emit("request_start", "INFO", fields)
    return rid


def end_request(error=None):
    if not _enabled:
        return
    fields = {}
    if error is not None:
        fields["error"] = str(error)[:200]
    _emit("request_end", "ERROR" if error else "INFO", fields)
    _rid.set(NO_REQUEST)


def log_event(*args, **fields):
    """Работает в обоих стилях:
    log_event("confirmation", stage=...)  и  log_event("advanced", event="plan_created", ...)
    """
    if not _enabled:
        return
    level = fields.pop("level", "INFO")
    event = fields.pop("event", None) or (args[0] if args else "unknown")
    _emit(event, level, fields)

class span:
    """with diagnostics.span('tool', tool=name) as sp: ... — duration_ms."""

    def __init__(self, event: str, **fields):
        self.event = event
        self.fields = fields
        self._t0 = None

    def __enter__(self):
        if _enabled:
            self._t0 = time.monotonic()
        return self

    def update(self, **fields):
        self.fields.update(fields)
        return self

    def __exit__(self, exc_type, exc, tb):
        if not _enabled or self._t0 is None:
            return False
        self.fields["duration_ms"] = round((time.monotonic() - self._t0) * 1000, 1)
        if exc is not None:
            self.fields["success"] = False
            self.fields["error"] = type(exc).__name__
        else:
            self.fields.setdefault("success", True)
        _emit(self.event, "ERROR" if exc else "INFO", self.fields)
        return False


def get_records() -> list:
    return list(_RECORDS)


def clear_records():
    _RECORDS.clear()


# ===== Мост: ловим существующие логи Security Gateway БЕЗ изменения модуля =====

class _BridgeHandler(logging.Handler):
    def emit(self, record):
        if not _enabled:
            return
        try:
            msg = record.getMessage()
        except Exception:
            return
        if msg.startswith("[SECURITY] ALLOW "):
            _emit("gateway_allow", "INFO", {"tool": msg.split()[2]})
        elif msg.startswith("[SECURITY] DENY "):
            parts = msg.split(" ", 3)
            tool = parts[2]
            reason = parts[3].strip("()") if len(parts) > 3 else "policy"
            _emit("gateway_deny", "WARNING", {"tool": tool, "reason": reason})
        elif msg.startswith("[SECURITY] CONFIRM "):
            _emit("gateway_confirm", "INFO", {"tool": msg.split()[2]})
        elif msg.startswith("[SECURITY] ADVANCED "):
            _emit("gateway_advanced", "INFO", {"tool": msg.split()[2]})


_bridge = None


def install_bridge():
    global _bridge
    sec_log = logging.getLogger("secretary.security")
    sec_log.setLevel(logging.DEBUG)  # чтобы INFO-события доходили до моста
    if _bridge is None:
        _bridge = _BridgeHandler()
        sec_log.addHandler(_bridge)
