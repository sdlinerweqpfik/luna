"""Генерация отрицательных примеров через Piper (разные голоса)"""
import subprocess
import os
from pathlib import Path

OUTPUT_DIR = "negative_synthetic"
os.makedirs(OUTPUT_DIR, exist_ok=True)

VOICES = ["~/piper-voices/ru_RU-denis-medium.onnx,
    "~/piper-voices/ru_RU-irina-medium.onnx",
]

# Фразы БЕЗ слова "Луна"
PHRASES = ["привет как дела,
    "расскажи анекдот",
    "какая погода",
    "включи музыку",
    "выключи свет",
    "поставь таймер",
    "напомни про встречу",
    "курс доллара",
    "температура на улице",
    "открой браузер",
    "закрой программу",
    "громче звук",
    "тише пожалуйста",
    "что нового",
    "доброе утро",
    "спокойной ночи",
    "спасибо большое",
    "пожалуйста повтори",
    "я тебя не понял",
    "пока до свидания",
    "сколько времени",
    "какой сегодня день",
    "помоги мне пожалуйста",
    "что такое любовь",
    "расскажи сказку",
]

SPEEDS = [0.8, 1.0, 1.2]

count = 0
for voice in VOICES:
    voice_path = os.path.expanduser(voice)
    
    if not Path(voice_path).exists():
        print(f"⚠️  Голос не найден: {voice_path}")
        continue
    
    print(f"\nГенерирую через {Path(voice_path).name}...")
    
    for phrase in PHRASES:
        for speed in SPEEDS:
            output_file = f"{OUTPUT_DIR}/neg_synth_{count:03d}.wav"
            
            cmd = ["piper,
                "--model", voice_path,
                "--output_file", output_file,
                "--length-scale", str(1.0/speed),
            ]
            
            try:
                subprocess.run(
                    cmd, 
                    input=phrase.encode(),
                    capture_output=True,
                    timeout=30
                )
                
                if Path(output_file).exists():
                    count += 1
                    if count % 20 == 0:
                        print(f"  Сгенерировано {count}...")
            except Exception as e:
                print(f"❌ Ошибка: {e}")

print(f"\n🎉 Сгенерировано {count} синтетических отрицательных примеров")
