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

# Confirmation Manager — для голосового подтверждения опасных действий
from core.confirmation import manager as confirmation_manager


def is_complex_fast(text):
    """Быстрый классификатор сложности (БЕЗ LLM!)"""
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


def handle_request(text, llm, tts, log, memory, personality):
    """Обрабатывает запрос: быстрые команды или LLM"""
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

    # === Confirmation Manager v3: доставка confirmation prompt ===
    # Если в этом запросе появился новый pending (status pending — ещё не
    # claimed), то answer — это confirmation prompt: жизненный цикл
    # claim_for_prompt -> TTS -> mark_prompted / abort_prompting.
    pending_after = confirmation_manager.get_active()
    if (pending_after is not None
            and pending_after is not pending_before
            and pending_after.status == "pending"):
        confirmation_manager.claim_for_prompt(pending_after.id)
        delivered = tts.speak_interruptible(answer)
        if delivered:
            confirmation_manager.mark_prompted(pending_after.id)
            log.info(f"Confirmation prompt {pending_after.id} доставлен")
        else:
            confirmation_manager.abort_prompting(pending_after.id)
            log.info(f"Confirmation prompt {pending_after.id} не доставлен — abort")
    else:
        tts.speak_interruptible(answer)


def conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality):
    """Режим активного диалога после активации"""
    timeout = cfg["audio"].get("conversation_timeout", 60)
    print(f"\n💬 РЕЖИМ ДИАЛОГА ({timeout} сек)")
    print("Говори, я слушаю...")
    print("Нажми 'q' или 'Esc' для остановки прослушивания")

    last_interaction = time.time()

    while True:
        try:
            elapsed = time.time() - last_interaction

            if elapsed >= timeout:
                print(f"\n👋 Таймаут ({timeout} сек), прощаюсь...")
                time.sleep(0.5)
                tts.speak("До свидания! Скажи Луна, если понадобишься.")
                recorder.play_beep(400, 0.3)
                time.sleep(3.0)
                return

            time.sleep(0.2)

            audio = recorder.listen_until_silence()

            # Проверяем остановку пользователем (клавиша q/Esc)
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

            # === ПОДТВЕРЖДЕНИЕ ОПАСНОГО ДЕЙСТВИЯ (только из голоса!) ===
            decision, pending_action = confirmation_manager.match_user_input(text)
            if decision == "confirm":
                print(f"🔐 Подтверждение действия: {pending_action.summary}")
                ok, result = confirmation_manager.confirm(pending_action.id, source="voice")
                print(f"   → {result}")
                log.info(f"Действие {pending_action.id} подтверждено: {result}")
                tts.speak_interruptible(result)
                last_interaction = time.time()
                continue  # слово "да" не передаём модели
            elif decision == "reject":
                print(f"🔐 Отказ от действия: {pending_action.summary}")
                result = confirmation_manager.reject(pending_action.id)
                print(f"   → {result}")
                log.info(f"Действие {pending_action.id} отклонено")
                tts.speak_interruptible(result)
                last_interaction = time.time()
                continue  # слово "нет" не передаём модели
            elif decision == "ambiguous":
                # Есть активное действие, но фраза неясная: НЕ подтверждаем
                # и НЕ передаём модели — просим чёткий ответ.
                print("🔐 Неоднозначный ответ — переспрашиваю")
                tts.speak_interruptible("Скажи просто: да или нет.")
                last_interaction = time.time()
                continue

            # Проверяем команды выхода (только как отдельные слова!)
            exit_words = text.lower().split()
            if any(word in exit_words for word in ["пока", "выход", "хватит", "закрой"]):
                print("👋 Команда выхода")
                tts.speak("До свидания!")
                recorder.play_beep(400, 0.3)
                time.sleep(2.0)
                return

            last_interaction = time.time()
            handle_request(text, llm, tts, log, memory, personality)

            time.sleep(1.0)
            print("\n🎤 Говори, я слушаю...")

        except KeyboardInterrupt:
            print("\n⏸️  Ctrl+C в диалоге — выход в режим ожидания")
            return
        except Exception as e:
            log.error(f"Ошибка в диалоге: {e}")
            print(f"⚠️ Ошибка: {e}")
            continue


def voice_mode(cfg, recorder, stt, llm, tts, detector, log, memory, personality):
    """Главный режим: ждём 'луна' → диалог → снова ждём"""
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

            # Voice ID: определяем говорящего один раз по аудио активации,
            # не на каждую реплику разговора (см. core/speaker_id.py про
            # обоснование). Если распознавание падает по любой причине
            # (модель не загрузилась, аудио пустое) — тихо остаёмся на
            # текущем speaker_id, разговор не должен срываться из-за этого.
            if wake_audio is not None:
                try:
                    recognized_id, distance = speaker_id_module.identify_speaker(wake_audio)
                    if recognized_id != memory.speaker_id:
                        log.info(f"Voice ID: переключение на говорящего '{recognized_id}' (было '{memory.speaker_id}')")
                        memory.switch_speaker(recognized_id)
                        personality.switch_speaker(recognized_id)
                except Exception as e:
                    log.warning(f"Voice ID не сработал, остаюсь на текущем профиле: {e}")

            time.sleep(0.3)
            tts.speak("Слушаю")
            time.sleep(0.5)

            conversation_mode(cfg, recorder, stt, llm, tts, log, memory, personality)

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
    log = logging.getLogger("secretary.main")

    # Единственный источник Memory/Personality на всё приложение.
    memory = Memory(speaker_id="default")
    personality = Personality(speaker_id="default")
    memory_tools.MEMORY = memory

    recorder = Recorder(cfg["audio"])
    stt = STT(cfg["whisper"])
    llm = LLM(cfg["models"], memory=memory, personality=personality)
    tts = TTS(cfg["piper"], recorder)

    # Подключаем модули которым нужен доступ к TTS для озвучки
    tools_module.TTS_INSTANCE = tts
    reminders_module.TTS_INSTANCE = tts
    reminders_module.schedule_all_pending()

    atexit.register(lambda: llm.unload(llm.fast))
    atexit.register(lambda: llm.unload(llm.smart))

    try:
        detector = WakeWordDetector(cfg["audio"], wake_word="луна")
        voice_mode(cfg, recorder, stt, llm, tts, detector, log, memory, personality)
    except KeyboardInterrupt:
        print("\nВыход — выгружаю модели...")


if __name__ == "__main__":
    main()
