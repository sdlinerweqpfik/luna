import sounddevice as sd
import soundfile as sf
import os
import time

SAMPLERATE = 16000
DURATION = 1.5  # секунды на запись
OUTPUT_DIR = "positive"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def record_person(name, prefix, count):
    """Записывает примеры для одного человека"""
    print(f"\n{'='*50}")
    print(f"🎤 ЗАПИСЬ ДЛЯ: {name} ({count} примеров)")
    print(f"{'='*50}")
    print("Советы:")
    print("  - Говори 'ЛУНА' с разной громкостью")
    print("  - Меняй интонацию: вопрос, утверждение, радость")
    print("  - Меняй расстояние до микрофона")
    print("  - Иногда говори быстро, иногда медленно")
    print()
    
    recorded = 0
    while recorded < count:
        input(f"[{name}] Нажми Enter для записи {recorded+1}/{count}...")
        print(f"🎤 Говори 'ЛУНА'...")
        
        try:
            audio = sd.rec(int(DURATION * SAMPLERATE), samplerate=SAMPLERATE, 
                           channels=1, dtype='float32')
            sd.wait()
            
            # Проверка что есть звук
            max_amp = abs(audio).max()
            if max_amp < 0.02:
                print("⚠️  Слишком тихо, давай ещё раз")
                continue
            
            filepath = os.path.join(OUTPUT_DIR, f"{prefix}_{recorded:03d}.wav")
            sf.write(filepath, audio, SAMPLERATE)
            print(f"✅ Сохранено: {filepath} (громкость: {max_amp:.2f})")
            recorded += 1
            
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    print(f"🎉 {name}: записано {recorded} примеров!")


def main():
    print("="*50)
    print("ЗАПИСЬ СЛОВА 'ЛУНА' ДЛЯ ВСЕЙ СЕМЬИ")
    print("="*50)
    
    # Записываем каждого человека
    record_person("Артем", "sdak", 20)
    record_person("Данил", "brother", 10)
    record_person("Ольга", "mom", 10)
    
    # Итоги
    files = os.listdir(OUTPUT_DIR)
    print(f"\n{'='*50}")
    print(f"🎊 ВСЕГО ЗАПИСАНО: {len(files)} примеров")
    print(f"{'='*50}")
    
    # Подсчёт по людям
    sdak_count = len([f for f in files if f.startswith("sdak")])
    brother_count = len([f for f in files if f.startswith("brother")])
    mom_count = len([f for f in files if f.startswith("mom")])
    
    print(f"  Aртем:   {sdak_count}")
    print(f"  Данил:   {brother_count}")
    print(f"  Ольга:   {mom_count}")


if __name__ == "__main__":
    main()
