"""
Context Manager v1 — тонкий read-only слой между состоянием Luna и LLM.

Собирает контекст из подсистем, НЕ изменяя их. Возвращает неизменяемый
snapshot (dataclass с копиями данных). Последующие изменения Memory,
dialog_buffer, ConfirmationManager, active_plan или Personality
не влияют на уже сформированный Context.

Не хранит вторую копию памяти. Не выполняет tools/confirmation/TTS/STT.
Не принимает решения о безопасности. Только читает и формирует Context.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

log = logging.getLogger("secretary.context")

# === Лимиты контекста ===
MAX_CORE_MEMORY = 3       # ядро: только самые важные факты
MAX_RELEVANT_MEMORY = 5   # релевантные запросу
MAX_DIALOG_TURNS = 6      # последних реплик (3 пары user/assistant)
MAX_CONTEXT_CHARS = 4000  # общий лимит символов всего контекста


@dataclass(frozen=True)
class Context:
    """Неизменяемый snapshot контекста для одного запроса."""
    core_memory: tuple = ()           # топ-N по importance
    relevant_memory: tuple = ()       # релевантные запросу (без дубликатов с core)
    recent_dialog: tuple = ()         # последние K реплик
    active_task_summary: str = ""     # "" если нет активного плана
    confirmation_hint: str = ""       # "" если нет pending
    personality_fragment: str = ""    # имя, стиль, город

    def to_messages(self) -> List[Dict[str, str]]:
        """Преобразовать Context в список messages для LLM."""
        messages = []

        # System prompt собирается вызывающим (llm.py), Context даёт только
        # дополнительные system-сообщения с памятью и состоянием.

        # Core memory (ядро личности — всегда показываем)
        if self.core_memory:
            lines = "\n".join(self.core_memory)
            messages.append({
                "role": "system",
                "content": f"Что известно о пользователе (основное):\n{lines}"
            })

        # Relevant memory (релевантное запросу)
        if self.relevant_memory:
            lines = "\n".join(self.relevant_memory)
            messages.append({
                "role": "system",
                "content": f"Релевантные факты для этого вопроса:\n{lines}"
            })

        # Active task
        if self.active_task_summary:
            messages.append({
                "role": "system",
                "content": f"Активная задача: {self.active_task_summary}"
            })

        # Confirmation hint
        if self.confirmation_hint:
            messages.append({
                "role": "system",
                "content": self.confirmation_hint
            })

        # Recent dialog → user/assistant messages
        for turn in self.recent_dialog:
            messages.append({
                "role": turn.get("role", "user"),
                "content": turn.get("text", "")
            })

        return messages

    def total_chars(self) -> int:
        """Общий размер контекста в символах."""
        total = 0
        for item in self.core_memory:
            total += len(item)
        for item in self.relevant_memory:
            total += len(item)
        for turn in self.recent_dialog:
            total += len(turn.get("text", ""))
        total += len(self.active_task_summary)
        total += len(self.confirmation_hint)
        total += len(self.personality_fragment)
        return total


def build_context(
    request: str,
    memory=None,
    personality=None,
    dialog_buffer: Optional[List[Dict]] = None,
    active_pending=None,
    active_plan=None,
) -> Context:
    """Собрать контекст для одного запроса. Read-only: не изменяет аргументы.

    Args:
        request: текущий запрос пользователя
        memory: Memory v2 instance (читается, не пишется)
        personality: Personality instance (читается, не пишется)
        dialog_buffer: список реплик диалога (читается, не пишется)
        active_pending: PendingAction или None (читается, не пишется)
        active_plan: Plan или None (читается, не пишется)

    Returns:
        Context (frozen dataclass, snapshot)
    """
    # === Core memory: топ по importance (без запроса) ===
    core_facts = []
    if memory is not None:
        try:
            core = memory.recall("", limit=MAX_CORE_MEMORY)
            core_facts = [
                f"[{f.get('category', '?')}] {f.get('text', '')}"
                for f in core
            ]
        except Exception as e:
            log.warning(f"Ошибка чтения core memory: {e}")

    # === Relevant memory: релевантные запросу ===
    relevant_facts = []
    if memory is not None and request.strip():
        try:
            relevant = memory.recall(request, limit=MAX_RELEVANT_MEMORY)
            # Убираем дубликаты с core (по тексту)
            core_texts = set(core_facts)
            relevant_facts = [
                f"[{f.get('category', '?')}] {f.get('text', '')}"
                for f in relevant
                if f"[{f.get('category', '?')}] {f.get('text', '')}" not in core_texts
            ]
        except Exception as e:
            log.warning(f"Ошибка чтения relevant memory: {e}")

    # === Recent dialog: копия последних K реплик ===
    recent = ()
    if dialog_buffer:
        recent = tuple(
            {"role": t.get("role", "user"), "text": t.get("text", "")}
            for t in dialog_buffer[-MAX_DIALOG_TURNS:]
        )

    # === Active task summary ===
    task_summary = ""
    if active_plan is not None:
        try:
            state = active_plan.state.value if hasattr(active_plan.state, 'value') else str(active_plan.state)
            goal = getattr(active_plan, 'goal', '')
            idx = getattr(active_plan, 'current_index', 0)
            total = len(getattr(active_plan, 'steps', []))
            task_summary = f"{goal} (шаг {idx}/{total}, состояние: {state})"
        except Exception as e:
            log.warning(f"Ошибка чтения active plan: {e}")

    # === Confirmation hint ===
    confirm_hint = ""
    if active_pending is not None:
        try:
            summary = getattr(active_pending, 'summary', '')
            status = getattr(active_pending, 'status', '')
            if status in ("prompted", "prompting"):
                confirm_hint = (
                    f"⚠️ Ожидается подтверждение действия: «{summary}». "
                    f"Не предлагай новые опасные действия, пока пользователь не ответит."
                )
        except Exception as e:
            log.warning(f"Ошибка чтения pending: {e}")

    # === Personality fragment ===
    pers_fragment = ""
    if personality is not None:
        try:
            pers_fragment = personality.get_system_prompt_fragment()
        except Exception as e:
            log.warning(f"Ошибка чтения personality: {e}")

    # === Сборка и обрезка по MAX_CONTEXT_CHARS ===
    ctx = Context(
        core_memory=tuple(core_facts),
        relevant_memory=tuple(relevant_facts),
        recent_dialog=recent,
        active_task_summary=task_summary,
        confirmation_hint=confirm_hint,
        personality_fragment=pers_fragment,
    )

    # Если превышен лимит — обрезаем с низшим приоритетом
    if ctx.total_chars() > MAX_CONTEXT_CHARS:
        ctx = _trim_context(ctx)

    return ctx


def _trim_context(ctx: Context) -> Context:
    """Обрезать контекст до MAX_CONTEXT_CHARS.
    Приоритет сохранения (от высшего к низшему):
    confirmation_hint > active_task > core_memory > relevant_memory > dialog
    """
    budget = MAX_CONTEXT_CHARS

    # Confirmation hint и active task — не обрезаем (они короткие и критичные)
    reserved = len(ctx.confirmation_hint) + len(ctx.active_task_summary) + len(ctx.personality_fragment)
    budget -= reserved

    # Core memory — сохраняем полностью если влезает
    core_chars = sum(len(c) for c in ctx.core_memory)
    if core_chars <= budget:
        budget -= core_chars
        core = ctx.core_memory
    else:
        # Обрезаем core с конца (менее важные)
        core = []
        for c in ctx.core_memory:
            if len(c) <= budget:
                core.append(c)
                budget -= len(c)
            else:
                break
        core = tuple(core)

    # Relevant memory
    relevant = []
    for r in ctx.relevant_memory:
        if len(r) <= budget:
            relevant.append(r)
            budget -= len(r)
        else:
            break
    relevant = tuple(relevant)

    # Dialog — остаток бюджета
    dialog = []
    for t in ctx.recent_dialog:
        tlen = len(t.get("text", ""))
        if tlen <= budget:
            dialog.append(t)
            budget -= tlen
        else:
            break
    dialog = tuple(dialog)

    return Context(
        core_memory=core,
        relevant_memory=relevant,
        recent_dialog=dialog,
        active_task_summary=ctx.active_task_summary,
        confirmation_hint=ctx.confirmation_hint,
        personality_fragment=ctx.personality_fragment,
    )
