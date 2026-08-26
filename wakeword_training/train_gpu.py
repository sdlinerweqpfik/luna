"""
Обучение модели wake word 'Луна' с поддержкой GPU + CPU.
Запуск: python train_gpu.py
"""
import os
import sys
import logging
import json
from pathlib import Path
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("training.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("training")


def check_gpu():
    """Проверяет доступность GPU"""
    log.info("Проверяю доступность GPU...")
    
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1e9
            log.info(f"✅ GPU доступна: {gpu_name} ({gpu_memory:.1f} ГБ)")
            return "cuda"
        else:
            log.warning("⚠️  CUDA не доступна в PyTorch, использую CPU")
            return "cpu"
    except ImportError:
        log.warning("⚠️  PyTorch не установлен, использую CPU")
        return "cpu"


def check_data():
    """Проверяет наличие данных"""
    positive_files = list(Path("positive").glob("*.wav"))
    generated_files = list(Path("generated").glob("*.wav"))
    negative_files = list(Path("negative").glob("*.wav"))
    
    log.info(f"Положительные примеры (семья): {len(positive_files)}")
    log.info(f"Синтетические примеры: {len(generated_files)}")
    log.info(f"Отрицательные примеры: {len(negative_files)}")
    
    if len(positive_files) < 10:
        log.error("❌ Слишком мало положительных примеров! Нужно минимум 10.")
        return False
    
    return True


def prepare_data():
    """Объединяет все данные для обучения"""
    import shutil
    
    all_positive_dir = Path("all_positive")
    if all_positive_dir.exists():
        shutil.rmtree(all_positive_dir)
    all_positive_dir.mkdir()
    
    # Копируем записи семьи
    for f in Path("positive").glob("*.wav"):
        shutil.copy(f, all_positive_dir / f.name)
    
    # Копируем синтетические
    for f in Path("generated").glob("*.wav"):
        shutil.copy(f, all_positive_dir / f.name)
    
    total = len(list(all_positive_dir.glob("*.wav")))
    log.info(f"Всего положительных примеров: {total}")
    
    return str(all_positive_dir)


def train_with_pytorch(positive_dir, negative_dir, device):
    """Обучение с использованием PyTorch (если доступен)"""
    log.info(f"Обучение на устройстве: {device}")
    
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
        from torch.utils.data import Dataset, DataLoader
        import numpy as np
        import soundfile as sf
        import librosa
        
        # Параметры
        SAMPLE_RATE = 16000
        DURATION = 1.5
        EPOCHS = 50
        BATCH_SIZE = 16
        LEARNING_RATE = 0.001
        
        # Датасет
        class WakeWordDataset(Dataset):
            def __init__(self, positive_dir, negative_dir):
                self.samples = []
                
                # Положительные примеры
                for f in Path(positive_dir).glob("*.wav"):
                    self.samples.append((str(f), 1))
                
                # Отрицательные примеры
                for f in Path(negative_dir).glob("*.wav"):
                    self.samples.append((str(f), 0))
                
                log.info(f"Всего образцов: {len(self.samples)}")
            
            def __len__(self):
                return len(self.samples)
            
            def __getitem__(self, idx):
                filepath, label = self.samples[idx]
                
                # Загружаем аудио
                audio, sr = sf.read(filepath)
                
                # Ресемплим если нужно
                if sr != SAMPLE_RATE:
                    audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
                
                # Обрезаем/дополняем до нужной длины
                target_len = int(SAMPLE_RATE * DURATION)
                if len(audio) > target_len:
                    audio = audio[:target_len]
                elif len(audio) < target_len:
                    audio = np.pad(audio, (0, target_len - len(audio)))
                
                # Нормализуем
                audio = audio.astype(np.float32)
                audio = audio / (np.abs(audio).max() + 1e-8)
                
                # Извлекаем мел-спектрограмму
                mel = librosa.feature.melspectrogram(
                    y=audio, sr=SAMPLE_RATE, n_mels=80, hop_length=160
                )
                mel = librosa.power_to_db(mel, ref=np.max)
                
                # Приводим к фиксированному размеру
                if mel.shape[1] > 150:
                    mel = mel[:, :150]
                elif mel.shape[1] < 150:
                    mel = np.pad(mel, ((0, 0), (0, 150 - mel.shape[1])))
                
                return torch.FloatTensor(mel).unsqueeze(0), torch.FloatTensor([label])
        
        # Простая модель классификации
        class WakeWordModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv2d(1, 32, 3, padding=1)
                self.conv2 = nn.Conv2d(32, 64, 3, padding=1)
                self.pool = nn.MaxPool2d(2)
                self.dropout = nn.Dropout(0.3)
                self.fc1 = nn.Linear(64 * 20 * 37, 128)
                self.fc2 = nn.Linear(128, 1)
                self.relu = nn.ReLU()
            
            def forward(self, x):
                x = self.pool(self.relu(self.conv1(x)))
                x = self.pool(self.relu(self.conv2(x)))
                x = x.view(x.size(0), -1)
                x = self.dropout(self.relu(self.fc1(x)))
                x = torch.sigmoid(self.fc2(x))
                return x
        
        # Создаём датасет и модель
        dataset = WakeWordDataset(positive_dir, negative_dir)
        dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)
        
        model = WakeWordModel().to(device)
        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
        
        # Обучение
        log.info(f"Начинаю обучение: {EPOCHS} эпох, {len(dataset)} образцов")
        
        for epoch in range(EPOCHS):
            model.train()
            total_loss = 0
            correct = 0
            total = 0
            
            for batch_idx, (data, labels) in enumerate(dataloader):
                data = data.to(device)
                labels = labels.to(device)
                
                optimizer.zero_grad()
                outputs = model(data)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
                
                total_loss += loss.item()
                predicted = (outputs > 0.5).float()
                correct += (predicted == labels).sum().item()
                total += labels.size(0)
            
            accuracy = 100 * correct / total
            avg_loss = total_loss / len(dataloader)
            
            if (epoch + 1) % 5 == 0 or epoch == 0:
                log.info(f"Эпоха {epoch+1}/{EPOCHS}: loss={avg_loss:.4f}, accuracy={accuracy:.1f}%")
        
        # Сохраняем модель
        torch.save(model.state_dict(), "luna_pytorch_model.pth")
        log.info("✅ Модель сохранена: luna_pytorch_model.pth")
        
        # Экспортируем в ONNX для использования в openwakeword
        log.info("Экспортирую модель в ONNX...")
        dummy_input = torch.randn(1, 1, 80, 150).to(device)
        torch.onnx.export(
            model,
            dummy_input,
            "luna_model.onnx",
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}}
        )
        log.info("✅ ONNX модель сохранена: luna_model.onnx")
        
        # Сохраняем статистику
        stats = {
            "epochs": EPOCHS,
            "final_accuracy": accuracy,
            "final_loss": avg_loss,
            "training_date": datetime.now().isoformat(),
            "device": device,
            "positive_samples": len([s for s in dataset.samples if s[1] == 1]),
            "negative_samples": len([s for s in dataset.samples if s[1] == 0]),
        }
        with open("model_stats.json", "w") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        log.info(f"Статистика: точность {accuracy:.1f}%")
        
        return True
        
    except Exception as e:
        log.error(f"❌ Ошибка PyTorch обучения: {e}")
        import traceback
        log.error(traceback.format_exc())
        return False


def train_with_openwakeword(positive_dir, negative_dir):
    """Обучение через openwakeword (если доступно)"""
    log.info("Пробую обучение через openwakeword...")
    
    try:
        import openwakeword
        from openwakeword.model import Model
        import openwakeword.utils
        
        # Скачиваем предобученные модели
        openwakeword.utils.download_models()
        
        # Пытаемся использовать встроенный метод обучения
        try:
            from openwakeword.train import train_model
            
            result = train_model(
                positive_dir=positive_dir,
                negative_dir=negative_dir,
                model_name="luna",
                epochs=50,
            )
            
            log.info(f"✅ Обучение openwakeword завершено: {result}")
            return True
            
        except ImportError:
            log.warning("Метод обучения не найден в openwakeword")
            return False
            
    except Exception as e:
        log.error(f"❌ Ошибка обучения через openwakeword: {e}")
        return False


def main():
    log.info("="*60)
    log.info("ОБУЧЕНИЕ WAKE WORD МОДЕЛИ 'ЛУНА' (GPU + CPU)")
    log.info("="*60)
    
    # Проверяем данные
    if not check_data():
        return False
    
    # Проверяем GPU
    device = check_gpu()
    
    # Подготавливаем данные
    positive_dir = prepare_data()
    negative_dir = "negative"
    
    # Пробуем обучение через openwakeword
    log.info("\n=== ПОПЫТКА 1: Обучение через openwakeword ===")
    if train_with_openwakeword(positive_dir, negative_dir):
        return True
    
    # Если не получилось — используем PyTorch
    log.info("\n=== ПОПЫТКА 2: Обучение через PyTorch ===")
    if train_with_pytorch(positive_dir, negative_dir, device):
        return True
    
    log.error("❌ Все методы обучения не удались")
    return False


if __name__ == "__main__":
    success = main()
    
    if success:
        print("\n" + "="*60)
        print("✅ ОБУЧЕНИЕ ЗАВЕРШЕНО!")
        print("="*60)
        print("Следующий шаг: запусти 'python test_model.py' для проверки")
    else:
        print("\n" + "="*60)
        print("❌ ОБУЧЕНИЕ НЕ УДАЛОСЬ")
        print("="*60)
        print("Смотри training.log для деталей")
