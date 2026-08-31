"""
Пасхалка «Торт — это ложь».

Триггер: 3 упоминания «торт/кейк/cake» в течение 10 минут ИЛИ точное
«заклинание» (торт — это ложь / the cake is a lie). Первые упоминания
НИЧЕГО не делают — пользователь должен засомневаться.

Эффект: голосовой режим aperture на 20 минут + настроение
aperture_secret (Луна отрицает всё). Событие для будущих панелей
логируется — панели подхватят его позже через MQTT.
"""
import re
import time
import logging

log = logging.getLogger("secretary.egg")

MENTION_PATTERN = r"\b(торт|кейк|cake)\b"
SPELL_PATTERN = r"(торт\s*(—|-)?\s*это\s+ложь|the\s+cake\s+is\s+a\s+lie)"

TRIGGER_MENTIONS = 3
MENTION_WINDOW = 600       # 10 минут
APERTURE_DURATION = 1200   # 20 минут

ACTIVATION_LINE = "Ты сказал это три раза. Ты сам виноват."


class ApertureEgg:
    def __init__(self):
        self._mentions = []
        self._aperture_until = 0.0

    def is_active(self):
        return time.time() < self._aperture_until

    def feed(self, text):
        """Скормить реплику пользователя.
        Возвращает dict {line, duration} при активации, иначе None."""
        if self.is_active():
            return None
        low = text.lower()
        if re.search(SPELL_PATTERN, low):
            return self._activate()
        if re.search(MENTION_PATTERN, low):
            now = time.time()
            self._mentions = [t for t in self._mentions
                              if now - t < MENTION_WINDOW]
            self._mentions.append(now)
            log.info(f" Упоминание торта {len(self._mentions)}/{TRIGGER_MENTIONS}")
            if len(self._mentions) >= TRIGGER_MENTIONS:
                self._mentions = []
                return self._activate()
        return None

    def _activate(self):
        self._aperture_until = time.time() + APERTURE_DURATION
        log.info("🎂 ПАСХАЛКА: aperture-режим на 20 минут. "
                 "Панели (будущие): повернуться к говорившему, мигнуть, вернуться.")
        return {"line": ACTIVATION_LINE, "duration": APERTURE_DURATION}


egg = ApertureEgg()
