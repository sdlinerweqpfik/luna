"""Скачивание датасетов шумов для аугментации"""
import os
import urllib.request
import zipfile
import tarfile
from pathlib import Path

NOISE_DIR = Path("noise")
NOISE_DIR.mkdir(exist_ok=True)

print("="*60)
print("СКАЧИВАНИЕ ДАТАСЕТОВ ШУМОВ")
print("="*60)

# MUSAN - музыка, речь, шумы (хорош для аугментации речи)
print("\n1. MUSAN (музыка, речь, шумы)...")
musan_url = "https://www.openslr.org/resources/17/musan.tar.gz"
musan_path = NOISE_DIR / "musan.tar.gz"

if not (NOISE_DIR / "musan").exists():
    print(f"   Скачиваю с {musan_url}...")
    try:
        urllib.request.urlretrieve(musan_url, musan_path)
        print("   Распаковываю...")
        with tarfile.open(musan_path, "r:gz") as tar:
            tar.extractall(NOISE_DIR)
        musan_path.unlink()  # Удаляем архив
        print("   ✅ MUSAN готов")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
else:
    print("   ✅ MUSAN уже скачан")

# ESC-50 - звуки окружения
print("\n2. ESC-50 (звуки окружения)...")
esc50_url = "https://github.com/karolpiczak/ESC-50/archive/master.zip"
esc50_path = NOISE_DIR / "esc50.zip"

if not (NOISE_DIR / "ESC-50-master").exists():
    print(f"   Скачиваю с {esc50_url}...")
    try:
        urllib.request.urlretrieve(esc50_url, esc50_path)
        print("   Распаковываю...")
        with zipfile.ZipFile(esc50_path, "r") as zip_ref:
            zip_ref.extractall(NOISE_DIR)
        esc50_path.unlink()
        print("   ✅ ESC-50 готов")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
else:
    print("   ✅ ESC-50 уже скачан")

print("\n" + "="*60)
print("✅ ДАТАСЕТЫ ШУМОВ ГОТОВЫ")
print("="*60)
print(f"Расположение: {NOISE_DIR}/")
print("\nИспользуем их для аугментации при обучении")
