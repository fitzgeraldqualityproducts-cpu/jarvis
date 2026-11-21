"""Mute: one switch that turns Jarvis's mic AND voice off.

While muted he doesn't listen at all (no wake word, no claps, no "stop" spotting), and he
doesn't talk out loud: anything he'd say shows up in the display's Comms panel instead.
Typing to him still works, and timer/reminder alarm sounds still ring.

Toggle it from the mic button on the display (or press M there), the menu bar / tray icon,
by saying "mute yourself", or by typing "unmute". It stays muted across restarts and updates,
until you unmute (or a timed mute like "mute for an hour" runs out)."""
import json
import threading
import time
from pathlib import Path

MUTED = threading.Event()
STATE_FILE = Path(__file__).with_name(".mute.json")
_until = {"t": None}            # when a timed mute ends (epoch seconds), or None
_lock = threading.Lock()


def _show():
    try:
        import display
        display.update(muted=MUTED.is_set(), mute_until=_until["t"])
    except Exception:
        pass


def _save():
    try:
        STATE_FILE.write_text(json.dumps({"muted": MUTED.is_set(), "until": _until["t"]}))
    except Exception as e:
        print(f"(couldn't save mute setting: {e})")


def is_muted():
    return MUTED.is_set()


def set_muted(on, minutes=None):
    """Mute (on=True) or unmute. minutes: unmute by himself after that long."""
    with _lock:
        if on:
            _until["t"] = time.time() + minutes * 60 if minutes else None
            MUTED.set()
        else:
            _until["t"] = None
            MUTED.clear()
        _save()
    print("(muted: not listening or talking)" if on else "(unmuted)")
    _show()
    try:
        import display
        display.update(status="paused" if on else "sleeping")
    except Exception:
        pass
    return on


def toggle():
    return set_muted(not MUTED.is_set())


def load():
    """At start-up: stay muted if he was muted before a restart. Starts the timed-mute clock."""
    try:
        s = json.loads(STATE_FILE.read_text())
    except Exception:
        s = {}
    until = s.get("until")
    if s.get("muted") and not (until and until <= time.time()):
        _until["t"] = until
        MUTED.set()
    _show()
    threading.Thread(target=_watch, daemon=True, name="mute-timer").start()


def _watch():
    while True:
        t = _until["t"]
        if MUTED.is_set() and t and time.time() >= t:
            set_muted(False)
            try:
                import display
                display.log("jarvis", "Mute's up. I'm listening again.")
            except Exception:
                pass
        time.sleep(5)


def describe():
    if not MUTED.is_set():
        return "I'm not muted."
    t = _until["t"]
    if t:
        mins = max(1, round((t - time.time()) / 60))
        return f"I'm muted for about {mins} more minute{'s' if mins != 1 else ''}."
    return "I'm muted until you unmute me."
