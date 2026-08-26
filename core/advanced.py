"""
Advanced Mode — свой lightweight интерпретатор кода.
Использует qwen3:14b для генерации Python кода под нестандартные задачи.
Не требует внешних зависимостей (tiktoken, open-interpreter).
"""
import logging
import subprocess
import sys
import tempfile
import os
import re
from pathlib import Path

log = logging.getLogger("secretary.advanced")

# === ЧЁРНЫЙ СПИСОК (запрещено даже с подтверждением) ===
FORBIDDEN_PATTERNS = [
    r'\brm\s+(-\w+\s+)?/',           # rm -rf /
    r'\brm\s+(-\w+\s+)?~',          # rm -rf ~
    r'\bmkfs\b',                     # форматирование
    r'\bdd\s+if=',                   # запись на диск
    r'\bshutdown\b',                 # выключение
    r'\breboot\b',                   # перезагрузка
    r':\(\)\s*\{',                   # fork bomb
    r'chmod\s+(-R\s+)?777',          # небезопасные права
    r'>\s*/dev/sd',                  # запись на диск
    r'curl.*\|\s*(bash|sh|python)',  # загрузка и выполнение
    r'wget.*\|\s*(bash|sh|python)',
    r'sudo\s+rm',                    # sudo + удаление
    r'os\.system\s*\(',              # опасный os.system
    r'subprocess\..*shell\s*=\s*True', # subprocess с shell=True
    r'__import__',                   # динамический импорт
    r'exec\s*\(',                    # exec в коде (двойная опасность)
    r'eval\s*\(',                    # eval в коде
    r'compile\s*\(',                 # compile
]

# === РАЗРЕШЁННЫЕ МОДУЛИ (whitelist для импортов) ===
ALLOWED_MODULES = {
    # Стандартная библиотека
    'os', 'pathlib', 'shutil', 'glob', 'fnmatch', 'stat',
    'datetime', 'time', 'calendar',
    'math', 'statistics', 'random',
    'json', 'csv', 'xml', 'html',
    're', 'string', 'textwrap',
    'collections', 'itertools', 'functools',
    'hashlib', 'hmac',
    'io', 'tempfile',
    'typing', 'dataclasses',
    'urllib', 'http',
    'socket', 'email', 'mimetypes',
    'subprocess',  # разрешён, но с ограничениями
    'base64', 'binascii',
    'pprint', 'copy',
    'platform', 'sys', 'inspect',
}


def _extract_code(llm_response: str) -> tuple[str, str]:
    """Извлекает Python код из ответа LLM.
    Возвращает (код, объяснение)."""
    # Пробуем найти блок ```python ... ```
    match = re.search(r'```(?:python)?\s*\n(.*?)```', llm_response, re.DOTALL)
    if match:
        code = match.group(1).strip()
        explanation = llm_response[:match.start()].strip()
        if not explanation:
            explanation = llm_response[match.end():].strip()
        return code, explanation
    
    # Если нет markdown блоков — ищем строки начинающиеся с import/def/class
    lines = llm_response.split('\n')
    code_lines = []
    in_code = False
    explanation_lines = []
    
    for line in lines:
        stripped = line.strip()
        if not in_code and (stripped.startswith(('import ', 'from ', 'def ', 'class ', 'if ', 'for ', 'while ', 'print(', 'os.', 'pathlib'))):
            in_code = True
        if in_code:
            code_lines.append(line)
        else:
            explanation_lines.append(line)
    
    if code_lines:
        return '\n'.join(code_lines).strip(), '\n'.join(explanation_lines).strip()
    
    return "", llm_response


def _is_safe_code(code: str) -> tuple[bool, str]:
    """Проверяет код на опасные паттерны и неразрешённые импорты."""
    # Проверка чёрного списка
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            return False, f"Обнаружена запрещённая конструкция: {pattern}"
    
    # Проверка импортов
    import_matches = re.findall(r'^(?:import|from)\s+(\w+)', code, re.MULTILINE)
    for module in import_matches:
        if module not in ALLOWED_MODULES:
            return False, f"Импорт неразрешённого модуля: {module}"
    
    # Проверка длины (слишком длинный код подозрителен)
    if len(code) > 5000:
        return False, "Код слишком длинный (>5000 символов)"
    
    return True, "OK"


def _confirm_with_user(task: str, code: str, explanation: str) -> bool:
    """Интерактивное подтверждение выполнения кода."""
    print("\n" + "=" * 60)
    print("🔧 РАСШИРЕННЫЙ РЕЖИМ (Advanced Mode)")
    print("=" * 60)
    print(f"📝 Задача: {task}")
    if explanation:
        print(f"💡 План: {explanation[:200]}")
    print(f"\n💻 Сгенерированный код:\n{'─' * 40}")
    print(code)
    print("─" * 40)
    print("\n⚠️  Внимание! Код будет выполнен на твоём компьютере.")
    print("   Внимательно проверь код выше.")
    
    response = input("\nВыполнить? [да/нет/показать еще раз]: ").strip().lower()
    
    if response in ["да", "yes", "y", "ок", "выполнить", "давай"]:
        return True
    if response in ["показать", "еще", "again"]:
        return _confirm_with_user(task, code, explanation)
    return False


def _execute_code(code: str, timeout: int = 60) -> tuple[bool, str]:
    """Выполняет код в изолированном subprocess."""
    # Создаём временный файл
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(code)
        temp_path = f.name
    
    try:
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path.home(),  # выполняем из домашней папки
        )
        
        output = result.stdout.strip()
        error = result.stderr.strip()
        
        if result.returncode == 0:
            return True, output if output else "Код выполнен успешно (без вывода)"
        else:
            return False, f"Ошибка выполнения:\n{error}"
    
    except subprocess.TimeoutExpired:
        return False, f"Код не завершился за {timeout} секунд"
    except Exception as e:
        return False, f"Ошибка запуска: {e}"
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass


def advanced_task(llm, task: str) -> str:
    """
    Решить нестандартную задачу путём генерации и выполнения Python кода.
    
    Используй ТОЛЬКО когда ни один из специализированных инструментов
    не подходит для задачи пользователя. Например: работа с файлами
    по шаблону, пакетная обработка, сложные вычисления, работа с данными.
    
    Args:
        task: подробное описание задачи на русском языке
    """
    if not llm:
        return "LLM недоступен для генерации кода."
    
    prompt = f"""Ты — Python-программист. Пользователь попросил решить задачу.
Напиши Python код для её решения.

ПРАВИЛА:
1. Используй только стандартную библиотеку Python (os, pathlib, shutil, glob, datetime, re, json, csv, math, subprocess и т.д.)
2. Код должен быть самодостаточным и выполняемым через `python script.py`
3. Не используй внешние библиотеки (numpy, pandas, requests и т.д.)
4. Печатай результат через print()
5. Добавляй комментарии на русском
6. Обрабатывай возможные ошибки
7. НЕ пиши пояснений — только код

ЗАДАЧА: {task}

Верни ТОЛЬКО код, без объяснений и markdown."""

    try:
        # Используем smart модель для лучшего кода
        response = llm.ask(llm.smart, prompt, include_history=False, max_tool_hops=0)
        
        # Извлекаем код
        code, explanation = _extract_code(response)
        
        if not code:
            log.warning(f"Не удалось извлечь код из ответа: {response[:200]}")
            return "Не смогла сгенерировать код для этой задачи."
        
        # Проверка безопасности
        is_safe, reason = _is_safe_code(code)
        if not is_safe:
            log.warning(f"Опасный код отклонён: {reason}")
            return f"⛔ Код отклонён системой безопасности: {reason}"
        
        # Подтверждение
        if not _confirm_with_user(task, code, explanation):
            return "Задача отменена пользователем."
        
        # Выполнение
        log.info(f"Выполняю сгенерированный код для задачи: {task[:100]}")
        success, output = _execute_code(code)
        
        if success:
            return f"✅ Задача выполнена:\n{output}"
        else:
            return f"❌ Ошибка при выполнении:\n{output}"
    
    except Exception as e:
        log.error(f"Ошибка в advanced_task: {e}")
        return f"Не смогла обработать задачу: {e}"
