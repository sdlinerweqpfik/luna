"""
Доверенные часы: аппаратный RTC материнской платы как источник времени,
независимый от системных часов и NTP.

Принцип: атакующий с root может подменить системное время (date -s),
но аппаратный RTC на кварце с батарейкой CR2032 тикает независимо.
Расхождение системного и аппаратного времени > DRIFT_THRESHOLD
= аномалия (либо NTP-спуфинг, либо ручная подмена, либо сдохла батарейка).

Используется:
- selfcheck.py (проверка целостности по доверенному времени)
- safeguard.py (TTL подтверждений по аппаратному, а не системному времени)
- audit log (метки времени, которые нельзя подделать софтом)
"""
import subprocess
import time
import logging
from datetime import datetime

from core import diagnostics

log = logging.getLogger("secretary.trusted_clock")

DRIFT_THRESHOLD_SECONDS = 30  # допустимое расхождение


def read_hwclock() -> float:
    """Читает аппаратный RTC. Возвращает unix timestamp.
    Требует доступа к /dev/rtc0 (обычно нужен root или группа rtc).
    """
    try:
        result = subprocess.run(
            ["hwclock", "--get", "--utc"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # hwclock выводит: "2026-09-05 14:23:45.123456+00:00"
            line = result.stdout.strip().split("\n")[0]
            # Убираем возможный суффикс с offset
            dt = datetime.fromisoformat(line.replace("+00:00", "+00:00"))
            return dt.timestamp()
    except Exception as e:
        log.warning(f"hwclock read failed: {e}")
    return 0.0


def read_hwclock_fallback() -> float:
    """Альтернатива: читает /sys/class/rtc/rtc0/since_epoch напрямую.
    Не требует root на большинстве систем.
    """
    try:
        with open("/sys/class/rtc/rtc0/since_epoch", "r") as f:
            return float(f.read().strip())
    except Exception as e:
        log.warning(f"rtc0 sysfs read failed: {e}")
    return 0.0


def trusted_time() -> float:
    """Возвращает доверенное время (unix timestamp).
    Приоритет: hwclock → sysfs → системное (с предупреждением).
    """
    hw = read_hwclock()
    if hw > 0:
        return hw
    hw = read_hwclock_fallback()
    if hw > 0:
        return hw
    log.warning("[TRUSTED_CLOCK] аппаратные часы недоступны, fallback на системное время")
    return time.time()


def check_drift() -> dict:
    """Проверяет расхождение системного и аппаратного времени.
    Возвращает dict с результатом.
    """
    hw = read_hwclock_fallback() or read_hwclock()
    if hw <= 0:
        return {"ok": None, "reason": "hwclock unavailable"}

    sys_time = time.time()
    drift = abs(sys_time - hw)

    result = {
        "ok": drift < DRIFT_THRESHOLD_SECONDS,
        "drift_seconds": round(drift, 2),
        "hw_time": hw,
        "sys_time": sys_time,
        "threshold": DRIFT_THRESHOLD_SECONDS,
    }

    if not result["ok"]:
        log.warning(f"[TRUSTED_CLOCK] DRIFT ALERT: {drift:.1f}s "
                    f"(порог {DRIFT_THRESHOLD_SECONDS}s)")
        diagnostics.log_event("trusted_clock", event="drift_alert",
                              drift=round(drift, 2))

    return result
