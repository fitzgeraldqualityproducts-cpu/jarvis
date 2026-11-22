"""Keeps the clipper's database healthy and its clips findable.

startup()   (when Jarvis starts the clipper, before any job runs) repairs a damaged clipper.db with
            dbrescue (the damaged copy is always kept), then re-links finished clip files that the
            database has lost track of.
daily()     a daily backup copy of clipper.db (clipper.db.bak), so a repair has a good copy to use.

Re-linking lost clips: every finished clip is saved as library/<channel>/<date>_job<J>_clip<C>.mp4
(plus a .jpg), and each project folder keeps its moments (candidates.json) and its log (job.log,
which names each clip as it's made). If a clip's row is missing, it's added back from those, with
its title, times, scores and titles, waiting for your review. Nothing is guessed: a clip whose
moment can't be matched gets its times from the file and a plain title, and says so."""
import json
import os
import re
import sys
import time
from pathlib import Path

from clipper import db, home, media

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dbrescue  # noqa: E402

CLIP_FILE = re.compile(r"_job(\d+)_clip(\d+)(?:_v(\d+))?\.mp4$")


def startup(log=print):
    """Repair (if needed) and re-link lost clips. Returns a list of plain-words notes."""
    notes = []
    path = db.path()
    if path.exists() and not dbrescue.check(path):
        db.reset_connections()
        msg = dbrescue.repair(path, log=log)
        db.reset_connections()
        if msg:
            notes.append(msg)
    try:
        n = relink_clips()
        if n:
            notes.append(f"found {n} finished clip{'s' if n != 1 else ''} the database had lost track of and added "
                         f"{'them' if n != 1 else 'it'} back for your review")
    except Exception as e:
        log(f"(clipper: couldn't check for lost clips: {e})")
    for n in notes:
        db.log(n[0].upper() + n[1:] + ("" if n.endswith(".") else "."), "warn", tell=True)
    return notes


def daily():
    return dbrescue.backup(db.path())


def _log_titles(folder):
    """{clip id: title} from a project's job.log ('Clip 8 ready: "Title" (...)')."""
    out = {}
    try:
        text = (Path(folder) / "job.log").read_text(errors="replace")
    except OSError:
        return out
    for m in re.finditer(r'Clip (\d+) ready: "(.*?)" \(', text):
        out[int(m.group(1))] = m.group(2)
    return out


def relink_clips():
    """Adds back clip rows for finished clip files in the library that the database doesn't know."""
    lib = home() / "library"
    if not lib.exists():
        return 0
    known = {r["id"] for r in db.q("SELECT id FROM clips")}
    files = {}
    for chdir in lib.iterdir():
        if not chdir.is_dir():
            continue
        for f in chdir.glob("*.mp4"):
            m = CLIP_FILE.search(f.name)
            if not m:
                continue
            jid, cid, ver = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
            if cid in known:
                continue
            if cid not in files or ver > files[cid][2]:
                files[cid] = (f, jid, ver, chdir.name)
    added = 0
    for cid, (f, jid, ver, channel) in sorted(files.items()):
        job = db.job(jid)
        folder = home() / "jobs" / str(jid)
        cands = (media.load(folder / "candidates.json") or {}).get("candidates") or []
        title = _log_titles(folder).get(cid)
        try:
            dur = float(media.probe(str(f)).get("duration") or 0)
        except Exception:
            dur = 0.0
        match = None
        if title:
            match = next((c for c in cands if (c.get("title") or "").strip() == title.strip()), None)
        if match is None and cands and dur:
            # the moment whose length is closest to the finished file (jump cuts only ever shorten it)
            fits = [c for c in cands if c.get("duration", 0) >= dur - 1.0]
            match = min(fits or cands, key=lambda c: abs(c.get("duration", 0) - dur)) if not title else None
        c = match or {}
        thumb = f.with_suffix(".jpg")
        extra = {k: c.get(k) for k in ("category", "parts", "measures", "highlight", "strongest", "audience", "weaknesses")
                 if c.get(k) is not None}
        extra.update(version=ver, relinked=True)
        meta = c.get("meta") or {}
        fields = dict(id=cid, job_id=jid, video_id=(job or {}).get("video_id"), channel=channel, rank=c.get("rank"),
                      start=c.get("start", 0.0), end=c.get("end", dur), duration=dur or c.get("duration"),
                      score=c.get("score"), scores=c.get("scores") or {}, hook=c.get("hook"), hook_type=c.get("hook_type"),
                      reason=c.get("reason"), transcript=c.get("transcript"), topic=c.get("topic"),
                      title=title or c.get("title") or f"Clip {cid}", titles=c.get("titles") or meta.get("titles") or [],
                      description=c.get("description"), hashtags=c.get("hashtags") or [], tags=c.get("tags") or [],
                      path=str(f), thumb=str(thumb) if thumb.exists() else None, state="awaiting_approval",
                      note="re-linked after the database was repaired" + ("" if match else " (its moment couldn't be matched)"),
                      extra=extra)
        db.add_clip(**{k: v for k, v in fields.items() if v is not None})
        added += 1
    return added
