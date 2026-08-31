import numpy as np
from scipy import signal
from scipy.signal import butter, lfilter
from faster_whisper import WhisperModel
import logging
import soundfile as sf

log = logging.getLogger("secretary.stt")


def _bandpass_filter(data, lowcut=300, highcut=3400, fs=16000, order=5):
    """Фильтр для очистки речи от низких/высоких частот"""
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return lfilter(b, a, data)


class STT:
    def __init__(self, cfg):
        print("Загрузка Whisper на CPU...")
        self.model = WhisperModel(
            cfg["model"],
            device=cfg["device"],
            compute_type=cfg["compute_type"]
        )
        self.language = cfg.get("language", "ru")
        self.samplerate_mic = cfg.get("samplerate", 44100)
        self.samplerate_whisper = 16000
        print("✅ Whisper готова")

    def transcribe(self, audio):
        # Сжимаем в 1D массив
        if hasattr(audio, "squeeze"):
            audio_1d = audio.squeeze()
        else:
            audio_1d = np.asarray(audio).squeeze()
        
        # Конвертируем в float32 если нужно
        if audio_1d.dtype != np.float32:
            audio_1d = audio_1d.astype(np.float32)
        
        # Проверка общей энергии аудио
        max_amp = np.abs(audio_1d).max()
        if max_amp < 0.005:
            log.info(f"Слишком тихий звук ({max_amp:.4f}) — пропускаем")
            return ""
        
        # Усиление если звук тихий
        if max_amp < 0.5:
            gain = min(2.0, 1.0 / max_amp)
            audio_1d = audio_1d * gain
            log.info(f"Усилено аудио: {max_amp:.3f} → {max_amp * gain:.3f}")
        
        # Ресемплинг до 16000 Гц (стандарт Whisper)
        if self.samplerate_mic != self.samplerate_whisper:
            new_length = int(len(audio_1d) * self.samplerate_whisper / self.samplerate_mic)
            audio_1d = signal.resample(audio_1d, new_length)
        
        # Фильтр шума (300-3400 Гц — диапазон человеческой речи)
        audio_1d = _bandpass_filter(audio_1d, fs=self.samplerate_whisper)
        
        # Сохраняем аудио для отладки (опционально)
        try:
            sf.write("/tmp/debug_audio.wav", audio_1d, self.samplerate_whisper)
        except Exception:
            pass
        
        # Параметры распознавания
        segments, info = self.model.transcribe(
            audio_1d,
            language=self.language,
            beam_size=5,
            best_of=3,
            temperature=0.0,
            condition_on_previous_text=False
        )
        
        # Собираем сегменты в список
        segments_list = list(segments)
        if not segments_list:
            log.info("Whisper не нашёл речи")
            return ""
        
        # Строгая фильтрация
        filtered = []
        for s in segments_list:
            if s.no_speech_prob < 0.5:  # было 0.8, стало строже
                if not self._is_hallucination(s.text):
                    filtered.append(s)
        
        # Собираем текст
        text = " ".join(s.text.strip() for s in filtered).strip()
        
        # Защита от повторов (типичная галлюцинация)
        if text:
            words = text.split()
            unique_words = set(words)
            if len(unique_words) < len(words) * 0.5:
                log.warning("Обнаружена галлюцинация (повторы)")
                return ""
        
        return text
    
    def _is_hallucination(self, text):
        """Проверяет типичные галлюцинации Whisper"""
        hallucinations = [
            # Титры и финальные фразы
            "субтитр", "редактор", "корректор", "перевод", "озвучка",
            "титры", "продолжение следует", "конец фильма", "конец серии",
            # Стандартные фразы YouTube/видео
            "спасибо за просмотр", "спасибо что смотрели", "подписывайтесь",
            "пожалуйста", "музыка", "динамичная",
            # Шумовые галлюцинации
            "you", "thank you", "please subscribe",
        ]
        
        text_lower = text.lower().strip()
        
        # 🆕 БЕЛЫЙ СПИСОК: короткие осмысленные команды (пропускаем несмотря на длину)
        # Галлюцинации Whisper — это случайный мусор, а не эти конкретные слова
        short_allowed = {
            "да", "нет", "не", "ок", "окей", "ага", "угу", "стоп",
            "хватит", "пока", "выход", "закрой",
        }
        
        # Нормализуем: убираем пробелы и знаки препинания по краям
        normalized = text_lower.strip(" .,!?…:;\"'")
        
        if normalized in short_allowed:
            # Это осмысленная короткая команда — НЕ галлюцинация
            return False
        
        # Слишком короткий текст (< 4 символов) — скорее всего шум
        if len(text_lower) < 4:
            log.info(f"Отфильтрован слишком короткий текст: '{text}'")
            return True
        
        # Проверка на известные галлюцинации
        for h in hallucinations:
            if h in text_lower:
                log.warning(f"Галлюцинация отфильтрована: {text}")
                return True
        
        # Повторяющиеся слова
        words = text_lower.split()
        if len(words) >= 3 and len(set(words)) == 1:
            log.warning(f"Галлюцинация (повтор слова): {text}")
            return True
        
        return False
