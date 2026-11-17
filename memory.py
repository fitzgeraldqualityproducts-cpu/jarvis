"""Long-term memory: facts you ask Jarvis to remember, plus recent conversation.
Saved in memory.json next to this file, so it survives restarts."""
import datetime
import difflib
import json
import re
import threading
from pathlib import Path

PATH = Path(__file__).with_name("memory.json")
_lock = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {"facts": [], "history": [], "last_briefing": ""}


def _save(data):
    PATH.write_text(json.dumps(data, indent=2))


def _tidy(fact):
    fact = fact.strip(" .")
    fact = re.sub(r"^(that|this)\s+", "", fact)
    return fact[:1].upper() + fact[1:]


def remember(fact):
    fact = _tidy(fact)
    with _lock:
        data = _load()
        if fact.lower() not in (f.lower() for f in data["facts"]):
            data["facts"].append(fact)
        _save(data)
    try:
        import obsidian
        obsidian.learn(fact)
    except Exception as e:
        print(f"(couldn't add that to the About Connor note: {e})")
    return "I'll remember that."


def forget(what):
    what = what.strip(" .").lower()
    what = re.sub(r"^(that|about|the fact that)\s+", "", what)
    with _lock:
        data = _load()
        if what in ("everything", "everything you know", "it all", "all of it"):
            n = len(data["facts"])
            data["facts"] = []
            _save(data)
            return f"Done. I've forgotten all {n} things." if n else "There was nothing to forget."
        lower = [f.lower() for f in data["facts"]]
        hits = [f for f in data["facts"] if what in f.lower()]
        if not hits:
            close = difflib.get_close_matches(what, lower, n=1, cutoff=0.4)
            hits = [data["facts"][lower.index(close[0])]] if close else []
        if not hits:
            return "I don't think I knew that."
        data["facts"] = [f for f in data["facts"] if f not in hits]
        _save(data)
    try:
        import obsidian
        obsidian.unlearn(hits)
    except Exception:
        pass
    return "Forgotten."


def facts():
    return _load()["facts"]


def recall_summary():
    fs = facts()
    if not fs:
        return "You haven't asked me to remember anything yet."
    if len(fs) > 8:
        return f"I know {len(fs)} things. The latest: " + "; ".join(fs[-8:]) + "."
    return "Here's what I know: " + "; ".join(fs) + "."


def add_exchange(user, jarvis):
    with _lock:
        data = _load()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        data["history"] = (data["history"] + [{"t": stamp, "user": user, "jarvis": jarvis}])[-30:]
        _save(data)


def history(n=8):
    return _load()["history"][-n:]


def first_wake_today():
    """True the first time this is called each day."""
    today = datetime.date.today().isoformat()
    with _lock:
        data = _load()
        if data.get("last_briefing") == today:
            return False
        data["last_briefing"] = today
        _save(data)
    return True
