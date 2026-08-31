"""Remote v1: R1–R4. Запуск: python tests/test_remote.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets
from core.remote_server import RemoteServer
from core.confirmation import request_confirmation, manager as cm
from core import diagnostics

PORT = 8791


class StubLLM:
    fast = "f"
    smart = "s"
    def ask(self, model, text, **kw):
        return "ответ llm"
    def unload(self, m):
        pass


def _server():
    return RemoteServer({"token": "test123", "port": PORT}, StubLLM(), None, None)


async def _auth(ws, token):
    await ws.send(json.dumps({"type": "auth", "token": token}))
    return json.loads(await ws.recv())


# R1: неверный токен отклоняется
def test_r1_bad_token():
    srv = _server()
    async def flow():
        asyncio.get_running_loop().create_task(srv.serve_ws())
        await asyncio.sleep(0.2)
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            resp = await _auth(ws, "wrong")
            assert resp["type"] == "auth_fail"
    asyncio.run(flow())


# R2: верный токен + ответ LLM
def test_r2_ok_and_reply():
    srv = _server()
    async def flow():
        asyncio.get_running_loop().create_task(srv.serve_ws())
        await asyncio.sleep(0.2)
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            resp = await _auth(ws, "test123")
            assert resp["type"] == "auth_ok"
            await ws.send(json.dumps({"type": "text", "text": "расскажи про квантовую физику"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "reply" and reply["text"] == "ответ llm"
    asyncio.run(flow())


# R3: pending → «да» → выполнение (безопасный executor)
def test_r3_confirm_flow():
    srv = _server()
    async def flow():
        asyncio.get_running_loop().create_task(srv.serve_ws())
        await asyncio.sleep(0.2)
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            await _auth(ws, "test123")
            request_confirmation("demo", "Тестовое действие", {}, lambda p: "выполнено безопасно")
            p = cm.get_active()
            cm.claim_for_prompt(p.id)     # как в голосовом потоке
            cm.mark_prompted(p.id)        # промпт «доставлен»
            await ws.send(json.dumps({"type": "text", "text": "да"}))
            reply = json.loads(await ws.recv())
            assert reply["type"] == "reply" and "выполнено безопасно" in reply["text"]
    asyncio.run(flow())

# R4: диагностика видит мобильные запросы
def test_r4_diagnostics():
    srv = _server()
    diagnostics.init({"enabled": True})
    diagnostics.clear_records()
    async def flow():
        asyncio.get_running_loop().create_task(srv.serve_ws())
        await asyncio.sleep(0.2)
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            await _auth(ws, "test123")
            await ws.send(json.dumps({"type": "text", "text": "расскажи про квантовую физику"}))
            await ws.recv()
    asyncio.run(flow())
    evs = [r["event"] for r in diagnostics.get_records()]
    assert "request_start" in evs and "request_end" in evs


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"❌ {t.__name__}: {e}")
    print(f"\nRemote тестов пройдено: {len(tests) - failed}/{len(tests)}")
