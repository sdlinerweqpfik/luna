import re
import py_compile
from pathlib import Path

p = Path.home() / "secretary" / "main.py"
src = p.read_text(encoding="utf-8")

def repl(m):
    i = m.group(1)
    i4 = i + "    "
    return (i + "# === ЯВНАЯ ОТМЕНА выключения — ДО да/нет матчера ===\n"
            + i + "if any(ph in text.lower() for ph in (\"отмена выключения\", \"отмени выключение\", \"отмени перезагрузку\")):\n"
            + i4 + "from core.tools import TOOLS_BY_NAME\n"
            + i4 + "from core import security_gateway\n"
            + i4 + "fn = TOOLS_BY_NAME.get(\"cancel_pc_shutdown\")\n"
            + i4 + "result = security_gateway.execute(\"cancel_pc_shutdown\", fn, {})\n"
            + i4 + "print(f\"   → {result}\")\n"
            + i4 + "tts.speak_interruptible(result)\n"
            + i4 + "last_interaction = time.time()\n"
            + i4 + "continue\n"
            + m.group(0))

new, n = re.subn(r'([ \t]*)# === ПОДТВЕРЖДЕНИЕ ОПАСНОГО ДЕЙСТВИЯ ===', repl, src, count=1)
if n:
    p.write_text(new, encoding="utf-8")
    print("✅ main.py: явная отмена до матчера")
else:
    print("❌ якорь не найден")
py_compile.compile(str(p), doraise=True)
print("✅ main.py: синтаксис OK")
