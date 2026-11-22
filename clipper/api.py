"""The dashboard's data and buttons (served by display.py at localhost:8765/clipper).

GET  /clipper/state                 everything the overview shows (no secrets: tokens never leave the disk)
GET  /clipper/job/<id>              one project: its steps with real timings, every candidate moment with
                                    its scores and why it was picked, the ones turned down, and its clips
GET  /clipper/clip/<id>             one clip for the editor: the words around it, its framing and settings
GET  /clipper/media/<clip>.mp4      a clip (with Range support so the preview can seek; ?download=1 saves it)
GET  /clipper/thumb/<clip>.jpg
GET  /clipper/frame/<clip>.jpg?t=   a frame of the source video (for positioning the crop by hand)
GET  /clipper/source/<job>.mp4      the whole source video, while it's still on disk (plays the transcript)
POST /clipper/action                buttons, with the page's token (same protection as the type box)
POST /clipper/upload                a video file dropped on the page (raw bytes, with the token)"""
import os
import re
import shutil
import sys
import time
from pathlib import Path

from clipper import captions, data_dir, db, jobs, media, moments, presets, publish, settings, sources, status

VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mts")
MAX_UPLOAD_GB = 20
STEPS = [("acquiring", "Getting the video"), ("audio", "Pulling out the audio"), ("transcribing", "Transcribing every word"),
         ("analyzing", "Understanding the content"), ("selecting", "Ranking the best moments"),
         ("editing", "Framing the clips"), ("rendering", "Captions and final render"), ("quality_check", "Quality check")]


def _clip_public(c):
    c = dict(c)
    c["has_file"] = bool(c.get("path") and os.path.exists(c["path"]))
    c.pop("path", None)
    c.pop("thumb", None)
    extra = c.get("extra") or {}
    c["category"] = extra.get("category")
    c["parts"] = extra.get("parts") or {}
    c["version"] = extra.get("version", 1)
    c["variant"], c["variant_of"] = extra.get("variant"), extra.get("variant_of")
    return c


def _disk():
    try:
        from clipper import disk
        return disk.report()
    except Exception as e:
        return {"error": str(e)[:200]}


def state():
    settings.ensure_default_channel()
    ch = settings.channel("main")
    from clipper import youtube
    clips = [_clip_public(c) for c in db.clips("state NOT IN ('regenerate','deleted')", limit=200)]
    for c in clips:
        m = db.one("SELECT views, avg_view_pct, likes, comments, shares, subs_gained, fetched FROM metrics WHERE clip_id=? "
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
    js = status.jobs(60)
    for j in js:
        j["steps"] = steps(j)
        j["clip_count"] = db.one("SELECT COUNT(*) n FROM clips WHERE job_id=? AND state NOT IN ('rejected','regenerate','deleted','qa_failed')",
                                 j["id"])["n"]
        j["waiting_retry"] = j["state"] == "queued" and bool(j.get("error"))
        j["options"] = {k: v for k, v in (j.get("options") or {}).items() if k in ("length", "stages", "preset")}
    return {
        "now": time.time(), "overview": status.overview(), "stats": stats(), "jobs": js, "clips": clips,
        "events": db.q("SELECT id, t, level, job_id, clip_id, msg FROM events ORDER BY id DESC LIMIT 80"),
        "channel": safe_ch, "sources": db.q("SELECT * FROM sources ORDER BY id"),
        "awaiting_auth": db.q("SELECT j.id job_id, v.id video_id, v.title, v.url, v.creator, v.auth_basis FROM jobs j "
                              "JOIN videos v ON v.id=j.video_id WHERE j.state='awaiting_authorization' ORDER BY j.id DESC LIMIT 30"),
        "youtube": {"connected": youtube.connected("main"), "client": bool(youtube.client()), "title": ch.get("youtube_title"),
                    "quota_used": youtube.quota_used(), "quota": settings.general()["youtube_daily_quota"]},
        "uploads": db.q("SELECT id, clip_id, status, privacy, publish_at, youtube_id, attempts, error FROM uploads ORDER BY id DESC LIMIT 60"),
        "analysis": analysis, "learned": (ch.get("learned") or {}).get("prefs", {}), "tuning": _tuning(),
        "general": settings.general(),
        "styles": [{"id": k, "name": captions.STYLE_NAMES.get(k, k), **{x: v[x] for x in ("font", "size", "color", "active", "keyword",
                    "stroke", "shadow", "y", "animation", "words_per_line", "uppercase")}} for k, v in captions.STYLES.items()],
        "fonts": sorted(captions.FONTS), "animations": captions.ANIMATIONS, "layouts": settings.LAYOUTS,
        "lengths": settings.LENGTH_MODES, "presets": presets.public(), "exports": export_presets(), "sorts": list(moments.SORTS), "export_dir": str(export_dir()),
        "platform": sys.platform, "disk": _disk(),
    }


def _tuning():
    try:
        from clipper import selftune
        return selftune.summary("main")
    except Exception as e:
        return {"error": str(e)[:200], "lessons": []}


def stats():
    week = time.time() - 7 * 86400
    one = lambda sql, *a: db.one(sql, *a) or {}  # noqa: E731
    done = db.q("SELECT started, finished FROM jobs WHERE finished IS NOT NULL AND started IS NOT NULL AND state NOT IN ('failed','cancelled') "
                "ORDER BY id DESC LIMIT 20")
    took = [j["finished"] - j["started"] for j in done if j["finished"] > j["started"]]
    return {
        "clips_week": one("SELECT COUNT(*) n FROM clips WHERE created>? AND state NOT IN ('rejected','regenerate','deleted','qa_failed')", week).get("n", 0),
        "avg_score": round(one("SELECT AVG(score) a FROM clips WHERE state NOT IN ('rejected','regenerate','deleted','qa_failed')").get("a") or 0, 1),
        "qa_pass": _rate("SELECT COUNT(*) n FROM clips WHERE state NOT IN ('qa_failed','regenerate')", "SELECT COUNT(*) n FROM clips WHERE state != 'regenerate'"),
        "avg_job_minutes": round(sum(took) / len(took) / 60, 1) if took else None,
        "minutes_clipped": round((one("SELECT SUM(duration) d FROM clips WHERE state NOT IN ('rejected','regenerate','deleted','qa_failed')").get("d") or 0) / 60, 1),
    }


def _rate(num_sql, den_sql):
    den = db.one(den_sql)["n"]
    return round(100 * db.one(num_sql)["n"] / den) if den else None


def steps(job):
    """The project's steps with their real state (done / running / waiting) and how long each took.
    Nothing is estimated: a step shows as done only once the job has really moved past it."""
    stages = (job.get("options") or {}).get("stages") or {}
    finished = job["state"] in ("done", "awaiting_approval", "published", "uploading")
    out = []
    for key, label in STEPS:
        info = stages.get("acquiring" if key == "audio" else key) or {}
        if not info:
            st = "skipped" if finished and key in ("editing", "rendering", "quality_check") else "waiting"
        elif info.get("end") or finished:
            st = "done"
        elif job["state"] in ("failed", "cancelled", "queued"):
            st = "stopped"
        else:
            st = "running"
        secs = round((info.get("end") or (time.time() if st == "running" else info.get("start", 0))) - info["start"], 1) \
            if info.get("start") else None
        out.append({"key": key, "label": label, "state": st, "seconds": secs if key != "audio" else None})
    return out


def job_detail(job_id):
    j = db.job(int(job_id))
    if not j:
        return {"error": "no such job"}
    v = db.video(j["video_id"]) or {}
    from clipper import pipeline
    found = media.load(pipeline.folder_for(j["id"]) / "candidates.json") or {}
    made = {c["rank"]: c["id"] for c in db.clips("job_id=? AND state NOT IN ('rejected','regenerate','deleted')", j["id"], limit=500)
            if not (c.get("extra") or {}).get("variant_of")}
    cands = []
    for c in found.get("candidates", []):
        cands.append({k: c.get(k) for k in ("rank", "title", "titles", "start", "end", "duration", "score", "parts", "scores",
                                            "category", "hook", "hook_type", "reason", "check", "notes", "measures", "similar",
                                            "topic", "weak", "method", "verified", "strongest", "strongest_at", "audience",
                                            "weaknesses", "manual")} | {"clip_id": made.get(c.get("rank"))})
    j["steps"] = steps(j)
    j["waiting_retry"] = j["state"] == "queued" and bool(j.get("error"))
    return {"job": j, "video": {k: v.get(k) for k in ("id", "title", "url", "platform", "creator", "duration", "auth_basis")},
            "candidates": cands, "rejected": found.get("rejected", []), "method": found.get("method"),
            "llm_error": found.get("llm_error"), "lengths": found.get("lengths"),
            "clips": [_clip_public(c) for c in db.clips("job_id=? AND state NOT IN ('regenerate','deleted')", j["id"], limit=200)]}


def clip_detail(clip_id):
    c = db.clip(int(clip_id))
    if not c:
        return {"error": "no such clip"}
    from clipper import pipeline
    folder = pipeline.folder_for(c["job_id"])
    tr = media.load(folder / "transcript.json") or {"words": []}
    lo, hi = c["start"] - 20, c["end"] + 20
    from clipper import caption_fix
    words = [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in caption_fix.words_for(tr.get("words", []), folder, lo, hi)
             if lo <= w["s"] <= hi]
    plan = media.load(folder / f"plan_{c['id']}.json") or {}
    v = db.video(c["video_id"]) or {}
    out = _clip_public(c)
    ex = c.get("extra") or {}
    root = ex.get("variant_of") or c["id"]
    family = [x for x in db.clips("job_id=? AND state NOT IN ('deleted','regenerate','rejected')", c["job_id"], limit=500)
              if x["id"] == root or (x.get("extra") or {}).get("variant_of") == root]
    out["family"] = [{"id": x["id"], "label": pipeline.VARIANTS.get((x.get("extra") or {}).get("variant"), "Original"),
                      "state": x["state"], "duration": x["duration"], "has_file": bool(x.get("path") and os.path.exists(x["path"])),
                      "caption_style": x.get("caption_style"), "layout": x.get("layout"), "version": (x.get("extra") or {}).get("version", 1)}
                     for x in sorted(family, key=lambda x: x["id"])]
    out["history"] = [{"version": h["version"], "t": h.get("t"), "duration": h.get("duration"),
                       "caption_style": h.get("caption_style"), "layout": h.get("layout"),
                       "has_file": bool(h.get("path") and os.path.exists(h["path"]))}
                      for h in reversed(ex.get("history") or [])]
    out.update(words=words, plan={k: plan.get(k) for k in ("layout", "reason", "zoom", "crop_w", "face_y", "manual", "gameplay")},
               x=(plan.get("x") or [0.5])[0] if plan.get("x") else 0.5, extra=c.get("extra") or {},
               source={"title": v.get("title"), "duration": v.get("duration"), "platform": v.get("platform")},
               window=[max(0.0, lo), hi], has_source=bool(_source_path(c)))
    return out


def transcript(job_id):
    """The whole video's transcript as sentences (number, start, end, speaker, text), for reading,
    searching and picking your own clip on the project page."""
    j = db.job(int(job_id))
    if not j:
        return {"sentences": [], "error": "no such job"}
    from clipper import pipeline
    tr = media.load(pipeline.folder_for(j["id"]) / "transcript.json") or {}
    sents = [{"id": x["id"], "s": round(x["s"], 2), "e": round(x["e"], 2), "sp": x.get("speaker", 0), "t": x["text"]}
             for x in tr.get("sentences") or []]
    return {"sentences": sents, "speakers": tr.get("speakers", 1), "has_source": bool(source_file(j["id"]))}


def source_file(job_id):
    """The job's whole source video if it's still on disk (for playing the transcript), else None."""
    j = db.job(int(job_id))
    if not j:
        return None
    v = db.video(j["video_id"]) or {}
    if v.get("platform") == "file" and v.get("path") and os.path.exists(v["path"]):
        return v["path"]
    from clipper import pipeline
    src = media.load(pipeline.folder_for(j["id"]) / "source.json") or {}
    return src["video"] if src.get("video") and os.path.exists(src["video"]) else None


def media_path(kind, clip_id, version=None):
    """A clip's file (or thumbnail); version = an earlier version kept in its history."""
    c = db.clip(int(clip_id))
    if not c:
        return None
    p = c.get("path") if kind == "media" else c.get("thumb")
    if version is not None and str(version).isdigit() and int(version) != int((c.get("extra") or {}).get("version", 1)):
        h = next((h for h in (c.get("extra") or {}).get("history") or [] if int(h.get("version", -1)) == int(version)), {})
        p = h.get("path") if kind == "media" else h.get("thumb")
    return p if p and os.path.exists(p) else None


def download_name(clip_id):
    c = db.clip(int(clip_id)) or {}
    return _safe(c.get("title") or f"clip {clip_id}") + ".mp4"


def _safe(name):
    name = re.sub(r"[^\w\s\-'!?,.&()]", "", str(name)).strip()[:80] or "clip"
    return re.sub(r"[?]", "", name)


def _source_path(c):
    """The video file a clip came from, if it's still on disk (downloads are deleted after a few days)."""
    v = db.video(c["video_id"]) or {}
    if v.get("platform") == "file" and v.get("path") and os.path.exists(v["path"]):
        return v["path"], 0.0
    from clipper import pipeline
    src = media.load(pipeline.folder_for(c["job_id"]) / "source.json") or {}
    if src.get("video") and os.path.exists(src["video"]):
        return src["video"], 0.0
    for k, p in (src.get("sections") or {}).items():
        a, b = (int(x) / 10 for x in k.split("_"))
        if a <= c["start"] and b >= c["end"] and os.path.exists(p):
            return p, a
    return None


def frame(clip_id, t):
    """A JPEG of the source video at time t (seconds in the original), for the editor's crop view."""
    c = db.clip(int(clip_id))
    if not c:
        return None
    sp = _source_path(c)
    if not sp:
        return None
    path, offset = sp
    out = data_dir("cache", "frames") / f"{c['id']}_{int(float(t) * 10)}.jpg"
    if not out.exists():
        media.run(["-y", "-ss", f"{max(0.0, float(t) - offset):.2f}", "-i", path, "-frames:v", "1",
                   "-vf", "scale=640:-2", "-q:v", "4", str(out)])
    return str(out) if out.exists() else None


def export_dir():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Movies" / "Jarvis Shorts"
    return home / "Videos" / "Jarvis Shorts"


# Export presets. Shorts, TikTok and Reels all take the same 1080x1920 H.264/AAC file the clipper
# makes, so those are a straight copy (no quality lost to re-encoding); they differ in how long a
# video may be, which is checked. The smaller sizes are re-encoded for sharing or slow uploads.
EXPORTS = {
    "shorts": {"label": "YouTube Shorts (1080p)", "height": None, "max_s": 180, "suffix": ""},
    "tiktok": {"label": "TikTok (1080p)", "height": None, "max_s": 600, "suffix": " [TikTok]"},
    "reels": {"label": "Instagram Reels (1080p)", "height": None, "max_s": 180, "suffix": " [Reels]"},
    "small": {"label": "Smaller file (720p)", "height": 1280, "crf": 25, "audio": "128k", "max_s": None, "suffix": " [720p]"},
    "preview": {"label": "Quick preview (540p)", "height": 960, "crf": 28, "audio": "96k", "max_s": None, "suffix": " [540p]"},
}


def export_presets():
    return [{"id": k, "label": v["label"], "max_s": v["max_s"]} for k, v in EXPORTS.items()]


def _transcode(src, dest, p):
    part = Path(str(dest) + ".part.mp4")
    media.run(["-y", "-i", src, "-vf", f"scale=-2:{p['height']}:flags=lanczos", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", str(p["crf"]), "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", p["audio"],
               "-movflags", "+faststart", str(part)], timeout=900)
    info = media.probe(str(part))               # never call it exported until the file really plays
    if int(info.get("height") or 0) != p["height"] or float(info.get("duration") or 0) <= 0:
        part.unlink(missing_ok=True)
        raise media.MediaError("the smaller copy didn't come out right")
    os.replace(part, dest)


def export(ids, preset="shorts"):
    """Copies finished clips (title-named MP4s) into the export folder, as the chosen export preset.
    Returns what happened, including clips too long for that platform (they're still exported)."""
    p = EXPORTS.get(preset or "shorts") or EXPORTS["shorts"]
    out = export_dir()
    out.mkdir(parents=True, exist_ok=True)
    done, missing, long, failed = [], [], [], []
    for i in ids:
        c = db.clip(int(i))
        if not c or not c.get("path") or not os.path.exists(c["path"]):
            missing.append(int(i))
            continue
        name = f"{_safe(c.get('title'))} (clip {c['id']}){p['suffix']}.mp4"
        try:
            if p["height"]:
                _transcode(c["path"], out / name, p)
            else:
                shutil.copy2(c["path"], out / name)
        except Exception as e:
            failed.append(f"clip {c['id']}: {str(e)[:120]}")
            continue
        if p["max_s"] and float(c.get("duration") or 0) > p["max_s"]:
            long.append(c["id"])
        done.append(name)
    what = p["label"]
    msg = f"Exported {len(done)} clip{'s' if len(done) != 1 else ''} as {what} to {out}." if done else "Nothing was exported."
    if missing:
        msg += f" {len(missing)} had no file (not rendered or deleted)."
    if long:
        msg += f" Heads up: clip{'s' if len(long) != 1 else ''} {', '.join(map(str, long))} {'are' if len(long) != 1 else 'is'} " \
               f"longer than {what.split(' (')[0]} allows ({p['max_s']}s)."
    if failed:
        msg += " Couldn't export " + "; ".join(failed) + "."
    if done:
        db.log(f"Export: {msg}")
    return {"ok": bool(done), "message": msg, "folder": str(out), "files": done}


def delete(clip_id):
    c = db.clip(int(clip_id))
    if not c:
        raise KeyError("no such clip")
    if c.get("youtube_id"):
        return "That clip is on YouTube, so I've kept it here too. Delete it on YouTube first if you want it gone."
    if c["state"] == "uploading":
        return "That clip is uploading right now."
    old_files = [f for h in (c.get("extra") or {}).get("history") or [] for f in (h.get("path"), h.get("thumb"))]
    for p in [c.get("path"), c.get("thumb")] + old_files:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.run("DELETE FROM uploads WHERE clip_id=? AND status IN ('queued','held','failed')", c["id"])
    if c["state"] not in ("rejected", "qa_failed", "deleted"):
        from clipper import selftune
        selftune.record("deleted", clip_id=c["id"])
    db.set_clip(c["id"], state="deleted", note="deleted from the dashboard")
    db.log(f"Deleted clip {c['id']}.", clip_id=c["id"])
    return f"Deleted clip {c['id']}."


def add_many(urls, req):
    """Batch: several links or file paths at once (one per line). Each becomes its own project in the
    queue, with the same preset, length and count; links already being processed aren't added twice."""
    if isinstance(urls, str):
        urls = urls.splitlines()
    seen, results = set(), []
    for u in urls:
        u = str(u).strip()
        if not u or u in seen:
            continue
        seen.add(u)
        r = action(dict(req, action="add", url=u))
        results.append({"url": u, "ok": r.get("ok"), "job": r.get("job"), "message": r.get("message")})
        if len(results) >= 50:
            break
    ok = [r for r in results if r["ok"]]
    msg = f"Queued {len(ok)} of {len(results)} video{'s' if len(results) != 1 else ''}."
    bad = [r for r in results if not r["ok"]]
    if bad:
        msg += " Not added: " + "; ".join(f"{r['url'][:60]} ({r['message']})" for r in bad[:5])
    return {"ok": bool(ok), "message": msg[:900], "results": results}


def check_link(text):
    """Quick validation for the input box (no downloading): what kind of source it is."""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "message": ""}
    try:
        platform, ext_id, url = sources.classify(text)
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    label = {"youtube": "YouTube video", "twitch": "Twitch VOD", "url": "video link", "file": "video file on this computer"}.get(platform, platform)
    out = {"ok": True, "platform": platform, "id": ext_id, "url": url, "message": f"Looks like a {label}."}
    if platform == "youtube":
        out["thumb"] = f"https://i.ytimg.com/vi/{ext_id}/hqdefault.jpg"
    return out


def save_upload(stream, length, filename):
    """Streams a dropped video file into clipper_data/uploads and queues it. Returns the action result."""
    name = Path(str(filename or "video.mp4")).name
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_TYPES:
        return {"ok": False, "message": f"{ext or 'That'} isn't a video type I can use ({', '.join(VIDEO_TYPES)})."}
    if length <= 0:
        return {"ok": False, "message": "The file was empty."}
    if length > MAX_UPLOAD_GB * 1024 ** 3:
        return {"ok": False, "message": f"That file is over {MAX_UPLOAD_GB} GB."}
    folder = data_dir("uploads")
    from clipper import acquire
    if acquire.free_gb(folder) - length / 1024 ** 3 < float(settings.general().get("min_free_gb", 4)):
        return {"ok": False, "message": "There isn't enough free disk space for that file."}
    stem = re.sub(r"[^\w\-. ]", "", Path(name).stem)[:60] or "video"
    dest = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{stem}{ext}"
    part = Path(str(dest) + ".part")
    left = length
    try:
        with open(part, "wb") as f:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left > 0:
            raise IOError("the upload stopped part way")
        os.replace(part, dest)
    except Exception as e:
        try:
            part.unlink()
        except OSError:
            pass
        return {"ok": False, "message": f"The upload didn't finish: {e}"}
    try:
        media.probe(str(dest))
    except Exception as e:
        dest.unlink(missing_ok=True)
        return {"ok": False, "message": f"I can't read that file as a video: {e}"}
    return {"ok": True, "path": str(dest), "message": "Uploaded."}


def action(req):
    """Runs a dashboard button. Returns {"ok": bool, "message": str} (plus data for some)."""
    a = str(req.get("action", ""))
    cid = req.get("clip")
    jid = req.get("job")
    try:
        if a == "add":
            opts = {}
            if req.get("length") in settings.LENGTH_MODES and req.get("length") not in ("custom", "auto"):
                opts["length"] = req["length"]
            if req.get("preset") in presets.PRESETS:
                opts["preset"] = req["preset"]
            v, job, msg = sources.add(str(req.get("url", "")).strip(), max_clips=req.get("count") or None, priority=8,
                                      options=opts or None)
            return {"ok": job is not None, "message": msg, "job": job["id"] if job else None}
        if a == "disk_clean":
            from clipper import disk
            r = disk.clean(urgent=True, by="dashboard")
            return {"ok": True, "message": disk.say_clean(r), "disk": disk.report(fresh=True)}
        if a == "check":
            return check_link(req.get("url"))
        if a == "approve":
            return {"ok": True, "message": publish.approve(int(cid), title=req.get("title"), description=req.get("description"),
                                                           privacy=req.get("privacy") or None)}
        if a == "approve_many":
            msgs = [publish.approve(int(i)) for i in req.get("clips") or []]
            return {"ok": bool(msgs), "message": f"Approved {len(msgs)} clip{'s' if len(msgs) != 1 else ''}."}
        if a == "reject":
            return {"ok": True, "message": publish.reject(int(cid), req.get("reason", ""))}
        if a in ("edit", "save_meta"):
            return {"ok": True, "message": publish.edit(int(cid), title=req.get("title"), description=req.get("description"),
                                                        pinned_comment=req.get("pinned_comment"))}
        if a == "rerender":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.edit(int(cid), req.get("edits") or {})}
        if a == "regenerate":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.regenerate(int(cid))}
        if a == "transcript":
            return dict(transcript(int(jid)), ok=True, message="")
        if a == "make_range":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.add_range(int(jid), int(req["start_id"]), int(req["end_id"]))}
        if a == "variants":
            from clipper import pipeline
            r = pipeline.make_variants(int(cid), [str(k) for k in req.get("kinds") or []])
            return {"ok": bool(r["clips"]), "message": r["message"], "clips": r["clips"]}
        if a == "restore":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.restore_version(int(cid), int(req["version"]))}
        if a == "pick":
            from clipper import pipeline
            return {"ok": True, "message": pipeline.pick(int(jid), req.get("ranks") or [])}
        if a == "delete":
            return {"ok": True, "message": delete(int(cid))}
        if a == "delete_many":
            msgs = [delete(int(i)) for i in req.get("clips") or []]
            return {"ok": True, "message": f"Deleted {sum(1 for m in msgs if m.startswith('Deleted'))} clip(s)."}
        if a == "export":
            return export(req.get("clips") or ([cid] if cid else []), req.get("preset") or "shorts")
        if a == "add_many":
            return add_many(req.get("urls") or [], req)
        if a == "open_exports":
            folder = export_dir()
            folder.mkdir(parents=True, exist_ok=True)
            import subprocess
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif sys.platform == "win32":
                os.startfile(str(folder))                       # noqa: (Windows only)
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return {"ok": True, "message": f"Opened {folder}."}
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
        if a in ("unlearn", "unlearn_all"):
            from clipper import selftune
            return {"ok": True, "message": selftune.forget("main", req.get("key") if a == "unlearn" else None)}
        if a == "settings":
            changes = {k: v for k, v in (req.get("changes") or {}).items() if k in settings.CHANNEL_DEFAULTS}
            before = settings.channel("main")
            settings.update_channel("main", **changes)
            from clipper import selftune
            selftune.user_set("main", [k for k, v in changes.items() if before.get(k) != v])
            return {"ok": True, "message": "Saved."}
        return {"ok": False, "message": f"unknown action {a}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
