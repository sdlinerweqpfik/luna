import subprocess
import threading
import numpy as np


class TTS:
    def __init__(self, cfg, player):
        self.voice = cfg["voice"]
        self.samplerate = cfg["samplerate"]
        self.player = player
        self._stop_event = threading.Event()

    def speak(self, text):
        """Блокирующее воспроизведение — оставлено для обратной совместимости
        и для мест, где прерывание не нужно (системные сообщения, прощание)."""
        process = subprocess.Popen(
            ["piper", "--model", self.voice, "--output_raw"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        audio, _ = process.communicate(text.encode())
        self.player.play(np.frombuffer(audio, dtype=np.int16), self.samplerate)

    def speak_interruptible(self, text, on_interrupted=None):
        """Воспроизводит речь, но может быть прервана появлением речи пользователя.

        Параллельно с воспроизведением слушает микрофон через
        player.listen_for_interruption(). Если пользователь начал говорить —
        немедленно останавливает TTS и вызывает on_interrupted() (если задан).

        Возвращает True если договорила до конца, False если была прервана.
        """
        process = subprocess.Popen(
            ["piper", "--model", self.voice, "--output_raw"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        audio, _ = process.communicate(text.encode())
        audio_np = np.frombuffer(audio, dtype=np.int16)

        self._stop_event.clear()
        interrupted = {"flag": False}

        def watch_for_interruption():
            # Слушает микрофон, пока звучит речь; как только услышала пользователя — стоп
            if self.player.listen_for_interruption(self._stop_event):
                interrupted["flag"] = True
                self._stop_event.set()

        watcher = threading.Thread(target=watch_for_interruption, daemon=True)
        watcher.start()

        self.player.play_interruptible(audio_np, self.samplerate, self._stop_event)

        self._stop_event.set()  # на случай если речь закончилась сама — останавливаем watcher
        watcher.join(timeout=1.0)

        if interrupted["flag"] and on_interrupted:
            on_interrupted()

        return not interrupted["flag"]
