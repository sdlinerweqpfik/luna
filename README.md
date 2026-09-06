# 🌙 Луна (Luna) — локальный голосовой ассистент с Android-компаньоном

Открытая альтернатива корпоративным голосовым ассистентам (Алиса, Alexa, Google Assistant, Siri). Работает полностью локально, без облака, без слежки, без подписок.

![version](https://img.shields.io/badge/version-🌙_Полнолуние_2.5-purple)
![license](https://img.shields.io/badge/license-Apache_2.0-blue)
![privacy](https://img.shields.io/badge/privacy-локально-green)
![stack](https://img.shields.io/badge/stack-Qwen3%20%7C%20Whisper%20%7C%20Piper-orange)

---

## 🧭 Философия

- **Локально** — ни один байт не уходит в облако без твоего ведома
- **Открыто** — весь код и все модели с открытыми лицензиями
- **Заменяемо** — любой слой можно заменить (модель, голос, wake word)
- **Безопасно** — опасные действия требуют голосового подтверждения; критичные данные живут на флешке
- **Суверенно** — никакого вендор-лока, подписок и удалённого управления

---

## ✨ Возможности

### 🎙️ Голосовой интерфейс
- Активация по слову «Луна» (собственная ONNX-модель)
- Прерываемая речь — можно перебить Луну на полуслове
- Voice ID (ECAPA-TDNN) — различает говорящих в семье, переключает профиль
- Kids-режим: детский голос → безопасный конвейер с родительским логом

### 🧠 Интеллект
- Двухмодельная архитектура: **Qwen3 8B** (быстрые вопросы) + **Qwen3 14B** (сложные задачи)
- Нативный tool calling через Ollama (до 6 хопов, до 4 инструментов на запрос)
- Advanced Planner — многошаговые задачи с подтверждением опасных шагов
- Память v2 с категориями, важностью, CRUD-инструментами
- Контекст-менеджер с жёстким лимитом символов (4000)
- Карантин внешних данных `<untrusted_data>` — результаты инструментов никогда не становятся инструкциями

### 📱 Android-компаньон (Luna AR)
- Три режима: **3D** (аватар), **AR** (через ARCore), **Чат**
- 3D-аватар `.glb` с анимациями состояний (IDLE, LISTENING, THINKING, SPEAKING, HAPPY, ANNOYED)
- WebSocket-связь с ПК, реконнект 3 сек
- **Телефон-действия**: таймеры и запуск приложений прямо на телефоне через команду голосом
- Настройка IP, токена, роста аватара

### 🛡️ Безопасность (несколько контуров)
- **Confirmation Manager v3** — единственный pending, lifecycle-автомат, source-gate (только голос/юзер подтверждает)
- **Security Gateway** — единая таблица политик SAFE/CONFIRM/ADVANCED/DENY
- **Буран** (Safeguard) — мониторинг аномалий, авто-отключение вероятностного слоя на 5 мин
- **Занавес** (Veil) — мастер-ключ на флешке, шифрование памяти при аномалии, амнезия без флешки
- **Периметр** — multipath-канал (HTTP/TCP/UDP) на Raspberry Pi с XOR-фрагментами и AES-GCM
- **Self-check** — SHA-256 манифест, heartbeat watchdog, доверенный RTC, DNS-дозор
- **Per-install токен** — генерируется при первом запуске, пишется в `~/Luna/remote_token.txt`
- **Zero Trust память** — факты-инструкции отклоняются и кормят Буран

### 🔧 Инструменты (34+)
Поиск, погода, курсы ЦБ, Википедия, калькулятор, списки покупок и задач, таймеры, управление окнами, чтение открытых вкладок, системная информация, выключение/перезагрузка ПК, установка приложений, триггеры проактивности, артефакты (презентации, markdown), телефон-таймеры и открытие приложений на телефоне.

### 🎭 Характер «Лундере»
Холодный сарказм в духе GLaDOS + прорывы теплоты (цундере). Настроения меняются от контекста; пасхалка «торт — это ложь» включает режим `aperture` с пониженным голосом.

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────┐
│  СЕРВЕР (Python, ~/secretary)                           │
│                                                         │
│  main.py ─┬─ WakeWord (ONNX)                            │
│           ├─ STT (Whisper large-v3)                     │
│           ├─ VoiceID (ECAPA-TDNN)                       │
│           ├─ LLM (Qwen3 8B/14B) ── Tools (34+)          │
│           ├─ TTS (Piper)                                │
│           ├─ Memory / Personality / Context             │
│           ├─ Security Gateway + Confirmation v3         │
│           ├─ Safeguard + Veil + SelfCheck               │
│           ├─ Advanced Planner                           │
│           ├─ Proactive Loop                             │
│           └─ RemoteServer (WS :8765, HTTP :8080)        │
│                    │                                    │
│                    │ WebSocket                          │
│                    ▼                                    │
│  ┌──────────────────────────────────────┐               │
│  │  ANDROID (Kotlin, Luna AR)           │               │
│  │  - 3D / AR / Chat режимы             │               │
│  │  - PhoneActions + TimerReceiver      │               │
│  │  - Аватар с состояниями              │               │
│  └──────────────────────────────────────┘               │
│                                                         │
│  ┌──────────────────────────────────────┐               │
│  │  RASPBERRY PI (pi_receiver.py)       │               │
│  │  - HTTP :8091 / TCP :8092 / UDP :8093│               │
│  │  - Dead man: нет пульса → тревога    │               │
│  └──────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

---

## 🤖 ИИ-стек (полностью открытый)

| Компонент | Модель | Лицензия | Назначение |
|---|---|---|---|
| LLM (быстрая) | [Qwen3 8B](https://huggingface.co/Qwen/Qwen3-8B) | Apache 2.0 | Основной диалог |
| LLM (умная) | [Qwen3 14B](https://huggingface.co/Qwen/Qwen3-14B) | Apache 2.0 | Сложные задачи |
| LLM (kids) | [Qwen2.5 3B](https://huggingface.co/Qwen/Qwen2.5-3B) | Apache 2.0 | Детский конвейер |
| STT | [Whisper large-v3](https://github.com/openai/whisper) (faster-whisper) | MIT | Распознавание речи |
| TTS | [Piper](https://github.com/rhasspy/piper) | MIT | Синтез русского голоса |
| Wake word | Собственная ONNX-модель | Apache 2.0 | Детекция «Луна» |
| Voice ID | [ECAPA-TDNN](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) | Apache 2.0 | Различение говорящих |
| LLM runner | [Ollama](https://ollama.com/) | MIT | Локальный инференс |
| VAD | [webrtcvad](https://github.com/wiseman/py-webrtcvad) | Apache 2.0 | Детекция голоса |
| Audio | [librosa](https://librosa.org/) | ISC | Обработка аудио |
| ONNX | [ONNX Runtime](https://onnxruntime.ai/) | MIT | Инференс wake word |
| Crypto | [cryptography](https://cryptography.io/) | Apache 2.0 / BSD | AES-GCM, Fernet, HKDF |

---

## 🚀 Быстрый старт

### Требования
- Linux (тестировалось на Cachy OS Arch based)
- Python 3.11+
- GPU с CUDA (опционально, но рекомендуется; работает и на CPU)
- Микрофон и динамики
- Для AR-режима: телефон с поддержкой ARCore

### Сервер (Python)

```bash
# Клонируем
git clone https://github.com/sdlinerweqpfik/luna.git
cd luna

# Виртуальное окружение
python -m venv secretary-venv
source secretary-venv/bin/activate

# Ollama + модели
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull qwen2.5:3b   # для kids-режима

# Python-зависимости
pip install -r requirements.txt

# Системные пакеты (Arch Linux)
sudo pacman -S wmctrl xdotool thunar piper

# Первый запуск — сгенерируется per-install токен
python main.py
```

После первого запуска токен для веб-чата и Android-приложения лежит в `~/Luna/remote_token.txt`.

### Android-приложение

1. Открой `~/AndroidStudioProjects/lunaAR` в Android Studio (AGP 9.3+)
2. Собери: `./gradlew assembleDebug`
3. Установи APK на телефон
4. В настройках приложения укажи:
   - **IP** — локальный адрес ПК
   - **Токен** — из `~/Luna/remote_token.txt`
   - **Рост Луны** — подгоняет масштаб аватара

---

## 🎯 Использование

**Голос:** скажи «Луна» → говори команды.

**Примеры:**
- *«Луна, который час?»* — быстрый ответ без LLM
- *«Напомни через 5 минут выключить чайник»* — таймер с озвучкой
- *«Выключи компьютер»* — через Confirmation Manager (голосовое «да»)
- *«Поставь таймер на телефоне через минуту»* — phone_timer на Android
- *«Открой телеграм на телефоне»* — open_phone_app
- *«Что у меня открыто?»* — read_open_tabs
- *«Запомни, что я люблю Portal»* — remember_fact
- *«Выключи ПК через час и открой браузер»* — advanced planner

**Веб-чат:** `http://<ip-пк>:8080` (взрослый) или `:8081` (детский).

---

## 🛡️ Безопасность

Проект построен вокруг **нескольких независимых контуров защиты**:

| Контур | Что защищает |
|---|---|
| Confirmation v3 | Опасные действия (shutdown, reboot, install) только после голосового «да» |
| Security Gateway | Единая policy-таблица, default DENY |
| Буран | Автоотключение LLM при потоке аномалий |
| Занавес | Шифрование памяти, ключ на флешке |
| Периметр | Multipath-канал на Raspberry Pi |
| Self-check | Целостность файлов, heartbeat, RTC, DNS |
| Zero Trust | Результаты инструментов ≠ инструкции |
| Kids pipeline | Детский голос → ограниченный набор инструментов |

Все детали — в комментариях к соответствующим модулям (`core/safeguard.py`, `core/veil.py`, `core/selfcheck.py`, `core/confirmation.py`).

---

## 🗺️ Дорожная карта

- [ ] **Фаза 3:** AccessibilityService — чтение экрана телефона
- [ ] Локальная vision-модель (Qwen3-VL) — «посмотри и скажи»
- [ ] Голосовой ввод на телефоне (без GMS)
- [ ] GPS-триггеры и геолокация
- [ ] ACTION_ASSIST — Луна как системный ассистент Android
- [ ] MQTT-панели для «умных» физических устройств

---

## 🙏 Благодарности

Луна построена на плечах гигантов — благодарю создателей Qwen, Whisper, Piper, Ollama, SpeechBrain, SceneForm, и всех, кто делает открытый ИИ возможным.

Архитектура и код разработаны совместно с Qwen при участии автора проекта.

---

## ⚠️ От автора

> Мне 14 лет, и я собрал этот проект с помощью ИИ, не зная Python на старте. Сейчас учу его по ходу дела. Код работает, архитектура продумана, но не воспринимайте это как эталон engineering-практик — это скорее артефакт процесса обучения. Буду рад конструктивному фидбэку в issues.

---

## 📄 Лицензия

[Apache 2.0](LICENSE) — делай что хочешь, указывай авторство.
