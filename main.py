import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

import atexit
import logging
import sys
import time

from core.memory import Memory
from core.personality import Personality
import core.memory_tools as memory_tools
import core.tools as tools_module
import core.reminders as reminders_module
import core.speaker_id as speaker_id_module

from config import load_config
from core.logger import setup_logging
from core.audio import Recorder
from core.stt import STT
from core.llm import LLM
from core.tts import TTS
from core.commands import fast_command
from core.wakeword import WakeWordDetector
from core import diagnostics

from core.confirmation import manager as confirmation_manager
from core.advanced import init_planner, get_planner, advanced_task

try:
    from core.mood_engine import engine as mood_engine
    from core.easter_eggs import egg as aperture_egg
    MOOD_AVAILABLE = True
except ImportError:
    MOOD_AVAILABLE = False


def is_complex_fast(text):
    text_lower = text.lower()
    complex_keywords = [
        "объясни", "расскажи подробно", "почему", "зачем",
        "сравни", "проанализируй", "напиши", "сочини",
        "придумай", "переведи", "объясни как",
        "квантов", "философ", "история", "наука",
        "стих", "рассказ", "эссе", "сочинение",
    ]
    word_count = len(text_lower.split())
    for kw in complex_keywords:
        if kw in text_lower:
            return True
    return word_count > 15


def sync_voice_mode(tts):
    if not hasattr(tts, "set_mode"):
        return
    if MOOD_AVAILABLE and aperture_egg.is_active():
        tts.set_mode("aperture")
    else:
        tts.set_mode(tts.default_mode)


def handle_request(text, llm, tts, log, memory, personality):
    """Граница запроса: start_request в начале, end_request ВСЕГДА в конце."""
    diagnostics.start_request("voice", text)
    try:
        pending_before = confirmation_manager.get_active()

        is_fast, fast_answer = fast_command(text, llm=llm, memory=memory, personality=personality)
        if is_fast:
            print("⚡ Быстрая команда")
            print(f"Луна: {fast_answer}")
            log.info(f"Быстрый ответ: {fast_answer}")
            tts.speak_interruptible(fast_answer)
            return

        if is_complex_fast(text):
            model = llm.smart
            print("🧠 Сложная задача — вызываю 14B...")
        else:
            model = llm.fast
            print("💬 Обычный вопрос — отвечаю (8B)")

        answer = llm.ask(model, text)
        log.info(f"Ответ ({model}): {answer}")

        if model == llm.smart:
            llm.unload(model)

        print(f"Луна: {answer}")

        # === Доставка confirmation prompt + события диагностики ===
        pending_after = confirmation_manager.get_active()
        if (pending_after is not None
                and pending_after is not pending_before
                and pending_after.status == "pending"):
            confirmation_manager.claim_for_prompt(pending_after.id)
            diagnostics.log_event("confirmation", stage="prompting",
                                  pending=pending_after.id,
                                  action=pending_after.action_type)
            delivered = tts.speak_interruptible(answer)
            if delivered:
                confirmation_manager.mark_prompted(pending_after.id)
                diagnostics.log_event("confirmation", stage="prompted",
                                      pending=pending_after.id)
                log.info(f"Confirmation prompt {pending_after.id} доставлен")
            else:
                confirmation_manager.abort_prompting(pending_after.id)
                diagnostics.log_event("confirmation", stage="aborted",
                                      pending=pending_after.id)
                log.info(f"Confirmation prompt {pending_after.id} не доставлен — abort")
        else:
            tts.speak_interruptible(answer)
    finally:
        diagnostics.end_request()


def _resume_advanced(tts):
    planner = get_planner()
    if planner is not None:
        resume_result = planner.resume_if_waiting()
        if resume_result:
            diagnostics.log_event("advanced", event="resume",
                                  result=resume_result[:80])
            print(f"📋 План: {resume_result}")
            tts.speak_interruptible(resume_result)


def conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality):
    timeout = cfg["audio"].get("conversation_timeout", 60)
    print(f"\n💬 РЕЖИМ ДИАЛОГА ({timeout} сек)")
    print("Говори, я слушаю...")
    print("Нажми 'q' или 'Esc' для остановки прослушивания")

    last_interaction = time.time()

    while True:
        try:
            elapsed = time.time() - last_interaction

            if elapsed >= timeout and not confirmation_manager.has_pending():
                print(f"\n👋 Таймаут ({timeout} сек), прощаюсь...")
                time.sleep(0.5)
                tts.speak("До свидания! Скажи Луна, если понадобишься.")
                recorder.play_beep(400, 0.3)
                time.sleep(3.0)
                return

            time.sleep(0.2)
            sync_voice_mode(tts)

            audio = recorder.listen_until_silence()

            if audio is None:
                print("\n⏸️  Прослушивание остановлено")
                print("Возврат к ожиданию слова 'Луна'")
                return

            if len(audio) < 1000:
                continue

            text = stt.transcribe(audio)

            if not text:
                continue

            print(f"Вы сказали: {text}")
            log.info(f"Запрос: {text}")

            # === ПОДТВЕРЖДЕНИЕ ОПАСНОГО ДЕЙСТВИЯ ===
            decision, pending_action = confirmation_manager.match_user_input(text)

            if decision == "confirm":
                diagnostics.start_request("voice", text)
                print(f"🔐 Подтверждение действия: {pending_action.summary}")
                ok, result = confirmation_manager.confirm(pending_action.id, source="voice")
                print(f"   → {result}")
                log.info(f"Действие {pending_action.id} подтверждено: {result}")
                diagnostics.log_event("confirmation", stage="confirm",
                                      action=pending_action.action_type, source="voice")
                tts.speak_interruptible(result)
                last_interaction = time.time()
                _resume_advanced(tts)
                diagnostics.end_request()
                continue

            elif decision == "reject":
                diagnostics.start_request("voice", text)
                print(f"🔐 Отказ от действия: {pending_action.summary}")
                result = confirmation_manager.reject(pending_action.id)
                print(f"   → {result}")
                log.info(f"Действие {pending_action.id} отклонено")
                diagnostics.log_event("confirmation", stage="reject",
                                      action=pending_action.action_type, source="voice")
                tts.speak_interruptible(result)
                last_interaction = time.time()
                _resume_advanced(tts)
                diagnostics.end_request()
                continue

            elif decision == "ambiguous":
                diagnostics.start_request("voice", text)
                print("🔐 Неоднозначный ответ — переспрашиваю")
                diagnostics.log_event("confirmation", stage="ambiguous")
                tts.speak_interruptible("Скажи просто: да или нет.")
                last_interaction = time.time()
                diagnostics.end_request()
                continue

            # === ПАСХАЛКА И НАСТРОЕНИЯ ===
            if MOOD_AVAILABLE:
                egg_result = aperture_egg.feed(text)
                if egg_result:
                    mood_engine.force_mood("aperture_secret", egg_result["duration"])
                    sync_voice_mode(tts)
                    print("🎂 Пасхалка: aperture-режим активирован")
                    tts.speak_interruptible(egg_result["line"])
                    last_interaction = time.time()
                    continue

                denial = mood_engine.maybe_deny(text)
                if denial:
                    print("🎂 Отрицание aperture-режима")
                    tts.speak_interruptible(denial)
                    last_interaction = time.time()
                    continue

                mood_engine.update_interaction()
                mood_engine.check_triggers(text=text)

            exit_words = text.lower().split()
            if any(word in exit_words for word in ["пока", "выход", "хватит", "закрой"]):
                print("👋 Команда выхода")
                tts.speak("До свидания!")
                recorder.play_beep(400, 0.3)
                time.sleep(2.0)
                return

            handle_request(text, llm, tts, log, memory, personality)
            last_interaction = time.time()

            time.sleep(1.0)
            print("\n🎤 Говори, я слушаю...")

        except KeyboardInterrupt:
            print("\n⏸️  Ctrl+C в диалоге — выход в режим ожидания")
            return
        except Exception as e:
            log.error(f"Ошибка в диалоге: {e}")
            print(f"⚠️ Ошибка: {e}")
            diagnostics.end_request()  # на всякий случай закрываем запрос
            continue


def voice_mode(cfg, recorder, stt, llm, tts, detector, log, memory, personality):
    print("\n" + "=" * 60)
    print("🎙️  ГОЛОСОВОЙ АССИСТЕНТ 'ЛУНА'")
    print("=" * 60)
    print("Скажи 'Луна', чтобы начать разговор")
    print("В режиме диалога говори без слова-активатора")
    print("Нажми 'q' или 'Esc' для остановки прослушивания")
    print("Ctrl+C — полный выход")
    print("=" * 60 + "\n")

    while True:
        try:
            print("🎙️  Ожидаю слово 'Луна'...")
            wake_audio = detector.wait_for_wake_word()

            print("\n🔔 Луна активирована!")
            recorder.play_beep(800, 0.15)

            if wake_audio is not None:
                try:
                    recognized_id, distance = speaker_id_module.identify_speaker(wake_audio)
                    if recognized_id != memory.speaker_id:
                        log.info(f"Voice ID: переключение на '{recognized_id}'")
                        memory.switch_speaker(recognized_id)
                        personality.switch_speaker(recognized_id)
                except Exception as e:
                    log.warning(f"Voice ID не сработал: {e}")

            if MOOD_AVAILABLE:
                mood_engine.start_session()
            sync_voice_mode(tts)

            time.sleep(0.3)
            tts.speak("Слушаю")
            time.sleep(0.5)

            conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality)

            if MOOD_AVAILABLE:
                mood_engine.end_session()

            print("\n" + "=" * 60)
            print("Возврат к ожиданию слова 'Луна'")
            print("=" * 60 + "\n")

        except KeyboardInterrupt:
            print("\n👋 Ctrl+C — полный выход из ассистента")
            raise
        except Exception as e:
            log.error(f"Ошибка в voice_mode: {e}")
            print(f"⚠️ Ошибка: {e}")
            time.sleep(1)


def main():
    cfg = load_config("config.yaml")
    setup_logging(cfg["logging"])
    diagnostics.init(cfg.get("diagnostics", {}))
    log = logging.getLogger("secretary.main")

    memory = Memory(speaker_id="default")
    personality = Personality(speaker_id="default")
    memory_tools.MEMORY = memory

    recorder = Recorder(cfg["audio"])
    stt = STT(cfg["whisper"])
    llm = LLM(cfg["models"], memory=memory, personality=personality)
    tts = TTS(cfg["piper"], recorder)

    tools_module.TTS_INSTANCE = tts
    reminders_module.TTS_INSTANCE = tts
    reminders_module.schedule_all_pending()

    init_planner(llm, memory=memory, personality=personality)
    tools_module.ALL_TOOLS.append(advanced_task)
    tools_module.TOOLS_BY_NAME[advanced_task.__name__] = advanced_task

    remote_cfg = cfg.get("remote", {})
    if remote_cfg.get("enabled"):
        from core.remote_server import RemoteServer
        RemoteServer(remote_cfg, llm, memory, personality,
                     planner=get_planner()).start()

    # Kids сервер (для младшего)
    kids_cfg = cfg.get("kids_remote", {})
    if kids_cfg.get("enabled"):
        from core.remote_server import RemoteServer
        RemoteServer(kids_cfg, llm, memory, personality,
                     planner=get_planner(), kids_mode=True).start()
    
    atexit.register(lambda: llm.unload(llm.fast))
    atexit.register(lambda: llm.unload(llm.smart))

    try:
        detector = WakeWordDetector(cfg["audio"], wake_word="луна")
        voice_mode(cfg, recorder, stt, llm, tts, detector, log, memory, personality)
    except KeyboardInterrupt:
        print("\nВыход — выгружаю модели...")


if __name__ == "__main__":
    main()
