"""Починка отступов после хотфикса + компиляция-проверка всех тронутых файлов."""
import re
import py_compile
from pathlib import Path

BASE = Path(__file__).resolve().parent
report = []

# ---------- remote_server.py ----------
p = BASE / "core" / "remote_server.py"
src = p.read_text(encoding="utf-8")

def remote_repl(m):
    i = m.group(1)
    i4 = i + "    "
    return (f"{i}if self.kids_mode:\n"
            f"{i4}decision, pending_action = None, None\n"
            f"{i}else:\n"
            f"{i4}decision, pending_action = confirmation_manager.match_user_input(text)")

src, n1 = re.subn(
    r'([ \t]*)if self\.kids_mode:\n[ \t]*decision, pending_action = None, None\n'
    r'[ \t]*else:\n[ \t]*decision, pending_action = confirmation_manager\.match_user_input\(text\)',
    remote_repl, src, count=1)
report.append("✅ remote: kids-guard выровнен" if n1 else "⚠️ remote: kids-guard блок не найден")

def resume_repl(m):
    d = m.group(1)
    i = d + "    "
    i2 = i + "    "
    return (f"{d}def _resume(self, out):\n"
            f"{i}if self.kids_mode or self.planner is None:\n"
            f"{i2}return\n"
            f"{i}if self.planner is not None:")

src, n2 = re.subn(
    r'([ \t]*)def _resume\(self, out\):\n[ \t]*if self\.kids_mode or self\.planner is None:\n'
    r'[ \t]*return\n[ \t]*if self\.planner is not None:',
    resume_repl, src, count=1)
report.append("✅ remote: _resume выровнен" if n2 else "⚠️ remote: _resume блок не найден")
p.write_text(src, encoding="utf-8")

# ---------- llm.py: нормализуем single-shot блок по отступу цикла for ----------
p = BASE / "core" / "llm.py"
src = p.read_text(encoding="utf-8")

def llm_repl(m):
    b = m.group(1)
    b4, b8, b12, b16 = b + "    ", b + "        ", b + "            ", b + "                "
    return (f"{b}# max_tool_hops=0: одиночная генерация БЕЗ инструментов\n"
            f"{b}# (нужно планировщику Advanced, который ждёт чистый JSON)\n"
            f"{b}if max_tool_hops <= 0:\n"
            f"{b4}with diagnostics.span(\"llm\", model=model, hop=0):\n"
            f"{b8}response = self.client.chat(\n"
            f"{b12}model=model,\n"
            f"{b12}messages=messages,\n"
            f"{b12}options={{\n"
            f"{b16}\"temperature\": 0.3,\n"
            f"{b16}\"num_predict\": 300,\n"
            f"{b16}\"num_gpu\": 12,\n"
            f"{b16}\"num_ctx\": 4096,\n"
            f"{b16}\"num_batch\": 256,\n"
            f"{b12}},\n"
            f"{b8})\n"
            f"{b4}answer = (response[\"message\"].get(\"content\") or \"\").strip()\n"
            f"{b4}return self.clean_response(answer) if answer else \"Не понял вопрос.\"\n"
            f"{b}for _hop in range(max_tool_hops):")

src, n3 = re.subn(
    r'[ \t]*# max_tool_hops=0: одиночная генерация БЕЗ инструментов\n.*?'
    r'Не понял вопрос\."\n([ \t]*)for _hop in range\(max_tool_hops\):',
    llm_repl, src, count=1, flags=re.S)
report.append("✅ llm: single-shot блок выровнен" if n3 else "⚠️ llm: single-shot блок не найден")
p.write_text(src, encoding="utf-8")

# ---------- компиляция-проверка ВСЕХ тронутых файлов ----------
for rel in ["main.py", "core/llm.py", "core/remote_server.py", "core/parental_control.py",
            "core/memory.py", "core/tools.py", "core/stt.py", "core/speaker_id.py",
            "core/kids_tools.py"]:
    try:
        py_compile.compile(str(BASE / rel), doraise=True)
        report.append(f"✅ {rel}: синтаксис OK")
    except Exception as e:
        report.append(f"❌ {rel}: {e}")

print()
print("\n".join(report))
