"""Turns spoken times like 'in 20 minutes', 'at 5 p.m.', 'tomorrow morning' into datetimes."""
import datetime
import re

WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
    "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "couple of": 2, "few": 3,
}
NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(map(re.escape, WORDS), key=len, reverse=True)) + r")"
UNIT = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}
DUR_RE = re.compile(NUM + r"\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _num(s):
    s = s.strip()
    return float(s) if re.match(r"\d", s) else float(WORDS[s])


TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}


def _compound(t):
    """'twenty five' -> '25'."""
    return re.sub(r"\b(" + "|".join(TENS) + r")[\s-](" + "|".join(ONES) + r")\b",
                  lambda m: str(TENS[m.group(1)] + ONES[m.group(2)]), t)


def parse_duration(text):
    """'10 minutes' -> 600, 'an hour and a half' -> 5400. None if no duration."""
    t = _compound(text.lower())
    total, found = 0.0, False
    if "half an hour" in t:
        total, found = 1800.0, True
        t = t.replace("half an hour", " ")
    for m in DUR_RE.finditer(t):
        unit = re.sub(r"s$", "", m.group(2))
        total += _num(m.group(1)) * UNIT.get(unit, UNIT.get(unit[:3], 60))
        found = True
    if "and a half" in t and list(DUR_RE.finditer(t)):
        last = list(DUR_RE.finditer(t))[-1]
        unit = re.sub(r"s$", "", last.group(2))
        total += 0.5 * UNIT.get(unit, UNIT.get(unit[:3], 60))
    return int(total) if found else None


def describe_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if s and not h:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " and ".join(parts) or "0 seconds"


def say_time(dt, now=None):
    """'5:30 PM', 'tomorrow at 9 AM', 'Monday at 2 PM'."""
    now = now or datetime.datetime.now()
    clock = dt.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    days = (dt.date() - now.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    if 1 < days < 7:
        return f"{dt.strftime('%A')} at {clock}"
    return f"{dt.strftime('%B')} {dt.day} at {clock}"


def parse_when(text, now=None):
    """Finds a time in text. Returns (datetime or None, text with the time words removed)."""
    now = now or datetime.datetime.now()
    t = _compound(text.lower()).replace("o'clock", "")
    t = " " + re.sub(r"\b([ap])\.?\s?m\b\.?", r"\1m", t) + " "
    when = None

    m = re.search(r"\bin\s+((?:" + NUM + r"\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)(?:\s+and\s+a\s+half)?\s*(?:and\s*)?)+|half an hour)", t)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            when = now + datetime.timedelta(seconds=secs)
            t = t[:m.start()] + " " + t[m.end():]
            return when, _clean(t)

    day = None
    default_hour = None
    for word, offset, hour in (("tomorrow morning", 1, 9), ("tomorrow afternoon", 1, 15),
                               ("tomorrow evening", 1, 18), ("tomorrow night", 1, 20),
                               ("tomorrow", 1, 9), ("tonight", 0, 20), ("this evening", 0, 18),
                               ("this afternoon", 0, 15), ("this morning", 0, 9), ("today", 0, None)):
        if re.search(r"\b" + word + r"\b", t):
            day = now.date() + datetime.timedelta(days=offset)
            default_hour = hour
            t = re.sub(r"\b" + word + r"\b", " ", t, count=1)
            break
    if day is None:
        m = re.search(r"\b(?:on\s+)?(?:next\s+)?(" + "|".join(WEEKDAYS) + r")\b", t)
        if m:
            target = WEEKDAYS.index(m.group(1))
            ahead = (target - now.weekday()) % 7 or 7
            day = now.date() + datetime.timedelta(days=ahead)
            default_hour = 9
            t = t[:m.start()] + " " + t[m.end():]

    hour = minute = None
    m = re.search(r"\b(?:at\s+)?(noon|midday|midnight)\b", t)
    if m:
        hour, minute = (0 if m.group(1) == "midnight" else 12), 0
        t = t[:m.start()] + " " + t[m.end():]
    else:
        m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t) or \
            re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t) or \
            re.search(r"\b(\d{1,2}):(\d{2})()\b", t)
        if m:
            hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm and hour < 12:
                evening = default_hour is not None and default_hour >= 15
                if evening or hour < 7:
                    hour += 12
            t = t[:m.start()] + " " + t[m.end():]

    if hour is None and default_hour is None:
        return None, _clean(t)
    if hour is None:
        hour, minute = default_hour, 0
    base = day or now.date()
    when = datetime.datetime.combine(base, datetime.time(hour % 24, minute))
    if day is None and when <= now:
        if hour < 12 and when + datetime.timedelta(hours=12) > now:
            when += datetime.timedelta(hours=12)
        else:
            when += datetime.timedelta(days=1)
    return when, _clean(t)


def _clean(t):
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    return re.sub(r"\b(at|on|for|by)$", "", t).strip(" ,.")
