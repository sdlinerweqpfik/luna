"""
Voice ID: различение говорящих (для 2-3 членов семьи) по голосовому
embedding, для персонализации памяти/имени — БЕЗ ограничения доступа
к опасным действиям (это осознанно вне scope, см. main.py комментарий).

Модель: ECAPA-TDNN через SpeechBrain (speechbrain/spkrec-ecapa-voxceleb) —
стандартная, проверенная модель для speaker verification, работает на CPU,
16kHz моно (тот же формат, что уже использует STT). EER ~0.69% на VoxCeleb —
с большим запасом для различения 2-3 голосов в спокойных бытовых условиях.

Модель считается тяжёлой ТОЛЬКО на первом вызове (holding модели в памяти
процесса) — enroll и identify после этого быстрые (доли секунды на CPU
для короткого клипа).

Распознавание происходит ОДИН РАЗ за разговор — на аудио самой активации
по wake word "Луна", а не на каждую последующую реплику. Это осознанный
компромисс: говорящий не меняется посреди диалога в реалистичном
сценарии использования, а гонять embedding на каждую реплику было бы
заметной лишней нагрузкой на CPU (i3-10105F) без практической пользы.
"""
import logging
import json
import numpy as np
from pathlib import Path

log = logging.getLogger("secretary.speaker_id")

PROFILES_DIR = Path.home() / "secretary" / "voice_profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_INDEX = PROFILES_DIR / "index.json"

# Косинусное расстояние ниже этого порога = "тот же говорящий".
# Изначально стоял консервативный 0.25 ("лучше не узнать своего, чем
# перепутать чужого"), но на реальных данных (диагностика через
# diagnose_speaker.py) это оказалось СЛИШКОМ строго: внутрипрофильный
# разброс (одни и те же люди, разные фразы/условия записи через бытовой
# микрофон, не студийная запись) у реальных пользователей доходил до
# 0.58, а межпрофильное расстояние (разные люди) было от 0.79. Порог 0.25
# резал даже легитимные совпадения. 0.65 — с запасом выше максимального
# наблюдённого внутрипрофильного разброса и заметно ниже минимального
# межпрофильного расстояния. Если после этого раза начнутся ложные
# срабатывания (путает людей) — можно подстроить точнее по факту.
DEFAULT_MATCH_THRESHOLD = 0.7

_classifier = None  # ленивая загрузка модели — не грузим при каждом импорте


def _get_classifier():
    """Загружает ECAPA-TDNN один раз и держит в памяти процесса.

    Явно фиксируем CPU (run_opts={"device": "cpu"}) вместо того чтобы
    полагаться на дефолт SpeechBrain — если torch в окружении видит CUDA
    (например GPU уже используется для Ollama, как на этой машине), по
    умолчанию модель может частично загрузиться на GPU, и тогда попытка
    сконвертировать тензор в numpy падает с ошибкой (тензор на GPU нельзя
    напрямую превратить в numpy-массив, сначала нужно скопировать на CPU).
    Для одного короткого speaker-embedding вызова GPU не даёт выигрыша,
    который стоил бы этой возни — CPU достаточно быстр для этой задачи."""
    global _classifier
    if _classifier is None:
        log.info("Загрузка модели speaker embedding (ECAPA-TDNN) на CPU...")
        from speechbrain.inference.speaker import EncoderClassifier
        _classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(PROFILES_DIR / ".model_cache"),
            run_opts={"device": "cpu"},
        )
        log.info("Модель speaker embedding загружена")
    return _classifier


def _extract_embedding(audio_16k: np.ndarray) -> np.ndarray:
    """Извлекает 192-мерный embedding из 16kHz моно аудио (float32, [-1, 1]).

    Args:
        audio_16k: аудио в формате, который уже отдаёт STT после ресемплинга
                   (см. core/stt.py) — переиспользуем ту же точку конвейера,
                   не считаем дважды.
    """
    import torch
    classifier = _get_classifier()
    signal = torch.from_numpy(audio_16k).float().unsqueeze(0)  # (1, samples)
    with torch.no_grad():
        embedding = classifier.encode_batch(signal)
    # .cpu() перед .numpy() — подстраховка, если тензор всё же окажется на
    # GPU (например run_opts не сработал по какой-то причине конкретного
    # окружения); .cpu() на CPU-тензоре — безопасный no-op, ничего не портит.
    return embedding.squeeze().cpu().numpy()


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """0.0 = идентичны, 2.0 = максимально далеки. Для голосов одного
    человека обычно < 0.15-0.2, для разных людей обычно > 0.3-0.4 —
    отсюда DEFAULT_MATCH_THRESHOLD=0.25 как разумная середина."""
    a_norm = a / (np.linalg.norm(a) + 1e-8)
    b_norm = b / (np.linalg.norm(b) + 1e-8)
    return float(1.0 - np.dot(a_norm, b_norm))


def _load_index():
    """speaker_id -> список embedding (list of list of float, JSON-совместимо)."""
    if PROFILES_INDEX.exists():
        try:
            with open(PROFILES_INDEX, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: [np.array(e) for e in v] for k, v in data.items()}
        except Exception as e:
            log.error(f"Ошибка загрузки профилей голосов: {e}")
    return {}


def _save_index(index):
    data = {k: [e.tolist() for e in v] for k, v in index.items()}
    with open(PROFILES_INDEX, "w", encoding="utf-8") as f:
        json.dump(data, f)


def enroll_speaker(speaker_id: str, audio_samples: list) -> str:
    """Регистрирует голосовой профиль говорящего по нескольким аудио-сэмплам.

    Args:
        speaker_id: идентификатор говорящего (тот же, что используется в
                    Memory/Personality, например 'mom', 'dad', 'default')
        audio_samples: список numpy-массивов 16kHz float32 моно — несколько
                       разных фраз для устойчивости профиля (рекомендуется
                       не меньше 3-5 фраз по 2-4 секунды каждая)
    """
    if len(audio_samples) < 3:
        log.warning(f"Мало сэмплов ({len(audio_samples)}) для enrollment {speaker_id} — рекомендуется от 3")

    embeddings = [_extract_embedding(a) for a in audio_samples]

    index = _load_index()
    index[speaker_id] = embeddings
    _save_index(index)

    log.info(f"Зарегистрирован голосовой профиль '{speaker_id}' ({len(embeddings)} сэмплов)")
    return f"Голосовой профиль для '{speaker_id}' сохранён по {len(embeddings)} образцам."


def identify_speaker(audio_16k: np.ndarray, threshold: float = DEFAULT_MATCH_THRESHOLD) -> tuple:
    """Определяет говорящего по аудио. Возвращает (speaker_id, distance)
    для лучшего совпадения, или ("default", None) если профилей нет
    или совпадение хуже threshold — то есть неопознанный голос НЕ
    подставляется под чужой профиль, а честно остаётся "default".

    Сравнение идёт со ВСЕМИ сэмплами каждого профиля — берётся минимальное
    расстояние (лучшее совпадение с любой из enrolled фраз), не среднее,
    потому что голос человека звучит по-разному в зависимости от фразы,
    и одно хорошее совпадение надёжнее усреднения по всем.
    """
    index = _load_index()
    if not index:
        return "default", None

    query_embedding = _extract_embedding(audio_16k)

    best_speaker = None
    best_distance = float("inf")

    for speaker_id, embeddings in index.items():
        for emb in embeddings:
            dist = _cosine_distance(query_embedding, emb)
            if dist < best_distance:
                best_distance = dist
                best_speaker = speaker_id

    if best_speaker is not None and best_distance <= threshold:
        log.info(f"Голос опознан как '{best_speaker}' (расстояние {best_distance:.3f})")
        return best_speaker, best_distance

    log.info(f"Голос не опознан (лучшее расстояние {best_distance:.3f} > порога {threshold})")
    return "default", best_distance


def list_enrolled_speakers() -> list:
    """Список speaker_id, для которых есть сохранённый голосовой профиль."""
    return list(_load_index().keys())


def remove_speaker(speaker_id: str) -> bool:
    """Удаляет голосовой профиль. Не трогает Memory/Personality данные
    этого speaker_id — только перестаёт его узнавать по голосу."""
    index = _load_index()
    if speaker_id in index:
        del index[speaker_id]
        _save_index(index)
        log.info(f"Голосовой профиль '{speaker_id}' удалён")
        return True
    return False
