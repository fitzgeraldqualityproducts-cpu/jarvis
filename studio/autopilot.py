"""Always on (Phase 11): keeps the production line stocked, one Short at a time.

maybe_make() (a background chore) starts a new Short when nothing is being made, fewer finished
Shorts are waiting than the buffer (publish mode manual: 1, otherwise "ready_buffer"), today's limit
isn't reached, and the autopilot isn't halted. Quality first: after "stop_after_failures" Shorts in a
row fail, it halts and tells Connor instead of spending his Claude plan on more of the same.
Everything it does is recorded (decisions / recovery_events) and undoable: `python -m studio autopilot off`.

keep_awake() is used by the worker on macOS: `caffeinate -i` while a stage runs, so the Mac doesn't idle
to sleep in the middle of a render or an upload (it can still sleep when the lid closes)."""
import datetime
import json
import subprocess
import sys
import time

from studio import config, db, guard, health

MAKING = ("researching", "scripting", "fact_checking", "generating_audio", "rendering", "validating")


def _day(now):
    return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def halted():
    return db.get("autopilot_halted") or None


def halt(reason):
    db.put("autopilot_halted", {"t": time.time(), "reason": reason})
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", time.time(), "autopilot",
              "paused making new Shorts", json.dumps({"reason": reason}))
    health.notify("autopilot", f"I've stopped making new YouTube Shorts for now: {reason}. "
                               "Say \"YouTube business status\" for details.", "warn")


def resume():
    db.put("autopilot_halted", None)
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", time.time(), "autopilot",
              "resumed making new Shorts", "{}")


def failure_streak():
    """How many of the most recent finished auto Shorts failed in a row."""
    n = 0
    for r in db.q("SELECT state FROM videos WHERE idem_key LIKE 'auto-%' AND state IN ('failed','published','ready',"
                  "'uploading','processing','scheduled') ORDER BY id DESC LIMIT 20"):
        if r["state"] != "failed":
            break
        n += 1
    return n


def status(now=None):
    from studio import publish
    now = now or time.time()
    g = config.general()
    m = publish.mode()
    return {"on": bool(g["autopilot"]), "halted": halted(), "mode": m,
            "making": db.one(f"SELECT COUNT(*) n FROM videos WHERE state IN ({','.join('?' * len(MAKING))})", *MAKING)["n"],
            "ready": db.one("SELECT COUNT(*) n FROM videos WHERE state='ready'")["n"],
            "buffer": g["manual_ready_buffer"] if m == "manual" else g["ready_buffer"],
            "started_today": int(db.get(f"autopilot_made_{_day(now)}", 0) or 0), "max_per_day": g["max_new_per_day"],
            "failure_streak": failure_streak()}


def maybe_make(now=None):
    """Starts one new Short if the line needs one. Returns its job id, or None."""
    from studio import jobs, topics
    now = now or time.time()
    g = config.general()
    if not config.enabled() or not g["autopilot"] or halted() or jobs.paused():
        return None
    if not guard.locked_channel() or not topics.niche():
        return None
    st = status(now)
    if st["failure_streak"] >= g["stop_after_failures"]:
        halt(f"the last {st['failure_streak']} Shorts failed their checks")
        return None
    if st["making"] or st["ready"] >= st["buffer"] or st["started_today"] >= st["max_per_day"]:
        return None
    if not topics.pool(now):
        return None                                      # discovery tops the pool up first
    n = st["started_today"] + 1
    db.put(f"autopilot_made_{_day(now)}", n)
    vid = jobs.create(idem_key=f"auto-{_day(now)}-{n}")
    health.recovery("autopilot", f"started Short {vid}", "queued", vid)
    return vid


class keep_awake:
    """with keep_awake(): ... -> macOS won't idle-sleep inside the block (no-op elsewhere or if off)."""

    def __enter__(self):
        self.p = None
        if sys.platform == "darwin" and config.general().get("keep_awake", True):
            try:
                self.p = subprocess.Popen(["caffeinate", "-i"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.p = None
        return self

    def __exit__(self, *exc):
        if self.p:
            try:
                self.p.terminate()
                self.p.wait(5)
            except Exception:
                pass
        return False
