"""Jarvis's own calendar, reminders and notes, used on Windows (where there's no Apple
Calendar or Reminders to talk to). Everything is kept in calendar.json in this folder,
and notes go to "Jarvis Notes.txt" in your Documents folder (or Obsidian, if connected)."""
import datetime
import json
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "calendar.json"
_lock = threading.Lock()
UNDATED_DAYS = 3          # reminders with no time are mentioned for this many days


def _load():
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("events", [])
    data.setdefault("reminders", [])
    return data


def _save(data):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE)


def _dt(s):
    return datetime.datetime.fromisoformat(s) if s else None


def add_event(title, start, all_day=False, minutes=60):
    with _lock:
        data = _load()
        if all_day:
            start = datetime.datetime.combine(start.date(), datetime.time())
        data["events"].append({"title": title, "start": start.isoformat(timespec="minutes"),
                               "all_day": bool(all_day), "minutes": int(minutes)})
        _save(data)


def events(day_offset=0):
    """[(start or None for all-day, title)] for that day."""
    day = datetime.date.today() + datetime.timedelta(days=day_offset)
    out = []
    for e in _load()["events"]:
        start = _dt(e["start"])
        if start and start.date() == day:
            out.append((None if e.get("all_day") else start, e["title"]))
    return out


def add_reminder(title, when=None):
    with _lock:
        data = _load()
        data["reminders"].append({"title": title, "due": when.isoformat(timespec="minutes") if when else None,
                                  "created": datetime.datetime.now().isoformat(timespec="minutes"),
                                  "done": False})
        _save(data)


def due_reminders(day_offset=0):
    """Open reminders due by the end of that day (overdue included), plus recent undated ones."""
    end = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=day_offset + 1), datetime.time())
    now = datetime.datetime.now()
    out = []
    for r in _load()["reminders"]:
        if r.get("done"):
            continue
        due = _dt(r.get("due"))
        if due and due < end:
            out.append((due, r["title"]))
        elif not due and (now - _dt(r["created"])).days < UNDATED_DAYS:
            out.append((None, r["title"]))
    return out


def _due_now():
    """Marks reminders whose time has come as done, and returns their titles."""
    now = datetime.datetime.now()
    hits = []
    with _lock:
        data = _load()
        for r in data["reminders"]:
            due = _dt(r.get("due"))
            if not r.get("done") and due and due <= now:
                r["done"] = True
                if now - due < datetime.timedelta(hours=12):     # don't read out ancient ones at start-up
                    hits.append(r["title"])
        if hits:
            _save(data)
    return hits


def watch(ring):
    """Calls ring(title) when a reminder is due. Runs while Jarvis is running."""
    def loop():
        while True:
            try:
                for title in _due_now():
                    ring(title)
            except Exception as e:
                print(f"(reminder check failed: {e})")
            time.sleep(20)
    threading.Thread(target=loop, daemon=True).start()


def notes_file():
    docs = Path.home() / "Documents"
    for p in (Path.home() / "OneDrive" / "Documents", docs):
        if p.is_dir():
            return p / "Jarvis Notes.txt"
    docs.mkdir(exist_ok=True)
    return docs / "Jarvis Notes.txt"


def add_note(text):
    stamp = datetime.datetime.now().strftime("%a %d %b %Y, %I:%M %p")
    with open(notes_file(), "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")
    return True
