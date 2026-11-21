"""Approvals, the publishing permission gate, the upload queue, and results.

Publishing modes (per channel "privacy", or per request):
  draft      never uploaded; the finished clip stays in the library
  private    uploaded as private (only you can see it)
  unlisted   uploaded, anyone with the link can watch
  scheduled  uploaded private with a publish time, YouTube makes it public then
  public     uploaded and public straight away
The gate (every upload passes through it, whoever asks):
  - nothing uploads during an emergency stop, or if upload_private is off
  - "scheduled" needs schedule_public, "public" needs publish_public; without them it's uploaded
    private instead, and the reason is saved
  - with require_approval on, nothing goes public/scheduled until you approved that clip
  - a clip is uploaded at most once (one upload row per clip, and its YouTube id is checked)
  - it must have passed QA and its source must still be authorized
  - at most max_uploads_per_day per channel, at the channel's publish_times, within the API quota"""
import datetime
import os
import time

from clipper import db, settings

UPLOADABLE = ("approved", "ready")


def approve(clip_id, title=None, description=None, hashtags=None, privacy=None, upload=True):
    c = db.clip(clip_id)
    if not c:
        return "There's no clip with that number."
    if c["state"] not in ("awaiting_approval", "ready", "approved", "upload_failed"):
        return f"Clip {clip_id} is {c['state'].replace('_', ' ')}, so there's nothing to approve."
    changes = {"approved": 1, "state": "approved"}
    if title:
        from clipper import metadata
        changes["title"] = metadata.clean_title(title)
    if description is not None:
        changes["description"] = description[:4800]
    if hashtags is not None:
        changes["hashtags"] = hashtags
    db.set_clip(clip_id, **changes)
    db.log(f"Clip {clip_id} approved.", clip_id=clip_id)
    if c["state"] in ("awaiting_approval", "ready"):
        from clipper import selftune
        selftune.record("approved", clip_id=clip_id)
    said = f"Clip {clip_id} approved."
    if upload and settings.allowed(c["channel"], "upload_private"):
        said += " " + queue(clip_id, privacy)
    _job_rollup(c["job_id"])
    return said


def reject(clip_id, reason=""):
    c = db.clip(clip_id)
    if not c:
        return "There's no clip with that number."
    if c["youtube_id"]:
        return f"Clip {clip_id} is already on YouTube; remove it in YouTube Studio if you don't want it."
    db.set_clip(clip_id, state="rejected", note=(reason or "rejected by you")[:300])
    db.run("DELETE FROM uploads WHERE clip_id=? AND status IN ('queued','held','waiting')", clip_id)
    db.log(f"Clip {clip_id} rejected{': ' + reason if reason else ''}.", clip_id=clip_id)
    if c["state"] != "rejected":
        from clipper import selftune
        selftune.record("rejected", clip_id=clip_id, reason=(reason or "")[:200])
    _job_rollup(c["job_id"])
    return f"Clip {clip_id} rejected."


def edit(clip_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("title", "description", "hashtags", "tags", "pinned_comment") and v is not None}
    if "title" in allowed:
        from clipper import metadata
        allowed["title"] = metadata.clean_title(allowed["title"])
        if not allowed["title"]:
            return "A title can't be empty."
    if not allowed:
        return "Nothing to change."
    c = db.clip(clip_id)
    if not c:
        return "There's no clip with that number."
    if c["youtube_id"]:
        return "That clip is already uploaded; change it in YouTube Studio."
    db.set_clip(clip_id, **allowed)
    return f"Updated clip {clip_id}."


# ---------- the gate ----------

def decide(clip, channel, requested=None):
    """-> (privacy or None to not upload, publish_at or None, note)."""
    perms = channel["permissions"]
    mode = requested or channel.get("privacy", "private")
    if settings.stopped():
        return None, None, "emergency stop is on"
    if mode == "draft":
        return None, None, "draft mode: kept in the library"
    if not perms.get("upload_private"):
        return None, None, "uploading is switched off for this channel"
    approved = bool(clip.get("approved")) or not perms.get("require_approval", True)
    if mode == "public":
        if perms.get("publish_public") and approved:
            return "public", None, ""
        return "private", None, ("public publishing isn't switched on" if not perms.get("publish_public")
                                 else "it needs your approval before going public") + ", so it's uploaded private"
    if mode == "scheduled":
        if perms.get("schedule_public") and approved:
            return "private", next_slot(channel), ""
        return "private", None, "scheduled publishing isn't allowed yet, so it's uploaded private"
    if mode in ("private", "unlisted"):
        return mode, None, ""
    return "private", None, f"unknown mode {mode}, uploaded private"


def next_slot(channel, after=None):
    """The next free publish time from the channel's publish_times (local time), as RFC 3339 UTC."""
    after = after or datetime.datetime.now().astimezone()
    taken = {r["publish_at"] for r in db.q("SELECT publish_at FROM uploads WHERE channel=? AND publish_at IS NOT NULL", channel["id"])}
    per_day = int(channel.get("max_uploads_per_day", 3))
    times = sorted(channel.get("publish_times") or ["18:00"])
    for day in range(0, 60):
        date = (after + datetime.timedelta(days=day)).date()
        used = sum(1 for t in taken if t and t.startswith(str(date)))
        for hm in times:
            h, m = (int(x) for x in hm.split(":"))
            local = datetime.datetime.combine(date, datetime.time(h, m)).astimezone()
            if local <= after + datetime.timedelta(minutes=20) or used >= per_day:
                continue
            stamp = local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if stamp not in taken:
                return stamp
    return None


def queue(clip_id, privacy=None):
    """Puts a clip in the upload queue if the gate allows. Returns what happened, in words."""
    c = db.clip(clip_id)
    if not c:
        return "There's no clip with that number."
    if c["youtube_id"]:
        return f"Clip {clip_id} is already on YouTube."
    if c["state"] not in UPLOADABLE and c["state"] != "upload_failed":
        return f"Clip {clip_id} isn't ready to upload ({c['state'].replace('_', ' ')})."
    if not (c.get("qa") or {}).get("passed"):
        return f"Clip {clip_id} hasn't passed the quality check."
    if not c["path"] or not os.path.exists(c["path"]):
        return f"Clip {clip_id}'s file is missing."
    v = db.video(c["video_id"])
    if not v or v["auth_status"] != "authorized":
        return f"Clip {clip_id}'s source isn't authorized any more, so it won't be uploaded."
    ch = settings.channel(c["channel"])
    priv, when, note = decide(c, ch, privacy)
    if priv is None:
        return f"Not uploading clip {clip_id}: {note}."
    existing = db.one("SELECT * FROM uploads WHERE clip_id=?", clip_id)
    now = time.time()
    if existing:
        if existing["status"] in ("done",):
            return f"Clip {clip_id} is already uploaded."
        db.run("UPDATE uploads SET status='queued', privacy=?, publish_at=?, not_before=0, error=NULL, updated=? WHERE clip_id=?",
               priv, when, now, clip_id)
    else:
        db.run("INSERT INTO uploads(clip_id, channel, status, privacy, publish_at, created, updated) VALUES(?,?,?,?,?,?,?)",
               clip_id, c["channel"], "queued", priv, when, now, now)
    db.set_clip(clip_id, privacy=priv, publish_at=when)
    where = f"{priv}" + (f", going public {_local(when)}" if when else "")
    return f"Clip {clip_id} is queued to upload ({where})." + (f" Note: {note}." if note else "")


def _local(stamp):
    try:
        t = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).astimezone()
        return t.strftime("%a %-I:%M %p") if os.name != "nt" else t.strftime("%a %I:%M %p")
    except Exception:
        return stamp


def upload_approved(channel=None, privacy=None):
    rows = db.clips("state='approved' AND youtube_id IS NULL" + (" AND channel=?" if channel else ""),
                    *([channel] if channel else []))
    if not rows:
        return "There are no approved clips waiting to upload."
    said = [queue(c["id"], privacy) for c in rows]
    return " ".join(said)


# ---------- running uploads ----------

def run_due(now=None, stop=None):
    """Uploads whatever is queued (one at a time), respecting the stop switch, daily caps and quota."""
    from clipper import youtube
    now = now or time.time()
    stop = stop or settings.stopped
    if stop():
        return 0
    done = 0
    for u in db.q("SELECT * FROM uploads WHERE status='queued' AND not_before<=? ORDER BY id", now):
        if stop():
            break
        ch = settings.channel(u["channel"])
        if not youtube.connected(u["channel"]):
            if not db.get(f"told_connect_{u['channel']}", False):
                db.put(f"told_connect_{u['channel']}", True)
                db.log("Clips are ready to upload, but YouTube isn't connected yet. Double-click Connect YouTube.",
                       "warn", tell=True)
            continue
        today = db.one("SELECT COUNT(*) n FROM uploads WHERE channel=? AND status='done' AND updated>?", u["channel"], now - 86400)["n"]
        if today >= int(ch.get("max_uploads_per_day", 3)) and not u["publish_at"]:
            db.run("UPDATE uploads SET not_before=? WHERE id=?", now + 3600, u["id"])
            continue
        if _upload_one(u, stop):
            done += 1
    try:
        refresh_status()
    except Exception as e:
        print(f"(clipper: status check failed: {e})")
    return done


def _meta(c, privacy, publish_at, ch):
    snippet = {"title": c["title"][:100], "description": (c.get("description") or "")[:4900],
               "tags": [t for t in (c.get("tags") or []) if t][:15], "categoryId": str(c.get("category_id") or ch.get("category_id", "24")),
               "defaultLanguage": ch.get("language", "en"), "defaultAudioLanguage": ch.get("language", "en")}
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
              "containsSyntheticMedia": False}
    if publish_at:
        status["publishAt"] = publish_at
    return {"snippet": snippet, "status": status}


def _upload_one(u, stop):
    from clipper import youtube
    c = db.clip(u["clip_id"])
    if not c or c["youtube_id"]:
        db.run("UPDATE uploads SET status='done', updated=? WHERE id=?", time.time(), u["id"])
        return False
    ch = settings.channel(c["channel"])
    priv, when, note = decide(c, ch, _requested(u, c))
    if priv is None:
        db.run("UPDATE uploads SET status='waiting', error=?, updated=? WHERE id=?", note, time.time(), u["id"])
        return False
    yt_channel = ch.get("youtube_channel_id")
    if not yt_channel:
        youtube.identify(c["channel"])
    db.run("UPDATE uploads SET status='uploading', attempts=attempts+1, updated=? WHERE id=?", time.time(), u["id"])
    db.set_clip(c["id"], state="uploading")
    try:
        session = u["session_url"]
        if not session:
            session = youtube.start_upload(c["channel"], c["path"], _meta(c, priv, when or u["publish_at"], ch))
            db.run("UPDATE uploads SET session_url=? WHERE id=?", session, u["id"])
        try:
            res = youtube.send_file(c["channel"], session, c["path"], stop=stop)
        except youtube.YouTubeError as e:
            if e.reason == "sessionExpired":
                db.run("UPDATE uploads SET session_url=NULL WHERE id=?", u["id"])
                session = youtube.start_upload(c["channel"], c["path"], _meta(c, priv, when or u["publish_at"], ch))
                db.run("UPDATE uploads SET session_url=? WHERE id=?", session, u["id"])
                res = youtube.send_file(c["channel"], session, c["path"], stop=stop)
            else:
                raise
        if res is None:                                    # stopped part-way: resume later
            db.run("UPDATE uploads SET status='held', updated=? WHERE id=?", time.time(), u["id"])
            db.set_clip(c["id"], state="approved" if c["approved"] else "ready")
            return False
        vid = res.get("id")
        if not vid:
            raise youtube.YouTubeError("YouTube finished the upload but returned no video id")
        got = (res.get("status") or {}).get("privacyStatus", priv)
        state = "published" if got == "public" else ("scheduled" if (when or u["publish_at"]) else "uploaded")
        db.run("UPDATE uploads SET status='done', youtube_id=?, error=NULL, updated=? WHERE id=?", vid, time.time(), u["id"])
        db.set_clip(c["id"], youtube_id=vid, state=state, privacy=got)
        db.log(f"Uploaded clip {c['id']} to YouTube as {got}: https://youtu.be/{vid}", clip_id=c["id"], tell=True)
        if priv != got and not (when or u["publish_at"]):
            db.set_clip(c["id"], note=f"asked for {priv}, YouTube locked it {got} (app not audited)")
            db.log(youtube.lock_note(), "warn", clip_id=c["id"], tell=True)
        _job_rollup(c["job_id"])
        return True
    except youtube.QuotaExceeded as e:
        db.run("UPDATE uploads SET status='queued', not_before=?, error=?, updated=? WHERE id=?",
               _next_pacific_midnight(), str(e), time.time(), u["id"])
        db.set_clip(c["id"], state="approved" if c["approved"] else "ready")
        db.log(f"{e}. Uploads continue after it resets.", "warn")
        return False
    except youtube.NotConnected as e:
        db.run("UPDATE uploads SET status='queued', not_before=?, error=?, updated=? WHERE id=?",
               time.time() + 3600, str(e), time.time(), u["id"])
        db.set_clip(c["id"], state="approved" if c["approved"] else "ready")
        db.log(str(e), "warn", tell=True)
        return False
    except Exception as e:
        attempts = (u["attempts"] or 0) + 1
        hard = isinstance(e, youtube.YouTubeError) and e.status and 400 <= e.status < 500
        if hard or attempts >= 5:
            db.run("UPDATE uploads SET status='failed', error=?, updated=? WHERE id=?", str(e)[:500], time.time(), u["id"])
            db.set_clip(c["id"], state="upload_failed", note=str(e)[:300])
            db.log(f"Uploading clip {c['id']} failed: {e}", "error", clip_id=c["id"], tell=True)
        else:
            wait = 120 * (3 ** (attempts - 1))
            db.run("UPDATE uploads SET status='queued', not_before=?, error=?, updated=? WHERE id=?",
                   time.time() + wait, str(e)[:500], time.time(), u["id"])
            db.set_clip(c["id"], state="approved" if c["approved"] else "ready")
            db.log(f"Upload of clip {c['id']} hit a problem ({e}); retrying in {wait // 60} min.", "warn", clip_id=c["id"])
        return False


def _requested(u, c):
    if u["publish_at"]:
        return "scheduled"
    return u["privacy"] or c.get("privacy")


def _next_pacific_midnight():
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=8)
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return time.time() + (nxt - now).total_seconds()


def _job_rollup(job_id):
    if not job_id:
        return
    rows = db.q("SELECT state FROM clips WHERE job_id=?", job_id)
    states = {r["state"] for r in rows}
    job = db.job(job_id)
    if not job or job["state"] in ("failed", "cancelled") or job["state"] in db.ACTIVE_STATES or job["state"] == "queued":
        return
    if states and states <= {"published", "scheduled", "uploaded", "rejected", "qa_failed"} and states & {"published", "scheduled", "uploaded"}:
        db.set_job(job_id, state="published")
    elif "awaiting_approval" in states:
        db.set_job(job_id, state="awaiting_approval")
    elif "uploading" in states:
        db.set_job(job_id, state="uploading")
    else:
        db.set_job(job_id, state="done")


def refresh_status():
    """Checks recent uploads on YouTube: processing finished? rejected? locked private by Google?"""
    from clipper import youtube
    for cid in settings.channels():
        if not youtube.connected(cid):
            continue
        rows = db.clips("channel=? AND youtube_id IS NOT NULL AND updated>?", cid, time.time() - 7 * 86400)
        if not rows:
            continue
        info = youtube.status(cid, [r["youtube_id"] for r in rows])
        for r in rows:
            s = info.get(r["youtube_id"])
            if not s:
                db.set_clip(r["id"], note="not found on YouTube any more (deleted?)")
                continue
            if s["upload"] in ("rejected", "failed"):
                db.set_clip(r["id"], state="upload_failed", note=f"YouTube {s['upload']} it: {s['rejection']}")
                db.log(f"YouTube {s['upload']} clip {r['id']}: {s['rejection']}", "error", clip_id=r["id"], tell=True)
            elif s["privacy"] == "public" and r["state"] != "published":
                db.set_clip(r["id"], state="published", privacy="public")
            elif r["privacy"] == "public" and s["privacy"] == "private" and not s.get("publish_at"):
                if "locked" not in (r.get("note") or ""):
                    db.set_clip(r["id"], note="locked private by YouTube (app not audited)")
                    db.log(youtube.lock_note(), "warn", clip_id=r["id"], tell=True)


def refresh_analytics():
    """Pulls the latest numbers for every uploaded clip, then updates what the clipper has learned."""
    from clipper import learning, youtube
    for cid in settings.channels():
        if not youtube.connected(cid):
            continue
        rows = db.clips("channel=? AND youtube_id IS NOT NULL", cid, limit=1000)
        if not rows:
            continue
        stats = youtube.analytics(cid, [r["youtube_id"] for r in rows])
        now = time.time()
        for r in rows:
            d = stats.get(r["youtube_id"])
            if not d:
                continue
            db.run("INSERT INTO metrics(clip_id, youtube_id, fetched, views, minutes, avg_view_duration, avg_view_pct, likes, "
                   "comments, shares, subs_gained) VALUES(?,?,?,?,?,?,?,?,?,?,?)", r["id"], r["youtube_id"], now,
                   int(d.get("views", 0)), float(d.get("estimatedMinutesWatched", 0)), float(d.get("averageViewDuration", 0)),
                   float(d.get("averageViewPercentage", 0)), int(d.get("likes", 0)), int(d.get("comments", 0)),
                   int(d.get("shares", 0)), int(d.get("subscribersGained", 0)))
        learning.update(cid)
        db.log(f"Updated YouTube numbers for {len(stats)} clips.")
