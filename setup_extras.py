"""One-time setup for Jarvis's extra abilities: permissions, wake word, start at login."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGIN_SCRIPT = HERE / "Jarvis at Login.command"


def step(title):
    print(f"\n== {title} ==")


def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)


def permissions():
    step("Calendar and Reminders access")
    print("macOS will ask to let Terminal see your calendars and reminders. Click Allow.")
    try:
        import organizer
        print("  Calendar: ", "allowed" if organizer.request_access("events") else "NOT allowed")
        print("  Reminders:", "allowed" if organizer.request_access("reminders") else "NOT allowed")
    except Exception as e:
        print(f"  Couldn't ask for access: {e}")

    step("Letting Jarvis control apps")
    print("You may see a few 'Terminal wants to control ...' pop-ups. Click OK on each.")
    for app, script in [("Reminders", 'tell application "Reminders" to count lists'),
                        ("Notes", 'tell application "Notes" to count notes'),
                        ("System Events", 'tell application "System Events" to count processes')]:
        r = osa(script)
        print(f"  {app}: {'ok' if r.returncode == 0 else 'not allowed (' + r.stderr.strip()[:80] + ')'}")
    if Path("/Applications/Spotify.app").exists():
        r = osa('tell application "Spotify" to player state as string')
        print(f"  Spotify: {'ok' if r.returncode == 0 else 'not allowed'}")


def wake_word():
    step("\"Hey Jarvis\" wake word")
    try:
        import openwakeword.utils
        print("  Downloading the wake word model (a few MB)...")
        openwakeword.utils.download_models()
        from openwakeword.model import Model
        Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        print("  Ready. Say \"Hey Jarvis\" to wake him.")
    except Exception as e:
        print(f"  The wake word couldn't be set up ({e}). Clapping twice still works.")


def login_item(enable):
    step("Start at login")
    name = LOGIN_SCRIPT.name
    osa(f'tell application "System Events" to delete (every login item whose path contains "{name}")')
    if not enable:
        print("  Jarvis won't start automatically.")
        return
    r = osa(f'tell application "System Events" to make login item at end with properties '
            f'{{path:"{LOGIN_SCRIPT}", hidden:true}}')
    if r.returncode == 0:
        print("  Done. Jarvis will start when you log in, with an icon in the menu bar.")
    else:
        print("  Couldn't add the login item. You can add 'Jarvis at Login.command' yourself in "
              "System Settings > General > Login Items.")


if __name__ == "__main__":
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    permissions()
    wake_word()
    answer = input("\nStart Jarvis automatically when you log in? (y/n) ").strip().lower()
    login_item(answer.startswith("y"))
    print("\nAll done. Start Jarvis with the Start Jarvis file on your Desktop.")
