"""
Память ассистента, организованная по говорящему (speaker_id).

Сейчас voice ID (распознавание конкретного говорящего по голосу) ещё не
реализовано, поэтому везде используется speaker_id="default" как заглушка —
де-факто один профиль, как было раньше. Но само хранение уже разделено по
speaker_id с самого начала, чтобы когда появится реальное распознавание
голоса, нужно было только начать передавать настоящий speaker_id вместо
"default" — остальную структуру (файл на диске, буфер диалога, факты)
переделывать не придётся.

Формат memory.json:
{
  "default": {"facts": [...], "preferences": {...}},
  "<speaker_id_2>": {"facts": [...], "preferences": {...}},
  ...
}
"""
import json
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("secretary.memory")

MEMORY_FILE = Path.home() / "secretary" / "memory.json"
MAX_BUFFER_SIZE = 20
DEFAULT_SPEAKER = "default"


class Memory:
    def __init__(self, speaker_id: str = DEFAULT_SPEAKER):
        self.speaker_id = speaker_id
        # dialog_buffer — кратковременный, per-speaker, но НЕ сохраняется на
        # диск (разговор текущей сессии), поэтому держим отдельно от long_term
        self.dialog_buffer = []
        self._all_speakers = self._load_all_speakers()
        if self.speaker_id not in self._all_speakers:
            self._all_speakers[self.speaker_id] = {"facts": [], "preferences": {}}
        log.info(f"Память инициализирована для speaker_id={self.speaker_id}")

    @property
    def long_term(self):
        """Данные текущего говорящего. Оставлено как property с тем же именем,
        что раньше (self.long_term), чтобы не ломать код, который к нему уже
        обращается (например memory_tools.forget_all_facts)."""
        return self._all_speakers[self.speaker_id]

    def switch_speaker(self, speaker_id: str):
        """Переключиться на профиль другого говорящего (для будущего voice ID).
        Буфер текущего диалога НЕ переносится — это осознанное решение: разговор
        привязан к конкретной "сессии слушания", а долговременные факты — к
        говорящему."""
        if speaker_id not in self._all_speakers:
            self._all_speakers[speaker_id] = {"facts": [], "preferences": {}}
        self.speaker_id = speaker_id
        self.dialog_buffer = []
        log.info(f"Переключено на speaker_id={speaker_id}")

    # === БУФЕР ДИАЛОГА (кратковременный, текущая сессия) ===

    def add_to_dialog(self, role, text):
        """Добавляет реплику в буфер"""
        self.dialog_buffer.append({
            "role": role,
            "text": text,
            "time": datetime.now().isoformat()
        })

        if len(self.dialog_buffer) > MAX_BUFFER_SIZE:
            self.dialog_buffer.pop(0)

    def get_dialog_context(self):
        """Возвращает контекст диалога для LLM"""
        if not self.dialog_buffer:
            return ""

        context = "Предыдущие реплики диалога:\n"
        for msg in self.dialog_buffer[-10:]:
            role = "Пользователь" if msg["role"] == "user" else "Луна"
            context += f"{role}: {msg['text']}\n"

        return context

    def clear_dialog(self):
        """Очищает буфер диалога"""
        self.dialog_buffer = []

    # === ДОЛГОВРЕМЕННАЯ ПАМЯТЬ (per-speaker, на диске) ===

    def _load_all_speakers(self):
        """Загружает память всех говорящих с диска. Поддерживает миграцию
        со старого плоского формата ({"facts": [...], "preferences": {...}})
        в новый ({"default": {"facts": [...], ...}})."""
        if MEMORY_FILE.exists():
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Миграция старого формата: если есть ключ "facts" на верхнем
                # уровне — это старый плоский файл одного профиля, заворачиваем
                # его в speaker_id="default"
                if "facts" in data and DEFAULT_SPEAKER not in data:
                    log.info("Обнаружен старый формат памяти — мигрирую в default")
                    return {DEFAULT_SPEAKER: data}

                return data
            except Exception as e:
                log.error(f"Ошибка загрузки памяти: {e}")

        return {}

    def _save_long_term(self):
        """Сохраняет память всех говорящих на диск"""
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._all_speakers, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"Ошибка сохранения памяти: {e}")

    def remember(self, text):
        """Запоминает факт для текущего говорящего"""
        self.long_term["facts"].append({
            "text": text,
            "time": datetime.now().isoformat()
        })
        self._save_long_term()

    def get_facts(self):
        """Возвращает все факты текущего говорящего"""
        return [f["text"] for f in self.long_term["facts"]]

    def get_full_context(self):
        """Полный контекст для LLM"""
        context = ""

        facts = self.get_facts()
        if facts:
            context += "Что я знаю о пользователе:\n"
            for fact in facts[-5:]:
                context += f"- {fact}\n"
            context += "\n"

        context += self.get_dialog_context()
        return context

    def memory_command(self, text):
        """Обрабатывает команды памяти по прямой фразе.

        Оставлено для случаев, когда явный текстовый триггер понятнее, чем
        полагаться на решение модели (например 'очисти память' — операция
        разрушительная, надёжнее ловить её напрямую). Для остального
        (remember_fact/recall_facts) основной путь теперь —
        core/memory_tools.py через tool calling.
        """
        text_lower = text.lower()

        if "очисти память" in text_lower or "забудь всё" in text_lower:
            self.long_term["facts"] = []
            self._save_long_term()
            self.clear_dialog()
            return True, "Память очищена"

        return False, ""
