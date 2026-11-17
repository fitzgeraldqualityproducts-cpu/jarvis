"""Hands: opens apps, but only the ones switched on in config.json."""
import difflib
import json
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")


def allowed_apps():
    cfg = json.loads(CONFIG.read_text())
    return [name for name, on in cfg.get("apps", {}).items() if on]


def find_app(spoken):
    """Match a spoken name ("spotify", "the notes app") to an allowed app."""
    spoken = spoken.lower().replace("the ", "").replace(" app", "").strip(" .?!")
    apps = allowed_apps()
    lower = {a.lower(): a for a in apps}
    if spoken in lower:
        return lower[spoken]
    for low, real in lower.items():
        if low in spoken or spoken in low:
            return real
    match = difflib.get_close_matches(spoken, list(lower), n=1, cutoff=0.6)
    return lower[match[0]] if match else None


def open_app(spoken):
    """Returns (opened_name or None, message)."""
    app = find_app(spoken)
    if not app:
        return None, f"I'm afraid {spoken} isn't on my list of apps I'm allowed to open."
    if sys.platform == "win32":
        import win
        ok = win.open_app(app)
    else:
        ok = subprocess.run(["open", "-a", app], capture_output=True, text=True).returncode == 0
    if not ok:
        return None, f"I couldn't open {app}. It may not be installed."
    return app, f"Opening {app}."
