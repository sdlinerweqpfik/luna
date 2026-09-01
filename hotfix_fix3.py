"""Возвращаем решётки комментарию в speaker_id.py."""
import re
import py_compile
from pathlib import Path

p = Path.home() / "secretary" / "core" / "speaker_id.py"
src = p.read_text(encoding="utf-8")
new, n = re.subn(
    r'#?резал даже легитимные совпадения\. Финальный порог 0\.7.*?по факту\.',
    '''# резал даже легитимные совпадения. Финальный порог 0.7 — консервативная
# середина между наблюдённым внутрипрофильным разбросом (до 0.58) и
# межпрофильным расстоянием (от 0.79). Если хозяин будет часто НЕ
# узнаваться — снизить до 0.65 и перепроверить через diagnose_speaker.py
# по факту.''',
    src, count=1, flags=re.S)
if n:
    p.write_text(new, encoding="utf-8")
    print("✅ speaker_id: комментарий снова комментарий")
else:
    print("❌ блок не найден")
py_compile.compile(str(p), doraise=True)
print("✅ speaker_id.py: синтаксис OK")
