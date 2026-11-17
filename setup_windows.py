"""One-time Windows setup, run by "Install Jarvis.bat": picks your apps, downloads Jarvis's
voice, hearing and wake word, tests the mic and speaker, and can start him at sign-in."""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))
CONFIG = HERE / "config.json"

import win  # noqa: E402

MAC_ONLY = {"Safari", "Finder", "Messages", "Music", "Notes", "Calendar", "Mail", "FaceTime", "Photos"}
WINDOWS_FAVOURITES = ["Spotify", "Google Chrome", "Microsoft Edge", "Firefox", "Brave", "Opera GX Browser",
                      "File Explorer", "Discord", "Steam", "OBS Studio", "Obsidian", "Notepad", "Calculator",
                      "Settings", "Outlook", "Microsoft Teams", "Xbox", "Epic Games Launcher", "Twitch",
                      "WhatsApp", "Snipping Tool", "Photos", "Microsoft Store", "Claude"]


def step(title):
    print(f"\n== {title} ==")


def load():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def choose_apps():
    step("Apps Jarvis may open")
    cfg = load()
    apps = cfg.get("apps", {})
    if cfg.get("windows_apps_set"):
        print("  Keeping your existing choices. Double-click \"Choose Jarvis Apps\" to change them.")
        return
    installed = win.app_names()
    lower = {n.lower(): n for n in installed}
    picked = {}
    for fav in WINDOWS_FAVOURITES:
        if fav.lower() in lower:
            picked[lower[fav.lower()]] = True
    for name in ("File Explorer", "Settings"):     # always openable even if not listed
        picked.setdefault(name, True)
    # keep any non-Mac choices that came over from the Mac
    for name, on in apps.items():
        if name not in MAC_ONLY and name.lower() in lower:
            picked[lower[name.lower()]] = on
    cfg["apps"] = dict(sorted(picked.items(), key=lambda kv: kv[0].lower()))
    cfg["windows_apps_set"] = True
    save(cfg)
    print("  Jarvis can open: " + ", ".join(n for n, on in cfg["apps"].items() if on))
    print("  To change this later, double-click \"Choose Jarvis Apps\" in the Jarvis folder.")


def microphone():
    step("Microphone check")
    try:
        import numpy as np
        import sounddevice as sd
        print("  Listening for 2 seconds. Say something...")
        rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        level = float(np.abs(rec).max()) / 32768
        if level < 0.01:
            print("  I couldn't hear anything. Check that a mic is plugged in, and in Windows Settings >")
            print("  Privacy & security > Microphone, turn on \"Let desktop apps access your microphone\".")
            print("  (Opening that page now.)")
            os.startfile("ms-settings:privacy-microphone")
        else:
            print("  Mic is working.")
    except Exception as e:
        print(f"  The microphone couldn't be opened ({e}).")
        print("  In Windows Settings > Privacy & security > Microphone, turn on access for desktop apps.")
        try:
            os.startfile("ms-settings:privacy-microphone")
        except Exception:
            pass


def make_icon():
    try:
        import menubar_win
        img = menubar_win.orb("listening").resize((256, 256))
        img.save(HERE / "jarvis.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        print(f"  (couldn't make the icon: {e})")


def main():
    cfg = load()
    cfg.setdefault("personality", "hype")
    save(cfg)
    choose_apps()
    microphone()

    import setup_voice          # Kokoro voice, optional ElevenLabs key, Whisper hearing, voice test
    setup_voice.main()

    import setup_extras         # "Hey Jarvis" wake word
    setup_extras.wake_word()

    step("Start at sign-in")
    answer = input("Start Jarvis automatically when you sign in to Windows? (y/n) ").strip().lower()
    win.set_login_item(answer.startswith("y"))
    print("  Done. He'll start quietly with an icon by the clock." if answer.startswith("y")
          else "  Jarvis won't start automatically.")
    make_icon()


if __name__ == "__main__":
    main()
