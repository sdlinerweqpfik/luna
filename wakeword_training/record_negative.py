import sounddevice as sd
import soundfile as sf
import os
import time

SAMPLERATE = 16000
DURATION = 2.0
OUTPUT_DIR = "negative"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PHRASES = ["Привет как дела,
    "Какая сегодня погода",
    "Сколько времени сейчас",
    "Расскажи анекдот",
    "Включи музыку",
    "Выключи свет",
    "Поставь таймер",
    "Напомни про встречу",
    "Курс доллара",
    "Температура на улице",
    "Открой браузер",
    "Закрой программу",
    "Громче звук",
    "Тише пожалуйста",
    "Что нового",
    "Доброе утро",
    "Спокойной ночи",
    "Спасибо большое",
    "Пожалуйста повтори",
    "Я тебя не понял",
]

print("="*60)
print("ЗАПИСЬ ОТРИЦАТЕЛЬНЫХ ПРИМЕРОВ (БЕЗ СЛОВА 'ЛУНА')")
print("="*60)
print(f"Нужно записать {len(PHRASES)} фраз")
print("Говори каждую фразу естественно, как в обычном разговоре")
print()

for i, phrase in enumerate(PHRASES):
    input(f"Нажми Enter для записи {i+1}/{len(PHRASES)}...")
    print(f"🎤 Скажи: '{phrase}'")
    
    audio = sd.rec(int(DURATION * SAMPLERATE), samplerate=SAMPLERATE, 
                   channels=1, dtype='float32')
    sd.wait()
    
    # Проверка что есть звук
    max_amp = abs(audio).max()
    if max_amp < 0.02:
        print("⚠️  Слишком тихо, давай ещё раз")
        i -= 1
        continue
    
    filepath = os.path.join(OUTPUT_DIR, f"neg_{i:03d}.wav")
    sf.write(filepath, audio, SAMPLERATE)
    print(f"✅ Сохранено: {filepath}")
    
    time.sleep(0.3)

print(f"\n🎉 Записано {len(PHRASES)} отрицательных примеров!")
print(f"Теперь можно запускать обучение")
