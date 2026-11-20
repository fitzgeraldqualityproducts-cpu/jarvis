"""The dashboard's data and buttons (served by display.py at localhost:8765/clipper).

GET  /clipper/state            everything the page shows (no secrets: tokens never leave the disk)
GET  /clipper/media/<clip>.mp4 a clip (with Range support so the preview can seek)
GET  /clipper/thumb/<clip>.jpg
POST /clipper/action           buttons, with the page's token (same protection as the type box)"""
import os
import time

from clipper import db, jobs, publish, settings, sources, status


def state():
    settings.ensure_default_channel()
    ch = settings.channel("main")
    from clipper import youtube
    clips = db.clips("state != 'regenerate'", limit=150)
    for c in clips:
        c["has_file"] = bool(c.get("path") and os.path.exists(c["path"]))
        c.pop("path", None)
        c.pop("thumb", None)
        m = db.one("SELECT views, avg_view_pct, likes, shares, subs_gained, fetched FROM metrics WHERE clip_id=? "
                   "ORDER BY fetched DESC LIMIT 1", c["id"])
        c["metrics"] = m
        v = db.video(c["video_id"]) or {}
        c["source"] = {"title": v.get("title"), "url": v.get("url"), "creator": v.get("creator"), "platform": v.get("platform"),
                       "basis": v.get("auth_basis")}
    safe_ch = {k: v for k, v in ch.items() if k not in ("learned",)}
    try:
        from clipper import learning
        analysis = learning.analyze("main")
    except Exception:
        analysis = {"n": 0}
    return {
        "now": time.time(), "overview": status.overview(), "jobs": status.jobs(60), "clips": clips,
        "events": db.q("SELECT id, t, level, job_id, clip_id, msg FROM events ORDER BY id DESC LIMIT 80"),
        "channel": safe_ch, "sources": db.q("SELECT * FROM sources ORDER BY id"),
        "awaiting_auth": db.q("SELECT j.id job_id, v.id video_id, v.title, v.url, v.creator, v.auth_basis FROM jobs j "
                              "JOIN videos v ON v.id=j.video_id WHERE j.state='awaiting_authorization' ORDER BY j.id DESC LIMIT 30"),
        "youtube": {"connected": youtube.connected("main"), "client": bool(youtube.client()), "title": ch.get("youtube_title"),
                    "quota_used": youtube.quota_used(), "quota": settings.general()["youtube_daily_quota"]},
        "uploads": db.q("SELECT id, clip_id, status, privacy, publish_at, youtube_id, attempts, error FROM uploads ORDER BY id DESC LIMIT 60"),
        "analysis": analysis, "learned": (ch.get("learned") or {}).get("prefs", {}),
        "general": settings.general(), "styles": settings.STYLES,
    }


def media_path(kind, clip_id):
    c = db.clip(int(clip_id))
    if not c:
        return None
    p = c.get("path") if kind == "media" else c.get("thumb")
    return p if p and os.path.exists(p) else None


def action(req):
    """Runs a dashboard button. Returns {"ok": bool, "message": str}."""
    a = str(req.get("action", ""))
    cid = req.get("clip")
    jid = req.get("job")
    try:
        if a == "add":
            v, job, msg = sources.add(str(req.get("url", "")).strip(), max_clips=req.get("count") or None, priority=8)
            return {"ok": job is not None, "message": msg}
        if a == "approve":
            return {"ok": True, "message": publish.approve(int(cid), title=req.get("title"), description=req.get("description"),
                                                           privacy=req.get("privacy") or None)}
        if a == "reject":
            return {"ok": True, "message": publish.reject(int(cid), req.get("reason", ""))}
        if a == "edit":
            return {"ok": True, "message": publish.edit(int(cid), title=req.get("title"), description=req.get("description"))}
        if a == "regenerate":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.regenerate(int(cid))}
        if a == "upload":
            return {"ok": True, "message": publish.queue(int(cid), req.get("privacy") or None)}
        if a == "retry":
            return {"ok": True, "message": jobs.retry(int(jid))}
        if a == "cancel":
            return {"ok": True, "message": jobs.cancel(int(jid))}
        if a == "grant":
            return {"ok": True, "message": sources.grant(int(req["video"]))}
        if a == "stop":
            return {"ok": True, "message": jobs.emergency_stop()}
        if a == "resume":
            return {"ok": True, "message": jobs.resume()}
        if a == "pause":
            return {"ok": True, "message": jobs.pause()}
        if a == "discover":
            found = sources.discover("main")
            return {"ok": True, "message": f"Found {len(found)} new video(s)."}
        if a == "add_source":
            s = sources.add_source(req["kind"], req["value"], authorized=bool(req.get("authorized")))
            return {"ok": True, "message": f"Added {s['kind']} {s['value']}."}
        if a == "remove_source":
            db.run("UPDATE sources SET enabled=0 WHERE id=?", int(req["id"]))
            return {"ok": True, "message": "Source switched off."}
        if a == "settings":
            changes = {k: v for k, v in (req.get("changes") or {}).items() if k in settings.CHANNEL_DEFAULTS}
            settings.update_channel("main", **changes)
            return {"ok": True, "message": "Saved."}
        return {"ok": False, "message": f"unknown action {a}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
