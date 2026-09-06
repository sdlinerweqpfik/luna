"""
Remote v3 — агентский режим с видимыми шагами.
WebSocket (:8765) + раздача web-чата (:8080). Без TLS (демо в домашней сети).
v2: фоновый watcher шлёт {"type":"pending"} ВСЕМ подключённым клиентам.
v3: {"type":"step", "name": "...", "result": "..."} — агентские шаги в реальном времени.
v4: {"type":"thinking"} — размышление модели отдельным сообщением до ответа.
v5: {"type":"action", ...} — действия для телефона (phone_timer, open_phone_app).
"""
import asyncio
import hmac
import json
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import websockets
from core.routing import is_complex_fast
from core import diagnostics
from core import security_gateway
from core.commands import fast_command
from core.confirmation import manager as confirmation_manager
from core.tools import TOOLS_BY_NAME, drain_phone_actions   # ← НОВЫЙ ИМПОРТ

log = logging.getLogger("secretary.remote")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
CANCEL_PHRASES = ("отмена выключения", "отмени выключение", "отмени перезагрузку")


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
        self._clients = set()
        self._current_loop = None

        def on_step_callback(tool_name, result):
            if self._current_loop is not None and self._clients:
                msg = {
                    "type": "step",
                    "name": tool_name,
                    "result": result[:300]
                }
                asyncio.run_coroutine_threadsafe(
                    self._broadcast(msg), self._current_loop
                )

        if kids_mode:
            from core.llm import KidsLLM
            self.llm = KidsLLM(
                cfg={"fast": "qwen2.5:3b", "smart": "qwen2.5:3b"},
                memory=memory,
                personality=personality,
                on_step=on_step_callback
            )
            log.info("👶 Запущен Kids-сервер (ограниченный доступ)")
        else:
            self.llm = llm
            self.llm.on_step = on_step_callback
            log.info("🌙 Запущен основной сервер")
        self.memory = memory
        self.personality = personality
        self.planner = planner

    def process_text(self, text: str, source: str = "web") -> list:
        out = []
        diagnostics.start_request(source, text)
        text_lower = text.lower().strip()

        if any(ph in text_lower for ph in CANCEL_PHRASES):
            fn = TOOLS_BY_NAME.get("cancel_pc_shutdown")
            result = security_gateway.execute("cancel_pc_shutdown", fn, {})
            out.append({"type": "reply", "text": result})
            diagnostics.end_request()
            return out

        if self.kids_mode:
            dangerous_keywords = [
                "выключи", "перезагрузи", "удали", "открой", "установи",
                "запусти", "скачай", "браузер", "проводник", "папку",
            ]
            if any(kw in text_lower for kw in dangerous_keywords):
                diagnostics.end_request()
                return [{
                    "type": "reply",
                    "text": "👶 Я не умею управлять компьютером. Я могу рассказать анекдот, сказать время, помочь с математикой или рассказать интересное!"
                }]

        try:
            if not self.kids_mode:
                decision, pending_action = confirmation_manager.match_user_input(text)
                if decision == "confirm":
                    _ok, result = confirmation_manager.confirm(pending_action.id, source="user")
                    diagnostics.log_event("confirmation", stage="confirm", source=source)
                    out.append({"type": "reply", "text": result})
                    self._resume(out)
                    return out
                if decision == "reject":
                    result = confirmation_manager.reject(pending_action.id)
                    diagnostics.log_event("confirmation", stage="reject", source=source)
                    out.append({"type": "reply", "text": result})
                    self._resume(out)
                    return out
                if decision == "ambiguous":
                    diagnostics.log_event("confirmation", stage="ambiguous", source=source)
                    out.append({"type": "reply", "text": "Скажи просто: да или нет."})
                    return out

            is_fast, fast_answer = fast_command(
                text, llm=self.llm, memory=self.memory, personality=self.personality
            )
            if is_fast:
                out.append({"type": "reply", "text": fast_answer})
                if self.kids_mode:
                    from core.parental_control import log_kids_request
                    log_kids_request(text, fast_answer, source=source)
                diagnostics.end_request()
                return out

            model = self.llm.smart if is_complex_fast(text) else self.llm.fast
            # ← НОВОЕ: передаём source, чтобы LLM знала, что запрос с телефона
            answer = self.llm.ask(model, text, source=source)
            if model == self.llm.smart:
                self.llm.unload(model)

            thinking = getattr(self.llm, "last_thinking", None)

            if self.kids_mode:
                from core.parental_control import log_kids_request, check_suspicious, notify_parent
                is_suspicious = check_suspicious(text)
                log_kids_request(text, answer, source=source, suspicious=is_suspicious)
                if is_suspicious:
                    notify_parent(text, answer, reason="подозрительный запрос")

            if thinking:
                out.append({"type": "thinking", "text": thinking[:800]})

            pending = confirmation_manager.get_active()
            if pending is not None and pending.status == "pending":
                confirmation_manager.claim_for_prompt(pending.id)
                confirmation_manager.mark_prompted(pending.id)
                diagnostics.log_event("confirmation", stage="prompted",
                                      pending=pending.id, via=source)
                out.append({"type": "reply", "text": answer})
                out.append({"type": "pending", "id": pending.id, "summary": pending.summary})
            else:
                out.append({"type": "reply", "text": answer})

            # ← НОВОЕ: сливаем накопленные phone actions в ответ клиенту
            for a in drain_phone_actions():
                out.append({"type": "action", **a})

            return out
        finally:
            diagnostics.end_request()

    def _resume(self, out):
        if self.kids_mode or self.planner is None:
            return
        r = self.planner.resume_if_waiting()
        if r:
            diagnostics.log_event("advanced", event="resume", via="mobile")
            out.append({"type": "reply", "text": r})

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
            self._clients.add(ws)
            pending = confirmation_manager.get_active()
            if pending is not None and pending.status in ("pending", "prompting", "prompted"):
                await ws.send(json.dumps(
                    {"type": "pending", "id": pending.id, "summary": pending.summary},
                    ensure_ascii=False))

            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "text":
                    continue
                text = str(msg.get("text", ""))[:500]
                loop = asyncio.get_running_loop()
                try:
                    # ← НОВОЕ: помечаем источник как mobile
                    messages = await loop.run_in_executor(
                        None, self.process_text, text, "mobile"
                    )
                except Exception as e:
                    log.error(f"remote process error: {e}")
                    messages = [{"type": "reply", "text": f"⚠ Ошибка: {e}"}]
                for m in messages:
                    await ws.send(json.dumps(m, ensure_ascii=False))
        except (websockets.ConnectionClosed, asyncio.TimeoutError):
            pass
        except Exception as e:
            log.error(f"remote ws error: {e}")
        finally:
            self._clients.discard(ws)

    async def _broadcast(self, msg):
        for ws in list(self._clients):
            try:
                await ws.send(json.dumps(msg, ensure_ascii=False))
            except Exception:
                self._clients.discard(ws)

    async def _pending_watcher(self):
        last_id = None
        while True:
            await asyncio.sleep(0.5)
            pending = confirmation_manager.get_active()
            pid = pending.id if (pending is not None
                                 and pending.status in ("pending", "prompting", "prompted")) else None
            if pid != last_id:
                if pid is not None:
                    await self._broadcast({"type": "pending", "id": pid, "summary": pending.summary})
                else:
                    await self._broadcast({"type": "pending_cleared"})
                last_id = pid

    async def serve_ws(self):
        self._current_loop = asyncio.get_running_loop()
        asyncio.get_running_loop().create_task(self._pending_watcher())
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

        if not self.kids_mode:
            from core.proactive import ProactiveLoop
            from core import tools as _tools
            def deliver(msg):
                try:
                    if _tools.TTS_INSTANCE is not None:
                        _tools.TTS_INSTANCE.speak(msg)
                except Exception as e:
                    log.error(f"proactive tts error: {e}")
                if getattr(self, "_current_loop", None) is not None and self._clients:
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast({"type": "proactive", "text": msg}),
                        self._current_loop,
                    )
            ProactiveLoop(self.llm, deliver).start()
            log.info("✨ Проактивность запущена")
