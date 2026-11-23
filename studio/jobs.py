"""Persistent production jobs and the worker.

A job is one Short (a row in `videos`). Its state is the stage it is in; each stage has a handler
registered by the phase that builds it (register()). The worker thread takes one job at a time:

  claim      the oldest job whose stage has a handler, that isn't waiting for a retry and isn't
             owned by a live worker. Claiming is one transaction, so a job never runs twice.
  heartbeat  a running stage calls ctx.beat(); a job whose heartbeat stops for stall_minutes (a
             crash, a hang, a restart) is taken over and resumed from its last finished stage.
  errors     every failure is classified: transient (retry with doubling waits), quota (wait for
             the reset), auth (stop and ask Connor), resource (wait for disk/memory), permanent
             (fail now, never retried). Each is logged with what was tried and what's left.
  stop       pause() finishes nothing new; shutdown() asks the running stage to stop at its next
             checkpoint and leaves the job where it was, so it carries on after a restart.
"""
import os
import socket
import threading
import time
import urllib.error

from studio import config, db, guard, health

STAGES = {}            # state -> handler(video, ctx) returning the next state
_thread = None
_stop = threading.Event()
_running = threading.Event()
_ident = f"{os.getpid()}"


class StudioError(Exception):
    kind = "transient"


class Transient(StudioError):
    kind = "transient"


class Permanent(StudioError):
    kind = "permanent"


class QuotaWait(StudioError):
    kind = "quota"

    def __init__(self, msg, until=None):
        super().__init__(msg)
        self.until = until


class AuthProblem(StudioError):
    kind = "auth"


class ResourceShort(StudioError):
    kind = "resource"


class Interrupted(StudioError):
    kind = "interrupted"


class Wait(StudioError):
    """Not a problem: the job waits until a time (e.g. its publishing slot). Nothing is logged as an error."""
    kind = "wait"

    def __init__(self, msg, until):
        super().__init__(msg)
        self.until = until


def register(state, handler):
    if state not in db.WORK_STATES:
        raise ValueError(f"{state} isn't a production stage")
    STAGES[state] = handler


def classify(err):
    """The kind of failure, so it's retried (or not) the right way."""
    if isinstance(err, StudioError):
        return err.kind
    if isinstance(err, guard.Forbidden):
        return "permanent"
    name = type(err).__name__
    if name == "QuotaExceeded":
        return "quota"
    if name == "NotConnected" or "invalid_grant" in str(err) or getattr(err, "status", None) in (401,):
        return "auth"
    if isinstance(err, OSError) and getattr(err, "errno", None) == 28:     # no space left on device
        return "resource"
    if isinstance(err, MemoryError):
        return "resource"
    if isinstance(err, (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout)):
        return "transient"
    if getattr(err, "status", None) and int(err.status) >= 500:
        return "transient"
    if isinstance(err, (ValueError, KeyError, TypeError, AttributeError)):
        return "permanent"             # a bug or bad data: retrying the same thing won't help
    return "transient"


def backoff(attempt):
    g = config.general()
    return min(g["retry_base_seconds"] * (2 ** max(attempt - 1, 0)), 6 * 3600)


def create(topic_id=None, idem_key=None, title=""):
    """A new job in the first stage. The same idem_key never makes a second job."""
    key = idem_key or (f"topic-{topic_id}" if topic_id else None)
    if key:
        old = db.one("SELECT id FROM videos WHERE idem_key=?", key)
        if old:
            return old["id"]
    now = time.time()
    return db.insert("INSERT INTO videos(topic_id, idem_key, state, title, created, updated) VALUES(?,?,?,?,?,?)",
                     topic_id, key, db.STATES[0], title, now, now)


class Context:
    def __init__(self, vid):
        self.vid = vid

    def beat(self, note=None):
        """Heartbeat (call often). Raises Interrupted when Jarvis is shutting down."""
        db.run("UPDATE videos SET heartbeat=? WHERE id=?", time.time(), self.vid)
        if _stop.is_set():
            raise Interrupted("Jarvis is stopping; I'll carry on after the restart")

    def stopping(self):
        return _stop.is_set()


def claim(now=None):
    """The next job to work on (its row), marked as ours, or None."""
    if not STAGES or paused():
        return None
    now = now or time.time()
    stall = 60 * config.general()["stall_minutes"]
    states = list(STAGES)
    with db.transaction() as c:
        r = c.execute(f"SELECT * FROM videos WHERE state IN ({','.join('?' * len(states))}) AND not_before<=? "
                      "AND (owner IS NULL OR heartbeat<?) ORDER BY id LIMIT 1", (*states, now, now - stall)).fetchone()
        if not r:
            return None
        if r["owner"]:
            c.execute("INSERT INTO recovery_events(t, what, video_id, detail, result) VALUES(?,?,?,?,?)",
                      (now, "stalled stage", r["id"], f"{r['state']} had no heartbeat", "taken over"))
        c.execute("UPDATE videos SET owner=?, heartbeat=?, updated=? WHERE id=?", (_ident, now, now, r["id"]))
    return dict(db.video(r["id"]))


def run_one(v):
    """Runs one stage of a claimed job and records the result. Returns the job's new state."""
    stage = v["state"]
    handler = STAGES.get(stage)
    try:
        if not handler:
            raise Permanent(f"no handler for {stage}")
        nxt = handler(v, Context(v["id"]))
        if nxt not in db.STATES:
            raise Permanent(f"stage {stage} returned an unknown state {nxt!r}")
        db.set_video(v["id"], state=nxt, stage_done=stage, attempts=0, owner=None, error=None, error_kind=None,
                     not_before=0, finished=time.time() if nxt in db.FINAL else None)
        return nxt
    except Exception as e:
        return _failed(v, stage, e)


def _failed(v, stage, err):
    kind = classify(err)
    msg = guard.redact(f"{type(err).__name__}: {err}")[:800]
    if kind == "wait":
        db.set_video(v["id"], state=stage, owner=None, error=None, error_kind=None,
                     not_before=max(getattr(err, "until", 0) or 0, time.time() + 60), meta=_note(v, str(err)))
        return stage
    attempts = int(v["attempts"] or 0) + (0 if kind in ("interrupted", "quota", "resource") else 1)
    g = config.general()
    state, wait, recovery, remaining = stage, 0, "", ""
    if kind == "interrupted":
        recovery, remaining = "left in place", "resumes after restart"
    elif kind == "quota":
        wait = max((getattr(err, "until", None) or time.time() + 3600) - time.time(), 600)
        recovery, remaining = "waiting for the quota to reset", "retry after reset"
    elif kind == "resource":
        wait, recovery, remaining = 900, "waiting for disk or memory", "retry when resources free up"
    elif kind == "auth":
        wait, recovery, remaining = 6 * 3600, "paused", "Connor needs to reconnect YouTube"
        health.notify("auth", "The YouTube business channel's sign-in stopped working. Double-click "
                              "Connect YouTube Business to sign in again.", "error")
    elif kind == "permanent" or attempts >= g["max_attempts"]:
        state, recovery, remaining = "failed", "gave up", "none (job failed)"
        health.notify(f"failed-{stage}", f"A Short failed at {stage.replace('_', ' ')}: {msg[:160]}", "warn")
    else:
        wait, recovery, remaining = backoff(attempts), f"retry {attempts + 1} of {g['max_attempts']}", "retry"
    db.set_video(v["id"], state=state, attempts=attempts, owner=None, error=msg, error_kind=kind,
                 not_before=time.time() + wait, finished=time.time() if state == "failed" else None)
    db.insert("INSERT INTO errors(t, kind, video_id, stage, detail, recovery, result, remaining) VALUES(?,?,?,?,?,?,?,?)",
              time.time(), kind, v["id"], stage, msg, recovery, state, remaining)
    return state


def _note(v, msg):
    import json
    m = json.loads(db.video(v["id"])["meta"] or "{}")
    m["waiting"] = msg
    return json.dumps(m)


def recover():
    """At start-up: jobs owned by a worker that no longer exists are released to resume."""
    rows = db.q("SELECT id, state FROM videos WHERE owner IS NOT NULL AND owner!=?", _ident)
    for r in rows:
        db.set_video(r["id"], owner=None)
        health.recovery("restart", f"job {r['id']} was in {r['state']}", "resumes from its last finished stage", r["id"])
    return len(rows)


def pause():
    db.put("paused", True)


def resume():
    db.put("paused", False)


def paused():
    return bool(db.get("paused", False))


def busy():
    return _running.is_set()


IDLE = []           # functions run when there's no job (e.g. topic discovery); each must be cheap to skip


def tick(busy=lambda: False):
    """One pass of the worker. Returns the new state of the job it worked on, or None."""
    if not config.enabled() or busy():
        return None
    ok, why = health.ok_to_work()
    if not ok:
        if "disk" in why or "studio folder" in why:
            health.notify("disk", f"I've paused the YouTube business because {why}.")
            _chore(_drive_urgent)                    # Shorts safe on Google Drive can make room
        return None
    v = claim()
    if not v:
        for f in IDLE:                               # background chores when no Short is being made
            _chore(f)
        return None
    _running.set()
    try:
        from studio import autopilot
        with autopilot.keep_awake():
            return run_one(v)
    finally:
        _running.clear()


def _drive_urgent():
    from studio import drive
    drive.maybe_run(urgent=True)


def _chore(f, name=None):
    try:
        f()
    except Exception as e:
        db.insert("INSERT INTO errors(t, kind, stage, detail, recovery, result) VALUES(?,?,?,?,?,?)",
                  time.time(), classify(e), name or getattr(f, "__name__", "idle"), guard.redact(str(e))[:800],
                  "will try again later", "")


def _loop(busy):
    while not _stop.is_set():
        try:
            tick(busy)
        except Exception as e:                       # never let the worker die
            try:
                db.insert("INSERT INTO errors(t, kind, stage, detail, recovery, result) VALUES(?,?,?,?,?,?)",
                          time.time(), classify(e), "worker", guard.redact(str(e))[:800], "worker kept running", "")
            except Exception:
                pass
        _stop.wait(config.general()["tick_seconds"])


def start(busy=lambda: False):
    """Start-up checks, recovery, then the worker thread. Safe to call more than once."""
    global _thread
    if _thread and _thread.is_alive():
        return False
    report = health.startup()
    recover()
    from studio import analytics, autopilot, drive, eligibility, pipeline, playlists, topics
    pipeline.register()
    for f in (topics.expire, topics.maybe_discover, analytics.maybe_run, eligibility.maybe_check, drive.maybe_run,
              autopilot.maybe_make, playlists.maybe_sync):
        if f not in IDLE:
            IDLE.append(f)
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(busy,), daemon=True, name="studio-worker")
    _thread.start()
    return report


def shutdown(wait=10):
    """Asks the running stage to stop at its next checkpoint and waits for the worker."""
    _stop.set()
    if _thread:
        _thread.join(wait)
    guard.audit("module.stop", True, "")
    return not (_thread and _thread.is_alive())
