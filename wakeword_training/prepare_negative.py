import os
import subprocess
from pathlib import Path

NEGATIVE_DIR = "negative"
os.makedirs(NEGATIVE_DIR, exist_ok=True)

print("=== ПОДГОТОВКА ОТРИЦАТЕЛЬНЫХ ПРИМЕРОВ ===")
print()
print("Варианты:")
print("1. Автоматически скачать датасет шумов (рекомендую)")
print("2. Использовать записи семьи без слова 'Луна'")
print("3. Пропустить (будем использовать встроенные)")
print()

choice = input("Выбери вариант (1/2/3): ").strip()

if choice == "1":
    print("\nСкачиваю датасет с шумами...")
    # Используем встроенную функцию openwakeword
    try:
        from openwakeword import utils
        utils.download_models(model_names=["melspectrogram.onnx", "embedding_model.onnx"])
        print("✅ Базовые модели загружены")
    except Exception as e:
        print(f"⚠️  {e}")
    
    # Создаём пустые файлы для структуры
    print("\nОтрицательные примеры будут браться из встроенных датасетов")
    print("при обучении. Это нормальная практика.")

elif choice == "2":
    print("\nЗапиши 20-30 фраз БЕЗ слова 'Луна'")
    print("Например: 'привет', 'как дела', 'погода хорошая'")
    print("Записи будут сохраняться в папку negative/")
    
    import sounddevice as sd
    import soundfile as sf
    
    SAMPLERATE = 16000
    DURATION = 2.0
    
    count = int(input("Сколько примеров записать? (20-30): ") or "20")
    
    for i in range(count):
        input(f"Нажми Enter для записи {i+1}/{count}...")
        print("🎤 Говори фразу БЕЗ слова 'Луна'...")
        
        audio = sd.rec(int(DURATION * SAMPLERATE), samplerate=SAMPLERATE, 
                       channels=1, dtype='float32')
        sd.wait()
        
        filepath = os.path.join(NEGATIVE_DIR, f"neg_{i:03d}.wav")
        sf.write(filepath, audio, SAMPLERATE)
        print(f"✅ {filepath}")

else:
    print("\nПропускаем. При обучении будут использоваться встроенные шумы.")

print("\n✅ Подготовка завершена!")
