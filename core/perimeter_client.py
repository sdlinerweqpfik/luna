"""
Клиент Периметра — сторона ПК Луны.

- секрет читается с флешки (как у Занавеса: ключ вне машины);
- пульс каждые 30 сек тремя транспортами («единый пульс» для Пи);
- команды (safe_mode и т.п.) уходят Пи при аномалиях;
- без флешки/конфига — молча не активируется (домашний режим без Пи).
"""
import json
import os
import secrets
import socket
import threading
import time
import logging
import urllib.request
from pathlib import Path

from core.multipath_channel import MultiPathChannel
from core.veil import USB_MOUNTS

log = logging.getLogger("secretary.perimeter")

SECRET_NAME = "luna_pi_secret.bin"
HEARTBEAT_INTERVAL = 30


def find_pi_secret():
    for mount in USB_MOUNTS:
        if not mount.exists():
            continue
        for c in mount.rglob(SECRET_NAME):
            return c
    return None


def pair():
    """ОДИН РАЗ: создаёт секрет Периметра на флешке."""
    for mount in USB_MOUNTS:
        if not mount.exists():
            continue
        for d in mount.iterdir():
            if d.is_dir():
                p = d / SECRET_NAME
                p.write_bytes(secrets.token_bytes(32))
                os.chmod(p, 0o600)
                return f"Секрет записан: {p}"
    return "Флешка не найдена"


class PerimeterClient:
    def __init__(self, config_path="~/Luna/perimeter_config.json"):
        self.channel = None
        self.pi_ip = None
        self.enabled = False
        self._last_error_log = 0.0

        cfg_path = Path(os.path.expanduser(config_path))
        if not cfg_path.exists():
            return
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            return
        self.pi_ip = cfg.get("pi_ip")
        if not self.pi_ip or not cfg.get("enabled", False):
            return

        secret_path = find_pi_secret()
        if secret_path is None:
            log.info("Периметр: секрет не найден на флешке — канал не активирован")
            return
        self.channel = MultiPathChannel(secret_path.read_bytes().strip())
        self.enabled = True
        log.info(f"Периметр активирован: цель {self.pi_ip}")

    def start(self):
        if not self.enabled:
            return
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def send_command(self, command: str, payload: dict = None):
        if not self.enabled:
            return
        try:
            self._send_sealed({"type": "command", "command": command,
                               "payload": payload or {}, "ts": time.time()})
        except Exception as e:
            self._quiet_error(e)

    # ---------- внутреннее ----------

    def _heartbeat_loop(self):
        while True:
            try:
                self._send_sealed({"type": "heartbeat", "ts": time.time()})
            except Exception as e:
                self._quiet_error(e)
            time.sleep(HEARTBEAT_INTERVAL)

    def _send_sealed(self, obj: dict):
        fragments = self.channel.seal(json.dumps(obj).encode())
        self._send_http(f"http://{self.pi_ip}:8091/fragment", fragments[0])
        self._send_tcp(self.pi_ip, 8092, fragments[1])
        self._send_udp(self.pi_ip, 8093, fragments[2])

    def _send_http(self, url, fragment):
        req = urllib.request.Request(
            url, data=json.dumps(fragment).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()

    def _send_tcp(self, host, port, fragment):
        with socket.create_connection((host, port), timeout=2) as s:
            s.sendall((json.dumps(fragment) + "\n").encode())

    def _send_udp(self, host, port, fragment):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(json.dumps(fragment).encode(), (host, port))

    def _quiet_error(self, e):
        # Пи может быть выключен — не спамим, раз в 5 минут
        if time.time() - self._last_error_log > 300:
            log.warning(f"Периметр: {type(e).__name__}: {str(e)[:100]}")
            self._last_error_log = time.time()
