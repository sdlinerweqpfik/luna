"""Доработка хотфикса v2.3.1: три правки regex-ом, нечувствительным к пробелам."""
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
report = []

def patch_regex(rel_path, pattern, repl, name):
    p = BASE / rel_path
    src = p.read_text(encoding="utf-8")
    new_src, n = pattern.subn(repl, src, count=1)
    if n == 0:
        report.append(f"❌ {name}: не нашлось в {rel_path}")
        return
    p.write_text(new_src, encoding="utf-8")
    report.append(f"✅ {name}")

# --- п.6 tools.py: погода без хардкода города ---
patch_regex("core/tools.py",
    re.compile(r'def get_weather\(city: str = "Чайковский"\) -> str:.*?(?=\n    try:)', re.S),
    '''def get_weather(city: str = "") -> str:
    """Погода в указанном городе.
    Args:
        city: название города (если пусто — город из профиля пользователя)
    """
    if not city and PERSONALITY is not None:
        city = PERSONALITY.profile.get("city", "")
    city = city or "Москва"''',
    "п.6 tools: погода без хардкода города")

# --- п.7 stt.py: отладочная запись только по флагу ---
def _stt_repl(m):
    i1 = m.group(1)
    i4 = i1 + "    "
    i8 = i1 + "        "
    return (f"{i1}# Сохраняем аудио для отладки (только если включено в конфиге)\n"
            f"{i1}if self.debug_audio:\n"
            f"{i4}try:\n"
            f"{i8}sf.write(\"/tmp/debug_audio.wav\", audio_1d, self.samplerate_whisper)\n"
            f"{i4}except Exception:\n"
            f"{i8}pass")

patch_regex("core/stt.py",
    re.compile(
        r'^([ \t]*)# Сохраняем аудио для отладки \(опционально\)\n'
        r'[ \t]*try:\n'
        r'[ \t]*sf\.write\("/tmp/debug_audio\.wav", audio_1d, self\.samplerate_whisper\)\n'
        r'[ \t]*except Exception:\n'
        r'[ \t]*pass', re.M),
    _stt_repl,
    "п.7 stt: запись opt-in")

# --- п.9 speaker_id.py: синхрон комментария и порога ---
patch_regex("core/speaker_id.py",
    re.compile(r'резал даже легитимные совпадения\..*?по факту\.', re.S),
    '''резал даже легитимные совпадения. Финальный порог 0.7 — консервативная
середина между наблюдённым внутрипрофильным разбросом (до 0.58) и
межпрофильным расстоянием (от 0.79). Если хозяин будет часто НЕ
узнаваться — снизить до 0.65 и перепроверить через diagnose_speaker.py
по факту.''',
    "п.9 speaker_id: комментарий синхронизирован")

print()
print("\n".join(report))
