"""Хотфикс v2.3.1: автоматически применяет пункты 1-9 ревью."""
from pathlib import Path

BASE = Path(__file__).resolve().parent
report = []

def patch(rel_path, old, new, name):
    p = BASE / rel_path
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    if n == 0:
        report.append(f"❌ {name}: не нашлось в {rel_path} — файл не тронут")
        return
    if n > 1:
        report.append(f"⚠️ {name}: {n} совпадений в {rel_path} — файл не тронут")
        return
    p.write_text(src.replace(old, new), encoding="utf-8")
    report.append(f"✅ {name}")

# --- Пункт 1: llm.py — одиночная генерация для планировщика ---
patch("core/llm.py",
"""        messages = self._build_messages(question, include_history)
        try:
            for _hop in range(max_tool_hops):""",
"""        messages = self._build_messages(question, include_history)
        try:
            # max_tool_hops=0: одиночная генерация БЕЗ инструментов
            # (нужно планировщику Advanced, который ждёт чистый JSON)
            if max_tool_hops <= 0:
                with diagnostics.span("llm", model=model, hop=0):
                    response = self.client.chat(
                        model=model,
                        messages=messages,
                        options={
                            "temperature": 0.3,
                            "num_predict": 300,
                            "num_gpu": 12,
                            "num_ctx": 4096,
                            "num_batch": 256,
                        },
                    )
                answer = (response["message"].get("content") or "").strip()
                return self.clean_response(answer) if answer else "Не понял вопрос."
            for _hop in range(max_tool_hops):""",
"п.1 llm.py: single-shot для Advanced")

# --- Пункт 4: llm.py — KidsLLM через security gateway ---
patch("core/llm.py",
"result = fn(**args) if args else fn()",
"result = security_gateway.execute(name=name, fn=fn, args=args)",
"п.4 llm.py: KidsLLM через gateway")

# --- Пункт 2: remote_server.py — дети не подтверждают pending ---
patch("core/remote_server.py",
"        decision, pending_action = confirmation_manager.match_user_input(text)",
"""        if self.kids_mode:
            decision, pending_action = None, None
        else:
            decision, pending_action = confirmation_manager.match_user_input(text)""",
"п.2 remote: kids не трогает подтверждения")

patch("core/remote_server.py",
"""    def _resume(self, out):
        if self.planner is not None:""",
"""    def _resume(self, out):
        if self.kids_mode or self.planner is None:
            return
        if self.planner is not None:""",
"п.2 remote: _resume отключён для kids")

# --- Пункт 8: remote_server.py — дубль слова и текст отказа ---
patch("core/remote_server.py",
'"запусти", "скачай", "браузер", "проводник", "папку", "папку"',
'"запусти", "скачай", "браузер", "проводник", "папку"',
"п.8 remote: убран дубль 'папку'")

patch("core/remote_server.py",
"помочь с математикой или перевести текст!",
"помочь с математикой или рассказать интересное!",
"п.8 remote: текст отказа без translate")

# --- Пункт 3: parental_control.py — токены вместо подстрок ---
patch("core/parental_control.py",
"""import json
import logging""",
"""import json
import logging
import re""",
"п.3 parental: import re")

patch("core/parental_control.py",
'''def check_suspicious(text: str) -> bool:
    """Проверить запрос на подозрительные слова."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SUSPICIOUS_KEYWORDS)''',
'''def check_suspicious(text: str) -> bool:
    """Проверить запрос на подозрительные слова (по токенам, не подстрокам)."""
    tokens = set(re.findall(r"[а-яёa-z]+", text.lower()))
    return any(kw.strip() in tokens for kw in SUSPICIOUS_KEYWORDS)''',
"п.3 parental: 'математика' больше не подозрительна")

# --- Пункт 5: memory.py — сброс диалога при смене говорящего ---
patch("core/memory.py",
"""    def switch_speaker(self, speaker_id: str):
        self.speaker_id = speaker_id
""",
"""    def switch_speaker(self, speaker_id: str):
        self.speaker_id = speaker_id
        self.dialog_buffer = []
""",
"п.5 memory: сброс dialog_buffer")

# --- Пункт 6: tools.py — город из профиля, не из кода ---
patch("core/tools.py",
"TTS_INSTANCE = None",
"TTS_INSTANCE = None\nPERSONALITY = None",
"п.6 tools: PERSONALITY")

patch("core/tools.py",
'''def get_weather(city: str = "Чайковский") -> str:
    """Погода в указанном городе.
    Args:
        city: название города (по умолчанию Чайковский)
    """''',
'''def get_weather(city: str = "") -> str:
    """Погода в указанном городе.
    Args:
        city: название города (если пусто — город из профиля пользователя)
    """
    if not city and PERSONALITY is not None:
        city = PERSONALITY.profile.get("city", "")
    city = city or "Москва"''',
"п.6 tools: погода без хардкода города")

patch("main.py",
"    tools_module.TTS_INSTANCE = tts",
"    tools_module.TTS_INSTANCE = tts\n    tools_module.PERSONALITY = personality",
"п.6 main: привязка PERSONALITY")

# --- Пункт 7: stt.py — отладочное аудио только по флагу ---
patch("core/stt.py",
"        self.samplerate_whisper = 16000",
"        self.samplerate_whisper = 16000\n        self.debug_audio = cfg.get(\"debug_audio\", False)",
"п.7 stt: флаг debug_audio")

patch("core/stt.py",
"""    # Сохраняем аудио для отладки (опционально)
    try:
        sf.write("/tmp/debug_audio.wav", audio_1d, self.samplerate_whisper)
    except Exception:
        pass""",
"""    # Сохраняем аудио для отладки (только если включено в конфиге)
    if self.debug_audio:
        try:
            sf.write("/tmp/debug_audio.wav", audio_1d, self.samplerate_whisper)
        except Exception:
            pass""",
"п.7 stt: запись opt-in")

# --- Пункт 9: speaker_id.py — синхрон комментария и порога ---
patch("core/speaker_id.py",
"""резал даже легитимные совпадения. 0.65 — с запасом выше максимального
наблюдённого внутрипрофильного разброса и заметно ниже минимального
межпрофильного расстояния. Если после этого раза начнутся ложные
срабатывания (путает людей) — можно подстроить точнее по факту.""",
"""резал даже легитимные совпадения. Финальный порог 0.7 — консервативная
середина между наблюдённым внутрипрофильным разбросом (до 0.58) и
межпрофильным расстоянием (от 0.79). Если хозяин будет часто НЕ
узнаваться — снизить до 0.65 и перепроверить через diagnose_speaker.py.""",
"п.9 speaker_id: комментарий синхронизирован")

# --- Пункт 8: kids_tools.py — полная перезапись (файл маленький) ---
(BASE / "core" / "kids_tools.py").write_text('''"""
Безопасные инструменты для детского профиля.
Только информационные — никакого управления ПК.
Анекдоты и факты идут через fast_command, translate появится в v2.5.
"""
from core.tools import TOOLS_BY_NAME

KIDS_ALLOWED_TOOLS = [
    "get_current_time",
    "get_weather",
    "calculate",
    "search_wikipedia",
]

KIDS_TOOLS_BY_NAME = {
    name: TOOLS_BY_NAME[name]
    for name in KIDS_ALLOWED_TOOLS
    if name in TOOLS_BY_NAME
}
''', encoding="utf-8")
report.append("✅ п.8 kids_tools.py: whitelist переписан целиком")

print()
print("\n".join(report))
bad = [r for r in report if not r.startswith("✅")]
print()
print("ГОТОВО. Все правки применены." if not bad else "ЕСТЬ ПРОПУСКИ — смотри ❌/⚠️ выше.")
