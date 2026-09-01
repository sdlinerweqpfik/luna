"""
Детский конвейер v1 — общая обработка детских запросов для голоса и remote.
Pre-LLM перехват опасных команд, KidsLLM, родительское логирование.
"""
import logging

from core import diagnostics
from core import parental_control
from core.commands import fast_command
from core.llm import KidsLLM

log = logging.getLogger("secretary.kids")

DANGEROUS_KEYWORDS = [
    "выключи", "перезагрузи", "удали", "открой", "установи",
    "запусти", "скачай", "браузер", "проводник", "папку",
]

REFUSAL = ("👶 Я не умею управлять компьютером. Я могу рассказать анекдот, "
           "сказать время, помочь с математикой или рассказать интересное!")


def is_dangerous_for_kids(text: str) -> bool:
    """Жёсткий pre-LLM перехват: опасные детские запросы не доходят до модели."""
    t = text.lower()
    return any(kw in t for kw in DANGEROUS_KEYWORDS)


class KidsPipeline:
    """Одна детская сессия (голос или remote)."""

    def __init__(self, memory=None, personality=None):
        self.llm = KidsLLM(cfg={}, memory=memory, personality=personality)
        self.memory = memory
        self.personality = personality

    def handle(self, text: str, source: str = "voice") -> str:
        """Полная обработка детского запроса. Возвращает текст ответа."""
        if is_dangerous_for_kids(text):
            diagnostics.log_event("kids", event="intercept", source=source)
            parental_control.log_kids_request(text, REFUSAL, source=source)
            return REFUSAL

        is_fast, fast_answer = fast_command(text, llm=self.llm,
                                            memory=self.memory,
                                            personality=self.personality)
        if is_fast:
            parental_control.log_kids_request(text, fast_answer, source=source)
            return fast_answer

        answer = self.llm.ask(self.llm.fast, text)
        suspicious = parental_control.check_suspicious(text)
        parental_control.log_kids_request(text, answer, source=source,
                                          suspicious=suspicious)
        if suspicious:
            parental_control.notify_parent(text, answer)
        return answer
