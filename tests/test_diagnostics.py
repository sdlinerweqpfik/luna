"""
Тесты Diagnostics v1 (D1–D14). D15 — прогон остальных наборов отдельно.
Запуск: python tests/test_diagnostics.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import diagnostics as diag

diag.init({"enabled": True, "log_content": False})


def _events(names=None):
    recs = diag.get_records()
    if names is None:
        return recs
    return [r for r in recs if r["event"] in names]


# D1: уникальный request_id
def test_d1_unique_request_id():
    diag.clear_records()
    rid = diag.start_request("voice", "тест")
    assert rid != diag.NO_REQUEST and len(rid) >= 8
    diag.end_request()


# D2: события одного запроса с одним request_id
def test_d2_same_request_id():
    diag.clear_records()
    rid = diag.start_request("text", "привет")
    diag.log_event("custom_a")
    diag.log_event("custom_b")
    diag.end_request()
    recs = _events({"custom_a", "custom_b"})
    assert len(recs) == 2 and all(r["request_id"] == rid for r in recs)


# D3: разные запросы — разные id
def test_d3_different_requests():
    r1 = diag.start_request("text")
    diag.end_request()
    r2 = diag.start_request("text")
    diag.end_request()
    assert r1 != r2


# D4: tool call виден в цепочке
def test_d4_tool_in_chain():
    from core import security_gateway
    diag.clear_records()
    rid = diag.start_request("text", "погода")
    with diag.span("tool", tool="get_weather"):
        security_gateway.execute("get_weather", lambda: "ok", {})
    diag.end_request()
    tools = _events({"tool"})
    assert any(t["tool"] == "get_weather" and t["request_id"] == rid for t in tools)


# D5: Gateway ALLOW виден (через мост, без изменения gateway)
def test_d5_gateway_allow_visible():
    from core import security_gateway
    diag.clear_records()
    diag.start_request("text")
    security_gateway.execute("get_current_time", lambda: "ok", {})
    assert _events({"gateway_allow"})
    diag.end_request()


# D6: Gateway DENY виден с причиной
def test_d6_gateway_deny_visible():
    from core import security_gateway
    diag.clear_records()
    diag.start_request("text")
    security_gateway.execute("unknown_tool", lambda: "x", {})
    denies = _events({"gateway_deny"})
    assert denies and denies[0]["tool"] == "unknown_tool"
    diag.end_request()


# D7: Confirmation lifecycle полностью виден
def test_d7_confirmation_lifecycle():
    from core.confirmation import request_confirmation, manager as cm
    diag.clear_records()
    diag.start_request("voice", "выключи пк")
    try:
        request_confirmation("t", "Тест", {}, lambda p: "done")
        pending = cm.get_active()          # pending достаём из менеджера
        assert pending is not None
        diag.log_event("confirmation", stage="pending", pending=pending.id)
        cm.claim_for_prompt(pending.id)
        diag.log_event("confirmation", stage="prompting", pending=pending.id)
        cm.mark_prompted(pending.id)
        diag.log_event("confirmation", stage="prompted", pending=pending.id)
        ok, _ = cm.confirm(pending.id, source="voice")
        assert ok
        diag.log_event("confirmation", stage="executed", pending=pending.id)
        stages = [r["stage"] for r in _events({"confirmation"})]
        assert stages == ["pending", "prompting", "prompted", "executed"]
    finally:
        diag.end_request()
        active = cm.get_active()
        if active is not None:
            cm.reject(active.id)

# D8: Advanced step transitions видны
def test_d8_advanced_transitions():
    import core.advanced as adv
    from core.advanced import AdvancedPlanner
    from core.confirmation import manager as cm

    class MockLLM:
        smart = "m"
        def __init__(self, resp): self.resp = resp
        def ask(self, *a, **k): return self.resp

    diag.clear_records()
    diag.start_request("text", "сделай шаги")
    llm = MockLLM('{"steps": [{"tool": "get_current_time", "args": {}}]}')
    p = AdvancedPlanner(llm)
    orig_tools, orig_cm = adv.TOOLS_BY_NAME, adv.confirmation_manager
    adv.TOOLS_BY_NAME = {"get_current_time": lambda: "ok"}
    adv.confirmation_manager = cm
    try:
        p.create_plan("цель")
        p.run_plan()
    finally:
        adv.TOOLS_BY_NAME, adv.confirmation_manager = orig_tools, orig_cm
    diag.end_request()
    evs = [r["event"] for r in diag.get_records()]
    assert "plan_created" in evs and "step_start" in evs and "step_done" in evs

# D9: Memory операция видна без утечки содержимого
def test_d9_memory_no_leak():
    import core.memory as mem_module
    tmp = Path(tempfile.mkdtemp(prefix="diag_mem_")) / "memory.json"
    orig = mem_module.MEMORY_FILE
    mem_module.MEMORY_FILE = tmp
    try:
        from core.memory import Memory
        m = Memory(speaker_id="dtest")
        diag.clear_records()
        diag.start_request("text")
        m.add_fact("СекретныйФакт_XYZ_12345", importance=5)
        m.recall("СекретныйФакт")
        diag.end_request()
        recs = _events({"memory"})
        assert recs and any(r.get("op") == "remember" for r in recs)
        blob = str(recs)
        assert "СекретныйФакт_XYZ_12345" not in blob
    finally:
        mem_module.MEMORY_FILE = orig


# D10: TTS/STT duration логируется
def test_d10_tts_stt_duration():
    diag.clear_records()
    diag.start_request("voice")
    with diag.span("tts") as sp:
        sp.update(delivered=True)
    with diag.span("stt") as sp:
        sp.update(ok=True)
    diag.end_request()
    for ev in ("tts", "stt"):
        recs = _events({ev})
        assert recs and "duration_ms" in recs[0]


# D11: выключенная диагностика не ломает выполнение
def test_d11_disabled_no_break():
    from core import security_gateway
    diag.init({"enabled": False})
    diag.clear_records()
    res = security_gateway.execute("get_current_time", lambda: "ok", {})
    assert res == "ok"
    assert diag.get_records() == []
    assert diag.start_request("text") == diag.NO_REQUEST
    diag.init({"enabled": True})


# D12: ошибка инструмента записывается
def test_d12_tool_error_recorded():
    diag.clear_records()
    diag.start_request("text")
    def bad(): raise RuntimeError("boom")
    try:
        with diag.span("tool", tool="bad"):
            bad()
    except RuntimeError:
        pass
    diag.end_request()
    recs = _events({"tool"})
    assert recs and recs[0]["success"] is False and recs[0]["error"] == "RuntimeError"


# D13: завершение запроса даже при exception
def test_d13_end_on_exception():
    diag.clear_records()
    diag.start_request("text", "падение")
    try:
        raise ValueError("oops")
    except Exception as e:
        diag.end_request(error=e)
    recs = _events({"request_end"})
    assert recs and "oops" in recs[0]["error"]


# D14: sensitive data не попадает в diagnostics
def test_d14_sensitive_scrubbed():
    diag.clear_records()
    diag.start_request("voice", "мой пароль hunter2")
    diag.log_event("custom", password="hunter2", token="abc", note="ok")
    diag.end_request()
    blob = str(diag.get_records())
    assert "hunter2" not in blob
    assert "***" in blob
    assert "text_len" in _events({"request_start"})[0]


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
    print(f"\nDiagnostics тестов пройдено: {len(tests) - failed}/{len(tests)}")
