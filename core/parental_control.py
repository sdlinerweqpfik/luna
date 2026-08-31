"""
Родительский контроль: логирование запросов и уведомления маме.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("secretary.parental")

KIDS_LOG_FILE = Path.home() / "secretary" / "kids_log.json"

# Ключевые слова для "подозрительных" запросов
SUSPICIOUS_KEYWORDS = [
    "убить", "взломать", "наркотики", "алкоголь", "секс",
    "порно", "мат", "дурак", "тупой", "ненавижу",
    "самоубийство", "оружие", "бомба", "взрыв",
    # Добавь свои триггеры
]


def log_kids_request(text: str, response: str, source: str = "mobile", suspicious: bool = False):
    """Записать запрос в лог.
    
    Args:
        text: Текст запроса
        response: Ответ Луны
        source: Источник (mobile/voice)
        suspicious: Флаг подозрительного запроса
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "text": text,
        "response": response[:200],  # обрезаем длинные ответы
        "source": source,
        "suspicious": suspicious,  # Теперь сохраняем флаг
    }
    try:
        logs = []
        if KIDS_LOG_FILE.exists():
            with open(KIDS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(entry)
        # Храним последние 1000 записей
        logs = logs[-1000:]
        with open(KIDS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Ошибка логирования: {e}")


def check_suspicious(text: str) -> bool:
    """Проверить запрос на подозрительные слова."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SUSPICIOUS_KEYWORDS)


def notify_parent(text: str, response: str = "", reason: str = "подозрительный запрос"):
    """Отправить уведомление маме (пока только лог)."""
    log.warning(f"🚨 РОДИТЕЛЬСКИЙ КОНТРОЛЬ: {reason} — «{text[:100]}»")
    # TODO: интеграция с Telegram-ботом мамы
    # send_telegram_message(mama_chat_id, f"⚠️ {reason}: {text}")
