"""Predictive intelligence: Jarvis learns Connor's routines and gets ahead of him.

  Routines   every command he handles is noted (habits.json, kept on this computer only).
             When Connor has done the same thing at about the same time of day on 3 or more
             different days, Jarvis offers it around that time: "You usually put on Lil Baby
             around now. Want me to?" Say yes and he does it; say no twice and he stops offering it.
  Calendar   a heads-up a few minutes before each event on today's calendar, said out loud
             when he's not busy.
  Looking ahead   once a day he looks over your calendar, routines, to-dos, notes, the projects
             his team is working on and what he knows about you, and works out what you'll
             probably need. It's added to the morning briefing.

  "what do you think I need" / "any suggestions"     hear his predictions now
  "what have you noticed about me" / "my routines"   hear the routines he's spotted
Set "predictive": false in config.json to turn it all off, or "heads_up_minutes" (default 10)."""
import datetime
import json
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "habits.json"
CONFIG = HERE / "config.json"
_lock = threading.RLock()
_thinking = threading.Event()

WINDOW = 40            # minutes either side of the usual time
MIN_DAYS = 3           # different days before it counts as a routine
MAX_SUGGESTIONS_PER_DAY = 3
# Not worth learning as routines: one-offs, questions about him, yes/no, settings.
SKIP = re.compile(r"^(yes|yeah|no|nope|ok|okay|sure|thanks?|thank you|stop|undo|cancel)\b|"
                  r"\b(upgrade|update|undo|self[- ]?check|remember|forget|team|agents?)\b|"
                  r"\b(timer|alarm|remind me|reminder|add .* calendar|schedule)\b")   # these change every time


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def enabled():
    return _cfg().get("predictive", True)


def _load():
    try:
        data = json.loads(FILE.read_text())
    except Exception:
        data = {}
    for k, v in (("log", []), ("answers", {}), ("offers", []), ("warned", []), ("insight", {})):
        data.setdefault(k, v)
    return data


def _save(data):
    cutoff = time.time() - 60 * 86400
    data["log"] = [e for e in data["log"] if e["t"] > cutoff][-2000:]
    data["offers"] = data["offers"][-40:]
    data["warned"] = data["warned"][-60:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(FILE)


def _key(t):
    t = re.sub(r"\b(?:hey |ok |okay )?jarvis\b", " ", t.lower())
    t = re.sub(r"^\s*(please|can you|could you|would you|now)\s+", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", t)).strip()


def _minute(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return d.hour * 60 + d.minute


def _gap(a, b):
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def _day(ts):
    return datetime.date.fromtimestamp(ts).isoformat()


def record(text, kind):
    """Note a command he just did for Connor (kind: which skill handled it)."""
    if not enabled() or not text:
        return
    key = _key(text)
    if len(key.split()) < 2 or SKIP.search(key):
        return
    with _lock:
        data = _load()
        data["log"].append({"t": time.time(), "key": key, "said": text[:200], "kind": kind})
        _save(data)


def routines(min_days=MIN_DAYS):
    """All routines spotted: list of {key, said, minute, days, weekdays}, most regular first."""
    data = _load()
    month = time.time() - 30 * 86400
    by_key = {}
    for e in data["log"]:
        if e["t"] > month:
            by_key.setdefault(e["key"], []).append(e)
    found = []
    for key, entries in by_key.items():
        days = {_day(e["t"]) for e in entries}
        if len(days) < min_days:
            continue
        # Find the time of day that the most different days cluster around.
        best = None
        for e in entries:
            m = _minute(e["t"])
            near = {_day(x["t"]) for x in entries if _gap(_minute(x["t"]), m) <= WINDOW}
            if best is None or len(near) > len(best[1]):
                best = (m, near)
        if len(best[1]) >= min_days:
            mins = sorted(_minute(x["t"]) for x in entries if _gap(_minute(x["t"]), best[0]) <= WINDOW)
            weekdays = {datetime.date.fromisoformat(d).weekday() for d in best[1]}
            found.append({"key": key, "said": entries[-1]["said"], "minute": mins[len(mins) // 2],
                          "days": len(best[1]), "weekdays": sorted(weekdays)})
    found.sort(key=lambda r: -r["days"])
    return found


def _clock(minute):
    h, m = divmod(minute, 60)
    return datetime.time(h, m).strftime("%I:%M %p").lstrip("0").replace(":00", "")


def _declined(data, key):
    a = data["answers"].get(key, {})
    return a.get("no", 0) >= 2 and a.get("no", 0) > a.get("yes", 0) * 2


def next_suggestion():
    """A routine to offer right now, or None. Doesn't nag: a few a day, not one right after another."""
    if not enabled():
        return None
    now, today = time.time(), _day(time.time())
    with _lock:
        data = _load()
        offers = data["offers"]
        if sum(1 for o in offers if o["day"] == today) >= MAX_SUGGESTIONS_PER_DAY or \
                (offers and now - offers[-1]["t"] < 45 * 60):
            return None
        minute = _minute(now)
        weekend = datetime.date.today().weekday() >= 5
        for r in routines():
            if _gap(r["minute"], minute) > 20 or _declined(data, r["key"]):
                continue
            # Only weekday habits on weekdays (and weekend ones at weekends) once there's a pattern.
            if len(r["weekdays"]) <= 5 and r["weekdays"] and all(d < 5 for d in r["weekdays"]) and weekend:
                continue
            if any(o["key"] == r["key"] and o["day"] == today for o in offers):
                continue
            if any(e["key"] == r["key"] and _day(e["t"]) == today and _gap(_minute(e["t"]), minute) <= WINDOW * 2
                   for e in data["log"]):
                continue                    # he's already done it today
            return r
    return None


def suggestion_line(r):
    said = r["said"].strip().rstrip(".?!")
    said = said[:1].lower() + said[1:]
    return f"You usually ask me to {said} around this time. Want me to do that now?"


def offered(key):
    with _lock:
        data = _load()
        data["offers"].append({"key": key, "day": _day(time.time()), "t": time.time()})
        _save(data)


def answered(key, yes):
    with _lock:
        data = _load()
        a = data["answers"].setdefault(key, {"yes": 0, "no": 0})
        a["yes" if yes else "no"] += 1
        _save(data)
    try:
        import learn
        learn.record_result("prediction", key, bool(yes))
    except Exception:
        pass


def heads_up():
    """(key, words) for a calendar event starting soon that he hasn't warned about, or None."""
    if not enabled():
        return None
    lead = float(_cfg().get("heads_up_minutes", 10))
    try:
        import organizer
        evs = organizer.events(0) or []
    except Exception as e:
        print(f"(heads-up: couldn't read the calendar: {e})")
        return None
    now = datetime.datetime.now()
    data = _load()
    for start, title in evs:
        if not start:
            continue
        mins = (start - now).total_seconds() / 60
        key = f"{title}|{start.isoformat()}"
        if 0.5 < mins <= lead and key not in data["warned"]:
            n = max(1, int(round(mins)))
            return key, f"Heads up, {title} starts in {n} minute{'s' if n != 1 else ''}."
    return None


def warned(key):
    with _lock:
        data = _load()
        data["warned"].append(key)
        _save(data)


def routines_summary():
    rs = routines()
    if not rs:
        n = len(_load()["log"])
        return ("I haven't spotted any routines yet. I'm watching for things you ask me at the same time on "
                "different days" + (f", and I've noted {n} commands so far." if n else "."))
    parts = [f"{r['said'].rstrip('.?!')} around {_clock(r['minute'])}" for r in rs[:4]]
    return "Here's what I've noticed you do: " + "; ".join(parts) + ". I'll offer them around those times."


# ---------- looking ahead (Claude works out what you'll need) ----------

PROMPT = """You are the predictive part of Jarvis, {owner}'s voice assistant. From what you know below, predict
what {owner} is likely to need {when}, before they ask. Think about upcoming events (and what to prepare or
when to leave), routines, deadlines and overdue to-dos, ongoing projects, and what they usually do at this
time. Be specific and practical, and only say things that follow from the information. Don't invent events.

Now: {now}
{info}

Reply with 1 to 3 short spoken sentences (no lists, markdown or links), as Jarvis talking to {owner}, starting
with the most useful prediction. If there's genuinely nothing useful to predict, reply with just: NOTHING"""


def _gather():
    """Everything he knows that helps him predict, as text."""
    out = []
    try:
        import organizer
        for off, word in ((0, "Today"), (1, "Tomorrow")):
            evs = organizer.events(off)
            if evs:
                out.append(f"{word}'s calendar: " + "; ".join(
                    f"{t} at {s.strftime('%I:%M %p').lstrip('0')}" if s else f"{t} (all day)" for s, t in evs[:10]))
        rems = organizer.due_reminders(0)
        if rems:
            out.append("Reminders due: " + "; ".join(t for _, t in rems[:10]))
    except Exception as e:
        print(f"(predict: calendar failed: {e})")
    rs = routines()
    if rs:
        out.append("Routines: " + "; ".join(f"{r['said']} around {_clock(r['minute'])} ({r['days']} days)" for r in rs[:8]))
    try:
        import obsidian
        if obsidian.enabled():
            todos = obsidian.open_todos(10) or []
            if todos:
                out.append("Open to-dos: " + "; ".join(f"{t}" + (f" (due {d})" if d else "") for t, d in todos))
            about = obsidian.profile(1500)
            if about:
                out.append("About them:\n" + about)
    except Exception as e:
        print(f"(predict: Obsidian failed: {e})")
    try:
        import memory
        fs = memory.facts()
        if fs:
            out.append("Things they asked Jarvis to remember: " + "; ".join(fs[-15:]))
        hist = memory.history(6)
        if hist:
            out.append("Recent conversation: " + " | ".join(f"{h['user']}" for h in hist))
    except Exception:
        pass
    try:
        import workforce
        p = workforce.projects_text()
        if p:
            out.append("Projects Jarvis's AI team has worked on: " + p)
    except Exception:
        pass
    return "\n".join(out)


def look_ahead(when="for the rest of today"):
    """Asks Claude what Connor will probably need. Returns spoken text, or None. Slow (10-30 s)."""
    import brain
    claude = brain.find_claude()
    if not claude:
        return None
    info = _gather()
    if not info.strip():
        return None
    try:
        import speaker
        owner = speaker.owner_name()
    except Exception:
        owner = "the owner"
    now = datetime.datetime.now().strftime("%A %B %d, %I:%M %p")
    prompt = PROMPT.format(owner=owner, when=when, now=now, info=info)
    try:
        out = brain.run_background([claude, "-p", prompt, "--disallowedTools",
                                    "Read Glob Grep Edit Write NotebookEdit Bash WebSearch WebFetch Task"],
                                   timeout=120, who="routines")
    except Exception as e:
        print(f"(predict: look ahead failed: {e})")
        return None
    text = (out.stdout or "").strip()
    if not text or text.upper().startswith("NOTHING"):
        return None
    return text


def insight_today():
    """Today's look-ahead if it's been worked out (for the morning briefing)."""
    ins = _load()["insight"]
    return ins.get("text") if ins.get("day") == _day(time.time()) else None


def _make_insight():
    if _thinking.is_set():
        return
    _thinking.set()
    try:
        text = look_ahead("today")
        with _lock:
            data = _load()
            data["insight"] = {"day": _day(time.time()), "text": text or "", "t": time.time()}
            _save(data)
        if text:
            print(f"(looked ahead: {text[:150]})")
    finally:
        _thinking.clear()


def start(is_busy):
    """Background: once a day, early on, work out what Connor will need today (for the briefing)."""
    def loop():
        time.sleep(90)
        while True:
            try:
                if enabled() and not is_busy() and datetime.datetime.now().hour >= 4 and \
                        _load()["insight"].get("day") != _day(time.time()):
                    _make_insight()
            except Exception as e:
                print(f"(predict loop failed: {e})")
            time.sleep(300)
    threading.Thread(target=loop, daemon=True, name="predict").start()
