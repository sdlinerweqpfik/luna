#!/usr/bin/env python
"""
Установка пароля для защищённого автозапуска 'Луны'.
Запустите один раз:
  ~/secretary-venv/bin/python ~/secretary/autostart/set_password.py
"""
import getpass
import hashlib
import os
from pathlib import Path

PASSWORD_FILE = Path.home() / "secretary" / "autostart" / ".luna_auth"


def hash_password(password, salt):
    """Хэширует пароль через PBKDF2-HMAC-SHA256 (100k итераций)."""
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)


def main():
    print("🔐 Установка пароля для автозапуска 'Луны'")
    print("=" * 50)

    p1 = getpass.getpass("Введите новый пароль: ")
    if len(p1) < 4:
        print("❌ Пароль слишком короткий (минимум 4 символа).")
        return

    p2 = getpass.getpass("Повторите пароль: ")
    if p1 != p2:
        print("❌ Пароли не совпадают.")
        return

    salt = os.urandom(16)          # случайная соль
    key = hash_password(p1, salt)

    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PASSWORD_FILE, "wb") as f:
        f.write(salt + key)        # 16 байт соли + хэш

    # Права 600 — читать/писать может только владелец
    os.chmod(PASSWORD_FILE, 0o600)

    print(f"\n✅ Пароль сохранён в {PASSWORD_FILE}")
    print("Теперь при старте системы Луна будет запрашивать этот пароль.")


if __name__ == "__main__":
    main()
