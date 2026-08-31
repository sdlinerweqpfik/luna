"""
Тесты Advanced Planner v3 (A1–A16).
Моки регистрируются под РЕАЛЬНЫМИ именами из policy table
Security Gateway (SAFE/CONFIRM), иначе default DENY.
Запуск: python tests/test_advanced_v2.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.advanced as adv
from core.advanced import AdvancedPlanner, PlanState, StepStatus
from core.confirmation import manager as cm, request_confirmation

# Реальные имена с политикой SAFE в Security Gateway
SAFE_A = "get_current_time"
SAFE_B = "get_weather"
SAFE_C = "get_currency_rate"
# Реальное имя с политикой CONFIRM
DANGER = "shutdown_pc"


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.smart = "mock_smart"

    def ask(self, model, question, **kwargs):
        self.prompts.append(question)
        if self.responses:
            return self.responses.pop(0)
        return ""


def _patch_tools(tools_map, confirm_manager):
    orig_tools = adv.TOOLS_BY_NAME
    orig_cm = adv.confirmation_manager
    adv.TOOLS_BY_NAME = tools_map
    adv.confirmation_manager = confirm_manager
    return orig_tools, orig_cm


def _restore(orig):
    adv.TOOLS_BY_NAME, adv.confirmation_manager = orig


class SafeRecorder:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def make(self, **kwargs):
        self.calls.append(kwargs)
        return f"{self.name}: ok"


def _dangerous_tool(recorder):
    def tool(**kwargs):
        recorder.calls.append(kwargs)

        def executor(payload):
            recorder.calls.append(("EXECUTOR",))
            return "выполнено"

        return request_confirmation(
            "test_danger", "Тестовое опасное действие", {}, executor
        )
    return tool


def test_a1_creation_valid():
    llm = MockLLM([f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: lambda: "ok"}, cm)
    try:
        plan = p.create_plan("цель")
        assert plan.state == PlanState.READY
        assert len(plan.steps) == 1
        assert len(plan.id) >= 12
    finally:
        _restore(orig)


def test_a2_invalid_json():
    llm = MockLLM(["это не json"])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({}, cm)
    try:
        assert p.create_plan("цель").state == PlanState.FAILED
    finally:
        _restore(orig)


def test_a3_unknown_tool():
    llm = MockLLM(['{"steps": [{"tool": "no_such_tool", "args": {}}]}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: lambda: "ok"}, cm)
    try:
        assert p.create_plan("цель").state == PlanState.FAILED
    finally:
        _restore(orig)


def test_a4_too_many_steps():
    steps = ", ".join(f'{{"tool": "{SAFE_A}", "args": {{}}}}' for _ in range(11))
    llm = MockLLM([f'{{"steps": [{steps}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: lambda: "ok"}, cm)
    try:
        assert p.create_plan("цель").state == PlanState.FAILED
    finally:
        _restore(orig)


def test_a5_unique_step_ids():
    llm = MockLLM([
        f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_B}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_C}", "args": {{}}}}]}}'
    ])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: lambda: "ok", SAFE_B: lambda: "ok", SAFE_C: lambda: "ok"}, cm)
    try:
        plan = p.create_plan("цель")
        ids = [s.id for s in plan.steps]
        assert len(ids) == len(set(ids))
    finally:
        _restore(orig)


def test_a6_sequential_execution():
    order = []
    def t(name):
        def fn(**kw):
            order.append(name)
            return name
        return fn
    llm = MockLLM([
        f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_B}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_C}", "args": {{}}}}]}}'
    ])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: t("a"), SAFE_B: t("b"), SAFE_C: t("c")}, cm)
    try:
        p.create_plan("цель")
        assert p.run_plan() == "План завершён успешно."
        assert order == ["a", "b", "c"]
        assert p.active_plan.state == PlanState.COMPLETED
    finally:
        _restore(orig)


def test_a7_failure_stops_plan():
    def bad(**kw):
        raise RuntimeError("boom")
    rec = SafeRecorder("after")
    llm = MockLLM([
        f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_B}", "args": {{}}}}]}}'
    ])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: bad, SAFE_B: rec.make}, cm)
    try:
        p.create_plan("цель")
        assert "Ошибка" in p.run_plan()
        assert p.active_plan.state == PlanState.FAILED
        assert rec.calls == []
    finally:
        _restore(orig)


def test_a8_no_double_execution():
    rec = SafeRecorder("t")
    llm = MockLLM([f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: rec.make}, cm)
    try:
        p.create_plan("цель")
        p.run_plan()
        assert len(rec.calls) == 1
        assert "не готов" in p.run_plan()
        assert len(rec.calls) == 1
        assert p.active_plan.steps[0].status == StepStatus.EXECUTED
    finally:
        _restore(orig)


def test_a9_dangerous_step_waiting():
    rec = SafeRecorder("d")
    llm = MockLLM([f'{{"steps": [{{"tool": "{DANGER}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({DANGER: _dangerous_tool(rec)}, cm)
    try:
        p.create_plan("цель")
        assert "Ожидает подтверждения" in p.run_plan()
        assert p.active_plan.state == PlanState.WAITING_FOR_CONFIRMATION
        step = p.active_plan.steps[0]
        assert step.status == StepStatus.WAITING_CONFIRMATION
        assert ("EXECUTOR",) not in rec.calls
        active = cm.get_active()
        assert active is not None
        cm.reject(active.id)
    finally:
        _restore(orig)


def test_a10_resume_after_confirm():
    rec = SafeRecorder("d")
    after = SafeRecorder("after")
    llm = MockLLM([
        f'{{"steps": [{{"tool": "{DANGER}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_B}", "args": {{}}}}]}}'
    ])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({DANGER: _dangerous_tool(rec), SAFE_B: after.make}, cm)
    try:
        p.create_plan("цель")
        p.run_plan()
        active = cm.get_active()
        # Реальный lifecycle: промпт доставлен ДО подтверждения (инвариант X1)
        cm.claim_for_prompt(active.id)
        cm.mark_prompted(active.id)
        ok, _ = cm.confirm(active.id, source="voice")
        assert ok
        assert p.resume_if_waiting() == "План завершён успешно."
        assert ("EXECUTOR",) in rec.calls
        assert len(after.calls) == 1
        assert p.active_plan.state == PlanState.COMPLETED
    finally:
        _restore(orig)

def test_a11_reject_cancelled():
    rec = SafeRecorder("d")
    llm = MockLLM([f'{{"steps": [{{"tool": "{DANGER}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({DANGER: _dangerous_tool(rec)}, cm)
    try:
        p.create_plan("цель")
        p.run_plan()
        cm.reject(cm.get_active().id)
        assert "отменён" in p.resume_if_waiting()
        assert p.active_plan.state == PlanState.CANCELLED
    finally:
        _restore(orig)


def test_a12_cancel_active_plan():
    rec = SafeRecorder("t")
    llm = MockLLM([f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: rec.make}, cm)
    try:
        p.create_plan("цель")
        assert "отменён" in p.cancel_plan()
        assert p.active_plan.state == PlanState.CANCELLED
        assert "не готов" in p.run_plan()
        assert rec.calls == []
    finally:
        _restore(orig)


def test_a13_cancelled_prevents_next_step():
    rec_d = SafeRecorder("d")
    rec_s = SafeRecorder("safe")
    llm = MockLLM([
        f'{{"steps": [{{"tool": "{DANGER}", "args": {{}}}}, '
        f'{{"tool": "{SAFE_B}", "args": {{}}}}]}}'
    ])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({DANGER: _dangerous_tool(rec_d), SAFE_B: rec_s.make}, cm)
    try:
        p.create_plan("цель")
        p.run_plan()
        assert "отменён" in p.cancel_plan()
        assert p.active_plan.state == PlanState.CANCELLED
        assert p.resume_if_waiting() == ""
        assert rec_s.calls == []
    finally:
        _restore(orig)


def test_a14_context_manager_integration():
    import core.memory as mem_module
    tmp = Path(tempfile.mkdtemp(prefix="adv_ctx_")) / "memory.json"
    orig_file = mem_module.MEMORY_FILE
    mem_module.MEMORY_FILE = tmp
    try:
        from core.memory import Memory
        m = Memory(speaker_id="advtest")
        m.add_fact("Люблю Minecraft", category="preference", importance=4)
        llm = MockLLM([f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}]}}'])
        p = AdvancedPlanner(llm, memory=m)
        orig = _patch_tools({SAFE_A: lambda: "ok"}, cm)
        try:
            p.create_plan("расскажи про мои игры")
            assert any("Minecraft" in pr for pr in llm.prompts)
        finally:
            _restore(orig)
    finally:
        mem_module.MEMORY_FILE = orig_file


def test_a15_gateway_not_bypassed():
    src = Path(adv.__file__).read_text()
    assert "security_gateway.execute" in src
    assert "fn(**step.args)" not in src
    rec = SafeRecorder("evil")
    llm = MockLLM(['{"steps": [{"tool": "evil", "args": {}}]}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({"evil": rec.make}, cm)
    try:
        p.create_plan("цель")
        assert "Ошибка" in p.run_plan()
        assert rec.calls == []
        assert p.active_plan.state == PlanState.FAILED
    finally:
        _restore(orig)


def test_a16_get_status_safe():
    rec = SafeRecorder("t")
    llm = MockLLM([f'{{"steps": [{{"tool": "{SAFE_A}", "args": {{}}}}]}}'])
    p = AdvancedPlanner(llm)
    orig = _patch_tools({SAFE_A: rec.make}, cm)
    try:
        p.create_plan("цель")
        p.run_plan()
        status = p.get_status()
        assert "executor" not in str(status)
        assert status["plan"]["state"] == "completed"
        assert status["plan"]["steps"][0]["pending_id"] is None
    finally:
        _restore(orig)


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
    print(f"\nAdvanced v3 тестов пройдено: {len(tests) - failed}/{len(tests)}")
