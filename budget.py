"""One shared daily limit for the Claude calls Jarvis makes on his own.

Jarvis's brain is Connor's Claude plan, and that plan's usage is shared by everything: answering
Connor, the Shorts clipper, the YouTube studio, the AI team, night school, routines and self-repair.
Only the studio had its own cap. This keeps the background work together under one daily number
(config.json "claude_background_per_day", default 150; 0 means no limit), so it can never use up the
plan and leave Jarvis unable to answer Connor. Answering Connor is never counted or blocked.

When any Claude call says the plan's limit has been reached, background work also waits an hour
(and answering Connor goes on as normal), instead of every part retrying into the same wall."""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PER_DAY = 150
COOL_DOWN = 3600
LIMIT = re.compile(r"usage limit|rate.?limit|too many requests|limit will reset|limit reached|credit balance", re.I)
_lock = threading.Lock()


def _file():
    return Path(os.environ.get("JARVIS_BUDGET_FILE") or HERE / ".claude-budget.json")


def _today():
    return time.strftime("%Y-%m-%d")


def per_day():
    try:
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        return int(cfg.get("claude_background_per_day", DEFAULT_PER_DAY))
    except (OSError, ValueError, TypeError):
        return DEFAULT_PER_DAY


def _load():
    try:
        st = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("day") != _today():
        st = {"day": _today(), "used": 0, "by": {}, "paused_until": st.get("paused_until", 0)}
    return st


def _save(st):
    try:
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.replace(tmp, _file())
    except OSError:
        pass


def check(who="background"):
    """(ok, reason). ok is False when today's background calls are used up or the plan said
    it's at its limit in the last hour."""
    with _lock:
        st = _load()
    if time.time() < st.get("paused_until", 0):
        mins = int((st["paused_until"] - time.time()) / 60) + 1
        return False, f"Claude's plan limit was reached, so background work waits about {mins} more minutes"
    cap = per_day()
    if cap > 0 and st.get("used", 0) >= cap:
        return False, f"background work has used today's {cap} Claude calls (the rest are kept for answering you)"
    return True, ""


def spend(who="background"):
    with _lock:
        st = _load()
        st["used"] = st.get("used", 0) + 1
        st.setdefault("by", {})[who] = st["by"].get(who, 0) + 1
        _save(st)


def take(who="background"):
    """Checks and, if allowed, counts one call. (ok, reason)."""
    ok, why = check(who)
    if ok:
        spend(who)
    return ok, why


def saw(text):
    """Call with the output of any failed Claude call: a plan-limit message pauses background work."""
    if text and LIMIT.search(text):
        with _lock:
            st = _load()
            st["paused_until"] = max(st.get("paused_until", 0), time.time() + COOL_DOWN)
            _save(st)
        return True
    return False


def paused_until():
    return _load().get("paused_until", 0)


def today():
    """For "how much Claude have you used": {"used": n, "cap": n, "by": {...}}."""
    st = _load()
    return {"used": st.get("used", 0), "cap": per_day(), "by": st.get("by", {})}


def refused(cmd, why):
    """A stand-in for a Claude call that wasn't made, shaped like a failed one, so every caller's
    usual 'that didn't work, try later' handling applies."""
    return subprocess.CompletedProcess(list(cmd), 1, "", f"(not run: {why})")
