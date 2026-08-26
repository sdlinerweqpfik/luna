import sounddevice as sd
import numpy as np
import time
import logging
import webrtcvad
import select
import sys
import termios
import tty
import threading

log = logging.getLogger("secretary.audio")


class Recorder:
    def __init__(self, cfg):
        self.device = cfg.get("mic_device")
        self.samplerate = cfg.get("samplerate", 44100)
        self.vad_samplerate = cfg.get("vad_samplerate", 16000)
        self.duration = cfg.get("duration", 8)
        self.silence_duration = cfg.get("silence_duration", 0.8)
        self.max_duration = cfg.get("max_duration", 30)
        self.min_speech_duration = cfg.get("min_speech_duration", 0.3)
        self.vad_aggressiveness = cfg.get("vad_aggressiveness", 1)
        
        self.vad = webrtcvad.Vad(self.vad_aggressiveness)
        self._speech_buffer = []
        self._stop_requested = False
        self._current_output_level = 0.0  # текущая громкость TTS, для отсечения эха при прерывании
        # Защищает от одновременного вызова sd.play() из разных потоков —
        # актуально когда фоновый таймер (core/tools.py:set_timer) срабатывает
        # ровно в момент, когда основной поток уже что-то говорит через TTS.
        self._playback_lock = threading.Lock()
        
        log.info(f"VAD инициализирован (запись {self.samplerate} Hz, VAD {self.vad_samplerate} Hz)")

    def _resample_for_vad(self, audio):
        """Конвертирует аудио в 16 кГц для VAD"""
        if self.samplerate == self.vad_samplerate:
            return audio
        
        duration = len(audio) / self.samplerate
        target_length = int(duration * self.vad_samplerate)
        
        indices = np.linspace(0, len(audio) - 1, target_length)
        resampled = np.interp(indices, np.arange(len(audio)), audio)
        
        return resampled

    def _is_speech(self, audio_chunk):
        """Проверяет содержит ли чанк речь через webrtcvad"""
        try:
            audio_16k = self._resample_for_vad(audio_chunk)
            
            if audio_16k.dtype == np.float32 or audio_16k.dtype == np.float64:
                audio_16k = (audio_16k * 32767).astype(np.int16)
            elif audio_16k.dtype != np.int16:
                audio_16k = audio_16k.astype(np.int16)
            
            frame_duration_ms = 30
            frame_size = int(self.vad_samplerate * frame_duration_ms / 1000)
            
            for i in range(0, len(audio_16k) - frame_size + 1, frame_size):
                frame = audio_16k[i:i + frame_size]
                if self.vad.is_speech(frame.tobytes(), self.vad_samplerate):
                    return True
            
            return False
        except Exception as e:
            log.warning(f"VAD ошибка: {e}")
            return np.abs(audio_chunk).max() > 0.01

    def _check_stop_key(self):
        """Проверяет нажата ли клавиша остановки (Esc или q)"""
        try:
            # Проверяем есть ли ввод в stdin
            if select.select([sys.stdin], [], [], 0.0)[0]:
                char = sys.stdin.read(1)
                if char in ['q', 'Q', '\x1b']:  # q, Q или Escape
                    log.info("Получена команда остановки")
                    return True
        except Exception:
            pass
        
        return False

    def wait_for_speech(self, timeout=15):
        """Ждёт начала речи, сохраняя буфер последних 500мс"""
        print("  [слушаю...]")
        start = time.time()
        
        frame_size = int(self.samplerate * 0.05)  # 50 мс для быстрой реакции
        buffer = []
        buffer_size = 16  # последние 800 мс
        
        self._speech_buffer = []
        
        # Сохраняем настройки терминала
        old_settings = termios.tcgetattr(sys.stdin)
        
        try:
            # Переводим терминал в режим посимвольного ввода
            tty.setcbreak(sys.stdin.fileno())
            
            while time.time() - start < timeout:
                # Проверяем клавишу остановки
                if self._check_stop_key():
                    self._stop_requested = True
                    return False
                
                try:
                    audio = sd.rec(
                        frame_size,
                        samplerate=self.samplerate,
                        channels=1,
                        dtype='float32',
                        device=self.device
                    )
                    sd.wait()
                    audio_1d = audio.squeeze()
                    
                    buffer.append(audio_1d)
                    if len(buffer) > buffer_size:
                        buffer.pop(0)
                    
                    if self._is_speech(audio_1d):
                        self._speech_buffer = list(buffer)
                        return True
                        
                except Exception:
                    time.sleep(0.01)
        finally:
            # Восстанавливаем настройки терминала
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        
        self._speech_buffer = []
        return False

    def listen_until_silence(self):
        """Записывает пока есть речь, останавливается после тишины"""
        self._stop_requested = False
        
        if not self.wait_for_speech(timeout=15):
            if self._stop_requested:
                log.info("Прослушивание остановлено пользователем")
                return None  # Специальное значение - остановка
            log.info("Речь не обнаружена за 15 секунд")
            return np.array([], dtype=np.float32)
        
        print("  🎤 [ЗАПИСЬ ИДЁТ - говорите...]")
        
        # Начинаем с буфера (чтобы не потерять начало фразы!)
        frames = list(self._speech_buffer)
        self._speech_buffer = []
        
        silence_frames = 0
        
        frame_duration_ms = 50  # 50 мс для точности
        frame_size = int(self.samplerate * frame_duration_ms / 1000)
        max_silence_frames = int(self.silence_duration * 1000 / frame_duration_ms)
        max_frames = int(self.max_duration * 1000 / frame_duration_ms)
        
        start_time = time.time()
        
        # Сохраняем настройки терминала
        old_settings = termios.tcgetattr(sys.stdin)
        
        try:
            tty.setcbreak(sys.stdin.fileno())
            
            for _ in range(max_frames):
                # Проверяем клавишу остановки
                if self._check_stop_key():
                    log.info("Запись остановлена пользователем")
                    return None  # Специальное значение - остановка
                
                try:
                    audio = sd.rec(
                        frame_size,
                        samplerate=self.samplerate,
                        channels=1,
                        dtype='float32',
                        device=self.device
                    )
                    sd.wait()
                    audio_1d = audio.squeeze()
                    
                    frames.append(audio_1d)
                    
                    if self._is_speech(audio_1d):
                        silence_frames = 0
                    else:
                        silence_frames += 1
                    
                    if silence_frames >= max_silence_frames:
                        log.info(f"Пауза {self.silence_duration} сек — останавливаюсь")
                        break
                    
                    if time.time() - start_time >= self.max_duration:
                        log.info("Максимум записи")
                        break
                        
                except Exception as e:
                    log.error(f"Ошибка записи: {e}")
                    break
        finally:
            # Восстанавливаем настройки терминала
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        
        if not frames:
            return np.array([], dtype=np.float32)
        
        audio = np.concatenate(frames, axis=0)
        
        if len(audio) < int(self.min_speech_duration * self.samplerate):
            log.info("Запись слишком короткая")
            return np.array([], dtype=np.float32)
        
        log.info(f"Записано {len(audio) / self.samplerate:.1f} сек")
        return audio

    def play(self, audio, samplerate):
        with self._playback_lock:
            sd.play(audio, samplerate=samplerate)
            sd.wait()

    def play_interruptible(self, audio, samplerate, stop_event):
        """Как play(), но проверяет stop_event и останавливает воспроизведение
        досрочно, если событие установлено извне (из потока-слушателя).

        Параллельно оценивает громкость проигрываемого кусками по 100мс и
        пишет в self._current_output_level — это использует
        listen_for_interruption для отсечения самослышания (эха)."""
        chunk_ms = 100
        chunk_size = int(samplerate * chunk_ms / 1000)

        with self._playback_lock:
            sd.play(audio, samplerate=samplerate)
            pos = 0
            while sd.get_stream().active:
                if stop_event.is_set():
                    sd.stop()
                    self._current_output_level = 0.0
                    return

                # Грубая оценка громкости текущего проигрываемого куска —
                # не синхронизировано идеально с реальным выводом звуковой карты,
                # но достаточно для порога "эхо vs реальная речь"
                chunk = audio[pos:pos + chunk_size]
                if len(chunk) > 0:
                    self._current_output_level = float(np.abs(chunk.astype(np.float32)).mean())
                pos += chunk_size

                time.sleep(chunk_ms / 1000)

            self._current_output_level = 0.0

    def listen_for_interruption(self, stop_event, check_interval=0.05, energy_margin=2.5):
        """Слушает микрофон короткими чанками, пока stop_event не установлен
        (обычно — пока играет TTS). Возвращает True, если поймала речь
        пользователя (значит нужно прервать TTS), False если stop_event
        сработал по другой причине (TTS доиграла сама).

        ВАЖНО про самослышание: если микрофон и динамик физически рядом
        (одна колонка), система без аппаратного эхоподавления (AEC) будет
        слышать собственную речь через микрофон. Тут это смягчается грубым
        порогом: замеряем фоновый уровень громкости излучения самого TTS
        через player.get_output_level() и реагируем только если входящий
        сигнал заметно (energy_margin раз) громче — то есть это не отражение
        динамика, а кто-то реально говорит поверх. Это НЕ настоящий AEC и
        не идеален (тихую речь поверх TTS может пропустить), но для
        одной колонки без спец. железа — рабочий компромисс.
        Если позже разнесёшь микрофон и динамик физически — можно убрать
        energy_margin проверку и реагировать на любую обнаруженную речь.
        """
        frame_size = int(self.samplerate * 0.1)  # 100 мс чанки — компромисс между отзывчивостью и нагрузкой

        while not stop_event.is_set():
            try:
                audio = sd.rec(
                    frame_size,
                    samplerate=self.samplerate,
                    channels=1,
                    dtype='float32',
                    device=self.device
                )
                sd.wait()
                audio_1d = audio.squeeze()

                if not self._is_speech(audio_1d):
                    continue

                # Похоже на речь — проверяем не эхо ли это самой TTS
                input_energy = float(np.abs(audio_1d).mean())
                output_energy = self.get_output_level()

                if output_energy <= 0 or input_energy > output_energy * energy_margin:
                    return True
                # иначе — вероятно собственное эхо, игнорируем и слушаем дальше
            except Exception:
                time.sleep(check_interval)

        return False

    def get_output_level(self):
        """Примерный текущий уровень громкости того, что сейчас играет через
        динамик (для грубого различения 'это эхо TTS' vs 'это пользователь').
        Обновляется из play_interruptible во время воспроизведения."""
        return getattr(self, "_current_output_level", 0.0)

    def stop(self):
        sd.stop()

    def play_beep(self, frequency=800, duration=0.15):
        """Короткий сигнал"""
        try:
            t = np.linspace(0, duration, int(self.samplerate * duration))
            tone = 0.3 * np.sin(2 * np.pi * frequency * t)
            sd.play(tone.astype(np.float32), samplerate=self.samplerate)
            sd.wait()
        except Exception as e:
            log.warning(f"Не удалось воспроизвести бип: {e}")
