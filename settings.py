"""Pick which apps Jarvis is allowed to open.

Run:  python3 settings.py
Shows every app installed on this computer with a checkbox. Type a number to
toggle it, 'a' to allow all, 'n' to allow none, 'q' to save and quit.
"""
import json
import os
import sys
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
APP_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
            os.path.expanduser("~/Applications")]


def load():
    return json.loads(CONFIG.read_text())


def save(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2))


def installed_apps():
    if sys.platform == "win32":
        import win
        return win.app_names()
    names = set()
    for d in APP_DIRS:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".app"):
                    names.add(f[:-4])
    return sorted(names, key=str.lower)


def main():
    cfg = load()
    apps = cfg.setdefault("apps", {})
    names = sorted(set(installed_apps()) | set(apps), key=str.lower)
    while True:
        print("\nApps Jarvis may open (number = toggle, a = all, n = none, q = save & quit)\n")
        for i, name in enumerate(names, 1):
            mark = "x" if apps.get(name) else " "
            print(f"  {i:3}. [{mark}] {name}")
        choice = input("\n> ").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            apps.update({n: True for n in names})
        elif choice == "n":
            apps.update({n: False for n in names})
        else:
            for part in choice.replace(",", " ").split():
                if part.isdigit() and 1 <= int(part) <= len(names):
                    n = names[int(part) - 1]
                    apps[n] = not apps.get(n, False)
    save(cfg)
    on = [n for n in names if apps.get(n)]
    print(f"Saved. Jarvis can open: {', '.join(on) or 'nothing'}")


if __name__ == "__main__":
    main()
