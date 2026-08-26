"""
Обучение модели wake word 'Луна' v2
Исправлен дисбаланс классов + улучшена архитектура
"""
import os
import logging
import json
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("training_v2.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("training")


def main():
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
    import numpy as np
    import soundfile as sf
    import librosa
    
    log.info("="*60)
    log.info("ОБУЧЕНИЕ 'ЛУНА' v2 (исправлен дисбаланс классов)")
    log.info("="*60)
    
    # === ПАРАМЕТРЫ ===
    SAMPLE_RATE = 16000
    DURATION = 1.5
    EPOCHS = 100
    BATCH_SIZE = 16
    LEARNING_RATE = 0.0005
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    
    log.info(f"Устройство: {DEVICE}")
    
    # === ПОДГОТОВКА ДАННЫХ ===
    all_positive_dir = Path("all_positive")
    if not all_positive_dir.exists():
        all_positive_dir.mkdir()
        import shutil
        for f in Path("positive").glob("*.wav"):
            shutil.copy(f, all_positive_dir / f.name)
        for f in Path("generated").glob("*.wav"):
            shutil.copy(f, all_positive_dir / f.name)
    
    positive_files = list(all_positive_dir.glob("*.wav"))
    negative_files = list(Path("negative").glob("*.wav"))
    
    log.info(f"Положительных: {len(positive_files)}")
    log.info(f"Отрицательных: {len(negative_files)}")
    
    # === ДАТАСЕТ ===
    class WakeWordDataset(Dataset):
        def __init__(self, positive_files, negative_files, augment=False):
            self.samples = [(str(f), 1) for f in positive_files] + \
                          [(str(f), 0) for f in negative_files]
            self.augment = augment
        
        def __len__(self):
            return len(self.samples)
        
        def __getitem__(self, idx):
            filepath, label = self.samples[idx]
            
            # Загружаем аудио
            audio, sr = sf.read(filepath)
            
            # Ресемплим если нужно
            if sr != SAMPLE_RATE:
                audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
            
            # Аугментация для положительных (если включена)
            if self.augment and label == 1:
                # Случайный сдвиг высоты тона
                if np.random.rand() < 0.3:
                    audio = librosa.effects.pitch_shift(
                        audio, sr=SAMPLE_RATE, n_steps=np.random.uniform(-2, 2)
                    )
                # Добавляем шум
                if np.random.rand() < 0.3:
                    noise = np.random.normal(0, 0.005, len(audio))
                    audio = audio + noise
            
            # Обрезаем/дополняем
            target_len = int(SAMPLE_RATE * DURATION)
            if len(audio) > target_len:
                audio = audio[:target_len]
            elif len(audio) < target_len:
                audio = np.pad(audio, (0, target_len - len(audio)))
            
            # Нормализуем
            audio = audio.astype(np.float32)
            max_amp = np.abs(audio).max()
            if max_amp > 0:
                audio = audio / max_amp
            
            # Извлекаем мел-спектрограмму
            mel = librosa.feature.melspectrogram(
                y=audio, sr=SAMPLE_RATE, n_mels=80, hop_length=160
            )
            mel = librosa.power_to_db(mel, ref=np.max)
            
            # Приводим к фиксированному размеру
            target_frames = 150
            if mel.shape[1] > target_frames:
                mel = mel[:, :target_frames]
            elif mel.shape[1] < target_frames:
                mel = np.pad(mel, ((0, 0), (0, target_frames - mel.shape[1])))
            
            return torch.FloatTensor(mel).unsqueeze(0), torch.FloatTensor([label])
    
    # === МОДЕЛЬ (улучшенная) ===
    class WakeWordModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 32, 3, padding=1),
                nn.BatchNorm2d(32),
                nn.ReLU(),
                nn.MaxPool2d(2),
                
                nn.Conv2d(32, 64, 3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.MaxPool2d(2),
                
                nn.Conv2d(64, 128, 3, padding=1),
                nn.BatchNorm2d(128),
                nn.ReLU(),
                nn.MaxPool2d(2),
            )
            
            self.classifier = nn.Sequential(
                nn.Dropout(0.5),
                nn.Linear(128 * 10 * 18, 256),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(256, 64),
                nn.ReLU(),
                nn.Linear(64, 1),
                nn.Sigmoid()
            )
        
        def forward(self, x):
            x = self.features(x)
            x = x.view(x.size(0), -1)
            x = self.classifier(x)
            return x
    
    # === СОЗДАЁМ ДАТАСЕТЫ ===
    train_dataset = WakeWordDataset(positive_files, negative_files, augment=True)
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    
    # === ВЗВЕШЕННЫЙ SAMPLER (борьба с дисбалансом) ===
    labels = [s[1] for s in train_dataset.samples]
    class_counts = [labels.count(0), labels.count(1)]
    log.info(f"Классы: 0 (нет Луны): {class_counts[0]}, 1 (Луна): {class_counts[1]}")
    
    # Веса обратно пропорциональны частоте класса
    class_weights = [1.0 / c for c in class_counts]
    sample_weights = [class_weights[label] for label in labels]
    
    sampler = WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(train_dataset),
        replacement=True
    )
    
    train_loader_balanced = DataLoader(
        train_dataset, 
        batch_size=BATCH_SIZE, 
        sampler=sampler
    )
    
    # === МОДЕЛЬ И ОПТИМИЗАТОР ===
    model = WakeWordModel().to(DEVICE)
    
    # Взвешенный loss
    pos_weight = torch.tensor([class_counts[0] / class_counts[1]]).to(DEVICE)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=10
    )
    
    # === ОБУЧЕНИЕ ===
    log.info(f"Начинаю обучение: {EPOCHS} эпох, {len(train_dataset)} образцов")
    log.info("Использую взвешенный sampler для баланса классов")
    
    best_accuracy = 0
    
    for epoch in range(EPOCHS):
        model.train()
        total_loss = 0
        correct = 0
        total = 0
        
        # Используем сбалансированный loader
        for batch_idx, (data, labels) in enumerate(train_loader_balanced):
            data = data.to(DEVICE)
            labels = labels.to(DEVICE)
            
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
        avg_loss = total_loss / len(train_loader_balanced)
        
        scheduler.step(avg_loss)
        
        # Логируем каждую 10 эпоху
        if (epoch + 1) % 10 == 0 or epoch == 0:
            log.info(f"Эпоха {epoch+1}/{EPOCHS}: loss={avg_loss:.4f}, accuracy={accuracy:.1f}%")
        
        # Сохраняем лучшую модель
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            torch.save(model.state_dict(), "luna_best_model.pth")
    
    log.info(f"✅ Обучение завершено. Лучшая точность: {best_accuracy:.1f}%")
    
    # === ЭКСПОРТ В ONNX ===
    log.info("Экспортирую модель в ONNX...")
    
    # Загружаем лучшую модель
    model.load_state_dict(torch.load("luna_best_model.pth"))
    model.eval()
    
    dummy_input = torch.randn(1, 1, 80, 150).to(DEVICE)
    
    try:
        torch.onnx.export(
            model,
            dummy_input,
            "luna_model.onnx",
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
            opset_version=14
        )
        log.info("✅ ONNX модель сохранена: luna_model.onnx")
    except Exception as e:
        log.error(f"❌ Ошибка экспорта: {e}")
        return False
    
    # === СОХРАНЯЕМ СТАТИСТИКУ ===
    stats = {
        "epochs": EPOCHS,
        "best_accuracy": best_accuracy,
        "final_loss": avg_loss,
        "training_date": datetime.now().isoformat(),
        "device": DEVICE,
        "positive_samples": len(positive_files),
        "negative_samples": len(negative_files),
        "class_balance_fix": True,
    }
    with open("model_stats.json", "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    log.info(f"Статистика: точность {best_accuracy:.1f}%")
    return True


if __name__ == "__main__":
    success = main()
    
    if success:
        print("\n" + "="*60)
        print("✅ ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО!")
        print("="*60)
        print("Запусти 'python test_model.py' для проверки")
    else:
        print("\n" + "="*60)
        print("❌ ОБУЧЕНИЕ НЕ УДАЛОСЬ")
        print("="*60)

