"""
Чувства Луны на уровне ПК: активное окно/вкладка, список окон,
запущенные приложения, температура CPU. Источники событий для
проактивности и триггеров. Без внешних зависимостей (wmctrl/xdotool опциональны).

🛡️ Защита от Prompt Injection через заголовки окон (Gemini VULN-04):
закрывающие и открывающие теги <untrusted_data> заменяются на нейтральные
строки. Case-insensitive замена через re.sub с re.IGNORECASE.
Злонамеренный сайт не сможет через название вкладки "прорвать"
карантин промпта и выдать инструкции LLM за факт.
"""
import re
import subprocess
from datetime import datetime

KNOWN_APPS = {
    "firefox": "Firefox", "chrome": "Chrome", "chromium": "Chromium",
    "code": "VS Code", "telegram": "Telegram", "discord": "Discord",
    "steam": "Steam", "blender": "Blender", "cura": "Cura",
    "obs": "OBS", "spotify": "Spotify", "vlc": "VLC", "kdenlive": "Kdenlive",
}
ENTERTAINMENT_KEYS = (
    "youtube", "ютуб", "twitch", "твич", "аниме", "anime", "кино", "фильм",
    "игра", "game", "steam", "стим", "tiktok", "мем", "reddit",
)


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def _safe_window_title(title: str) -> str:
    """Защита от prompt injection через разрыв <untrusted_data> тега.
    Gemini VULN-04: case-insensitive замена через re.sub.
    """
    if not title:
        return title
    # Case-insensitive замена закрывающего тега
    title = re.sub(r'</untrusted_data>', '[закрытый_тег]', title, flags=re.IGNORECASE)
    # Case-insensitive замена открывающего тега
    title = re.sub(r'<untrusted_data>', '[открытый_тег]', title, flags=re.IGNORECASE)
    # Дополнительные защиты
    title = re.sub(r'<script', '[script', title, flags=re.IGNORECASE)
    title = re.sub(r'SYSTEM:', '[system]', title, flags=re.IGNORECASE)
    return title


def get_active_window():
    t = _run(["xdotool", "getactivewindow", "getwindowname"])
    if t:
        return _safe_window_title(t)
    titles = get_window_titles()
    return titles[0] if titles else ""


def get_window_titles():
    """Заголовки окон; у браузера в заголовке = активная вкладка."""
    out = _run(["wmctrl", "-l"])
    titles = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[3].strip():
            titles.append(_safe_window_title(parts[3].strip()))
    return titles


def get_running_apps():
    out = _run(["ps", "-eo", "comm="])
    running = set()
    for line in out.splitlines():
        name = line.strip()
        if name in KNOWN_APPS:
            running.add(KNOWN_APPS[name])
    return sorted(running)


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) // 1000
    except Exception:
        return None


def snapshot():
    now = datetime.now()
    windows = get_window_titles()
    active = get_active_window() or (windows[0] if windows else " ")
    low = active.lower()
    return {
        "time": now.strftime("%H:%M"),
        "hour": now.hour,
        "active_window": active,
        "windows": windows[:8],
        "apps": get_running_apps(),
        "cpu_temp": get_cpu_temp(),
        "entertainment": any(k in low for k in ENTERTAINMENT_KEYS),
    }
