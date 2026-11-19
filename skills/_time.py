"""Helpers shared by skills (files starting with _ aren't skills themselves)."""
import datetime


def when(text):
    """'in 20 minutes', 'tomorrow at 3 pm' or '2026-11-20T15:00' -> datetime, or None."""
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(str(text).strip().replace("Z", ""))
    except ValueError:
        pass
    import timeparse
    found, _ = timeparse.parse_when(str(text))
    if not found:
        found, _ = timeparse.parse_when("at " + str(text))
    return found


WHEN_HELP = "natural time like 'in 20 minutes', 'tomorrow at 3 pm', or 'YYYY-MM-DDTHH:MM'"
