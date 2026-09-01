"""v2.5 voice-трек: пункты 10-13 (unknown, маршрутизация, recheck, kids-конвейер)."""
import re
import py_compile
from pathlib import Path

BASE = Path(__file__).resolve().parent
report = []

# ================= core/kids_pipeline.py (новый файл) =================
(BASE / "core" / "kids_pipeline.py").write_text('''"""
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
''', encoding="utf-8")
report.append("✅ core/kids_pipeline.py создан")

# ================= tests/test_kids_pipeline.py =================
(BASE / "tests" / "test_kids_pipeline.py").write_text('''"""Тесты детского конвейера и токен-матчера родительского контроля."""
from core.kids_pipeline import is_dangerous_for_kids
from core.parental_control import check_suspicious


def test_intercept_dangerous():
    assert is_dangerous_for_kids("выключи компьютер")
    assert is_dangerous_for_kids("открой браузер")
    assert is_dangerous_for_kids("удали папку")


def test_intercept_safe():
    assert not is_dangerous_for_kids("расскажи анекдот")
    assert not is_dangerous_for_kids("сколько будет два плюс два")
    assert not is_dangerous_for_kids("который час")


def test_suspicious_tokens_not_substrings():
    assert not check_suspicious("помоги с математикой")
    assert not check_suspicious("объясни формат даты")
    assert check_suspicious("мат")
    assert check_suspicious("ненавижу уроки")
''', encoding="utf-8")
report.append("✅ tests/test_kids_pipeline.py создан")

# ================= speaker_id.py: unknown вместо default =================
p = BASE / "core" / "speaker_id.py"
src = p.read_text(encoding="utf-8")

def s1(m):
    return m.group(1) + 'log.info(f"Голос не опознан (лучшее расстояние {best_distance:.3f} > порога {threshold})")\n' + m.group(2) + 'return "unknown", best_distance'

src, n1 = re.subn(
    r'([ \t]*)log\.info\(f"Голос не опознан[^\n]*\n([ \t]*)return "default", best_distance',
    s1, src, count=1)
report.append("✅ speaker_id: no-match -> unknown" if n1 else "❌ speaker_id: блок no-match не найден")

src, n2 = src.count('а честно остаётся "default"'), src.replace(
    'а честно остаётся "default"',
    'а честно получает метку "unknown" (безопасный режим)') if src.count('а честно остаётся "default"') else (src, 0)
# аккуратнее: считаем и заменяем
cnt = src.count('а честно остаётся "default"')
if cnt == 1:
    src = src.replace('а честно остаётся "default"', 'а честно получает метку "unknown" (безопасный режим)')
    report.append("✅ speaker_id: docstring обновлён")
else:
    report.append("⚠️ speaker_id: docstring-фраза не найдена")
p.write_text(src, encoding="utf-8")

# ================= main.py: маршрутизация =================
p = BASE / "main.py"
src = p.read_text(encoding="utf-8")

# импорт
src, n3 = re.subn(
    r'from core\.wakeword import WakeWordDetector',
    'from core.wakeword import WakeWordDetector\nfrom core.kids_pipeline import KidsPipeline',
    src, count=1)
report.append("✅ main: импорт KidsPipeline" if n3 else "❌ main: импорт не встал")

# блок идентификации в voice_mode -> маршрутизация
def route(m):
    i = m.group(1)
    i4, i8, i12, i16 = i + "    ", i + "        ", i + "            ", i + "                "
    return (i + "session_kids = False\n"
            + i + "voice_notice = \"\"\n"
            + i + "if wake_audio is not None:\n"
            + i4 + "try:\n"
            + i8 + "recognized_id, distance = speaker_id_module.identify_speaker(wake_audio)\n"
            + i8 + "diagnostics.log_event(\"voice_id\", speaker=recognized_id,\n"
            + i8 + "                    distance=round(distance, 3) if distance is not None else None)\n"
            + i8 + "kids_speakers = set(cfg.get(\"voice_access\", {}).get(\"kids_speakers\", [\"kid\"]))\n"
            + i8 + "if recognized_id == \"unknown\":\n"
            + i12 + "session_kids = True\n"
            + i12 + "voice_notice = (\"Не узнаю голос. Работаю в детском режиме. \"\n"
            + i12 + "                    \"Разблокировка — через веб-чат с паролем.\")\n"
            + i12 + "log.info(\"Voice ID: голос не узнан — безопасный детский режим\")\n"
            + i8 + "elif recognized_id in kids_speakers:\n"
            + i12 + "session_kids = True\n"
            + i12 + "if recognized_id != memory.speaker_id:\n"
            + i16 + "memory.switch_speaker(recognized_id)\n"
            + i16 + "personality.switch_speaker(recognized_id)\n"
            + i8 + "else:\n"
            + i12 + "if recognized_id != memory.speaker_id:\n"
            + i16 + "log.info(f\"Voice ID: переключение на '{recognized_id}'\")\n"
            + i16 + "memory.switch_speaker(recognized_id)\n"
            + i16 + "personality.switch_speaker(recognized_id)\n"
            + i4 + "except Exception as e:\n"
            + i8 + "log.warning(f\"Voice ID не сработал: {e}\")\n"
            + i + "kids_pipeline = KidsPipeline(memory=memory, personality=personality) if session_kids else None")

src, n4 = re.subn(
    r'([ \t]*)if wake_audio is not None:\n[ \t]*try:\n[ \t]*recognized_id, distance = '
    r'speaker_id_module\.identify_speaker\(wake_audio\)\n.*?Voice ID не сработал[^\n]*',
    route, src, count=1, flags=re.S)
report.append("✅ main: маршрутизация voice_mode" if n4 else "❌ main: блок идентификации не найден")

# озвучка активации с уведомлением
def spk(m):
    i = m.group(1)
    return (i + "if voice_notice:\n"
            + i + "    tts.speak(voice_notice)\n"
            + i + "else:\n"
            + i + "    tts.speak(\"Слушаю\")")

src, n5 = re.subn(r'([ \t]*)tts\.speak\("Слушаю"\)', spk, src, count=1)
report.append("✅ main: озвучка с voice_notice" if n5 else "❌ main: tts.speak('Слушаю') не найдена")

# вызов conversation_mode с новыми параметрами
src, n6 = src.count("conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality)"), 0
if src.count("conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality)") == 1:
    src = src.replace(
        "conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality)",
        "conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality, kids_pipeline=kids_pipeline, wake_audio=wake_audio)")
    report.append("✅ main: вызов conversation_mode расширен")
else:
    report.append("❌ main: вызов conversation_mode не найден")

# сигнатура conversation_mode
src, n7 = re.subn(
    r'def conversation_mode\(cfg, recorder, stt, llm, tts, log, memory, personality\):',
    'def conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality, kids_pipeline=None, wake_audio=None):',
    src, count=1)
report.append("✅ main: сигнатура conversation_mode" if n7 else "❌ main: сигнатура не найдена")

# вставка recheck + детского контура после log.info(f"Запрос: {text}")
def ins(m):
    i = m.group(1)
    i4, i8, i12, i16 = i + "    ", i + "        ", i + "            ", i + "                "
    return (m.group(0)
            + i + "# === Voice ID recheck по первой фразе (пункт 12) ===\n"
            + i + "if wake_audio is not None:\n"
            + i4 + "try:\n"
            + i8 + "from scipy import signal as _sig\n"
            + i8 + "import numpy as _np\n"
            + i8 + "n16 = int(len(audio) * 16000 / recorder.samplerate)\n"
            + i8 + "a16 = _sig.resample(_np.asarray(audio).squeeze(), n16).astype(_np.float32)\n"
            + i8 + "combined = _np.concatenate([wake_audio, a16])\n"
            + i8 + "rid2, dist2 = speaker_id_module.identify_speaker(combined)\n"
            + i8 + "diagnostics.log_event(\"voice_id\", event=\"recheck\", speaker=rid2,\n"
            + i8 + "                    distance=round(dist2, 3) if dist2 is not None else None)\n"
            + i8 + "kids_speakers = set(cfg.get(\"voice_access\", {}).get(\"kids_speakers\", [\"kid\"]))\n"
            + i8 + "if rid2 in kids_speakers and kids_pipeline is None:\n"
            + i12 + "kids_pipeline = KidsPipeline(memory=memory, personality=personality)\n"
            + i12 + "if rid2 != memory.speaker_id:\n"
            + i16 + "memory.switch_speaker(rid2)\n"
            + i16 + "personality.switch_speaker(rid2)\n"
            + i12 + "log.info(f\"Voice ID recheck: детский профиль '{rid2}'\")\n"
            + i8 + "elif rid2 != \"unknown\" and rid2 != memory.speaker_id and kids_pipeline is None:\n"
            + i12 + "memory.switch_speaker(rid2)\n"
            + i12 + "personality.switch_speaker(rid2)\n"
            + i4 + "except Exception as e:\n"
            + i8 + "log.warning(f\"Voice ID recheck не сработал: {e}\")\n"
            + i4 + "wake_audio = None\n"
            + i + "# === Детский голосовой контур (пункт 11) ===\n"
            + i + "if kids_pipeline is not None:\n"
            + i4 + "answer = kids_pipeline.handle(text, source=\"voice\")\n"
            + i4 + "print(f\"Луна (детский): {answer}\")\n"
            + i4 + "log.info(f\"Детский ответ: {answer}\")\n"
            + i4 + "tts.speak_interruptible(answer)\n"
            + i4 + "last_interaction = time.time()\n"
            + i4 + "continue\n")

src, n8 = re.subn(r'([ \t]*)log\.info\(f"Запрос: \{text\}"\)\n', ins, src, count=1)
report.append("✅ main: recheck + детский контур в диалоге" if n8 else "❌ main: точка вставки не найдена")
p.write_text(src, encoding="utf-8")

# ================= компиляция-проверка =================
for rel in ["main.py", "core/speaker_id.py", "core/kids_pipeline.py", "tests/test_kids_pipeline.py"]:
    try:
        py_compile.compile(str(BASE / rel), doraise=True)
        report.append("✅ " + rel + ": синтаксис OK")
    except Exception as e:
        report.append("❌ " + rel + ": " + str(e))

print()
print("\n".join(report))
