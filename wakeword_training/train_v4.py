"""
Обучение wake word 'Луна' v4
Исправлено:
- Убран двойной sigmoid (критический баг!)
- Уменьшена модель (меньше параметров для маленького датасета)
- Добавлен validation split
- Поднят learning rate
"""
import os
import logging
import json
import random
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("training_v4.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("training")


def collect_negative_files():
    """Собирает все отрицательные примеры"""
    negative_files = []
    
    family_dir = Path("negative")
    if family_dir.exists():
        files = list(family_dir.glob("*.wav"))
        negative_files.extend(files)
        log.info(f"Отрицательные (семья): {len(files)}")
    
    synth_dir = Path("negative_synthetic")
    if synth_dir.exists():
        files = list(synth_dir.glob("*.wav"))
        negative_files.extend(files)
        log.info(f"Отрицательные (синтез): {len(files)}")
    
    esc50_dir = Path("noise/ESC-50-master/audio")
    if esc50_dir.exists():
        files = list(esc50_dir.glob("*.wav"))[:200]
        negative_files.extend(files)
        log.info(f"Шумы из ESC-50: {len(files)}")
    
    return negative_files


def main():
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler, random_split
    import numpy as np
    import soundfile as sf
    import librosa
    
    log.info("="*60)
    log.info("ОБУЧЕНИЕ 'ЛУНА' v4 (исправлен двойной sigmoid)")
    log.info("="*60)
    
    # === ПАРАМЕТРЫ ===
    SAMPLE_RATE = 16000
    DURATION = 1.5
    EPOCHS = 100
    BATCH_SIZE = 16
    LEARNING_RATE = 0.001  # подняли с 0.0005
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    
    log.info(f"Устройство: {DEVICE}")
    
    # === ДАННЫЕ ===
    all_positive_dir = Path("all_positive")
    if not all_positive_dir.exists():
        all_positive_dir.mkdir()
        import shutil
        for f in Path("positive").glob("*.wav"):
            shutil.copy(f, all_positive_dir / f.name)
        for f in Path("generated").glob("*.wav"):
            shutil.copy(f, all_positive_dir / f.name)
    
    positive_files = list(all_positive_dir.glob("*.wav"))
    negative_files = collect_negative_files()
    
    log.info(f"Положительных: {len(positive_files)}")
    log.info(f"Отрицательных: {len(negative_files)}")
    
    # === ДАТАСЕТ ===
    class WakeWordDataset(Dataset):
        def __init__(self, positive_files, negative_files, noise_files=None, augment=False):
            self.samples = [(str(f), 1) for f in positive_files] + \
                          [(str(f), 0) for f in negative_files]
            self.noise_files = noise_files or []
            self.augment = augment
        
        def __len__(self):
            return len(self.samples)
        
        def __getitem__(self, idx):
            filepath, label = self.samples[idx]
            
            try:
                audio, sr = sf.read(filepath)
                
                if len(audio.shape) > 1:
                    audio = audio.mean(axis=1)
                
                if sr != SAMPLE_RATE:
                    audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
                
                # Лёгкая аугментация
                if self.augment and label == 1:
                    if np.random.rand() < 0.3:
                        # Случайный сдвиг по времени
                        shift = np.random.randint(-1600, 1600)
                        audio = np.roll(audio, shift)
                    
                    if np.random.rand() < 0.3:
                        # Небольшой шум
                        noise = np.random.normal(0, 0.005, len(audio))
                        audio = audio + noise
                
                target_len = int(SAMPLE_RATE * DURATION)
                if len(audio) > target_len:
                    audio = audio[:target_len]
                elif len(audio) < target_len:
                    audio = np.pad(audio, (0, target_len - len(audio)))
                
                audio = audio.astype(np.float32)
                max_amp = np.abs(audio).max()
                if max_amp > 0:
                    audio = audio / max_amp
                
                mel = librosa.feature.melspectrogram(
                    y=audio, sr=SAMPLE_RATE, n_mels=80, hop_length=160
                )
                mel = librosa.power_to_db(mel, ref=np.max)
                
                # Нормализуем спектрограмму
                mel = (mel - mel.mean()) / (mel.std() + 1e-8)
                
                target_frames = 150
                if mel.shape[1] > target_frames:
                    mel = mel[:, :target_frames]
                elif mel.shape[1] < target_frames:
                    mel = np.pad(mel, ((0, 0), (0, target_frames - mel.shape[1])))
                
                return torch.FloatTensor(mel).unsqueeze(0), torch.FloatTensor([label])
            except Exception as e:
                log.warning(f"Ошибка чтения {filepath}: {e}")
                mel = np.zeros((80, 150), dtype=np.float32)
                return torch.FloatTensor(mel).unsqueeze(0), torch.FloatTensor([0])
    
    # === КОМПАКТНАЯ МОДЕЛЬ (БЕЗ SIGMOID!) ===
    class WakeWordModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 16, 3, padding=1),
                nn.BatchNorm2d(16),
                nn.ReLU(),
                nn.MaxPool2d(2),
                
                nn.Conv2d(16, 32, 3, padding=1),
                nn.BatchNorm2d(32),
                nn.ReLU(),
                nn.MaxPool2d(2),
                
                nn.Conv2d(32, 64, 3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.AdaptiveAvgPool2d((4, 4)),  # адаптивный пулинг — компактно
            )
            
            # ВАЖНО: НЕТ sigmoid! BCEWithLogitsLoss сам его применит
            self.classifier = nn.Sequential(
                nn.Dropout(0.5),
                nn.Linear(64 * 4 * 4, 64),
                nn.ReLU(),
                nn.Dropout(0.3),
                nn.Linear(64, 1)  # logits, не вероятности!
            )
        
        def forward(self, x):
            x = self.features(x)
            x = x.view(x.size(0), -1)
            x = self.classifier(x)
            return x
    
    # === ШУМЫ ДЛЯ АУГМЕНТАЦИИ ===
    noise_files = []
    esc50_dir = Path("noise/ESC-50-master/audio")
    if esc50_dir.exists():
        noise_files = list(esc50_dir.glob("*.wav"))[:200]
    
    log.info(f"Шумов для аугментации: {len(noise_files)}")
    
    # === СОЗДАЁМ ДАТАСЕТ ===
    full_dataset = WakeWordDataset(
        positive_files, negative_files, noise_files, augment=True
    )
    
    # Разделение на train/validation (85/15)
    total_len = len(full_dataset)
    val_len = int(total_len * 0.15)
    train_len = total_len - val_len
    
    train_dataset, val_dataset = random_split(
        full_dataset, [train_len, val_len],
        generator=torch.Generator().manual_seed(42)
    )
    
    log.info(f"Train: {train_len}, Validation: {val_len}")
    
    # === ВЗВЕШЕННЫЙ SAMPLER ===
    # Считаем классы только в train
    train_indices = train_dataset.indices
    train_labels = [full_dataset.samples[i][1] for i in train_indices]
    class_counts = [train_labels.count(0), train_labels.count(1)]
    log.info(f"Классы в train: 0={class_counts[0]}, 1={class_counts[1]}")
    
    class_weights = [1.0 / max(c, 1) for c in class_counts]
    sample_weights = [class_weights[train_labels[i]] for i in range(len(train_labels))]
    
    sampler = WeightedRandomSampler(
        weights=sample_weights,
        num_samples=len(train_dataset),
        replacement=True
    )
    
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, sampler=sampler)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False)
    
    # === МОДЕЛЬ ===
    model = WakeWordModel().to(DEVICE)
    
    # Количество параметров
    total_params = sum(p.numel() for p in model.parameters())
    log.info(f"Параметров модели: {total_params:,}")
    
    # BCEWithLogitsLoss (БЕЗ pos_weight — sampler уже балансирует)
    criterion = nn.BCEWithLogitsLoss()
    
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='max', factor=0.5, patience=10
    )
    
    # === ФУНКЦИИ ===
    def evaluate(loader):
        model.eval()
        correct = 0
        total = 0
        tp, fp, tn, fn = 0, 0, 0, 0
        
        with torch.no_grad():
            for data, labels in loader:
                data = data.to(DEVICE)
                labels = labels.to(DEVICE)
                
                outputs = model(data)
                probs = torch.sigmoid(outputs)  # применяем sigmoid только для предсказания
                predicted = (probs > 0.5).float()
                
                correct += (predicted == labels).sum().item()
                total += labels.size(0)
                
                # Матрица ошибок
                tp += ((predicted == 1) & (labels == 1)).sum().item()
                fp += ((predicted == 1) & (labels == 0)).sum().item()
                tn += ((predicted == 0) & (labels == 0)).sum().item()
                fn += ((predicted == 0) & (labels == 1)).sum().item()
        
        accuracy = 100 * correct / total
        
        # Precision и Recall
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)
        
        return accuracy, precision, recall, f1
    
    # === ОБУЧЕНИЕ ===
    log.info(f"Начинаю обучение: {EPOCHS} эпох")
    
    best_f1 = 0
    patience_counter = 0
    max_patience = 25
    
    for epoch in range(EPOCHS):
        # Train
        model.train()
        total_loss = 0
        
        for data, labels in train_loader:
            data = data.to(DEVICE)
            labels = labels.to(DEVICE)
            
            optimizer.zero_grad()
            outputs = model(data)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
        
        avg_loss = total_loss / len(train_loader)
        
        # Validation
        val_acc, val_prec, val_rec, val_f1 = evaluate(val_loader)
        train_acc, _, _, _ = evaluate(train_loader)
        
        scheduler.step(val_f1)
        
        # Лог
        if (epoch + 1) % 5 == 0 or epoch == 0:
            log.info(
                f"Эпоха {epoch+1}/{EPOCHS}: "
                f"loss={avg_loss:.4f} | "
                f"train_acc={train_acc:.1f}% | "
                f"val_acc={val_acc:.1f}% | "
                f"val_f1={val_f1:.3f}")
        
        # Сохраняем лучшую модель (по F1)
        if val_f1 > best_f1:
            best_f1 = val_f1
            torch.save(model.state_dict(), "luna_best_model.pth")
            log.info(f"  💾 Лучшая модель сохранена (F1={val_f1:.3f})")
            patience_counter = 0
        else:
            patience_counter += 1
        
        # Early stopping
        if patience_counter >= max_patience:
            log.info(f"Early stopping на эпохе {epoch+1}")
            break
    
    log.info(f"✅ Обучение завершено. Лучший F1: {best_f1:.3f}")
    
    # === ФИНАЛЬНАЯ ОЦЕНКА ===
    model.load_state_dict(torch.load("luna_best_model.pth"))
    val_acc, val_prec, val_rec, val_f1 = evaluate(val_loader)
    log.info(f"Финальная валидация: acc={val_acc:.1f}%, precision={val_prec:.3f}, recall={val_rec:.3f}, F1={val_f1:.3f}")
    
    # === ЭКСПОРТ В ONNX ===
    log.info("Экспортирую модель в ONNX...")
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
        log.info("Попробуй установить: pip install onnx onnxscript")
        # Не возвращаем False — модель обучена, просто ONNX не экспортировался
    
    # === СТАТИСТИКА ===
    stats = {
        "epochs_trained": epoch + 1,
        "best_f1": best_f1,
        "final_val_accuracy": val_acc,
        "final_val_precision": val_prec,
        "final_val_recall": val_rec,
        "training_date": datetime.now().isoformat(),
        "device": DEVICE,
        "positive_samples": len(positive_files),
        "negative_samples": len(negative_files),
    }
    with open("model_stats.json", "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    return True


if __name__ == "__main__":
    success = main()
    
    if success:
        print("\n" + "="*60)
        print("✅ ОБУЧЕНИЕ ЗАВЕРШЕНО!")
        print("="*60)
    else:
        print("\n❌ ОБУЧЕНИЕ НЕ УДАЛОСЬ")
