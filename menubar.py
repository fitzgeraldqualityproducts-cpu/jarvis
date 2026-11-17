"""Menu bar icon for Jarvis (needs the 'rumps' package)."""
import os
import signal
import subprocess

import rumps

import display

ICONS = {"sleeping": "◎", "listening": "◉", "thinking": "◌", "speaking": "◉", "paused": "⊘"}
LABELS = {"sleeping": "Waiting for \"Hey Jarvis\" or a double clap", "listening": "Listening…",
          "thinking": "Thinking…", "speaking": "Speaking…", "paused": "Paused (not listening)"}


def run(paused):
    app = rumps.App("Jarvis", title=ICONS["sleeping"], quit_button=None)
    status = rumps.MenuItem(LABELS["sleeping"])
    pause_item = rumps.MenuItem("Pause listening")

    def open_display(_):
        subprocess.run(["open", f"http://localhost:{display.PORT}"])

    def type_to(_):
        subprocess.run(["open", f"http://localhost:{display.PORT}/?type=1"])

    def toggle(_):
        if paused.is_set():
            paused.clear()
            pause_item.title = "Pause listening"
        else:
            paused.set()
            pause_item.title = "Resume listening"

    def quit_app(_):
        os._exit(0)

    pause_item.set_callback(toggle)
    app.menu = [status, None, rumps.MenuItem("Open display", callback=open_display),
                rumps.MenuItem("Type to Jarvis…", callback=type_to), pause_item,
                None, rumps.MenuItem("Quit Jarvis", callback=quit_app)]

    @rumps.timer(0.5)
    def refresh(_):
        s = display.STATE.get("status", "sleeping")
        app.title = ICONS.get(s, "◎")
        status.title = LABELS.get(s, s)

    signal.signal(signal.SIGINT, lambda *a: os._exit(0))
    app.run()
