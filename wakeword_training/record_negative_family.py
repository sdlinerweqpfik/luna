"""Запись отрицательных примеров разными членами семьи"""
import sounddevice as sd
import soundfile as sf
import os
import time

SAMPLERATE = 16000
DURATION = 2.0
OUTPUT_DIR = "negative"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Разные люди и разные фразы
PEOPLE = [
    ("sdak", ["привет как дела,
        "расскажи про космос",
        "включи свет",
        "погода сегодня",
        "спасибо большое",
        "доброе утро",
        "пока до завтра",
        "что нового",
        "помоги мне",
        "как тебя зовут",
    ]),
    ("brother", ["привет,
        "дай воды",
        "идём гулять",
        "где мой телефон",
        "я дома",
        "включи игру",
        "мам привет",
        "хочу есть",
        "скучно",
        "пошли играть",
    ]),
    ("mom", ["здравствуй,
        "убери комнату",
        "сделай уроки",
        "позвони бабушке",
        "что на обед",
        "купить хлеб",
        "не забудь про школу",
        "спокойной ночи",
        "будь осторожен",
        "я тебя люблю",
    ]),
]

print("="*60)
print("ЗАПИСЬ ОТРИЦАТЕЛЬНЫХ ПРИМЕРОВ СЕМЬЁЙ")
print("="*60)
print("ВАЖНО: НЕ говорите слово 'ЛУНА'!")
print()

total_recorded = 0
for person_name, phrases in PEOPLE:
    print(f"\n{'='*50}")
    print(f"🎤 ЗАПИСЬ ДЛЯ: {person_name}")
    print(f"{'='*50}")
    
    for i, phrase in enumerate(phrases):
        input(f"[{person_name}] Enter для записи {i+1}/{len(phrases)}...")
        print(f"   Скажи: '{phrase}'")
        
        try:
            audio = sd.rec(int(DURATION * SAMPLERATE), samplerate=SAMPLERATE, 
                           channels=1, dtype='float32')
            sd.wait()
            
            max_amp = abs(audio).max()
            if max_amp < 0.02:
                print("⚠️  Слишком тихо, пропускаю")
                continue
            
            filepath = os.path.join(OUTPUT_DIR, f"neg_{person_name}_{i:03d}.wav")
            sf.write(filepath, audio, SAMPLERATE)
            print(f"✅ {filepath}")
            total_recorded += 1
            
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ Ошибка: {e}")

print(f"\n🎉 Записано {total_recorded} отрицательных примеров семьёй")
