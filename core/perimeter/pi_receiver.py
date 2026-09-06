"""
Приёмник Периметра — запускать на Raspberry Pi.

Три независимых транспорта: HTTP :8091, TCP :8092, UDP :8093.
Фрагменты собираются по msg_id; полный комплект -> расшифровка.
Пульса нет > 3 минут -> «Луна мертва» (пока лог, дальше — реакции).

Секрет: luna_pi_secret.bin рядом со скриптом (копируется с флешки один раз).
Зависимость: cryptography (на Пи ставь 64-битную ОС, чтобы колесо встало).

🛡️ Исправлено после аудита Gemini VULN-03: добавлен TTL (5 минут) и лимит
буфера (1000 записей) для защиты от DoS через уникальные msg_id.
"""
import json
import socketserver
import threading
import time
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

from multipath_channel import MultiPathChannel

log = logging.getLogger("perimeter.pi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SECRET_FILE = "luna_pi_secret.bin"
PULSE_TIMEOUT = 180
BUFFER_TTL = 300      # 5 минут — удаляем старые неполные сообщения
BUFFER_MAX_SIZE = 1000  # лимит записей в буфере

with open(SECRET_FILE, "rb") as f:
    channel = MultiPathChannel(f.read().strip())

_lock = threading.Lock()
_buffer = {}  # msg_id -> {"frags": {...}, "ts": float}
last_pulse = time.time()


def _cleanup_buffer():
    """Удаляет старые записи из буфера (TTL) и ограничивает размер."""
    now = time.time()
    # Удаляем записи старше BUFFER_TTL
    expired = [mid for mid, entry in _buffer.items() if now - entry["ts"] > BUFFER_TTL]
    for mid in expired:
        _buffer.pop(mid, None)
    # Если буфер всё ещё слишком большой — удаляем самые старые
    if len(_buffer) > BUFFER_MAX_SIZE:
        sorted_items = sorted(_buffer.items(), key=lambda x: x[1]["ts"])
        to_remove = sorted_items[:len(_buffer) - BUFFER_MAX_SIZE]
        for mid, _ in to_remove:
            _buffer.pop(mid, None)


def feed(fragment: dict):
    msg_id = fragment.get("msg_id")
    if not msg_id:
        return
    with _lock:
        _cleanup_buffer()
        entry = _buffer.setdefault(msg_id, {"frags": {}, "ts": time.time()})
        entry["frags"][int(fragment["path"])] = fragment
        ready = len(entry["frags"]) == channel.paths
        if ready:
            entry = _buffer.pop(msg_id)
    if not ready:
        return
    try:
        message = channel.open(list(entry["frags"].values()))
        dispatch(json.loads(message))
    except Exception as e:
        log.warning(f"Сообщение отклонено: {e}")


def dispatch(msg):
    global last_pulse
    if msg.get("type") == "heartbeat":
        last_pulse = time.time()
        log.info("💓 пульс получен")
    elif msg.get("type") == "command":
        log.warning(f"🛰 команда: {msg.get('command')} {msg.get('payload')}")
        # Будущее: GPIO-реле на сеть ПК, рестарт сервиса Луны, зуммер


class _HttpHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            feed(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
        except Exception:
            self.send_response(500)
            self.end_headers()

    def log_message(self, *a):
        pass


class _TcpHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            try:
                feed(json.loads(line))
            except Exception:
                pass


class _UdpHandler(socketserver.DatagramRequestHandler):
    def handle(self):
        try:
            feed(json.loads(self.packet))
        except Exception:
            pass


def watchdog():
    """Dead man: пульс Луны пропал -> тревога."""
    warned = False
    while True:
        time.sleep(10)
        global last_pulse
        silence = time.time() - last_pulse
        if silence > PULSE_TIMEOUT and not warned:
            log.error(f"🚨 НЕТ ПУЛЬСА {silence:.0f} сек — Луна считается мёртвой")
            warned = True
        elif silence <= PULSE_TIMEOUT:
            warned = False


def main():
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8091), _HttpHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=lambda: socketserver.TCPServer(("0.0.0.0", 8092), _TcpHandler).serve_forever(), daemon=True).start()
    threading.Thread(target=lambda: socketserver.UDPServer(("0.0.0.0", 8093), _UdpHandler).serve_forever(), daemon=True).start()
    log.info("🛰 Пи-приёмник запущен: HTTP 8091, TCP 8092, UDP 8093")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
