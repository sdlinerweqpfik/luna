"""
Multipath-канал «Периметр»: расщепление + шифрование по разным путям.

Принципы:
- сообщение режется на N XOR-фрагментов: для восстановления нужны ВСЕ;
- каждый фрагмент шифруется СВОИМ ключом (HKDF из общего секрета)
  и идёт СВОИМ транспортом (HTTP/TCP/UDP);
- AAD привязывает фрагмент к msg_id и номеру пути — подмена фрагмента
  между путями ломает расшифровку (защита от swap-атаки);
- секрет — общий, живёт на флешке, на диск не пишется.

Честное ограничение: это all-or-nothing N-из-N, а не схема Шамира k-из-n.
Потерянный фрагмент = переотправка сообщения. Для редкого командного
канала это проще и надёжнее полиномиальной интерполяции: меньше
движущихся частей = меньше дыр. Апгрейд до Шамира — при необходимости.
"""
import base64
import secrets
import uuid

from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL = "luna-perimeter"


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


class MultiPathChannel:
    def __init__(self, shared_secret: bytes, paths: int = 3):
        if len(shared_secret) < 32:
            raise ValueError("Общий секрет должен быть минимум 32 байта")
        self.paths = paths
        self.keys = []
        for i in range(paths):
            hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                        info=f"{PROTOCOL}-path-{i}".encode())
            self.keys.append(hkdf.derive(shared_secret))

    def seal(self, message: bytes, msg_id: str = None) -> list:
        """Режет сообщение на N фрагментов и шифрует каждый ключом пути.
        
        Правильная XOR-цепочка (исправлено после аудита Gemini):
        - f0 = m ^ p0
        - f1 = p0 ^ p1
        - f2 = p1
        Восстановление: f0 ^ f1 ^ f2 = (m ^ p0) ^ (p0 ^ p1) ^ p1 = m
        """
        msg_id = msg_id or uuid.uuid4().hex[:12]
        pads = [secrets.token_bytes(len(message)) for _ in range(self.paths - 1)]
        
        # Правильная цепочка: f0 = m ^ p0, f1 = p0 ^ p1, ..., fN-1 = pN-2
        frags = [_xor(message, pads[0])]  # f0 = m ^ p0
        for i in range(len(pads) - 1):
            frags.append(_xor(pads[i], pads[i + 1]))  # f1 = p0 ^ p1, f2 = p1 ^ p2, ...
        frags.append(pads[-1])  # fN-1 = последний pad
        
        out = []
        for path, frag in enumerate(frags):
            nonce = secrets.token_bytes(12)
            aad = f"{PROTOCOL}|{msg_id}|{path}".encode()
            ct = AESGCM(self.keys[path]).encrypt(nonce, frag, aad)
            out.append({
                "msg_id": msg_id,
                "path": path,
                "data": base64.b64encode(nonce + ct).decode(),
            })
        return out

    def open(self, fragments: list) -> bytes:
        """Собирает сообщение из ВСЕХ N фрагментов.
        ValueError при нехватке, порче или подмене фрагмента."""
        by_path = {}
        msg_ids = set()
        for fr in fragments:
            by_path[int(fr["path"])] = fr
            msg_ids.add(fr["msg_id"])
        if len(msg_ids) != 1:
            raise ValueError("Фрагменты из разных сообщений")
        if len(by_path) != self.paths:
            raise ValueError(f"Не хватает фрагментов: {len(by_path)}/{self.paths}")
        msg_id = msg_ids.pop()

        plains = []
        for path in range(self.paths):
            raw = base64.b64decode(by_path[path]["data"])
            nonce, ct = raw[:12], raw[12:]
            aad = f"{PROTOCOL}|{msg_id}|{path}".encode()
            plains.append(AESGCM(self.keys[path]).decrypt(nonce, ct, aad))

        # Восстановление: XOR всех фрагментов
        msg = plains[0]
        for p in plains[1:]:
            msg = _xor(msg, p)
        return msg
