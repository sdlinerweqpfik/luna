"""
Инструменты памяти для LLM tool calling.
remember_fact / recall_facts — обратная совместимость.
update_fact / delete_fact / list_my_facts — новые инструменты v2.
"""
import logging

log = logging.getLogger("secretary.memory_tools")

# Заполняется в main.py: memory_tools.MEMORY = memory
MEMORY = None


def remember_fact(fact: str, category: str = "personal", importance: int = 3) -> str:
    """Запомнить важный факт о пользователе.

    Используй ТОЛЬКО для действительно полезных долгосрочных фактов:
    предпочтения, проекты, важные даты, технические детали, привычки.

    НЕ сохраняй: обычные вопросы, одноразовые действия, временные состояния,
    содержимое диалога, предположения. Если не уверена — НЕ сохраняй.

    Args:
        fact: текст факта, например 'Люблю Portal' или 'Проект называется Luna'
        category: одна из: preference, project, routine, important_date, personal, technical, temporary
        importance: важность от 1 до 5 (5 = критическое, 1 = минимальное)
    """
    if MEMORY is None:
        return "Память недоступна."
    return MEMORY.add_fact(fact, category=category, importance=importance)


def recall_facts(query: str = "") -> str:
    """Вспомнить факты о пользователе.

    Если указан запрос — вернёт релевантные факты.
    Если пустой — вернёт самые важные общие факты.

    Args:
        query: тема поиска, например 'Minecraft' или 'предпочтения'. Пустая строка для всех важных.
    """
    if MEMORY is None:
        return "Память недоступна."
    facts = MEMORY.recall(query)
    if not facts:
        return "Ничего не помню по этому запросу."
    lines = []
    for f in facts:
        lines.append(f"[{f.get('category', '?')}] {f.get('text', '')}")
    return "\n".join(lines)


def update_fact(fact_id: str, new_text: str) -> str:
    """Обновить существующий факт по его ID.

    Используй когда пользователь просит исправить информацию.
    ID факта можно получить через list_my_facts.

    Args:
        fact_id: ID факта (12 символов)
        new_text: новый текст факта
    """
    if MEMORY is None:
        return "Память недоступна."
    return MEMORY.update_fact(fact_id, new_text)


def delete_fact(fact_id: str) -> str:
    """Удалить факт по ID.

    Используй когда пользователь просит забыть что-то.
    ID факта можно получить через list_my_facts.

    Args:
        fact_id: ID факта (12 символов)
    """
    if MEMORY is None:
        return "Память недоступна."
    return MEMORY.delete_fact(fact_id)


def list_my_facts() -> str:
    """Показать все сохранённые факты пользователя с их ID.

    Используй когда пользователь спрашивает 'что ты обо мне помнишь?'
    или когда нужен ID для update_fact/delete_fact.
    """
    if MEMORY is None:
        return "Память недоступна."
    facts = MEMORY.list_facts()
    if not facts:
        return "Пока ничего не помню о тебе."
    lines = []
    for f in facts:
        lines.append(
            f"[{f.get('id', '?')}] ({f.get('category', '?')}, "
            f"важность {f.get('importance', '?')}): {f.get('text', '')}"
        )
    return "\n".join(lines)


# Реестр для tools.py
MEMORY_TOOLS = [
    remember_fact,
    recall_facts,
    update_fact,
    delete_fact,
    list_my_facts,
]
