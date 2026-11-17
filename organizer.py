"""Calendar, Reminders, timers and Notes.

Reading your calendar and reminders uses Apple's EventKit (needs the
pyobjc-framework-EventKit package and a one-time permission). Adding reminders
and notes uses AppleScript, so it works even without that package.
On Windows, Jarvis keeps his own calendar and reminders instead (see wincal.py)."""
import datetime
import subprocess
import sys
import threading
import time

import timeparse

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import wincal

# ---------------- EventKit (read calendar + reminders) ----------------

_store = None
_access = {}


def _eventkit():
    global _store
    if _store is None:
        from EventKit import EKEventStore
        _store = EKEventStore.alloc().init()
    return _store


def request_access(kind):
    """kind: 'events' or 'reminders'. Returns True if allowed."""
    if kind in _access:
        return _access[kind]
    try:
        store = _eventkit()
    except Exception as e:
        print(f"(calendar access unavailable: {e})")
        _access[kind] = False
        return False
    done, result = threading.Event(), {"ok": False}

    def callback(granted, error):
        result["ok"] = bool(granted)
        done.set()

    full = "requestFullAccessToEventsWithCompletion_" if kind == "events" else "requestFullAccessToRemindersWithCompletion_"
    if hasattr(store, full):
        getattr(store, full)(callback)
    else:
        store.requestAccessToEntityType_completion_(0 if kind == "events" else 1, callback)
    done.wait(60)
    _access[kind] = result["ok"]
    return result["ok"]


def _nsdate(dt):
    from Foundation import NSDate
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _pydate(nsdate):
    return datetime.datetime.fromtimestamp(nsdate.timeIntervalSince1970())


def events(day_offset=0):
    """List of (start datetime or None for all-day, title) for a day. None if no access."""
    if WINDOWS:
        return sorted(wincal.events(day_offset), key=lambda x: x[0] or datetime.datetime.min)
    if not request_access("events"):
        return None
    store = _eventkit()
    start = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=day_offset), datetime.time())
    end = start + datetime.timedelta(days=1)
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), None)
    out = []
    for e in store.eventsMatchingPredicate_(pred) or []:
        title = str(e.title() or "Untitled event")
        out.append((None if e.isAllDay() else _pydate(e.startDate()), title))
    out.sort(key=lambda x: x[0] or start)
    return out


def due_reminders(day_offset=0):
    """Incomplete reminders due by the end of that day (includes overdue). None if no access."""
    if WINDOWS:
        return sorted(wincal.due_reminders(day_offset), key=lambda x: x[0] or datetime.datetime.max)
    if not request_access("reminders"):
        return None
    from Foundation import NSCalendar
    store = _eventkit()
    end = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=day_offset + 1), datetime.time())
    pred = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, _nsdate(end), None)
    done, found = threading.Event(), []

    def callback(reminders):
        found.extend(list(reminders or []))
        done.set()

    store.fetchRemindersMatchingPredicate_completion_(pred, callback)
    done.wait(15)
    out = []
    cal = NSCalendar.currentCalendar()
    for r in found:
        comps = r.dueDateComponents()
        due = _pydate(cal.dateFromComponents_(comps)) if comps is not None else None
        out.append((due, str(r.title() or "Untitled reminder")))
    out.sort(key=lambda x: x[0] or end)
    return out


def _clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0").replace(":00", "")


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def calendar_summary(day_offset=0):
    word = "today" if day_offset == 0 else "tomorrow"
    evs = events(day_offset)
    if evs is None:
        return "I don't have access to your calendar yet. Run the Install Jarvis Extras file in my folder."
    if not evs:
        return f"Your calendar is clear {word}."
    items = [f"{t} at {_clock(s)}" if s else f"{t}, all day" for s, t in evs[:6]]
    more = f", plus {len(evs) - 6} more" if len(evs) > 6 else ""
    n = len(evs)
    return f"You have {n} event{'s' if n != 1 else ''} {word}: {_join(items)}{more}."


def reminders_summary(day_offset=0):
    rems = due_reminders(day_offset)
    if rems is None:
        return "I don't have access to your reminders yet. Run the Install Jarvis Extras file in my folder."
    if not rems:
        return "No reminders due." if day_offset == 0 else "No reminders due tomorrow."
    now = datetime.datetime.now()
    items = []
    for due, title in rems[:6]:
        if due and due < now - datetime.timedelta(minutes=1) and due.date() < now.date():
            items.append(f"{title}, which is overdue")
        elif due and (due.hour or due.minute):
            items.append(f"{title} at {_clock(due)}")
        else:
            items.append(title)
    n = len(rems)
    return f"You have {n} reminder{'s' if n != 1 else ''}: {_join(items)}."


def today_items():
    """For the display: list of {time, text, kind}."""
    out = []
    try:
        for s, t in events(0) or []:
            out.append({"time": _clock(s) if s else "All day", "text": t, "kind": "event", "ts": s.timestamp() if s else 0})
        for d, t in due_reminders(0) or []:
            out.append({"time": _clock(d) if d and (d.hour or d.minute) else "", "text": t, "kind": "reminder",
                        "ts": d.timestamp() if d else 9e12})
    except Exception as e:
        print(f"(couldn't read today's items: {e})")
    out.sort(key=lambda x: x["ts"])
    return out


def add_event(title, when, all_day=False, minutes=60):
    """Adds an event to your default calendar. Returns what to say."""
    title = title[:1].upper() + title[1:]
    if WINDOWS:
        wincal.add_event(title, when, all_day, minutes)
        if all_day:
            return f"Added {title} to your calendar for {_day_words(when)}."
        return f"Added {title} to your calendar, {timeparse.say_time(when)}."
    try:
        if not request_access("events"):
            raise RuntimeError("no calendar access")
        from EventKit import EKEvent
        store = _eventkit()
        ev = EKEvent.eventWithEventStore_(store)
        ev.setTitle_(title)
        start = datetime.datetime.combine(when.date(), datetime.time()) if all_day else when
        ev.setStartDate_(_nsdate(start))
        ev.setEndDate_(_nsdate(start + (datetime.timedelta(days=1) if all_day else datetime.timedelta(minutes=minutes))))
        ev.setAllDay_(all_day)
        ev.setCalendar_(store.defaultCalendarForNewEvents())
        result = store.saveEvent_span_error_(ev, 0, None)
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if not ok:
            raise RuntimeError(str(result))
    except Exception as e:
        print(f"(EventKit couldn't add the event, trying AppleScript: {e})")
        secs = 0 if all_day else when.hour * 3600 + when.minute * 60
        script = f"""
set d to current date
set day of d to 1
set year of d to {when.year}
set month of d to {when.month}
set day of d to {when.day}
set time of d to {secs}
tell application "Calendar"
  tell (first calendar whose writable is true)
    make new event with properties {{summary:{_q(title)}, start date:d, end date:d + {86400 if all_day else minutes * 60}, allday event:{str(all_day).lower()}}}
  end tell
end tell"""
        if not _osa(script):
            return "I couldn't add that to your calendar. Check that Terminal is allowed to use Calendar in System Settings, Privacy."
    if all_day:
        return f"Added {title} to your calendar for {_day_words(when)}."
    return f"Added {title} to your calendar, {timeparse.say_time(when)}."


def _day_words(when):
    return "today" if when.date() == datetime.date.today() else \
        "tomorrow" if when.date() == datetime.date.today() + datetime.timedelta(days=1) else when.strftime("%A, %B ") + str(when.day)


# ---------------- AppleScript (add reminders, notes) ----------------

def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osa(script):
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        print(f"(AppleScript error: {out.stderr.strip()[:200]})")
    return out.returncode == 0


def add_reminder(title, when=None):
    title = title[:1].upper() + title[1:]
    if WINDOWS:
        wincal.add_reminder(title, when)
        return f"I'll remind you to {title[0].lower() + title[1:]}" + (
            f" {timeparse.say_time(when)}. Keep me running so I can tell you." if when else ".")
    if when:
        script = f"""
set d to current date
set day of d to 1
set year of d to {when.year}
set month of d to {when.month}
set day of d to {when.day}
set time of d to {when.hour * 3600 + when.minute * 60}
tell application "Reminders"
  tell default list
    make new reminder with properties {{name:{_q(title)}, due date:d, remind me date:d}}
  end tell
end tell"""
    else:
        script = f"""tell application "Reminders"
  tell default list
    make new reminder with properties {{name:{_q(title)}}}
  end tell
end tell"""
    if not _osa(script):
        return "I couldn't add that to Reminders. You may need to allow access in System Settings, Privacy, Automation."
    return f"I'll remind you to {title[0].lower() + title[1:]}" + (f" {timeparse.say_time(when)}." if when else ".")


def add_note(text):
    text = text.strip()
    title = text[:1].upper() + text[1:]
    if WINDOWS:
        try:
            wincal.add_note(title)
            return "Noted. It's in Jarvis Notes in your Documents folder."
        except Exception as e:
            print(f"(couldn't save the note: {e})")
            return "I couldn't save that note."
    body = f"<div><b>{title[:60]}</b></div><div>{title}</div>" if len(title) > 60 else f"<div>{title}</div>"
    ok = _osa(f'tell application "Notes" to make new note with properties {{body:{_q(body)}}}') or \
        _osa(f'tell application "Notes" to tell default account to make new note at folder 1 with properties {{body:{_q(body)}}}')
    return "Noted." if ok else "I couldn't save that to Notes."


# ---------------- Timers (only while Jarvis is running) ----------------

_timers = []
_lock = threading.Lock()


def _publish():
    try:
        import display
        with _lock:
            display.update(timers=[{"label": t["label"], "end": t["end"]} for t in _timers])
    except Exception:
        pass


def _ring(timer):
    with _lock:
        if timer not in _timers:
            return
        _timers.remove(timer)
    _publish()
    try:
        if WINDOWS:
            import win
            win.system_sound("alarm")
        else:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
            subprocess.run(["osascript", "-e", f'display notification {_q(timer["message"])} with title "Jarvis"'])
    except Exception as e:
        print(f"(timer sound failed: {e})")
    import voice
    voice.say(timer["message"], interruptible=False)


def start_reminder_watch():
    """Windows: say reminders out loud when they're due (Jarvis has to be running)."""
    if not WINDOWS:
        return

    def ring(title):
        try:
            import win
            win.system_sound("alarm")
        except Exception:
            pass
        import voice
        voice.say(f"Reminder: {title[0].lower() + title[1:]}.", interruptible=False)
    wincal.watch(ring)


def set_timer(seconds, label=None):
    label = label or f"{timeparse.describe_duration(seconds)} timer"
    timer = {"label": label, "end": time.time() + seconds,
             "message": f"Your {label} is done." if "timer" in label else f"It's time: {label}."}
    timer["thread"] = threading.Timer(seconds, _ring, args=(timer,))
    timer["thread"].daemon = True
    with _lock:
        _timers.append(timer)
    timer["thread"].start()
    _publish()
    return f"{timeparse.describe_duration(seconds).capitalize()}, starting now."


def set_alarm(when):
    seconds = (when - datetime.datetime.now()).total_seconds()
    timer = {"label": f"alarm for {timeparse.say_time(when)}", "end": when.timestamp(),
             "message": "Good morning. This is your alarm." if when.hour < 12 else "This is your alarm."}
    timer["thread"] = threading.Timer(max(1, seconds), _ring, args=(timer,))
    timer["thread"].daemon = True
    with _lock:
        _timers.append(timer)
    timer["thread"].start()
    _publish()
    return f"Alarm set for {timeparse.say_time(when)}. Keep me running and I'll wake you."


def timers_status():
    with _lock:
        active = list(_timers)
    if not active:
        return "You don't have any timers running."
    now = time.time()
    parts = [f"{timeparse.describe_duration(t['end'] - now)} left on your {t['label']}" for t in active]
    return "There's " + _join(parts) + "."


def cancel_timers():
    with _lock:
        n = len(_timers)
        for t in _timers:
            t["thread"].cancel()
        _timers.clear()
    _publish()
    return "Cancelled." if n else "There weren't any timers running."
