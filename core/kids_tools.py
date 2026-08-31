"""
Безопасные инструменты для детского профиля.
Только информационные и развлекательные — никакого управления ПК.
"""
from core.tools import TOOLS_BY_NAME

KIDS_ALLOWED_TOOLS = [
    "get_current_time",
    "get_weather",
    "get_joke",
    "get_fact",
    "calculate",
    "search_wikipedia",
    "translate",
    # Добавь сюда только то, что безопасно для ребёнка
]

KIDS_TOOLS_BY_NAME = {
    name: TOOLS_BY_NAME[name]
    for name in KIDS_ALLOWED_TOOLS
    if name in TOOLS_BY_NAME
}
