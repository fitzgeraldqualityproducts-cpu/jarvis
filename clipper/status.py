"""What the clipper is doing, in numbers (dashboard) and in words (Jarvis's voice).
Everything here is read from the database, so Jarvis only ever reports what really happened."""
import time

from clipper import db, settings

STATE_WORDS = {"acquiring": "getting the video", "transcribing": "transcribing", "analyzing": "finding moments",
               "selecting": "ranking moments", "editing": "editing", "rendering": "rendering",
               "quality_check": "checking quality", "uploading": "uploading"}


def overview(channel=None):
    w = " WHERE channel=?" if channel else ""
    a = [channel] if channel else []

    def n(sql, *args):
        return db.one(sql, *args)["n"]
    return {
        "videos": n(f"SELECT COUNT(*) n FROM videos{w}", *a),
        "processed": n(f"SELECT COUNT(*) n FROM jobs WHERE state IN ('done','published','awaiting_approval','uploading')"
                       + (" AND channel=?" if channel else ""), *a),
        "clips": n(f"SELECT COUNT(*) n FROM clips WHERE state NOT IN ('rejected','regenerate')" + (" AND channel=?" if channel else ""), *a),
        "published": n("SELECT COUNT(*) n FROM clips WHERE youtube_id IS NOT NULL" + (" AND channel=?" if channel else ""), *a),
        "active": n("SELECT COUNT(*) n FROM jobs WHERE state IN ({})".format(",".join("?" * len(db.ACTIVE_STATES))), *db.ACTIVE_STATES),
        "queued": n("SELECT COUNT(*) n FROM jobs WHERE state='queued'"),
        "failed": n("SELECT COUNT(*) n FROM jobs WHERE state='failed'"),
        "pending": n("SELECT COUNT(*) n FROM clips WHERE state='awaiting_approval'"),
        "needs_ok": n("SELECT COUNT(*) n FROM jobs WHERE state='awaiting_authorization'"),
        "uploads_queued": n("SELECT COUNT(*) n FROM uploads WHERE status IN ('queued','uploading')"),
        "stopped": settings.stopped(), "paused": settings.paused(),
    }


def eta(job):
    """Estimated finish, only when it can be estimated from earlier jobs (None otherwise)."""
    rates = db.get("speed", {}) or {}
    v = db.video(job["video_id"]) or {}
    mins = float(v.get("duration") or 0) / 60
    r = rates.get("per_source_minute")
    if not r or rates.get("n", 0) < 2 or not mins:
        return None
    total = r * mins
    spent = time.time() - float(job.get("started") or time.time())
    return max(30.0, total - spent)


def record_speed(job_id):
    j = db.job(job_id)
    v = db.video(j["video_id"]) if j else None
    if not j or not v or not v.get("duration") or not j.get("started") or not j.get("finished"):
        return
    rate = (float(j["finished"]) - float(j["started"])) / (float(v["duration"]) / 60)
    s = db.get("speed", {}) or {}
    n = int(s.get("n", 0))
    s["per_source_minute"] = (float(s.get("per_source_minute", rate)) * n + rate) / (n + 1)
    s["n"] = n + 1
    db.put("speed", s)


def jobs(limit=50):
    rows = db.q("SELECT j.*, v.title, v.platform, v.url, v.duration, v.auth_basis, v.creator FROM jobs j "
                "LEFT JOIN videos v ON v.id=j.video_id ORDER BY CASE WHEN j.state IN ('queued','acquiring','transcribing',"
                "'analyzing','selecting','editing','rendering','quality_check','uploading','awaiting_authorization') "
                "THEN 0 ELSE 1 END, j.id DESC LIMIT ?", limit)
    for r in rows:
        r["options"] = db.loads(r["options"])
        r["eta"] = eta(r) if r["state"] in db.ACTIVE_STATES else None
    return rows


def say_status():
    o = overview()
    bits = []
    if o["stopped"]:
        bits.append("The emergency stop is on, so nothing new is running.")
    elif o["paused"]:
        bits.append("Clipping is paused.")
    running = db.q("SELECT j.id, j.state, j.progress, v.title FROM jobs j JOIN videos v ON v.id=j.video_id WHERE j.state IN ({})"
                   .format(",".join("?" * len(db.ACTIVE_STATES))), *db.ACTIVE_STATES)
    for r in running:
        bits.append(f"Job {r['id']} is {STATE_WORDS.get(r['state'], r['state'])} \"{str(r['title'])[:50]}\", "
                    f"about {int((r['progress'] or 0) * 100)}% through that step.")
    if o["queued"]:
        bits.append(f"{o['queued']} more in the queue.")
    if o["pending"]:
        bits.append(f"{o['pending']} clip{'s' if o['pending'] != 1 else ''} waiting for your OK.")
    if o["needs_ok"]:
        bits.append(f"{o['needs_ok']} video{'s' if o['needs_ok'] != 1 else ''} need you to confirm permission.")
    if o["failed"]:
        bits.append(f"{o['failed']} job{'s' if o['failed'] != 1 else ''} failed.")
    if not bits:
        bits.append(f"Nothing's running. So far: {o['clips']} clips made, {o['published']} on YouTube.")
    return " ".join(bits)


def today_report():
    since = time.time() - 86400
    made = db.one("SELECT COUNT(*) n FROM clips WHERE created>? AND state NOT IN ('rejected','qa_failed','regenerate')", since)["n"]
    failed_qa = db.one("SELECT COUNT(*) n FROM clips WHERE created>? AND state='qa_failed'", since)["n"]
    jobs_done = db.one("SELECT COUNT(*) n FROM jobs WHERE finished>? AND state NOT IN ('failed','cancelled')", since)["n"]
    jobs_failed = db.one("SELECT COUNT(*) n FROM jobs WHERE finished>? AND state='failed'", since)["n"]
    up = db.one("SELECT COUNT(*) n FROM uploads WHERE updated>? AND status='done'", since)["n"]
    found = db.one("SELECT COUNT(*) n FROM videos WHERE discovered>?", since)["n"]
    o = overview()
    parts = [f"In the last 24 hours I found {found} new video{'s' if found != 1 else ''}, finished {jobs_done} job"
             f"{'s' if jobs_done != 1 else ''} and made {made} clip{'s' if made != 1 else ''}."]
    if failed_qa:
        parts.append(f"{failed_qa} clip{'s' if failed_qa != 1 else ''} failed the quality check and weren't kept.")
    if jobs_failed:
        parts.append(f"{jobs_failed} job{'s' if jobs_failed != 1 else ''} failed.")
    parts.append(f"{up} upload{'s' if up != 1 else ''} to YouTube." if up else "Nothing was uploaded.")
    if o["pending"]:
        parts.append(f"{o['pending']} clip{'s are' if o['pending'] != 1 else ' is'} waiting for your OK.")
    return " ".join(parts)


def pending(limit=10):
    return db.clips("state='awaiting_approval'", limit=limit)
