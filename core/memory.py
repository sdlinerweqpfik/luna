"""
Memory System v2 + Diagnostics v1 — структурированная долгосрочная память.

Хранение: JSON с атомарной записью (tmp + os.replace).
Факт: id, text, category, importance, created_at, updated_at.
Дедупликация: точное совпадение нормализованного текста.
Recall: keyword scoring + importance, без vector DB / RAG.
"""
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from core import diagnostics

log = logging.getLogger("secretary.memory")

MEMORY_FILE = Path.home() / "secretary" / "memory.json"

# === Ограничения ===
MAX_FACTS = 500
MAX_TEXT_LENGTH = 500
MAX_RECALL_RESULTS = 10
MAX_CONTEXT_FACTS = 8

# === Категории ===
VALID_CATEGORIES = {
    "preference", "project", "routine", "important_date",
    "personal", "technical", "temporary",
}

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5

_STOPWORDS = frozenset({
    "и", "в", "на", "с", "по", "к", "о", "а", "но", "не", "что", "как",
    "это", "то", "да", "нет", "для", "от", "до", "за", "из", "у", "при",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "no", "only", "own", "same", "than", "too", "very",
    "just", "because", "if", "when", "where", "how", "what", "which",
    "who", "whom", "this", "that", "these", "those", "i", "me", "my",
    "we", "our", "you", "your", "he", "him", "his", "she", "her", "it",
    "its", "they", "them", "their", "am",
})


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r'[^\w\s]', '', t, flags=re.UNICODE)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _extract_keywords(text: str) -> set:
    words = re.findall(r'[\w]+', text.lower(), flags=re.UNICODE)
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Fact:
    def __init__(self, text: str, category: str = "personal",
                 importance: int = 3, fact_id: str = None,
                 created_at: str = None, updated_at: str = None):
        self.id = fact_id or uuid.uuid4().hex[:12]
        self.text = text[:MAX_TEXT_LENGTH]
        self.category = category if category in VALID_CATEGORIES else "personal"
        self.importance = max(MIN_IMPORTANCE, min(MAX_IMPORTANCE, importance))
        self.created_at = created_at or _now_iso()
        self.updated_at = updated_at or self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(
            text=d.get("text", ""),
            category=d.get("category", "personal"),
            importance=d.get("importance", 3),
            fact_id=d.get("id"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def __repr__(self):
        return f"Fact({self.id}, imp={self.importance}, cat={self.category}, '{self.text[:40]}')"


class Memory:
    def __init__(self, speaker_id: str = "default"):
        self.speaker_id = speaker_id
        self._data: Dict[str, List[Dict]] = {}
        self.dialog_buffer: List[Dict[str, str]] = []
        self._load()

    # ================= ПЕРСИСТЕНТНОСТЬ =================

    def _load(self):
        if not MEMORY_FILE.exists():
            self._data = {}
            return
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            log.error(f"Ошибка загрузки памяти: {e}. Начинаю с пустой.")
            self._data = {}
            return

        migrated = False
        for sid, facts in raw.items():
            if isinstance(facts, list) and facts and isinstance(facts[0], str):
                log.info(f"Миграция памяти speaker='{sid}' из старого формата")
                self._data[sid] = [
                    Fact(text=s, category="personal", importance=3).to_dict()
                    for s in facts if isinstance(s, str) and s.strip()
                ]
                migrated = True
            elif isinstance(facts, list):
                self._data[sid] = facts
            else:
                self._data[sid] = []

        if migrated:
            self._save()

    def _save(self):
        try:
            dir_path = MEMORY_FILE.parent
            dir_path.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", prefix="memory_", dir=str(dir_path)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, str(MEMORY_FILE))
            except Exception:
                os.unlink(tmp_path)
                raise
        except Exception as e:
            log.error(f"Ошибка сохранения памяти: {e}")

    # ================= SPEAKER =================

    def switch_speaker(self, speaker_id: str):
        self.speaker_id = speaker_id
        self.dialog_buffer = []

    def _facts(self) -> List[Dict]:
        return self._data.setdefault(self.speaker_id, [])

    # ================= CRUD =================

    def add_fact(self, text: str, category: str = "personal",
                 importance: int = 3) -> str:
        """Добавить факт. Возвращает сообщение о результате."""
        text = text.strip()[:MAX_TEXT_LENGTH]
        if not text:
            return "Пустой факт — не сохранён."

        norm = _normalize(text)
        facts = self._facts()

        for f in facts:
            if _normalize(f.get("text", "")) == norm:
                f["updated_at"] = _now_iso()
                self._save()
                diagnostics.log_event("memory", op="remember_dup")
                return f"Уже знаю: «{f['text']}» (обновлено)."

        if len(facts) >= MAX_FACTS:
            facts.sort(key=lambda x: (x.get("importance", 1), x.get("created_at", "")))
            removed = facts.pop(0)
            log.info(f"Лимит фактов: удалён «{removed.get('text', '')[:40]}»")

        fact = Fact(text=text, category=category, importance=importance)
        facts.append(fact.to_dict())
        self._save()
        diagnostics.log_event("memory", op="remember", total=len(facts))
        return f"Запомнила: «{fact.text}»"

    def get_fact_by_id(self, fact_id: str) -> Optional[Dict]:
        for f in self._facts():
            if f.get("id") == fact_id:
                return f
        return None

    def update_fact(self, fact_id: str, new_text: str) -> str:
        new_text = new_text.strip()[:MAX_TEXT_LENGTH]
        if not new_text:
            return "Пустой текст — обновление отменено."

        facts = self._facts()
        target = None
        for f in facts:
            if f.get("id") == fact_id:
                target = f
                break

        if target is None:
            return f"Факт с ID {fact_id} не найден."

        norm_new = _normalize(new_text)
        for f in facts:
            if f.get("id") != fact_id and _normalize(f.get("text", "")) == norm_new:
                return f"Такой факт уже существует: «{f['text']}» (ID: {f['id']})."

        target["text"] = new_text
        target["updated_at"] = _now_iso()
        self._save()
        diagnostics.log_event("memory", op="update")
        return f"Обновила факт {fact_id}: «{new_text}»"

    def delete_fact(self, fact_id: str) -> str:
        facts = self._facts()
        before = len(facts)
        self._data[self.speaker_id] = [f for f in facts if f.get("id") != fact_id]
        after = len(self._data[self.speaker_id])
        if before == after:
            return f"Факт с ID {fact_id} не найден."
        self._save()
        diagnostics.log_event("memory", op="delete")
        return f"Удалила факт {fact_id}."

    def list_facts(self) -> List[Dict]:
        return list(self._facts())

    def clear_facts(self) -> str:
        count = len(self._facts())
        self._data[self.speaker_id] = []
        self._save()
        return f"Очищена память: удалено {count} фактов."

        # === Совместимость с commands.py: быстрые команды памяти ===
    # === Совместимость с commands.py: быстрые команды памяти ===
    def memory_command(self, text: str):
        """Возвращает (обработано: bool, ответ: str)."""
        t = text.lower().strip()

        if t.startswith("запомни"):
            payload = text.strip()[len("запомни"):].strip()
            if not payload:
                return True, "Что именно запомнить?"
            return True, self.add_fact(payload)

        if ("что ты помнишь" in t or "что помнишь" in t
                or "что ты знаешь" in t):
            facts = self.list_facts()
            if not facts:
                return True, "Пока ничего не запомнила."
            top = sorted(facts, key=lambda f: -f.get("importance", 1))[:8]
            return True, "Помню: " + "; ".join(f.get("text", "") for f in top)

        if t.startswith("забудь всё") or t.startswith("забудь все") \
                or "очисти память" in t:
            return True, self.clear_facts()

        if t.startswith("забудь"):
            payload = text.strip()[len("забудь"):].strip()
            target = None
            for f in self.list_facts():
                if payload and payload in f.get("text", "").lower():
                    target = f
                    break
            if target is None:
                return True, "Не нашла такой факт."
            return True, self.delete_fact(target["id"])

        return False, ""

    # ================= RECALL =================

    def recall(self, query: str = "", limit: int = MAX_RECALL_RESULTS) -> List[Dict]:
        """Релевантный recall: keyword scoring + importance."""
        facts = self._facts()
        if not facts:
            diagnostics.log_event("memory", op="recall", found=0)
            return []

        if not query.strip():
            result = sorted(
                facts, key=lambda x: (-x.get("importance", 1), x.get("updated_at", ""))
            )[:limit]
            diagnostics.log_event("memory", op="recall", found=len(result))
            return result

        keywords = _extract_keywords(query)
        if not keywords:
            result = sorted(
                facts, key=lambda x: (-x.get("importance", 1), x.get("updated_at", ""))
            )[:limit]
            diagnostics.log_event("memory", op="recall", found=len(result))
            return result

        scored = []
        for f in facts:
            text_lower = f.get("text", "").lower()
            cat = f.get("category", "personal")
            imp = f.get("importance", 1)

            kw_matches = sum(1 for kw in keywords if kw in text_lower)
            cat_bonus = 1 if cat in ("preference", "project", "technical") else 0
            temp_penalty = -2 if cat == "temporary" else 0

            score = imp * 2 + kw_matches * 3 + cat_bonus + temp_penalty
            scored.append((score, f))

        scored.sort(key=lambda x: (-x[0], x[1].get("updated_at", "")))
        result = [f for _, f in scored[:limit]]
        diagnostics.log_event("memory", op="recall", found=len(result))
        return result

    # ================= КОНТЕКСТ ДЛЯ LLM =================

    def get_context_facts(self, query: str = "") -> List[str]:
        if not self._facts():
            return []
        relevant = self.recall(query, limit=MAX_CONTEXT_FACTS)
        return [f"- [{f.get('category', '?')}] {f.get('text', '')}" for f in relevant]

    # ================= ОБРАТНАЯ СОВМЕСТИМОСТЬ =================

    def get_facts(self) -> List[str]:
        return [f.get("text", "") for f in self._facts()]

    def add_to_dialog(self, role: str, text: str):
        self.dialog_buffer.append({"role": role, "text": text})
        if len(self.dialog_buffer) > 20:
            self.dialog_buffer = self.dialog_buffer[-20:]
