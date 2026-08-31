"""
Диагностика Voice ID — не меняет логику, только показывает реальные числа.

Запуск: python diagnose_speaker.py

Печатает:
1. Расстояния МЕЖДУ твоими же enrollment-сэмплами (внутрикластерный разброс)
   — если оно уже большое (> 0.3), проблема в качестве/условиях записи
   enrollment, а не в коротком окне активации.
2. Сколько сэмплов сохранено для каждого speaker_id.
"""
import json
import numpy as np
from pathlib import Path

from core.speaker_id import PROFILES_INDEX, _cosine_distance


def main():
    if not PROFILES_INDEX.exists():
        print("Нет сохранённых профилей — сначала запусти enroll_speaker.py")
        return

    with open(PROFILES_INDEX, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        print("Файл профилей пуст.")
        return

    print("=" * 60)
    print("ДИАГНОСТИКА ГОЛОСОВЫХ ПРОФИЛЕЙ")
    print("=" * 60)

    for speaker_id, embeddings_raw in data.items():
        embeddings = [np.array(e) for e in embeddings_raw]
        n = len(embeddings)
        print(f"\n👤 {speaker_id}: {n} сэмпл(ов)")

        if n < 2:
            print("   (нужно минимум 2 сэмпла для сравнения — пропускаю)")
            continue

        distances = []
        for i in range(n):
            for j in range(i + 1, n):
                d = _cosine_distance(embeddings[i], embeddings[j])
                distances.append(d)
                print(f"   сэмпл {i+1} <-> сэмпл {j+1}: расстояние {d:.3f}")

        avg = np.mean(distances)
        max_d = np.max(distances)
        print(f"   Среднее внутри профиля: {avg:.3f}, максимум: {max_d:.3f}")

        if avg > 0.3:
            print("   ⚠️  Большой разброс МЕЖДУ ТВОИМИ ЖЕ сэмплами — сами")
            print("      записи enrollment нестабильны (разные условия/громкость/дистанция)")
        elif avg > 0.15:
            print("   ⚠️  Умеренный разброс — приемлемо, но не идеально")
        else:
            print("   ✅ Профиль стабильный")

    # Если несколько профилей — межпрофильное расстояние тоже интересно
    if len(data) >= 2:
        print("\n" + "-" * 60)
        print("Межпрофильные расстояния (должны быть заметно БОЛЬШЕ внутрипрофильных):")
        speaker_ids = list(data.keys())
        for i in range(len(speaker_ids)):
            for j in range(i + 1, len(speaker_ids)):
                sid_a, sid_b = speaker_ids[i], speaker_ids[j]
                emb_a = [np.array(e) for e in data[sid_a]]
                emb_b = [np.array(e) for e in data[sid_b]]
                cross_distances = [_cosine_distance(a, b) for a in emb_a for b in emb_b]
                print(f"   {sid_a} <-> {sid_b}: среднее {np.mean(cross_distances):.3f}, "
                      f"минимум {np.min(cross_distances):.3f}")

    print("\n" + "=" * 60)
    print(f"Текущий порог совпадения (DEFAULT_MATCH_THRESHOLD): 0.25")
    print("=" * 60)


if __name__ == "__main__":
    main()
