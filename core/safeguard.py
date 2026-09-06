"""
Предохранитель «Буран»: мониторинг аномалий и безопасный режим.

Идея из бортового компьютера Бурана: подсистема, которая «разошлась
с большинством», отключается от контура управления. В Луне вероятностный
слой (LLM + tool calling) — тот самый компьютер: при потоке аномалий он
отключается на SAFE_MODE_SECONDS, а детерминированное ядро (быстрые
команды, Confirmation Manager) продолжает работать.

Сигналы и веса:
  tool_burst  (3) — LLM попыталась сделать >4 инструментов за запрос
  injection   (2) — попытка отравить память инструкцией
  deny        (1) — шлюз отклонил неизвестный/запрещённый инструмент

Счёт затухает за DECAY_SECONDS. Порог THRESHOLD -> безопасный режим.
"""
import threading
import time
import logging

from core import diagnostics

log = logging.getLogger("secretary.safeguard")

WEIGHTS = {"tool_burst": 3, "injection": 2, "deny": 1, "integrity": 4}
THRESHOLD = 4
DECAY_SECONDS = 600        # окно затухания аномалий
SAFE_MODE_SECONDS = 300    # безопасный режим: 5 минут

# Заполняется в main.py, как TTS_INSTANCE в tools.py
TTS_INSTANCE = None


class Safeguard:
    def __init__(self):
        self._events = []          # (timestamp, weight)
        self._safe_until = 0.0
        self._lock = threading.Lock()

    def report(self, signal: str):
        """Зарегистрировать аномалию. При пороге — безопасный режим."""
        weight = WEIGHTS.get(signal, 1)
        now = time.time()
        with self._lock:
            self._events = [(ts, w) for ts, w in self._events
                            if now - ts < DECAY_SECONDS]
            self._events.append((now, weight))
            score = sum(w for _, w in self._events)
        log.warning(f"[SAFEGUARD] сигнал {signal}, счёт {score}")
        diagnostics.log_event("safeguard", signal=signal, score=score)
        if score >= THRESHOLD and not self.is_safe_mode():
            self._enter_safe_mode()

    def _enter_safe_mode(self):
        with self._lock:
            self._safe_until = time.time() + SAFE_MODE_SECONDS
            self._events = []
        log.warning("🛰 БУРАН: порог аномалий — вероятностный слой отключён на 5 минут")
        diagnostics.log_event("safeguard", event="safe_mode_on")
        
        # Попытка опустить Занавес (если veil доступен)
        try:
            from core.veil import lockdown
            lockdown()
        except Exception as e:
            log.error(f"veil lockdown error: {e}")
        
        if TTS_INSTANCE is not None:
            try:
                TTS_INSTANCE.speak(
                    "Обнаружена аномальная активность. Перехожу в безопасный режим. "
                    "Быстрые команды работают, сложные задачи вернутся через пять минут."
                )
            except Exception as e:
                log.error(f"safeguard tts error: {e}")

    def is_safe_mode(self):
        return time.time() < self._safe_until

    def remaining(self):
        return max(0, int(self._safe_until - time.time()))

    def release(self):
        """Досрочный возврат — только явной командой человека."""
        with self._lock:
            self._safe_until = 0.0
        log.info("🛰 БУРАН: безопасный режим снят человеком")
        diagnostics.log_event("safeguard", event="safe_mode_off")


safeguard = Safeguard()
