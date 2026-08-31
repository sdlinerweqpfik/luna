"""
Remote v1 — текстовая демка Луны в локальной сети.
WebSocket (:8765) + раздача web-чата (:8080). Без TLS (демо в домашней сети).
Поддерживает те же команды, что и голос: быстрые команды, LLM, да/нет для подтверждений.
Поддерживает kids_mode с ограниченным набором инструментов и логированием.
"""
import asyncio
import hmac
import json
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import websockets

from core import diagnostics
from core.commands import fast_command
from core.confirmation import manager as confirmation_manager

log = logging.getLogger("secretary.remote")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def is_complex_fast(text):
    text_lower = text.lower()
    complex_keywords = [
        "объясни", "расскажи подробно", "почему", "зачем",
        "сравни", "проанализируй", "напиши", "сочини",
        "придумай", "переведи",
    ]
    if any(kw in text_lower for kw in complex_keywords):
        return True
    return len(text_lower.split()) > 15


class _ChatHandler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB_DIR), **kw)

    def log_message(self, *a):
        pass


class RemoteServer:
    def __init__(self, cfg_remote, llm, memory, personality, planner=None, kids_mode=False):
        self.token = str(cfg_remote.get("token", ""))
        self.host = cfg_remote.get("host", "0.0.0.0")
        self.port = int(cfg_remote.get("port", 8765))
        self.http_port = int(cfg_remote.get("http_port", 8080))
        self.kids_mode = kids_mode
        
        if kids_mode:
            from core.llm import KidsLLM
            self.llm = KidsLLM(cfg={"fast": "qwen2.5:3b", "smart": "qwen2.5:3b"}, memory=memory, personality=personality)
            log.info("👶 Запущен Kids-сервер (ограниченный доступ)")
        else:
            self.llm = llm
            log.info("🌙 Запущен основной сервер")
        
        self.memory = memory
        self.personality = personality
        self.planner = planner

    # ---------- обработка текста: зеркало conversation_mode, без аудио ----------
    def process_text(self, text: str) -> list:
        out = []
        diagnostics.start_request("mobile", text)
        text_lower = text.lower().strip()
        
        # === ЖЁСТКАЯ ПРОГРАММНАЯ ЗАЩИТА ДЛЯ ДЕТЕЙ ===
        if self.kids_mode:
            dangerous_keywords = [
                "выключи", "перезагрузи", "удали", "открой", "установи", 
                "запусти", "скачай", "браузер", "проводник", "папку", "папку"
            ]
            if any(kw in text_lower for kw in dangerous_keywords):
                diagnostics.end_request()
                return [{
                    "type": "reply", 
                    "text": "👶 Я не умею управлять компьютером. Я могу рассказать анекдот, сказать время, помочь с математикой или перевести текст!"
                }]

        try:
            decision, pending_action = confirmation_manager.match_user_input(text)

            if decision == "confirm":
                _ok, result = confirmation_manager.confirm(pending_action.id, source="user")
                diagnostics.log_event("confirmation", stage="confirm", source="mobile")
                out.append({"type": "reply", "text": result})
                self._resume(out)
                return out

            if decision == "reject":
                result = confirmation_manager.reject(pending_action.id)
                diagnostics.log_event("confirmation", stage="reject", source="mobile")
                out.append({"type": "reply", "text": result})
                self._resume(out)
                return out

            if decision == "ambiguous":
                diagnostics.log_event("confirmation", stage="ambiguous", source="mobile")
                out.append({"type": "reply", "text": "Скажи просто: да или нет."})
                return out

            is_fast, fast_answer = fast_command(
                text, llm=self.llm, memory=self.memory, personality=self.personality
            )
            if is_fast:
                out.append({"type": "reply", "text": fast_answer})
                if self.kids_mode:
                    from core.parental_control import log_kids_request
                    log_kids_request(text, fast_answer, source="mobile")
                diagnostics.end_request()
                return out

            model = self.llm.smart if is_complex_fast(text) else self.llm.fast
            answer = self.llm.ask(model, text)
            if model == self.llm.smart:
                self.llm.unload(model)

            # Логирование для kids_mode
            if self.kids_mode:
                from core.parental_control import log_kids_request, check_suspicious, notify_parent
                is_suspicious = check_suspicious(text)
                log_kids_request(text, answer, source="mobile", suspicious=is_suspicious)
                if is_suspicious:
                    notify_parent(text, answer, reason="подозрительный запрос")

            pending = confirmation_manager.get_active()
            if pending is not None and pending.status == "pending":
                confirmation_manager.claim_for_prompt(pending.id)
                confirmation_manager.mark_prompted(pending.id)
                diagnostics.log_event("confirmation", stage="prompted", pending=pending.id, via="mobile")
                out.append({"type": "reply", "text": answer})
                out.append({"type": "pending", "id": pending.id, "summary": pending.summary})
            else:
                out.append({"type": "reply", "text": answer})
            return out
        finally:
            diagnostics.end_request()

    def _resume(self, out):
        if self.planner is not None:
            r = self.planner.resume_if_waiting()
            if r:
                diagnostics.log_event("advanced", event="resume", via="mobile")
                out.append({"type": "reply", "text": r})

    # ---------- WebSocket ----------
    async def _ws_handler(self, ws):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if (msg.get("type") != "auth"
                    or not hmac.compare_digest(str(msg.get("token", "")), self.token)):
                await ws.send(json.dumps({"type": "auth_fail"}))
                await ws.close()
                return
            await ws.send(json.dumps({"type": "auth_ok"}))

            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "text":
                    continue
                text = str(msg.get("text", ""))[:500]
                loop = asyncio.get_running_loop()
                try:
                    messages = await loop.run_in_executor(None, self.process_text, text)
                except Exception as e:
                    log.error(f"remote process error: {e}")
                    messages = [{"type": "reply", "text": f"⚠ Ошибка: {e}"}]
                for m in messages:
                    await ws.send(json.dumps(m, ensure_ascii=False))
        except (websockets.ConnectionClosed, asyncio.TimeoutError):
            pass
        except Exception as e:
            log.error(f"remote ws error: {e}")

    async def serve_ws(self):
        async with websockets.serve(self._ws_handler, self.host, self.port):
            await asyncio.Future()

    def start(self):
        def run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.serve_ws())

        threading.Thread(target=run_ws, daemon=True).start()
        http = HTTPServer((self.host, self.http_port), _ChatHandler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        log.info(f"Remote demo: чат http://<ip-пк>:{self.http_port}, ws :{self.port}")
