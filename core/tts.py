import subprocess
import threading
import numpy as np


class TTS:
    def __init__(self, cfg, player):
        self.voice = cfg["voice"]
        self.samplerate = cfg["samplerate"]
        self.player = player
        self._stop_event = threading.Event()
        # Голосовые режимы: normal (стоковый piper) + prosody-режимы из конфига
        self.modes = cfg.get("prosody", {}) or {}
        self.default_mode = cfg.get("default_mode", "normal")
        self.mode = self.default_mode

    def set_mode(self, mode):
        """Переключить голосовой режим: normal / cold / aperture."""
        if mode == "normal" or mode in self.modes:
            self.mode = mode

    def _synthesize(self, text):
        """Синтез фразы с учётом текущего режима. Возвращает int16-массив."""
        params = self.modes.get(self.mode, {})
        cmd = ["piper", "--model", self.voice, "--output_raw"]
        if "noise_scale" in params:
            cmd += ["--noise-scale", str(params["noise_scale"])]
        if "noise_w_scale" in params:
            cmd += ["--noise-w-scale", str(params["noise_w_scale"])]
        if "length_scale" in params:
            cmd += ["--length-scale", str(params["length_scale"])]

        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        audio, _ = process.communicate(text.encode())
        audio_np = np.frombuffer(audio, dtype=np.int16)

        steps = params.get("pitch_steps")
        if steps:
            # Aperture-обработка: питч вниз с сохранением темпа.
            # librosa УЖЕ в зависимостях (её использует wake word);
            # импорт ленивый, чтобы не замедлять старт.
            import librosa
            y = audio_np.astype(np.float32) / 32768.0
            y = librosa.effects.pitch_shift(
                y, sr=self.samplerate, n_steps=float(steps))
            audio_np = np.clip(y * 32768.0, -32768, 32767).astype(np.int16)
        return audio_np

    def speak(self, text):
        """Блокирующее воспроизведение — для обратной совместимости
        и мест, где прерывание не нужно (системные сообщения, прощание)."""
        self.player.play(self._synthesize(text), self.samplerate)

    def speak_interruptible(self, text, on_interrupted=None):
        """Воспроизводит речь, но может быть прервана появлением речи пользователя.
        Параллельно с воспроизведением слушает микрофон через
        player.listen_for_interruption(). Если пользователь начал говорить —
        немедленно останавливает TTS и вызывает on_interrupted() (если задан).
        Возвращает True если договорила до конца, False если была прервана.
        """
        audio_np = self._synthesize(text)
        self._stop_event.clear()
        interrupted = {"flag": False}

        def watch_for_interruption():
            if self.player.listen_for_interruption(self._stop_event):
                interrupted["flag"] = True
                self._stop_event.set()

        watcher = threading.Thread(target=watch_for_interruption, daemon=True)
        watcher.start()
        self.player.play_interruptible(audio_np, self.samplerate, self._stop_event)
        self._stop_event.set()
        watcher.join(timeout=1.0)
        if interrupted["flag"] and on_interrupted:
            on_interrupted()
        return not interrupted["flag"]
