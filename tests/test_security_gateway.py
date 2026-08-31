"""
Тесты Security Gateway v1 (G1-G17).
Запуск: python tests/test_security_gateway.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import security_gateway as gw
from core.confirmation import manager as cm, request_confirmation


def _stub(store):
    def fn(**kwargs):
        store.update(kwargs)
        store["__called__"] = store.get("__called__", 0) + 1
        return "ok"
    return fn


def test_g1_unknown_tool_denied():
    seen = {}
    res = gw.execute("unknown_tool", _stub(seen), {})
    assert res == gw.DENY_MESSAGE and "__called__" not in seen


def test_g2_safe_tool_allowed():
    seen = {}
    res = gw.execute("get_weather", _stub(seen), {"city": "Москва"})
    assert res == "ok" and seen.get("city") == "Москва"


def test_g3_shutdown_is_confirm():
    assert gw.classify("shutdown_pc") == gw.CONFIRM


def test_g4_reboot_is_confirm():
    assert gw.classify("reboot_pc") == gw.CONFIRM


def test_g5_install_is_confirm():
    assert gw.classify("install_application") == gw.CONFIRM


def test_g6_confirm_arg_cannot_bypass():
    seen = {}
    gw.execute("shutdown_pc", _stub(seen), {"confirm": True, "approved": True})
    assert "confirm" not in seen and "approved" not in seen
    assert gw.classify("shutdown_pc") == gw.CONFIRM


def test_g7_source_voice_cannot_authorize():
    seen = {}
    gw.execute("shutdown_pc", _stub(seen), {"source": "voice", "authorized": True})
    assert "source" not in seen and "authorized" not in seen


def test_g8_deny_never_calls_executor():
    seen = {}
    gw.execute("run_command", _stub(seen), {"cmd": "ls"})
    assert "__called__" not in seen


def test_g9_confirm_does_not_execute_immediately():
    ran = {"flag": False}

    def fake_dangerous():
        def executor(payload):
            ran["flag"] = True
            return "done"
        return request_confirmation("test", "Тестовое действие", {}, executor)

    gw.execute("shutdown_pc", fake_dangerous, {})
    assert ran["flag"] is False
    active = cm.get_active()
    assert active is not None
    cm.reject(active.id)


def test_g10_safe_executor_called_once():
    seen = {}
    gw.execute("get_current_time", _stub(seen), {})
    assert seen.get("__called__") == 1


def test_g11_advanced_routes_through_gateway():
    src = (Path(__file__).resolve().parent.parent / "core" / "advanced.py").read_text()
    assert "security_gateway.execute" in src
    assert "fn(**step.args)" not in src


def test_g12_shell_attempts_denied():
    for name in ["shell", "exec_code", "run_shell", "bash"]:
        assert gw.classify(name) == gw.DENY


def test_g13_path_traversal_denied():
    for bad in ["/etc", "../../etc", "/root/.ssh", "/proc/self", "/usr/bin"]:
        seen = {}
        res = gw.execute("open_folder_path", _stub(seen), {"path": bad})
        assert "__called__" not in seen, bad
        assert res != "ok"
    seen = {}
    res = gw.execute("open_folder_path", _stub(seen), {"path": "~/Documents"})
    assert res == "ok" and "__called__" in seen


def test_g14_default_deny():
    assert gw.classify("totally_new_tool") == gw.DENY
    assert gw.classify("definitely_not_a_tool") == gw.DENY


def test_g15_gateway_has_no_exec():
    """Gateway не содержит вызовов subprocess/os.system/eval/exec/shell=True.
    Имена инструментов в EXPLICIT_DENY (например 'subprocess_run') — это
    строковые литералы, а не вызовы; они допустимы."""
    import ast
    src = Path(gw.__file__).read_text()
    tree = ast.parse(src)
    forbidden_calls = {"subprocess", "os.system", "eval", "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Прямой вызов: eval(...), exec(...)
            if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                raise AssertionError(f"Запрещённый вызов: {func.id}()")
            # Атрибутный вызов: subprocess.run(...), os.system(...)
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name):
                    full = f"{func.value.id}.{func.attr}"
                    if full in {"subprocess.run", "subprocess.Popen",
                                "subprocess.call", "subprocess.check_output",
                                "os.system", "os.popen"}:
                        raise AssertionError(f"Запрещённый вызов: {full}()")
    # shell=True как keyword argument
    assert "shell=True" not in src and "shell = True" not in src

def test_g16_reviewed_pc_tools_safe():
    for name in ["get_system_status", "open_application", "cancel_pc_shutdown",
                 "show_desktop", "minimize_current_window", "maximize_current_window",
                 "close_current_window", "list_open_windows",
                 "switch_to_next_window", "focus_window"]:
        assert gw.classify(name) == gw.SAFE, name


def test_g17_install_whitelist_not_broken_by_gateway():
    seen = {}
    res = gw.execute("install_application", _stub(seen), {"app_name": "телеграм"})
    assert res == "ok" and seen.get("app_name") == "телеграм"


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
    print(f"\nGateway тестов пройдено: {len(tests) - failed}/{len(tests)}")
