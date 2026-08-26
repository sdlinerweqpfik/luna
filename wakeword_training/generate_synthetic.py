import subprocess
import os
from pathlib import Path

OUTPUT_DIR = "generated"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Голоса, которые у тебя есть
VOICES = ["~/piper-voices/ru_RU-denis-medium.onnx,
    "~/piper-voices/ru_RU-irina-medium.onnx",
]

# Разные варианты произношения слова "Луна"
PHRASES = ["Луна,
    "Луна",
    "Луна!",
    "Эй, Луна",
    "Луна, слушай",
]

# Разные скорости речи
SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.2]

count = 0
for voice in VOICES:
    voice_path = os.path.expanduser(voice)
    
    # Проверяем что голос существует
    if not Path(voice_path).exists():
        print(f"⚠️  Голос не найден: {voice_path}")
        continue
    
    for phrase in PHRASES:
        for speed in SPEEDS:
            output_file = f"{OUTPUT_DIR}/synth_{count:03d}.wav"
            
            cmd = ["piper,
                "--model", voice_path,
                "--output_file", output_file,
                "--length-scale", str(1.0/speed),
            ]
            
            try:
                process = subprocess.run(
                    cmd, 
                    input=phrase.encode(),
                    capture_output=True,
                    timeout=30
                )
                
                if Path(output_file).exists():
                    count += 1
                    if count % 10 == 0:
                        print(f"  Сгенерировано {count} примеров...")
            except Exception as e:
                print(f"❌ Ошибка: {e}")

print(f"\n🎉 Сгенерировано {count} синтетических примеров")
