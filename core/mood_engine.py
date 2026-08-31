"""
Mood Engine — характер и настроения Луны («Лундере»).

- статическая часть характера (character + quirks) и динамический хвост
  настроения (moods) подмешиваются в системный промпт llm.py;
- ленивые таймстемпы сессий: долгое молчание между активациями =
  холодный старт (без фоновых таймеров);
- триггеры настроения из текста и событий (pet — для будущих панелей);
- режим отрицания для пасхалки «торт».
"""
import re
import time
import random
import logging
from pathlib import Path

import yaml

log = logging.getLogger("secretary.mood")

PERSONALITY_FILE = Path(__file__).resolve().parent.parent / "personality.yaml"


class MoodEngine:
    def __init__(self, path=PERSONALITY_FILE):
        self._cfg = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._cfg = yaml.safe_load(f) or {}
        except Exception as e:
            log.warning(f"personality.yaml не загружен: {e}")
        self.current_mood = "neutral"
        self.mood_until = 0.0
        self.last_interaction = time.time()
        self.last_session_end = None

    # ================= ПРОМПТ ДЛЯ LLM =================

    def get_character_prompt(self):
        """Статическая часть характера (всегда в промпте)."""
        parts = []
        if self._cfg.get("character"):
            parts.append(str(self._cfg["character"]).strip())
        if self._cfg.get("quirks"):
            parts.append(str(self._cfg["quirks"]).strip())
        return "\n".join(parts)

    def get_mood_prompt(self):
        """Динамический хвост: текущее настроение."""
        if time.time() > self.mood_until:
            self.current_mood = "neutral"
        text = self._cfg.get("moods", {}).get(self.current_mood, "")
        return str(text).strip() if text else ""

    # ================= СЕССИИ (ленивое молчание) =================

    def end_session(self):
        """Вызывается когда разговор закончился."""
        self.last_session_end = time.time()

    def start_session(self):
        """Вызывается при активации. Долгое молчание = холодный старт."""
        if self.last_session_end is not None:
            silence = time.time() - self.last_session_end
            if silence > 300:
                self._set_mood("tsundere_cold", 30)
                log.info(f"Долгое молчание ({silence:.0f} сек) — холодный старт")

    def update_interaction(self):
        self.last_interaction = time.time()

    # ================= ТРИГГЕРЫ =================

    def check_triggers(self, text=None, event=None):
        """Смена настроения по тексту или событию (pet и т.п.)."""
        for tr in self._cfg.get("mood_triggers", []):
            if event and tr.get("event") == event:
                self._set_mood(tr["mood"], tr.get("duration", 30))
                return
            if text and "pattern" in tr:
                if re.search(tr["pattern"], text, re.IGNORECASE):
                    self._set_mood(tr["mood"], tr.get("duration", 30))
                    return

    def force_mood(self, mood, duration):
        """Принудительное настроение (пасхалки)."""
        self._set_mood(mood, duration)

    # ================= КОНСЕРВНЫЕ РЕПЛИКИ =================

    def maybe_deny(self, text):
        """В режиме aperture_secret — отрицать всё про голос/торт."""
        if self.current_mood != "aperture_secret" or time.time() > self.mood_until:
            return None
        resp = self._cfg.get("responses", {}).get("denial", {})
        pattern = resp.get("questions_pattern", "")
        if pattern and re.search(pattern, text, re.IGNORECASE):
            lines = resp.get("lines", [])
            return random.choice(lines) if lines else None
        return None

    def get_response(self, key):
        """Случайная консервная реплика (pet и т.п.)."""
        lines = self._cfg.get("responses", {}).get(key, {}).get("lines", [])
        return random.choice(lines) if lines else ""

    # ================= ВНУТРЕННЕЕ =================

    def _set_mood(self, mood, duration):
        self.current_mood = mood
        self.mood_until = time.time() + duration
        log.info(f"Настроение: {mood} на {duration} сек")


engine = MoodEngine()
