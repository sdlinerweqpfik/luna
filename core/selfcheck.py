"""
Самопроверка контура «Буран + Периметр»:
1. Манифест целостности: sha256 критичных файлов, сверка на старте и по расписанию
2. Heartbeat watchdog: потоки шлют пульс, тишина → рестарт + уведомление
3. Доверенное время: аппаратный RTC как источник, drift = аномалия
4. Сетевой дозор: DNS-запросы вне белого списка = «Луна сама вышла в интернет»

🛡️ Исправлено после аудита Gemini VULN-06: добавлены main.py, senses.py,
veil.py, commands.py в CRITICAL_FILES. Убраны хвостовые пробелы.
"""
import hashlib
import json
import os
import threading
import time
import logging

from core import diagnostics
from core.safeguard import safeguard

log = logging.getLogger("secretary.selfcheck")

MANIFEST_FILE = os.path.expanduser("~/Luna/integrity_manifest.json")

# Полный список критичных файлов (исправлено после Gemini VULN-06)
CRITICAL_FILES = [
    "core/security_gateway.py",
    "core/confirmation.py",
    "core/safeguard.py",
    "core/llm.py",
    "core/trusted_clock.py",
    "core/selfcheck.py",
    "core/pc_tools.py",
    "core/kids_tools.py",
    "core/senses.py",      # ← добавлено
    "core/veil.py",        # ← добавлено
    "core/commands.py",    # ← добавлено
    "main.py",             # ← добавлено
    "config.yaml",
]

# Белый список доменов, куда Луна имеет право обращаться
DNS_WHITELIST = {
    "wttr.in",                    # погода
    "www.cbr-xml-daily.ru",       # курсы ЦБ
    "ru.wikipedia.org",           # Википедия
    "en.wikipedia.org",
    "api.duckduckgo.com",         # поиск
    "html.duckduckgo.com",
    "lite.duckduckgo.com",
    "localhost",                  # Ollama
    "127.0.0.1",
}

HEARTBEAT_TIMEOUT = 180  # 3 минуты тишины = поток мёртв
CHECK_INTERVAL = 3600    # проверка целостности раз в час


# ===================== МАНИФЕСТ =====================

def _hash_file(path: str) -> str:
    """SHA-256 файла."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return "MISSING"
    except Exception as e:
        return f"ERROR:{e}"


def _resolve_paths(base_dir: str = None):
    """Резолвит пути критичных файлов относительно base_dir."""
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return {f: os.path.join(base_dir, f) for f in CRITICAL_FILES}


def seal(base_dir: str = None):
    """Опечатать контур: сохранить хеши критичных файлов.
    Вызывается ВРУЧНУЮ после аудита кода: 'python -c "from core.selfcheck import seal; seal()"'
    """
    paths = _resolve_paths(base_dir)
    manifest = {}
    for name, full_path in paths.items():
        manifest[name] = _hash_file(full_path)
    os.makedirs(os.path.dirname(MANIFEST_FILE), exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)
    log.info(f"🔏 Контур опечатан: {len(manifest)} файлов → {MANIFEST_FILE}")
    return manifest


def check_integrity(base_dir: str = None) -> dict:
    """Проверить целостность: сравнить текущие хеши с манифестом.
    Возвращает {"ok": bool, "violations": [...], "missing_manifest": bool}
    """
    if not os.path.exists(MANIFEST_FILE):
        return {"ok": None, "violations": [], "missing_manifest": True}

    with open(MANIFEST_FILE, "r") as f:
        manifest = json.load(f)

    paths = _resolve_paths(base_dir)
    violations = []
    for name, expected_hash in manifest.items():
        full_path = paths.get(name, name)
        actual = _hash_file(full_path)
        if actual != expected_hash:
            violations.append({
                "file": name,
                "expected": expected_hash[:16] + "…",
                "actual": actual[:16] + "…",
            })

    ok = len(violations) == 0
    if not ok:
        log.warning(f"[SELFCHECK] INTEGRITY VIOLATION: {violations}")
        diagnostics.log_event("selfcheck", event="integrity_violation",
                              files=[v["file"] for v in violations])
        safeguard.report("integrity")

    return {"ok": ok, "violations": violations, "missing_manifest": False}


# ===================== HEARTBEAT =====================

_heartbeats = {}
_heartbeat_lock = threading.Lock()


def heartbeat(name: str):
    """Поток/модуль регистрирует свой пульс."""
    with _heartbeat_lock:
        _heartbeats[name] = time.time()


def check_heartbeats() -> dict:
    """Проверяет, все ли потоки живы."""
    now = time.time()
    dead = {}
    with _heartbeat_lock:
        for name, last in _heartbeats.items():
            silence = now - last
            if silence > HEARTBEAT_TIMEOUT:
                dead[name] = round(silence, 1)
    if dead:
        log.warning(f"[SELFCHECK] DEAD THREADS: {dead}")
        diagnostics.log_event("selfcheck", event="dead_threads", threads=dead)
    return {"ok": len(dead) == 0, "dead": dead}


# ===================== СЕТЕВОЙ ДОЗОР =====================

def check_dns_log(log_path: str = "/var/log/pihole.log") -> dict:
    """Проверяет DNS-лог (pi-hole или systemd-resolved) на запросы вне белого списка.
    Для начала: парсит последние N строк лога.
    """
    unknown_domains = []
    try:
        if not os.path.exists(log_path):
            return {"ok": None, "reason": "dns log not found"}
        with open(log_path, "r") as f:
            lines = f.readlines()[-200:]
        for line in lines:
            for domain in DNS_WHITELIST:
                if domain in line:
                    break
            else:
                # Строка не содержит ни одного белого домена
                # Простой парсер: ищем query-строки
                if "query" in line.lower():
                    unknown_domains.append(line.strip()[:120])
    except PermissionError:
        return {"ok": None, "reason": "no permission to read dns log"}
    except Exception as e:
        return {"ok": None, "reason": str(e)}

    if unknown_domains:
        log.warning(f"[SELFCHECK] UNKNOWN DNS: {len(unknown_domains)} запросов")
        diagnostics.log_event("selfcheck", event="unknown_dns",
                              count=len(unknown_domains))
    return {"ok": len(unknown_domains) == 0, "unknown": unknown_domains[:10]}


# ===================== ФОНОВЫЙ ЦИКЛ =====================

class SelfCheckLoop:
    """Фоновый цикл проверок: целостность + пульс + drift + DNS."""

    def __init__(self, tts=None):
        self.tts = tts

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        log.info("🛰 SelfCheck loop запущен")

    def _run(self):
        # Первая проверка через 30 секунд после старта (дать время загрузиться)
        time.sleep(30)
        while True:
            try:
                self._tick()
            except Exception as e:
                log.error(f"selfcheck tick error: {e}")
            time.sleep(CHECK_INTERVAL)

    def _tick(self):
        # 1. Целостность файлов
        integrity = check_integrity()
        if integrity["missing_manifest"]:
            log.info("[SELFCHECK] манифест не найден — опечатай контур: "
                     "python -c 'from core.selfcheck import seal; seal()'")
        elif not integrity["ok"]:
            files = ", ".join(v["file"] for v in integrity["violations"])
            self._alert(f"Целостность нарушена: {files}")

        # 2. Drift часов
        from core.trusted_clock import check_drift
        drift = check_drift()
        if drift["ok"] is False:
            self._alert(f"Расхождение часов: {drift['drift_seconds']} секунд. "
                        "Возможна подмена системного времени.")
            safeguard.report("integrity")

        # 3. Heartbeat
        hb = check_heartbeats()
        if not hb["ok"]:
            names = ", ".join(hb["dead"].keys())
            self._alert(f"Потоки не отвечают: {names}")

        # 4. DNS (если доступен лог)
        dns = check_dns_log()
        if dns.get("ok") is False:
            self._alert("Обнаружены DNS-запросы вне белого списка. "
                        "Возможен несанкционированный выход в сеть.")
            safeguard.report("integrity")

    def _alert(self, message: str):
        """Уведомление: голос + лог + диагностика."""
        log.warning(f"🚨 SELFCHECK ALERT: {message}")
        diagnostics.log_event("selfcheck", event="alert", message=message)
        if self.tts is not None:
            try:
                self.tts.speak(f"Внимание. {message}")
            except Exception:
                pass
