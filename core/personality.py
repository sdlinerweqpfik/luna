"""
Персонализация ассистента, организованная по говорящему (speaker_id).

Как и Memory — до появления реального voice ID используется
speaker_id="default". Структура уже готова под несколько профилей (разных
членов семьи), чтобы при добавлении распознавания голоса не пришлось
переделывать хранение с нуля.

Формат profile.json:
{
  "default": {"user_name": "...", "communication_style": "...", "city": "..."},
  "<speaker_id_2>": {...},
  ...
}
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("secretary.personality")

PROFILE_FILE = Path.home() / "secretary" / "profile.json"
DEFAULT_SPEAKER = "default"

_DEFAULT_PROFILE = {
    "user_name": "хозяин",
    "communication_style": "дружелюбный",
    "city": "",
}


class Personality:
    def __init__(self, speaker_id: str = DEFAULT_SPEAKER):
        self.speaker_id = speaker_id
        self._all_profiles = self._load_all_profiles()
        if self.speaker_id not in self._all_profiles:
            self._all_profiles[self.speaker_id] = dict(_DEFAULT_PROFILE)

    @property
    def profile(self):
        """Профиль текущего говорящего."""
        return self._all_profiles[self.speaker_id]

    def switch_speaker(self, speaker_id: str):
        """Переключиться на профиль другого говорящего (для будущего voice ID)."""
        if speaker_id not in self._all_profiles:
            self._all_profiles[speaker_id] = dict(_DEFAULT_PROFILE)
        self.speaker_id = speaker_id

    def _load_all_profiles(self):
        """Загружает профили всех говорящих. Мигрирует старый плоский формат
        (один профиль без speaker_id) в новый при первом запуске."""
        if PROFILE_FILE.exists():
            try:
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "user_name" in data and DEFAULT_SPEAKER not in data:
                    log.info("Обнаружен старый формат профиля — мигрирую в default")
                    return {DEFAULT_SPEAKER: data}

                return data
            except Exception:
                pass

        return {}

    def _save_profile(self):
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._all_profiles, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"Ошибка сохранения профиля: {e}")

    def get_user_name(self):
        return self.profile.get("user_name", "хозяин")

    def set_user_name(self, name):
        self.profile["user_name"] = name
        self._save_profile()

    def get_greeting(self):
        name = self.get_user_name()
        return f"Привет, {name}! Чем могу помочь?"

    def get_system_prompt_fragment(self):
        """Кусок system prompt с персонализацией текущего говорящего —
        подмешивается в llm.py к общему BASE_SYSTEM_PROMPT."""
        name = self.get_user_name()
        style = self.profile.get("communication_style", "дружелюбный")
        city = self.profile.get("city", "")

        fragment = f"Пользователя зовут {name}. Стиль общения: {style}."
        if city:
            fragment += f" Пользователь живёт в городе {city}."
        return fragment

    def personality_command(self, text):
        """Обрабатывает прямые команды персонализации по фразе."""
        text_lower = text.lower()

        if "меня зовут" in text_lower:
            parts = text_lower.split("меня зовут")
            if len(parts) > 1:
                name = parts[1].strip().capitalize()
                if name:
                    self.set_user_name(name)
                    return True, f"Приятно познакомиться, {name}! Буду звать тебя так"

        if "как меня зовут" in text_lower:
            return True, f"Тебя зовут {self.get_user_name()}"

        if "будь формальной" in text_lower or "официальный стиль" in text_lower:
            self.profile["communication_style"] = "формальный"
            self._save_profile()
            return True, "Хорошо, буду общаться формально"

        if "будь краткой" in text_lower or "краткий стиль" in text_lower:
            self.profile["communication_style"] = "краткий"
            self._save_profile()
            return True, "Хорошо, буду отвечать кратко"

        if "я живу в" in text_lower:
            parts = text_lower.split("я живу в")
            if len(parts) > 1:
                city = parts[1].strip().capitalize()
                if city:
                    self.profile["city"] = city
                    self._save_profile()
                    return True, f"Запомнила, ты живёшь в городе {city}"

        return False, ""
