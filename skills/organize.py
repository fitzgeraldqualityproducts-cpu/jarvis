"""Timers, alarms, reminders, calendar, notes, to-dos and the daily briefing."""
from skills import tool
from skills._time import WHEN_HELP, when as parse_when


@tool("Start a countdown timer (for 'remind me in 20 minutes to X' use add_reminder instead).", minutes="how long, in minutes (can be a fraction)", label="optional name, e.g. 'pasta'")
def set_timer(minutes: float, label: str = ""):
    import organizer
    return organizer.set_timer(int(float(minutes) * 60), f"{label} timer" if label else None)


@tool("Set an alarm.", time=WHEN_HELP)
def set_alarm(time: str):
    import organizer
    at = parse_when(time)
    return organizer.set_alarm(at) if at else f"I couldn't work out the time '{time}'."


@tool("How long is left on the timers and alarms.")
def timers_status():
    import organizer
    return organizer.timers_status()


@tool("Cancel all timers and alarms.")
def cancel_timers():
    import organizer
    return organizer.cancel_timers()


@tool("Add a reminder: use it for 'remind me to X', 'remind me in 20 minutes to X' or 'at 5'. It pops up at that time.", task="what to remind them about",
      time=WHEN_HELP + "; leave empty for no time")
def add_reminder(task: str, time: str = ""):
    import briefing
    import organizer
    at = parse_when(time) if time else None
    if time and not at:
        return f"I couldn't work out the time '{time}'."
    reply = organizer.add_reminder(task, at)
    briefing.refresh_display()
    return reply


@tool("Read the reminders for today or tomorrow.", day="today or tomorrow")
def list_reminders(day: str = "today"):
    import organizer
    return organizer.reminders_summary(1 if "tomorrow" in day.lower() else 0)


@tool("Add an event to the calendar.", title="what it is", start=WHEN_HELP,
      all_day="true if it has no time", minutes="how long it lasts")
def add_event(title: str, start: str, all_day: bool = False, minutes: int = 60):
    import briefing
    import organizer
    at = parse_when(start)
    if not at:
        return f"I couldn't work out when '{start}' is."
    reply = organizer.add_event(title, at, all_day=all_day, minutes=minutes)
    briefing.refresh_display()
    return reply


@tool("What's on the calendar (and reminders) today or tomorrow.", day="today or tomorrow")
def calendar(day: str = "today"):
    import organizer
    offset = 1 if "tomorrow" in day.lower() else 0
    reply = organizer.calendar_summary(offset)
    rems = organizer.reminders_summary(offset)
    if not rems.startswith(("No reminders", "I don't")):
        reply += " " + rems
    return reply


@tool("Write a note down (in Obsidian if it's set up).", text="the note")
def take_note(text: str):
    import obsidian
    import organizer
    return (obsidian.add_note(text) if obsidian.enabled() else None) or organizer.add_note(text)


@tool("Add a to-do to the Obsidian to-do list.", text="the to-do")
def add_todo(text: str):
    import obsidian
    if not obsidian.enabled():
        return "Obsidian isn't set up, so I added it as a reminder. " + add_reminder(text)
    return obsidian.add_todo(text)


@tool("Read the open to-dos.")
def list_todos():
    import obsidian
    return obsidian.todos_summary() if obsidian.enabled() else list_reminders()


@tool("Answer a question from the user's Obsidian notes.", question="what they want to know from their notes")
def search_notes(question: str):
    import obsidian
    return obsidian.ask(question) if obsidian.enabled() else "Obsidian isn't set up yet."


@tool("The morning briefing: weather, calendar, reminders and news.")
def briefing():
    import briefing as b
    return b.full()
