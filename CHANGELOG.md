# История изменений

## [2.3] 🌓 Первая четверть — 2026-08-31

Релиз контролируемых подсистем: Confirmation Manager v3, Memory v2,
Context Manager, Security Gateway, Advanced v2, диагностика и удалённый
доступ с детским профилем. 90/90 тестов зелёные.

### 🔐 Confirmation Manager v3 — state machine подтверждений
Было: модель сама передавала `confirm=True` в опасный инструмент.
Стало: полноценная машина состояний
`pending → prompting → prompted → executed/rejected/expired`.
- Единственный pending-слот с атомарными переходами под lock
- Уникальные ID действий; `claim_for_prompt()` / `mark_prompted()` / `abort_prompting()`
- TTL по monotonic-часам, стартует только с момента фактической доставки промпта
- Атомарный `confirm()`: executor ВСЕГДА вне lock, защита от параллельных подтверждений
- Source-gate: подтверждают только источники `voice` и `user`
- Защита от замещения уже озвученного pending; отказ подтверждения до доставки — fail-safe
- Строгий matcher: whitelist коротких фраз; негация, смешанные токены («да, нет»),
  контекст и неизвестные фразы → `ambiguous`, никогда не подтверждает

### 🧰 pc_tools: модель физически не может самоподтвердиться
- `shutdown_pc(confirm=...)`, `reboot_pc(confirm=...)`,
  `install_application(..., confirm=...)` → сигнатуры БЕЗ параметра confirm
- Опасный вызов теперь всегда создаёт запрос в Confirmation Manager;
  решение принимает только человек
- Whitelist приложений, валидация пакетных команд и защита путей — без изменений

### 🔗 Доставка промпта (main.py + голосовой цикл)
- `handle_request()`: сравнение pending до/после запроса →
  `claim_for_prompt()` → TTS → `mark_prompted()`; при сбое озвучки — `abort_prompting()`
- `conversation_mode()`: ветки confirm / reject / ambiguous;
  подтверждение принимается только из голоса (`source="voice"`)

### 🧠 LLM: новый протокол опасных действий
- Из системного промпта полностью убрана схема «вызови снова с confirm=True»
- Модель обязана озвучить действие и попросить «да/нет»; повторный вызов
  инструмента ради подтверждения запрещён

### 💾 Memory v2
- Структурированные факты: `id, text, category, importance, created_at, updated_at`
- Категории (preference / project / routine / important_date / personal /
  technical / temporary) и важность 1–5
- Точная дедупликация и нормализация текста
- Релевантный отбор вместо «последние N»: keyword recall + recency bonus +
  category weighting + лимиты
- Atomic JSON writes, миграция старого формата, graceful degradation
  при повреждённом файле
- `update_fact()`, `delete_fact()`, `list_my_facts()`

### 🧩 Context Manager
- Сборка контекста вынесена из llm.py в отдельный слой:
  core_memory, relevant_memory, recent_dialog, active_task_summary,
  confirmation_hint, personality_fragment → messages для LLM

### 🛡️ Security Gateway
- Единый обязательный слой выполнения инструментов:
  и обычный tool call, и Advanced проходят через `security_gateway.execute(...)`
- Advanced не получил отдельного обхода безопасности

### 🧗 Advanced Mode v2
- Уникальные ID шагов; чистая state machine (STEP_RUNNING, CANCELLED;
  убран неиспользуемый VERIFYING)
- `cancel_plan()`, `get_status()`, защита от повторного выполнения шага
- Пауза плана на WAITING_FOR_CONFIRMATION и resume с того же шага
  после «да» или «нет»
- Интеграция с Context Manager; каждый шаг — через Security Gateway

### 📊 Диагностика
- Структурированные JSON-события: request_start/request_end, span'ы
  llm/tool/context
- Пометка источника запроса (voice / mobile), счётчики без содержимого —
  приватность сохранена

### 📱 Remote-доступ и детский профиль (v1)
- WebSocket-сервер с токен-аутентификацией (hmac-сравнение) + веб-чат
  в локальной сети; зеркало диалоговой логики без аудио
- Ошибки обработки возвращаются в чат, а не обрывают соединение
- **Kids Mode**: изолированный порт/токен, отдельная лёгкая модель,
  белый список инструментов (`kids_tools`), программный pre-LLM перехват
  опасных команд ДО нейросети
- **Родительский контроль**: лог запросов детского профиля, детекция
  подозрительных ключевых слов, страница просмотра журнала
- Веб-клиент: раздельное хранение токенов, корректный повторный запрос
  при неверном токене вместо цикла реконнектов

### 🐛 Исправления
- Контракт `memory_command()`: строго `(обработано: bool, ответ: str)`
- Фильтрация инструментов KidsLLM по `__name__` (функции, а не словари)
- Стабилизация веб-сокета при ошибках обработки текста

### 🧪 Тесты
- Confirmation Manager 11/11, Voice Cycle 5/5, Security Gateway 17/17,
  Memory v2 15/15, остальные модули — суммарно **90/90 зелёных**

### 🚫 Осознанные отказы (design decisions)
Не добавляли: отдельный intent-слой, второй planner, vector DB ради
десятков фактов, отдельную LLM для планирования, rollback, persistence
планов, dynamic replanning. Не переписывали STT/TTS/Audio/Personality.
Native tool calling Qwen/Ollama сохранён и усилен, а не заменён.
