"""Keeps Jarvis from filling the disk.

Video work is big: a two-hour VOD's audio alone is about 200 MB, and half-finished downloads and
renders pile up if a job is interrupted. This clears what is safe to clear, only inside Jarvis's own
folder, and never touches anything you haven't dealt with yet.

What gets cleared (clean()):
    sources    downloaded videos and audio of finished projects. Normally keep_sources_days after the
               project finishes (keep_failed_days for failed ones). "Clean up now", or a nearly full
               disk, clears them straight away, EXCEPT for projects with clips you still have to
               review: those keep their video until keep_sources_days, so you can still edit them.
    temp       half-finished downloads and renders (.part, .ytdl, ...) of projects that aren't running.
    rejected   the video files of clips you rejected (or that failed the quality check), after
               keep_rejected_days (1 day on "clean up now"). The clip's details stay in the database.
    backups    old copies of Jarvis's Python setup that Upgrade Jarvis leaves behind (.venv-old-*),
               once they are 2 days old and the current setup is there.
    logs       a log file over 20 MB is trimmed to its last 2 MB.

Never cleared: clips waiting for your review, approved, ready, uploading or on YouTube; your own
video files (dropped or picked from a folder); projects that are running; models, memory, settings.

When it runs: every 6 hours, and as soon as free space drops near min_free_gb (then using the
"clean up now" rules), so the clipper can keep going. You can run it from the dashboard's Disk space
card or by saying "Jarvis, free up some disk space"."""
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from clipper import ROOT, acquire, db, home, media, settings

GB = 1 << 30
TEMP_SUFFIXES = (".part", ".ytdl", ".part.mp4", ".part.wav", ".fix.mp4", ".tmp", ".temp")
KEEP_NOTES = (".json", ".log", ".jpg")                     # small notes and pictures stay with a project
PROTECTED_CLIPS = ("rendered", "awaiting_approval", "approved", "ready", "uploading", "uploaded",
                   "scheduled", "published", "upload_failed")
GONE_CLIPS = ("rejected", "qa_failed", "regenerate", "deleted")
LOG_MAX = 20 << 20
LOG_KEEP = 2 << 20
_lock = threading.Lock()
_cache = {"t": 0, "v": None}


def free_gb():
    return acquire.free_gb(home() if home().exists() else ROOT)


def low():
    """True when free space is near the clipper's minimum (cleaning then uses the "now" rules)."""
    return free_gb() < float(settings.general().get("min_free_gb", 4)) + 1.0


def _size(p):
    p = Path(p)
    try:
        if p.is_symlink():
            return 0
        if p.is_file():
            return p.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, _, files in os.walk(str(p)):
        for f in files:
            try:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _inside(path, folder):
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except (ValueError, OSError):
        return False


def _protected_files():
    """Files that belong to clips you haven't finished with, or to your own videos."""
    keep = set()
    for c in db.q("SELECT path, thumb, state, youtube_id FROM clips"):
        if c["state"] in PROTECTED_CLIPS or c.get("youtube_id"):
            for p in (c.get("path"), c.get("thumb")):
                if p:
                    keep.add(os.path.realpath(p))
    for v in db.q("SELECT path FROM videos WHERE path IS NOT NULL AND path != ''"):
        keep.add(os.path.realpath(v["path"]))
    return keep


def _job_plan(now, urgent, protected):
    """[(path, bytes, category, why)] for the job folders."""
    g = settings.general()
    days = float(g.get("keep_sources_days", 3))
    fdays = float(g.get("keep_failed_days", 14))
    out = []
    kept_review = 0
    jobs_dir = home() / "jobs"
    if not jobs_dir.exists():
        return out, kept_review
    rows = {j["id"]: j for j in db.q("SELECT id, channel, state, finished, updated FROM jobs")}
    for folder in sorted(jobs_dir.iterdir()):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        j = rows.get(int(folder.name))
        if j and (j["state"] in db.ACTIVE_STATES or j["state"] in ("uploading",) or
                  (j["state"] == "queued" and not j.get("finished"))):
            continue                                       # running (or about to): hands off
        if j and not settings.allowed(j["channel"], "delete_files"):
            continue
        src = (media.load(folder / "source.json") or {})
        own = src.get("video") if src.get("mode") == "full" and not src.get("downloaded") else None
        try:
            finished = float((j or {}).get("finished") or (j or {}).get("updated") or folder.stat().st_mtime)
        except OSError:
            continue
        age_days = (now - finished) / 86400 if finished else 999
        waiting = j and db.one("SELECT COUNT(*) n FROM clips WHERE job_id=? AND state IN ('awaiting_approval','rendered')",
                               j["id"])["n"]
        if not j:
            ok_sources = age_days >= fdays                 # a folder the database doesn't know: wait a while
        elif j["state"] in ("failed", "cancelled"):
            ok_sources = urgent or age_days >= fdays
        elif waiting:
            ok_sources = age_days >= days                  # you can still edit those clips until then
        else:
            ok_sources = urgent or age_days >= days
        for f in folder.iterdir():
            if f.is_dir() or f.is_symlink():
                continue
            real = os.path.realpath(f)
            if real in protected or (own and real == os.path.realpath(own)):
                continue
            name = f.name.lower()
            try:
                st = f.stat()
            except OSError:
                continue
            size = st.st_size
            if now - st.st_mtime < 3600:
                continue                                   # touched in the last hour: maybe in use right now
            if name.endswith(TEMP_SUFFIXES):
                out.append((f, size, "temp", "half-finished download or render"))
            elif name.endswith(KEEP_NOTES):
                continue
            elif ok_sources:
                out.append((f, size, "sources", f"project {folder.name}'s downloaded video/audio"))
        if j and not ok_sources and waiting and size_of_media(folder, protected, own) > 0:
            kept_review += 1
    return out, kept_review


def size_of_media(folder, protected, own):
    total = 0
    for f in Path(folder).iterdir():
        if f.is_file() and not f.name.lower().endswith(KEEP_NOTES + TEMP_SUFFIXES):
            real = os.path.realpath(f)
            if real not in protected and real != (os.path.realpath(own) if own else None):
                total += f.stat().st_size
    return total


def _clip_plan(now, urgent, protected):
    days = 1.0 if urgent else float(settings.general().get("keep_rejected_days", 7))
    out = []
    for c in db.q("SELECT id, path, thumb, state, youtube_id, updated, channel FROM clips WHERE state IN "
                  f"({','.join('?' * len(GONE_CLIPS))})", *GONE_CLIPS):
        if c.get("youtube_id") or not settings.allowed(c["channel"], "delete_files"):
            continue
        if (now - float(c.get("updated") or now)) < days * 86400 and c["state"] != "deleted":
            continue
        for p in (c.get("path"), c.get("thumb")):
            if p and os.path.isfile(p) and os.path.realpath(p) not in protected and _inside(p, home()):
                out.append((Path(p), _size(p), "rejected", f"clip {c['id']} ({c['state'].replace('_', ' ')})"))
    return out


def _upload_temp(now, urgent):
    out = []
    up = home() / "uploads"
    if up.exists():
        for f in up.iterdir():
            if f.is_file() and f.name.endswith(TEMP_SUFFIXES):
                try:
                    if urgent or now - f.stat().st_mtime > 86400:
                        out.append((f, f.stat().st_size, "temp", "half-finished upload"))
                except OSError:
                    pass
    return out


def _backups(now):
    out = []
    if not (ROOT / ".venv").is_dir():
        return out
    running = os.path.realpath(sys.prefix)
    for d in ROOT.glob(".venv-old-*"):
        if not d.is_dir() or d.is_symlink() or _inside(running, d):
            continue
        try:
            age = now - d.stat().st_mtime
        except OSError:
            continue
        m = re.search(r"(\d{8})(\d{6})?$", d.name)
        if m:
            try:
                age = max(age, now - time.mktime(time.strptime(m.group(1), "%Y%m%d")))
            except ValueError:
                pass
        if age >= 2 * 86400:
            out.append((d, _size(d), "backups", "old copy of Jarvis's Python setup"))
    return out


def _logs():
    out = []
    files = set(ROOT.glob("*.log")) | (set(home().glob("*.log")) if home().exists() else set())
    for f in files:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size > LOG_MAX:
            out.append((f, size - LOG_KEEP, "logs", "trimmed to its last 2 MB"))
    return out


def plan(urgent=False, now=None):
    """Everything clean() would clear right now, without clearing anything."""
    now = now or time.time()
    protected = _protected_files()
    jobs_part, kept = _job_plan(now, urgent, protected)
    items = jobs_part + _clip_plan(now, urgent, protected) + _upload_temp(now, urgent) + _backups(now) + _logs()
    return items, kept


def clean(urgent=False, by="schedule", now=None):
    """Clears what's safe (see the top of this file). Returns a summary dict."""
    now = now or time.time()
    if not _lock.acquire(blocking=False):
        return {"freed": 0, "busy": True, "by": by, "t": now, "parts": {}, "kept_review": 0, "free_gb": free_gb()}
    try:
        items, kept = plan(urgent, now)
        freed, parts = 0, {}
        for path, size, cat, _why in items:
            if not _safe(path):
                continue
            try:
                if cat == "logs":
                    _trim(path)
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                continue
            freed += size
            parts[cat] = parts.get(cat, 0) + size
        summary = {"t": now, "by": by, "urgent": bool(urgent), "freed": freed, "parts": parts,
                   "kept_review": kept, "free_gb": round(free_gb(), 2)}
        db.put("disk_last_clean", summary)
        db.put("disk_freed_total", float(db.get("disk_freed_total", 0) or 0) + freed)
        _cache["v"] = None                                 # the dashboard measures again
        if freed > 100 << 20 or by != "schedule":
            db.log(f"Disk clean-up: cleared {freed / GB:.1f} GB ({_parts_words(parts)}). "
                   f"{summary['free_gb']:.1f} GB free now.", tell=freed > 500 << 20 and by != "voice")
        return summary
    finally:
        _lock.release()


def _safe(path):
    """Only ever inside Jarvis's own folder (or the clipper's data folder)."""
    path = Path(path)
    if path.is_symlink():
        return False
    return _inside(path, home()) or _inside(path, ROOT)


def _trim(path):
    with open(path, "rb+") as f:
        f.seek(-LOG_KEEP, os.SEEK_END)
        tail = f.read()
        nl = tail.find(b"\n")
        tail = tail[nl + 1:] if 0 <= nl < 4096 else tail
        f.seek(0)
        f.write(b"(older lines trimmed to save disk space)\n" + tail)
        f.truncate()


WORDS = {"sources": "old downloaded videos", "temp": "half-finished files", "rejected": "rejected clips",
         "backups": "old setup backups", "logs": "big logs"}


def _parts_words(parts):
    bits = [f"{WORDS.get(k, k)} {v / GB:.1f} GB" if v >= 0.1 * GB else f"{WORDS.get(k, k)} {v / (1 << 20):.0f} MB"
            for k, v in sorted(parts.items(), key=lambda kv: -kv[1]) if v]
    return ", ".join(bits) or "nothing needed clearing"


def maybe_clean(now=None):
    """Called by the clipper's scheduler every minute: the 6-hourly clean-up, and an urgent one (at
    most every 30 minutes) when the disk is nearly full."""
    now = now or time.time()
    if low():
        if now - float(db.get("disk_urgent_at", 0) or 0) > 1800:
            db.put("disk_urgent_at", now)
            r = clean(urgent=True, by="low_disk", now=now)
            if low():
                _warn_low()
            return r
        return None
    if now - float(db.get("cleanup_at", 0) or 0) > 6 * 3600:
        db.put("cleanup_at", now)
        return clean(by="schedule", now=now)
    return None


def _warn_low():
    k = "warned_disk_low"
    if time.time() - float(db.get(k, 0) or 0) > 12 * 3600:
        db.put(k, time.time())
        biggest = sorted(report(fresh=True)["parts"], key=lambda p: -p["gb"])[:2]
        names = " and ".join(f"{p['label'].lower()} ({p['gb']:.1f} GB)" for p in biggest if p["gb"] >= 0.1)
        db.log(f"Your disk is still almost full ({free_gb():.1f} GB free) after I cleared everything that's safe. "
               + (f"The biggest things in my folder are {names}. " if names else "")
               + "Clips waiting for your review are kept, so reviewing or deleting some helps, or free space elsewhere "
                 "on the Mac.", "warn", tell=True)


def report(fresh=False):
    """For the dashboard: free space, what's in Jarvis's folder, and what could be cleared now.
    Adding up Jarvis's folder takes a second or two, so the dashboard gets the last answer (at most
    5 minutes old) and a fresh one is worked out in the background."""
    if not fresh and _cache["v"]:
        if time.time() - _cache["t"] > 300 and not _cache.get("busy"):
            _cache["busy"] = True
            threading.Thread(target=_refresh, daemon=True, name="clipper-disk").start()
        return _cache["v"]
    return _measure()


def _refresh():
    try:
        _measure()
    except Exception:
        pass
    finally:
        _cache["busy"] = False


def _measure():
    g = settings.general()
    try:
        du = shutil.disk_usage(str(home() if home().exists() else ROOT))
        total, free = du.total / GB, du.free / GB
    except Exception:
        total, free = 0.0, 0.0
    lib = _size(home() / "library")
    jobs = _size(home() / "jobs")
    models = _size(ROOT / "models") + _size(home() / "models")
    venv = _size(ROOT / ".venv")
    backups = sum(_size(d) for d in ROOT.glob(".venv-old-*") if d.is_dir())
    everything = _size(ROOT) + (0 if _inside(home(), ROOT) else _size(home()))
    other = max(0, everything - lib - jobs - models - venv - backups)
    items, kept = plan(urgent=True)
    can = sum(s for _p, s, _c, _w in items)
    soon = {}
    for _p, s, c, _w in items:
        soon[c] = soon.get(c, 0) + s
    review = db.one("SELECT COUNT(*) n FROM clips WHERE state IN ('awaiting_approval','rendered')")["n"]
    kept_clips = db.one(f"SELECT COUNT(*) n FROM clips WHERE state IN ({','.join('?' * len(PROTECTED_CLIPS))})",
                        *PROTECTED_CLIPS)["n"]
    v = {"free_gb": round(free, 2), "total_gb": round(total, 1), "min_free_gb": float(g.get("min_free_gb", 4)),
         "low": free < float(g.get("min_free_gb", 4)) + 1.0, "jarvis_gb": round(everything / GB, 2),
         "parts": [{"key": k, "label": l, "gb": round(b / GB, 2)} for k, l, b in (
             ("library", "Your clips", lib), ("jobs", "Project working files", jobs),
             ("models", "Speech and voice models", models), ("venv", "Jarvis's Python setup", venv),
             ("backups", "Old setup backups", backups), ("other", "Everything else", other)) if b > 0],
         "can_free_gb": round(can / GB, 2), "can_free": {k: round(b / GB, 2) for k, b in soon.items()},
         "kept_review_projects": kept, "review_clips": review, "kept_clips": kept_clips,
         "last": db.get("disk_last_clean"), "freed_total_gb": round(float(db.get("disk_freed_total", 0) or 0) / GB, 2),
         "settings": {k: g.get(k) for k in ("keep_sources_days", "keep_failed_days", "keep_rejected_days", "min_free_gb")}}
    _cache.update(t=time.time(), v=v)
    return v


def say():
    """One or two sentences about disk space, for Jarvis to say."""
    r = report(fresh=True)
    s = f"You've got {r['free_gb']:.1f} GB free"
    s += f" out of {r['total_gb']:.0f}." if r["total_gb"] else "."
    s += f" My folder uses {r['jarvis_gb']:.1f} GB."
    if r["can_free_gb"] >= 0.05:
        s += f" I can safely clear about {r['can_free_gb']:.1f} GB. Say free up disk space and I'll do it."
    else:
        s += " There's nothing old left for me to clear."
    if r["low"]:
        s += " That's below what the clipper needs, so new clipping waits until there's more room."
    return s


def say_clean(summary):
    if summary.get("busy"):
        return "I'm already cleaning up. Give me a moment."
    freed = summary["freed"]
    if freed < 20 << 20:
        s = f"Nothing needed clearing. You've got {summary['free_gb']:.1f} GB free."
    else:
        amount = f"{freed / GB:.1f} GB" if freed >= 0.1 * GB else f"{freed / (1 << 20):.0f} MB"
        s = f"Done. I cleared {amount}: {_parts_words(summary['parts'])}. You've got {summary['free_gb']:.1f} GB free now."
    if summary.get("kept_review"):
        n = summary["kept_review"]
        s += (f" I kept the videos for {n} project{'s' if n != 1 else ''} with clips you haven't reviewed yet, "
              "so you can still edit them.")
    return s
