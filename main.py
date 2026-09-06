import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

import atexit
import logging
import sys
import time

from core.selfcheck import heartbeat
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
from core.kids_pipeline import KidsPipeline
from core import diagnostics

from core.confirmation import manager as confirmation_manager
from core.advanced import (init_planner, get_planner,
                           advanced_task, cancel_advanced_task)
from core import safeguard as safeguard_module
from core.routing import is_complex_fast

try:
    from core.mood_engine import engine as mood_engine
    from core.easter_eggs import egg as aperture_egg
    MOOD_AVAILABLE = True
except ImportError:
    MOOD_AVAILABLE = False




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


def conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality, kids_pipeline=None, wake_audio=None):
    timeout = cfg["audio"].get("conversation_timeout", 60)
    print(f"\n💬 РЕЖИМ ДИАЛОГА ({timeout} сек)")
    print("Говори, я слушаю...")
    print("Нажми 'q' или 'Esc' для остановки прослушивания")

    last_interaction = time.time()

    while True:
        try:
            heartbeat("voice_loop")
            elapsed = time.time() - last_interaction

                        # Проверяем безопасный режим
            if safeguard_module.safeguard.is_safe_mode():
                remaining = safeguard_module.safeguard.remaining()
                tts.speak_interruptible(
                    f"Безопасный режим ещё {remaining} секунд. Сложные задачи отключены."
                )
                continue
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
            # === Voice ID recheck по первой фразе (пункт 12) ===
            if wake_audio is not None:
                try:
                    from scipy import signal as _sig
                    import numpy as _np
                    n16 = int(len(audio) * 16000 / recorder.samplerate)
                    a16 = _sig.resample(_np.asarray(audio).squeeze(), n16).astype(_np.float32)
                    combined = _np.concatenate([wake_audio, a16])
                    rid2, dist2 = speaker_id_module.identify_speaker(combined)
                    diagnostics.log_event("voice_id", event="recheck", speaker=rid2,
                                        distance=round(dist2, 3) if dist2 is not None else None)
                    kids_speakers = set(cfg.get("voice_access", {}).get("kids_speakers", ["kid"]))
                    if rid2 in kids_speakers and kids_pipeline is None:
                        kids_pipeline = KidsPipeline(memory=memory, personality=personality)
                        if rid2 != memory.speaker_id:
                            memory.switch_speaker(rid2)
                            personality.switch_speaker(rid2)
                        log.info(f"Voice ID recheck: детский профиль '{rid2}'")
                    elif rid2 != "unknown" and rid2 != memory.speaker_id and kids_pipeline is None:
                        memory.switch_speaker(rid2)
                        personality.switch_speaker(rid2)
                except Exception as e:
                    log.warning(f"Voice ID recheck не сработал: {e}")
                wake_audio = None
            # === Детский голосовой контур (пункт 11) ===
            if kids_pipeline is not None:
                answer = kids_pipeline.handle(text, source="voice")
                print(f"Луна (детский): {answer}")
                log.info(f"Детский ответ: {answer}")
                tts.speak_interruptible(answer)
                last_interaction = time.time()
                continue

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
            if any(word in exit_words for word in ["пока", "выход", "хватит"]):
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

            session_kids = False
            voice_notice = ""
            if wake_audio is not None:
                try:
                    recognized_id, distance = speaker_id_module.identify_speaker(wake_audio)
                    diagnostics.log_event("voice_id", speaker=recognized_id,
                                        distance=round(distance, 3) if distance is not None else None)
                    kids_speakers = set(cfg.get("voice_access", {}).get("kids_speakers", ["kid"]))
                    if recognized_id == "unknown":
                        session_kids = True
                        voice_notice = ("Не узнаю голос. Работаю в детском режиме. "
                                            "Разблокировка — через веб-чат с паролем.")
                        log.info("Voice ID: голос не узнан — безопасный детский режим")
                    elif recognized_id in kids_speakers:
                        session_kids = True
                        if recognized_id != memory.speaker_id:
                            memory.switch_speaker(recognized_id)
                            personality.switch_speaker(recognized_id)
                    else:
                        if recognized_id != memory.speaker_id:
                            log.info(f"Voice ID: переключение на '{recognized_id}'")
                            memory.switch_speaker(recognized_id)
                            personality.switch_speaker(recognized_id)
                except Exception as e:
                    # 🛡️ FAIL-CLOSED (GPT C-4, Claude): при ошибке Voice ID
                    # переходим в безопасный (детский) режим, а не во взрослый.
                    log.warning(f"Voice ID не сработал: {e}. Переход в безопасный режим.")
                    session_kids = True
                    voice_notice = "Не удалось подтвердить голос. Работаю в безопасном режиме."
            kids_pipeline = KidsPipeline(memory=memory, personality=personality) if session_kids else None
            if MOOD_AVAILABLE:
                mood_engine.start_session()
            sync_voice_mode(tts)

            time.sleep(0.3)
            if voice_notice:
                tts.speak(voice_notice)
            else:
                tts.speak("Слушаю")
            time.sleep(0.5)

            conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality, kids_pipeline=kids_pipeline, wake_audio=wake_audio)

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
        # 🛡️ PER-INSTALL ТОКЕН (GPT C-1, Gemini #3)
    import secrets
    from pathlib import Path
    token_file = Path.home() / "Luna" / "remote_token.txt"
    if not token_file.exists():
        new_token = secrets.token_urlsafe(32)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(new_token)
        print(f"\n🔑 Сгенерирован новый токен Remote: {new_token}")
        print(f"   Сохранён в {token_file}")
        print(f"   Используй его в веб-чате вместо 'lunamain'.\n")
    else:
        fresh_token = token_file.read_text().strip()
        if "remote" in cfg:
            cfg["remote"]["token"] = fresh_token
        if "kids_remote" in cfg:
            cfg["kids_remote"]["token"] = fresh_token + "_kids"
    setup_logging(cfg["logging"])
    diagnostics.init(cfg.get("diagnostics", {}))
    
    # 🛡️ ГЕНЕРАЦИЯ PER-INSTALL ТОКЕНА
    import secrets
    from pathlib import Path
    token_file = Path.home() / "Luna" / "remote_token.txt"
    if not token_file.exists():
        new_token = secrets.token_urlsafe(32)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(new_token)
        print(f"🔑 Сгенерирован новый токен для Remote: {new_token}")
        print("   Сохрани его и используй в веб-чате. Старый 'lunamain' больше не работает.")
    else:
        # Перезаписываем токен в конфиге на лету, не трогая config.yaml
        cfg["remote"]["token"] = token_file.read_text().strip()
        cfg["kids_remote"]["token"] = token_file.read_text().strip() + "_kids"
    
    from core import veil
    veil.try_load_key()
    if veil.is_veiled():
        print("🎭 Занавес опущен. Луна работает с амнезией до снятия занавеса.")
    log = logging.getLogger("secretary.main")

    memory = Memory(speaker_id="default")
    personality = Personality(speaker_id="default")
    memory_tools.MEMORY = memory

    recorder = Recorder(cfg["audio"])
    stt = STT(cfg["whisper"])
    llm = LLM(cfg["models"], memory=memory, personality=personality)
    tts = TTS(cfg["piper"], recorder)

    tools_module.TTS_INSTANCE = tts
    safeguard_module.TTS_INSTANCE = tts
        # === САМОПРОВЕРКА КОНТУРА ===
    from core.selfcheck import SelfCheckLoop, heartbeat as _hb, check_integrity
    startup_check = check_integrity()
    if startup_check["missing_manifest"]:
        log.info("🔏 Манифест не найден. Опечатай контур: "
                 "python -c 'from core.selfcheck import seal; seal()'")
    elif not startup_check["ok"]:
        log.warning(f"🚨 ЦЕЛОСТНОСТЬ НАРУШЕНА ПРИ СТАРТЕ: {startup_check['violations']}")
        tts.speak("Внимание: целостность контура нарушена при запуске. Безопасный режим.")
    SelfCheckLoop(tts=tts).start()
    tools_module.PERSONALITY = personality
    reminders_module.TTS_INSTANCE = tts
    reminders_module.schedule_all_pending()

    init_planner(llm, memory=memory, personality=personality)
    tools_module.ALL_TOOLS.append(advanced_task)
    tools_module.TOOLS_BY_NAME[advanced_task.__name__] = advanced_task

    tools_module.ALL_TOOLS.append(cancel_advanced_task)
    tools_module.TOOLS_BY_NAME[cancel_advanced_task.__name__] = cancel_advanced_task

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
