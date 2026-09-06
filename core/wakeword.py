from core.selfcheck import heartbeat
import sounddevice as sd
import numpy as np
import librosa
import onnxruntime as ort
from pathlib import Path
import logging

log = logging.getLogger("secretary.wakeword")


class WakeWordDetector:
    def __init__(self, cfg, wake_word="луна"):
        self.wake_word = wake_word.lower()
        self.device = cfg.get("mic_device")
        self.samplerate = cfg.get("samplerate", 44100)
        
        # Параметры модели
        self.model_path = cfg.get("wake_model_path", "~/secretary/wakeword_training/luna_model.onnx")
        self.model_path = Path(self.model_path).expanduser()
        
        # Порог срабатывания
        self.threshold = cfg.get("wake_threshold", 0.5)
        
        # Размер окна анализа
        self.window_duration = cfg.get("wake_window", 1.5)
        self.overlap = cfg.get("wake_overlap", 0.5)
        
        # Отладка
        self.debug = cfg.get("wake_debug", False)
        
        # Проверяем что модель существует
        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель не найдена: {self.model_path}")
        
        # Загружаем ONNX модель
        print(f"Загрузка модели '{wake_word}' из {self.model_path}...")
        self.session = ort.InferenceSession(str(self.model_path))
        self.input_name = self.session.get_inputs()[0].name
        print("✅ Модель загружена")
        
        # Буфер для накопления аудио
        self.audio_buffer = []
        self.buffer_size = int(self.window_duration * self.samplerate)

    def _extract_features(self, audio):
        """Извлекает мел-спектрограмму из аудио"""
        # Ресемплим до 16000
        if self.samplerate != 16000:
            audio_16k = librosa.resample(audio, orig_sr=self.samplerate, target_sr=16000)
        else:
            audio_16k = audio
        
        # Нормализуем
        audio_16k = audio_16k.astype(np.float32)
        max_amp = np.abs(audio_16k).max()
        if max_amp > 0:
            audio_16k = audio_16k / max_amp
        
        # Извлекаем мел-спектрограмму
        mel = librosa.feature.melspectrogram(
            y=audio_16k, sr=16000, n_mels=80, hop_length=160
        )
        mel = librosa.power_to_db(mel, ref=np.max)
        
        # Нормализуем спектрограмму
        mel = (mel - mel.mean()) / (mel.std() + 1e-8)
        
        # Приводим к фиксированному размеру (150 фреймов)
        target_frames = 150
        if mel.shape[1] > target_frames:
            mel = mel[:, :target_frames]
        elif mel.shape[1] < target_frames:
            mel = np.pad(mel, ((0, 0), (0, target_frames - mel.shape[1])))
        
        # Добавляем batch и channel измерения: (1, 1, 80, 150)
        mel = mel[np.newaxis, np.newaxis, :, :]
        
        return mel.astype(np.float32)

    def check_audio(self, audio):
        """Проверяет аудио на наличие слова"""
        try:
            # Проверка на тишину
            if np.abs(audio).max() < 0.02:
                return False, 0.0
            
            # Извлекаем признаки
            features = self._extract_features(audio)
            
            # Запускаем модель
            outputs = self.session.run(None, {self.input_name: features})
            score = float(outputs[0][0][0])  # logit
            
            # Применяем sigmoid для получения вероятности
            probability = 1.0 / (1.0 + np.exp(-score))
            
            if self.debug:
                log.info(f"Score: {probability:.3f}")
                if probability > 0.3:
                    print(f"  [score: {probability:.3f}]")
            
            # Проверяем порог
            if probability >= self.threshold:
                return True, probability
            
            return False, probability
            
        except Exception as e:
            log.error(f"Ошибка детекции: {e}")
            return False, 0.0

    def listen_once(self):
        """Слушает одно окно и проверяет наличие слова.
        Возвращает (is_detected, audio_16k) — audio_16k нужен для Voice ID
        сразу после активации, чтобы не записывать отдельно ещё раз."""
        try:
            # Записываем окно
            audio = sd.rec(
                int(self.window_duration * self.samplerate),
                samplerate=self.samplerate,
                channels=1,
                dtype='float32',
                device=self.device
            )
            sd.wait()
            audio_1d = audio.squeeze()

            # Проверяем
            is_detected, score = self.check_audio(audio_1d)

            # Ресемплим до 16kHz для переиспользования в speaker_id
            # (та же логика, что внутри _extract_features, но тут нужен
            # именно ресемплированный сигнал отдельно, не мел-спектрограмма)
            if self.samplerate != 16000:
                audio_16k = librosa.resample(audio_1d, orig_sr=self.samplerate, target_sr=16000)
            else:
                audio_16k = audio_1d
            audio_16k = audio_16k.astype(np.float32)

            return is_detected, audio_16k

        except Exception as e:
            log.error(f"Ошибка записи: {e}")
            return False, None

    def wait_for_wake_word(self):
        """Блокирующее ожидание слова.
        Возвращает audio_16k окна активации (для Voice ID), или None если
        по какой-то причине аудио недоступно."""
        log.info(f"Ожидаю слово '{self.wake_word}'...")

        while True:
            heartbeat("voice_loop")
            is_detected, audio_16k = self.listen_once()
            if is_detected:
                log.info(f"Wake word '{self.wake_word}' активирован!")
                return audio_16k

    def stop(self):
        self.active = False
