"""Jarvis's icon in the Windows taskbar tray (next to the clock). Needs 'pystray' and 'Pillow'.
Click it to open the display; right-click for pause and quit."""
import ctypes
import os
import threading
import time
import webbrowser

import pystray
from PIL import Image, ImageDraw

import display

COLORS = {"sleeping": (40, 120, 160), "listening": (0, 220, 255), "thinking": (255, 190, 40),
          "speaking": (0, 255, 180), "paused": (120, 120, 120)}
LABELS = {"sleeping": "Waiting for \"Hey Jarvis\" or a double clap", "listening": "Listening…",
          "thinking": "Thinking…", "speaking": "Speaking…", "paused": "Paused (not listening)"}
_images = {}


def orb(status):
    """A glowing ring, coloured by what Jarvis is doing."""
    if status not in _images:
        r, g, b = COLORS.get(status, COLORS["sleeping"])
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(r, g, b, 70))
        d.ellipse((10, 10, 54, 54), outline=(r, g, b, 255), width=6)
        d.ellipse((24, 24, 40, 40), fill=(r, g, b, 255))
        _images[status] = img
    return _images[status]


def _exit_on_ctrl_c():
    """Ctrl+C or closing the window quits, even while the tray icon owns the main thread."""
    try:
        handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(lambda _kind: os._exit(0))
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
        _exit_on_ctrl_c.keep = handler       # stop it being garbage-collected
    except Exception:
        pass


def run(paused):
    _exit_on_ctrl_c()

    def open_display(icon=None, item=None):
        webbrowser.open(f"http://localhost:{display.PORT}")

    def type_to(icon=None, item=None):
        webbrowser.open(f"http://localhost:{display.PORT}/?type=1")

    def toggle(icon, item):
        if paused.is_set():
            paused.clear()
        else:
            paused.set()

    def quit_app(icon, item):
        icon.visible = False
        os._exit(0)

    def status():
        return "paused" if paused.is_set() else display.STATE.get("status", "sleeping")

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: LABELS.get(status(), status()), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open display", open_display, default=True),
        pystray.MenuItem("Type to Jarvis…", type_to),
        pystray.MenuItem(lambda item: "Resume listening" if paused.is_set() else "Pause listening", toggle),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Jarvis", quit_app),
    )
    icon = pystray.Icon("Jarvis", orb("sleeping"), "Jarvis", menu)

    def refresh(icon):
        icon.visible = True
        last = None
        while True:
            s = status()
            if s != last:
                icon.icon = orb(s)
                icon.title = f"Jarvis: {LABELS.get(s, s)}"
                try:
                    icon.update_menu()
                except Exception:
                    pass
                last = s
            time.sleep(0.4)

    icon.run(setup=lambda i: threading.Thread(target=refresh, args=(i,), daemon=True).start())
