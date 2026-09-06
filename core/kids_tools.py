"""
Безопасные инструменты для детского профиля.
Только информационные — никакого управления ПК.
Анекдоты и факты идут через fast_command, translate появится в v2.5.
"""
from core.tools import TOOLS_BY_NAME

KIDS_ALLOWED_TOOLS = [
    "get_current_time",
    "get_weather",
    "calculate",
    "get_wikipedia",
]

KIDS_TOOLS_BY_NAME = {
    name: TOOLS_BY_NAME[name]
    for name in KIDS_ALLOWED_TOOLS
    if name in TOOLS_BY_NAME
}
