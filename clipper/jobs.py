"""The clipper's engine room: a persistent job queue, a worker, and a scheduler.

  worker     takes the best queued job (priority, then oldest), runs it in its own Python process
             (pipeline.py) at low priority, watches its heartbeat, kills it if it hangs, retries
             recoverable failures with growing waits (max_attempts), and never runs more jobs at
             once than max_parallel_jobs.
  scheduler  every minute: discovery (every few hours), uploads that are due, YouTube status checks,
             analytics (daily), disk clean-up.
  recovery   at start-up, jobs that were running when Jarvis stopped go back in the queue and carry
             on from their last finished stage; half-done uploads resume where they stopped.
  limits     disk space (min free / max size), one transcription and render at a time, YouTube
             quota, daily upload limits.
  stops      pause (finish nothing new) and the EMERGENCY STOP (no new jobs, no uploads; running jobs
             stop safely at the next checkpoint and resume later).
"""
import os
import subprocess
import sys
import threading
import time

from clipper import ROOT, acquire, data_dir, db, settings

_threads = {}
_children = {}
_stop = threading.Event()
_lock = threading.Lock()


def start(busy=lambda: False):
    """Starts the worker and scheduler threads (inside Jarvis, or `python -m clipper run`)."""
    if _threads:
        return
    settings.ensure_default_channel()
    recover()
    for name, fn in (("clipper-worker", _worker_loop), ("clipper-scheduler", _scheduler_loop)):
        t = threading.Thread(target=fn, args=(busy,), daemon=True, name=name)
        t.start()
        _threads[name] = t
    db.log("Clipper started.")


def shutdown(wait=10):
    """Graceful stop: running jobs are told to stop at their next checkpoint."""
    _stop.set()
    for proc in list(_children.values()):
        try:
            proc.terminate()
        except Exception:
            pass
    end = time.time() + wait
    for proc in list(_children.values()):
        try:
            proc.wait(max(0.1, end - time.time()))
        except Exception:
            proc.kill()


def recover():
    """Jobs cut off by a restart go back in the queue (their finished stages are kept)."""
    stuck = db.q(f"SELECT id, state FROM jobs WHERE state IN ({','.join('?' for _ in db.ACTIVE_STATES)})", *db.ACTIVE_STATES)
    for j in stuck:
        _end_orphan((db.job(j["id"]) or {}).get("pid"))
        db.set_job(j["id"], state="queued", pid=None, detail=f"resuming after a restart (was {j['state']})")
        db.log(f"Resuming after a restart (was {j['state'].replace('_', ' ')}).", job_id=j["id"])
    # clips stuck half-made by an older version (no file, job already finished): make them again
    for j in db.q("SELECT DISTINCT c.job_id AS id FROM clips c JOIN jobs j ON j.id=c.job_id WHERE c.state='rendered' "
                  "AND (c.path IS NULL OR c.path='') AND j.state IN ('awaiting_approval','done','failed')"):
        db.set_job(j["id"], state="queued", attempts=0, not_before=0, error=None, held=0, finished=None,
                   detail="finishing clips that didn't render")
        db.log("Some clips didn't finish rendering last time; making them again.", job_id=j["id"])
    n = db.run("UPDATE uploads SET status='queued' WHERE status='uploading'")
    if n:
        db.log(f"{n} interrupted upload{'s' if n != 1 else ''} will resume.")
    return len(stuck)


def _end_orphan(pid):
    """A job process left over from before a restart: stop it so the job doesn't run twice."""
    if not pid or pid == os.getpid():
        return
    try:
        if sys.platform == "win32":
            out = subprocess.run(["wmic", "process", "where", f"ProcessId={int(pid)}", "get", "CommandLine"],
                                 capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
            if "clipper" in out:
                subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, timeout=10,
                               creationflags=0x08000000)
        else:
            out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True,
                                 timeout=10).stdout
            if "clipper" in out and " job " in out:
                os.kill(int(pid), 15)
    except Exception:
        pass


# ---------- the worker ----------

def claim():
    """The next job to run, marked as started (or None). Nothing is claimed while stopped/paused,
    or when the disk is too full."""
    if settings.stopped() or settings.paused():
        return None
    g = settings.general()
    with _lock:
        running = len([p for p in _children.values() if p.poll() is None])
        if running >= int(g.get("max_parallel_jobs", 1)):
            return None
        home = data_dir()
        if acquire.free_gb(home) < float(g.get("min_free_gb", 4)):
            from clipper import disk
            disk.maybe_clean()                             # clear what's safe first (at most every 30 min)
            if acquire.free_gb(home) < float(g.get("min_free_gb", 4)):
                _warn_once("disk", f"I've paused clipping because the disk is almost full "
                                   f"({acquire.free_gb(home):.1f} GB free, I need {g.get('min_free_gb', 4)} GB). "
                                   "I've cleared what's safe; say free up disk space to see what's left.")
                return None
        if acquire.folder_gb(home) > float(g.get("max_disk_gb", 25)):
            cleanup(force=True)
            if acquire.folder_gb(home) > float(g.get("max_disk_gb", 25)):
                _warn_once("size", f"The clipper's folder is over its {g.get('max_disk_gb')} GB limit, so I'm not "
                                   "starting new jobs. Delete old clips or raise the limit.")
                return None
        with db.transaction():
            j = db.conn().execute("SELECT id FROM jobs WHERE state='queued' AND held=0 AND not_before<=? "
                                  "ORDER BY priority DESC, id LIMIT 1", (time.time(),)).fetchone()
            if not j:
                return None
            db.conn().execute("UPDATE jobs SET state='acquiring', started=COALESCE(started, ?), heartbeat=?, "
                              "updated=? WHERE id=?", (time.time(), time.time(), time.time(), j["id"]))
        return j["id"]


def _warn_once(key, msg):
    k = f"warned_{key}"
    if time.time() - float(db.get(k, 0) or 0) > 6 * 3600:
        db.put(k, time.time())
        db.log(msg, "warn", tell=True)


def run_child(job_id):
    """Runs pipeline.run_job in a separate, low-priority Python process and waits for it."""
    env = dict(os.environ, PYTHONUTF8="1", JARVIS_CHECK="1", JARVIS_CLIPPER_PARENT=str(os.getpid()))
    kw = {"creationflags": 0x08000000 | 0x00004000} if sys.platform == "win32" else {}
    log = data_dir("jobs", str(job_id)) / "job.log"
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"\n--- {time.ctime()} ---\n")
        out.flush()
        proc = subprocess.Popen([sys.executable, "-m", "clipper", "job", str(job_id)], cwd=str(ROOT), env=env,
                                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        _children[job_id] = proc
        db.set_job(job_id, pid=proc.pid)
        limit = float(settings.general().get("stage_timeout_minutes", 240)) * 60
        try:
            while proc.poll() is None:
                time.sleep(2)
                j = db.job(job_id) or {}
                if time.time() - float(j.get("heartbeat") or time.time()) > limit:
                    proc.kill()
                    proc.wait()
                    from clipper import pipeline
                    return pipeline._retry(job_id, (j.get("attempts") or 0) + 1,
                                           RuntimeError(f"stuck in {j.get('state')} for {limit / 60:.0f} minutes"))
                if _stop.is_set():
                    proc.terminate()
        finally:
            _children.pop(job_id, None)
    code = proc.returncode
    if code not in (0, 1, 2, 3):                      # crashed hard (not a handled error)
        j = db.job(job_id)
        if j and j["state"] in db.ACTIVE_STATES:
            from clipper import pipeline
            return pipeline._retry(job_id, (j.get("attempts") or 0) + 1, RuntimeError(f"the job process crashed (code {code})"))
    return code


def _worker_loop(busy):
    while not _stop.is_set():
        try:
            jid = claim()
            if jid is None:
                _stop.wait(5)
                continue
            db.log("Starting.", job_id=jid)
            run_child(jid)
            _self_repair()
        except Exception as e:
            print(f"(clipper worker error: {e})")
            _stop.wait(15)


def _self_repair():
    """A crash in the clipper's own code goes to Jarvis's self-repair mechanic (in the background)."""
    if db.one("SELECT id FROM feedback WHERE kind='bug' AND t>?", time.time() - 600) is None:
        return
    if "clipper-repair" in _threads and _threads["clipper-repair"].is_alive():
        return
    from clipper import selftune
    t = threading.Thread(target=selftune.repair_pending, daemon=True, name="clipper-repair")
    _threads["clipper-repair"] = t
    t.start()


# ---------- the scheduler ----------

def _scheduler_loop(busy):
    _stop.wait(20)
    while not _stop.is_set():
        try:
            tick()
        except Exception as e:
            print(f"(clipper scheduler error: {e})")
        _stop.wait(60)


def tick(now=None):
    """One round of scheduled work (also called by tests)."""
    now = now or time.time()
    g = settings.general()
    for cid in settings.channels():
        last = float(db.get(f"discovered_{cid}", 0) or 0)
        if not settings.stopped() and not settings.paused() and now - last > float(g["discovery_every_hours"]) * 3600:
            db.put(f"discovered_{cid}", now)
            try:
                from clipper import sources
                sources.discover(cid)
            except Exception as e:
                db.log(f"Discovery failed: {e}", "warn")
    try:
        from clipper import publish
        publish.run_due(now)
    except Exception as e:
        db.log(f"Upload check failed: {e}", "warn")
    last = float(db.get("analytics_at", 0) or 0)
    if now - last > float(g["analytics_every_hours"]) * 3600:
        db.put("analytics_at", now)
        try:
            from clipper import publish
            publish.refresh_analytics()
        except Exception as e:
            db.log(f"Analytics refresh failed: {e}", "warn")
    try:
        from clipper import disk
        disk.maybe_clean(now)
    except Exception as e:
        db.log(f"Disk clean-up failed: {e}", "warn")


def cleanup(force=False):
    """Clears old downloads, half-finished files and rejected clips (see disk.py for the rules).
    force: the "clean up now" rules. Clips you haven't reviewed are never deleted. Returns bytes freed."""
    from clipper import disk
    return disk.clean(urgent=force, by="limit" if force else "schedule")["freed"]


# ---------- controls ----------

def cancel(job_id):
    j = db.job(job_id)
    if not j:
        return "There's no job with that number."
    if j["state"] in db.FINAL_STATES:
        return f"Job {job_id} is already {j['state']}."
    proc = _children.get(job_id)
    if proc and proc.poll() is None:
        proc.terminate()
    db.set_job(job_id, state="cancelled", finished=time.time(), pid=None)
    db.log("Cancelled.", job_id=job_id)
    return f"Cancelled job {job_id}."


def retry(job_id):
    j = db.job(job_id)
    if not j:
        return "There's no job with that number."
    waiting = j["state"] == "queued" and (j.get("error") or float(j.get("not_before") or 0) > time.time())
    if j["state"] not in ("failed", "cancelled") and not waiting:
        return f"Job {job_id} is {j['state'].replace('_', ' ')}, not failed."
    db.set_job(job_id, state="queued", attempts=0, not_before=0, error=None, held=0, finished=None)
    db.log("Retrying now." if waiting else "Retrying.", job_id=job_id)
    return f"Job {job_id} will try again now." if waiting else f"Job {job_id} is back in the queue."


def emergency_stop():
    settings.set_stopped(True)
    n = db.run("UPDATE uploads SET status='held' WHERE status IN ('queued','scheduled')")
    return ("Emergency stop. No new clipping jobs and no uploads until you say resume clipping."
            + (f" {n} queued upload{'s' if n != 1 else ''} put on hold." if n else ""))


def resume():
    was = settings.stopped() or settings.paused()
    settings.set_stopped(False)
    settings.set_paused(False)
    db.run("UPDATE uploads SET status='queued' WHERE status='held'")
    return "Clipping's back on." if was else "Clipping was already running."


def pause():
    settings.set_paused(True)
    return "Paused. Running jobs stop at their next checkpoint and pick up when you resume."


def busy():
    return any(p.poll() is None for p in _children.values())
