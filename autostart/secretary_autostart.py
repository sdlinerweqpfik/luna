#!/usr/bin/env python
"""
Автозапуск голосового ассистента 'Луна' с защитой паролем и подтверждением.

Запускается при входе в систему через ~/.config/autostart/luna-assistant.desktop.
1. Запрашивает пароль (до 3 попыток, ввод скрыт).
2. После успешной проверки спрашивает подтверждение запуска [y/n].
3. Только при 'y' запускает main.py.

Пароль хранится как PBKDF2-хэш с солью, а не в открытом виде.
"""
import getpass
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path

SECRETARY_DIR = Path.home() / "secretary"
PASSWORD_FILE = SECRETARY_DIR / "autostart" / ".luna_auth"
MAIN_SCRIPT = SECRETARY_DIR / "main.py"

MAX_ATTEMPTS = 3


def load_credentials():
    """Возвращает (соль, хэш) из файла аутентификации."""
    if not PASSWORD_FILE.exists():
        return None, None
    try:
        with open(PASSWORD_FILE, "rb") as f:
            data = f.read()
        return data[:16], data[16:]
    except Exception:
        return None, None


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)


def verify_password(password):
    """Сравнивает введённый пароль с сохранённым хэшем (защита от timing-атак)."""
    salt, stored_key = load_credentials()
    if salt is None or stored_key is None:
        return False
    entered_key = hash_password(password, salt)
    return secrets.compare_digest(entered_key, stored_key)


def pause_exit(message="Нажмите Enter для выхода..."):
    try:
        input(f"\n{message}")
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    print("=" * 60)
    print("🌙 Голосовой ассистент 'Луна' — защищённый запуск")
    print("=" * 60)
    print()

    # Шаг 1: проверяем что пароль вообще установлен
    salt, _ = load_credentials()
    if salt is None:
        print("⚠️  Пароль ещё не установлен!")
        print("Установите его командой:")
        print("  ~/secretary-venv/bin/python ~/secretary/autostart/set_password.py")
        pause_exit()
        return

    # Шаг 2: запрос пароля с ограничением попыток
    authenticated = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            password = getpass.getpass(f"🔒 Пароль (попытка {attempt}/{MAX_ATTEMPTS}): ")
        except (EOFError, KeyboardInterrupt):
            print("\nОтмена.")
            return
        if verify_password(password):
            authenticated = True
            print("✅ Пароль верный.")
            break
        print("❌ Неверный пароль.")

    if not authenticated:
        print(f"\n🚫 Превышено число попыток ({MAX_ATTEMPTS}). Запуск отменён.")
        pause_exit()
        return

    # Шаг 3: подтверждение запуска
    try:
        answer = input("\n🚀 Запустить ассистента 'Луна'? [y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nОтмена.")
        return

    if answer not in ("y", "yes", "да", "д"):
        print("Запуск отменён пользователем. Хорошего дня!")
        pause_exit()
        return

    # Шаг 4: запуск ассистента
    print("\n🎙️  Запускаю Луну...\n")
    try:
        subprocess.run([sys.executable, str(MAIN_SCRIPT)], cwd=str(SECRETARY_DIR))
    except KeyboardInterrupt:
        print("\nАссистент остановлен.")


if __name__ == "__main__":
    main()
