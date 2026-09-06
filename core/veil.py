"""
«Занавес» — изоляция и шифрование чувствительных данных.

Принципы:
- мастер-ключ живёт ТОЛЬКО на физической флешке (luna_key.bin);
- при старте с флешкой ключ читается в RAM и НЕ пишется на диск;
- при аномалии (Буран/Perimeter) данные шифруются в vault, plaintext
  уничтожается, ключ стирается из RAM — машина сама себя не разблокирует;
- снятие занавеса — только человек: флешка (+ резервный путь: пароль,
  если при init был задан passphrase-wrapped ключ);
- старт без флешки после занавеса = амнезия: ядро работает, личность пуста.

Базовый слой честности: это прикладной контур. Настоящая защита от
полного компромиса диска — LUKS сверху; Занавес защищает от атак
уровня приложения и от кражи/осмотра машины «на ходу».
"""
import os
import json
import shutil
import logging
import hashlib
from pathlib import Path

log = logging.getLogger("secretary.veil")

KEY_FILENAME = "luna_key.bin"
USB_MOUNTS = [Path("/run/media/sdak"), Path("/media/sdak"), Path("/mnt")]
VAULT_DIR = Path.home() / "Luna" / "veil"
STATE_FILE = VAULT_DIR / "veil_active.json"

SENSITIVE = [
    Path.home() / "secretary" / "memory.json",
    Path.home() / "secretary" / "profile.json",
    Path.home() / "Luna" / "proactive_log.json",
    Path.home() / "Luna" / "integrity_manifest.json",
]

_fernet = None          # ключ в RAM, только если флешка была при старте
_passphrase_backup = None


def find_usb_key():
    """Ищет luna_key.bin на примонтированных носителях."""
    for mount in USB_MOUNTS:
        if not mount.exists():
            continue
        for candidate in mount.rglob(KEY_FILENAME):
            return candidate
    return None


def _read_key(path) -> bytes:
    with open(path, "rb") as f:
        return f.read().strip()


def init(passphrase: str = None):
    """ОПЕРАЦИЯ ЧЕЛОВЕКА (один раз): генерирует ключ, пишет НА ФЛЕШКУ.
    На диск ключ НЕ попадает — только passphrase-wrapped резервная копия."""
    from cryptography.fernet import Fernet
    usb = find_usb_key()
    if usb is None:
        return "Вставь флешку и повтори: ключ будет записан только на неё."
    key = Fernet.generate_key()
    with open(usb, "wb") as f:
        f.write(key)
    os.chmod(usb, 0o600)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    if passphrase:
        wrapped = _wrap_with_passphrase(key, passphrase)
        with open(VAULT_DIR / "pass_backup.bin", "wb") as f:
            f.write(wrapped)
    log.info("🎭 Занавес инициализирован: ключ на флешке")
    return "Ключ записан на флешку. Держи её в сейфе."


def try_load_key():
    """При старте: если флешка на месте — ключ в RAM. Иначе — амнезия."""
    global _fernet
    from cryptography.fernet import Fernet
    usb = find_usb_key()
    if usb is None:
        _fernet = None
        if is_veiled():
            log.warning("🎭 Занавес опущен, флешки нет — старт с амнезией")
        return False
    _fernet = Fernet(_read_key(usb))
    return True


def is_veiled() -> bool:
    return STATE_FILE.exists()


def lockdown():
    """Вызывается предохранителем при аномалии. Шифрует и уничтожает plaintext."""
    global _fernet
    from cryptography.fernet import Fernet
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    if _fernet is None:
        # Ключа в RAM нет (флешку не вставляли при старте) — только изоляция
        log.warning("🎭 LOCKDOWN без ключа: только изоляция, без шифрования")
        for src in SENSITIVE:
            if src.exists():
                shutil.move(str(src), str(VAULT_DIR / (src.name + ".quarantine")))
    else:
        for src in SENSITIVE:
            if not src.exists():
                continue
            data = src.read_bytes()
            token = _fernet.encrypt(data)
            (VAULT_DIR / (src.name + ".enc")).write_bytes(token)
            _shred(src)
    STATE_FILE.write_text(json.dumps({"ts": __import__("time").time()}))
    _fernet = None  # машина больше не может открыть себя сама
    log.warning("🎭 ЗАНАВЕС ОПУЩЕН: данные в vault, ключ стёрт из RAM")


def unlock(passphrase: str = None):
    """ОПЕРАЦИЯ ЧЕЛОВЕКА: флешка (или пароль) → расшифровка и восстановление."""
    from cryptography.fernet import Fernet, InvalidToken
    key = None
    usb = find_usb_key()
    if usb is not None:
        key = _read_key(usb)
    elif passphrase:
        key = _unwrap_with_passphrase(passphrase)
    if key is None:
        return "Нужна флешка с ключом или резервный пароль."
    f = Fernet(key)
    restored = 0
    for src in SENSITIVE:
        enc = VAULT_DIR / (src.name + ".enc")
        q = VAULT_DIR / (src.name + ".quarantine")
        try:
            if enc.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_bytes(f.decrypt(enc.read_bytes()))
                enc.unlink()
                restored += 1
            elif q.exists():
                shutil.move(str(q), str(src))
                restored += 1
        except InvalidToken:
            return "Ключ не подошёл — расшифровка прервана."
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    log.info(f"🎭 Занавес снят человеком: восстановлено {restored} файлов")
    return f"Занавес снят. Восстановлено файлов: {restored}."


def _shred(path: Path):
    """Простое затирание перед удалением (честно: на SSD не идеально —
    поэтому базовым слоем остаётся LUKS)."""
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(os.urandom(min(size, 65536)))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass
    path.unlink(missing_ok=True)


def _wrap_with_passphrase(key: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    kek = kdf.derive(passphrase.encode())
    import base64
    wrapped = Fernet(base64.urlsafe_b64encode(kek)).encrypt(key)
    return salt + wrapped


def _unwrap_with_passphrase(passphrase: str) -> bytes:
    try:
        import base64
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        raw = (VAULT_DIR / "pass_backup.bin").read_bytes()
        salt, wrapped = raw[:16], raw[16:]
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
        kek = kdf.derive(passphrase.encode())
        return Fernet(base64.urlsafe_b64encode(kek)).decrypt(wrapped)
    except Exception:
        return None
