"""Runs one clipping job, stage by stage. Every stage saves its result in the job's folder
(clipper_data/jobs/<id>/), so a job interrupted by a restart, update or crash carries on where it
stopped instead of starting over.

    acquiring -> transcribing -> analyzing -> selecting -> editing/rendering -> quality_check
    -> awaiting_approval (or done)

jobs.py runs this in a separate Python process (`python -m clipper job <id>`), so heavy video work
can't slow down or crash Jarvis himself. Exit codes: 0 finished, 1 retry later, 2 failed for good,
3 stopped (emergency stop or pause)."""
import os
import shutil
import sys
import time
import traceback

from clipper import acquire, captions, crop, data_dir, db, media, metadata, moments, qa, render, settings, transcribe

OK, RETRY, FATAL, STOPPED = 0, 1, 2, 3


class Stop(Exception):
    pass


class Fatal(Exception):
    pass


def folder_for(job_id):
    return data_dir("jobs", str(job_id))


def _progress(job_id, state):
    last = [0.0]

    def report(frac, detail=""):
        now = time.time()
        if now - last[0] < 1.5 and frac < 1:
            return
        last[0] = now
        db.set_job(job_id, state=state, progress=round(max(0.0, min(1.0, frac)), 3), detail=detail[:200], heartbeat=now)
        if settings.stopped() or not _parent_alive():
            raise Stop()
    return report


def _parent_alive():
    """A job started by Jarvis stops (safely, to resume later) if Jarvis quits."""
    pid = int(os.environ.get("JARVIS_CLIPPER_PARENT", "0") or 0)
    if not pid:
        return True
    try:
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259                  # STILL_ACTIVE
        os.kill(pid, 0)
        return os.getppid() == pid
    except Exception:
        return False


def _stage(job_id, state, detail=""):
    if settings.stopped() or settings.paused() or not _parent_alive():
        raise Stop()
    db.set_job(job_id, state=state, progress=0.0, detail=detail, heartbeat=time.time())
    return _progress(job_id, state)


def run_job(job_id):
    job = db.job(job_id)
    if not job:
        return FATAL
    video = db.video(job["video_id"])
    try:
        ch = settings.channel(job["channel"])
    except KeyError:
        db.set_job(job_id, state="failed", error="its channel no longer exists", finished=time.time())
        return FATAL
    folder = folder_for(job_id)
    try:
        return _run(job, video, ch, folder)
    except Stop:
        db.set_job(job_id, state="queued", detail="paused (emergency stop or pause)", pid=None)
        db.log("Stopped safely; it'll carry on when you resume.", job_id=job_id)
        return STOPPED
    except (Fatal, acquire.NotPermitted) as e:
        db.set_job(job_id, state="failed", error=str(e)[:500], finished=time.time(), pid=None)
        db.log(f"Job failed: {e}", "error", job_id=job_id, tell=True)
        return FATAL
    except media.MediaError as e:
        attempts = (job.get("attempts") or 0) + 1
        if "can't read" in str(e) or "not found" in str(e):
            db.set_job(job_id, state="failed", error=str(e)[:500], finished=time.time(), pid=None)
            db.log(f"Job failed: {e}", "error", job_id=job_id, tell=True)
            return FATAL
        return _retry(job_id, attempts, e)
    except Exception as e:
        traceback.print_exc()
        return _retry(job_id, (job.get("attempts") or 0) + 1, e)


def _retry(job_id, attempts, e):
    limit = int(settings.general().get("max_attempts", 3))
    detail = f"{type(e).__name__}: {e}"[:500]
    if attempts >= limit:
        db.set_job(job_id, state="failed", attempts=attempts, error=detail, finished=time.time(), pid=None)
        db.log(f"Job failed after {attempts} tries: {detail}", "error", job_id=job_id, tell=True)
        return FATAL
    wait = 60 * (4 ** (attempts - 1))                  # 1 min, 4 min, 16 min...
    db.set_job(job_id, state="queued", attempts=attempts, error=detail, not_before=time.time() + wait, pid=None,
               detail=f"will retry in {wait // 60} min")
    db.log(f"Problem ({detail}); retrying in {wait // 60} minutes (try {attempts + 1} of {limit}).", "warn", job_id=job_id)
    return RETRY


def _run(job, video, ch, folder):
    jid = job["id"]
    g = settings.general()
    if video["auth_status"] != "authorized":
        raise Fatal(f"not authorized: {video['auth_basis']}")
    if not settings.allowed(ch["id"], "process"):
        raise Fatal("processing is switched off for this channel")
    if acquire.free_gb(folder) < float(g.get("min_free_gb", 4)):
        raise RuntimeError("not enough free disk space right now")

    # 1. the video
    p = _stage(jid, "acquiring", "getting the video")
    src = acquire.acquire(video, folder, p)
    if not video.get("duration") and src.get("info"):
        db.run("UPDATE videos SET duration=? WHERE id=?", src["info"]["duration"], video["id"])
        video["duration"] = src["info"]["duration"]

    # 2. transcript
    tpath = folder / "transcript.json"
    tr = media.load(tpath)
    if not tr:
        p = _stage(jid, "transcribing", "listening to every word")
        names = ", ".join(ch.get("names") or [])
        tr = transcribe.transcribe(src["audio"], p, prompt=f"Names: {names}." if names else "", language=ch.get("language", "en"))
        media.dump(tr, tpath)
        db.log(f"Transcribed {len(tr['words'])} words ({tr['engine']}, {tr['speakers']} voice"
               f"{'s' if tr['speakers'] != 1 else ''}).", job_id=jid)
    if len(tr["words"]) < 40:
        raise Fatal("there's hardly any speech in this video, so there's nothing to clip")

    # 3. moments
    cpath = folder / "candidates.json"
    found = media.load(cpath)
    if not found:
        p = _stage(jid, "analyzing", "looking for the best moments")
        try:
            from clipper import learning
            learned = learning.preferences(ch["id"])
        except Exception:
            learned = {}
        cands, method = moments.find(tr, ch, video, max_clips=job["max_clips"] * 2, learned=learned, progress=p)
        _stage(jid, "selecting", f"ranking {len(cands)} moments")
        metadata.generate(cands, video, ch)
        found = {"method": method, "candidates": cands, "made": time.time()}
        media.dump(found, cpath)
        db.log(f"Found {len(cands)} strong moment{'s' if len(cands) != 1 else ''} ({method}).", job_id=jid)
    cands = found["candidates"]
    if not cands:
        db.set_job(jid, state="done", progress=1.0, detail="no moments strong enough for a Short",
                   finished=time.time(), pid=None)
        db.log("No moments were strong enough for a Short, so I made none.", job_id=jid, tell=True)
        return OK
    if not settings.allowed(ch["id"], "render"):
        db.set_job(jid, state="done", detail="clip making is switched off; moments saved", finished=time.time(), pid=None)
        return OK

    # 4-6. edit, render, check, every chosen moment
    want = int(job["max_clips"])
    made = _render_all(jid, video, ch, folder, src, tr, cands, want, g)
    waiting = db.one("SELECT COUNT(*) n FROM clips WHERE job_id=? AND state='awaiting_approval'", jid)["n"]
    state = "awaiting_approval" if waiting else "done"
    db.set_job(jid, state=state, progress=1.0, finished=time.time(), pid=None,
               detail=f"{made} clip{'s' if made != 1 else ''} ready", error=None)
    try:
        from clipper import status
        status.record_speed(jid)
    except Exception:
        pass
    if made:
        db.log(f"{made} new clip{'s' if made != 1 else ''} from \"{str(video['title'])[:60]}\" "
               + ("waiting for your OK." if waiting else "saved."), job_id=jid, tell=True)
    if src.get("mode") == "sections":                 # the downloaded pieces aren't needed any more
        for path in (src.get("sections") or {}).values():
            try:
                os.remove(path)
            except OSError:
                pass
    return OK


def _render_all(jid, video, ch, folder, src, tr, cands, want, g):
    """Renders moments until the job has `want` good clips (skipping ones already made)."""
    regen = set(db.job(jid)["options"].get("regenerate", []))
    for rid in regen:
        old = db.clip(rid)
        if old and old["job_id"] == jid:
            db.set_clip(rid, state="rejected", note="replaced by a regenerated clip")
    existing = db.clips("job_id=?", jid, limit=500)
    used = {c["rank"] for c in existing}
    good = [c for c in existing if c["state"] not in ("qa_failed", "rejected", "regenerate")]
    for n, c in enumerate(cands):
        c.setdefault("rank", n + 1)
    todo = [c for c in cands if c["rank"] not in used]
    need = want - len(good)
    total, made, k = max(1, min(need, len(todo))), 0, 0
    for c in todo:
        if need <= 0:
            break
        p = _stage(jid, "editing", f"framing clip {k + 1} of {total}")
        if _make_clip(jid, video, ch, folder, src, tr, c, g, p, min(k, total - 1), total):
            made += 1
            need -= 1
        k += 1
    if regen:
        if need > 0:
            db.log("There were no other strong moments left in that video for a replacement.", job_id=jid, tell=True)
        opts = db.job(jid)["options"]
        opts.pop("regenerate", None)
        db.set_job(jid, options=opts)
    return made


def _make_clip(jid, video, ch, folder, src, tr, c, g, p, k, total):
    from clipper import learning
    cid = db.add_clip(job_id=jid, video_id=video["id"], channel=ch["id"], rank=c["rank"], start=c["start"], end=c["end"],
                      duration=c["duration"], score=c["score"], scores=c["scores"], hook=c.get("hook"),
                      hook_type=c.get("hook_type"), reason=c.get("reason"), transcript=c["transcript"],
                      topic=c.get("topic"), title=c.get("title"), titles=c.get("titles", []),
                      description=c.get("description"), hashtags=c.get("hashtags", []), tags=c.get("tags", []),
                      category_id=c.get("category_id"), audience=c.get("audience"), pinned_comment=c.get("pinned_comment"),
                      state="rendered", note="; ".join(c.get("notes", []))[:300])
    clip = db.clip(cid)
    clip["keywords"], clip["hook_text"] = c.get("keywords", []), c.get("hook_text", "")
    clip["fingerprint"] = qa.fingerprint(clip, video["id"])
    db.set_clip(cid, fingerprint=clip["fingerprint"])
    dup = qa.duplicate_of(clip)
    if dup:
        db.set_clip(cid, state="rejected", note=f"same moment as clip {dup}")
        return False
    path, offset = acquire.section(src, folder, c["start"], c["end"])
    info = media.probe(path)
    dur = c["end"] - c["start"]
    analysis = crop.analyze(path, max(0.0, c["start"] - offset), dur,
                            lambda f, d="": p((k + f * 0.15) / total, f"clip {k + 1}/{total}: {d}"))
    ws = [dict(w, s=w["s"] - c["start"], e=w["e"] - c["start"]) for w in tr["words"] if c["start"] <= w["s"] <= c["end"]]
    learned = learning.preferences(ch["id"])
    plan = crop.plan(analysis, dur, ws, info["width"], info["height"], prefer=learned.get("layout"))
    media.dump(plan, folder / f"plan_{cid}.json")
    style = captions.resolve(captions.choose_style(ch, c.get("caption_style"), learned), ch)
    db.set_clip(cid, layout=plan["layout"], caption_style=style["name"])
    p((k + 0.15) / total, f"rendering clip {k + 1}/{total}")
    db.set_job(jid, state="rendering")
    rep = render.render(folder, clip, tr["words"], plan, (path, offset), ch, style, g,
                        progress=lambda f, d="": p((k + 0.15 + f * 0.75) / total, f"clip {k + 1}/{total}: {d}"))
    db.set_job(jid, state="quality_check", heartbeat=time.time())
    visible = crop.visible_share(plan, analysis)
    result = qa.check(rep["path"], clip, rep, video, ch, plan, visible)
    if not result["passed"] and qa.autofix(rep["path"], result):
        result = qa.check(rep["path"], clip, rep, video, ch, plan, visible)
        result["autofixed"] = True
    if not result["passed"]:
        report = qa.save_report(folder, cid, result)
        db.set_clip(cid, state="qa_failed", qa=result, path=rep["path"], thumb=rep["thumb"], note=qa.describe(result))
        db.log(f"Clip {cid} didn't pass the quality check ({qa.describe(result)}). Report: {report}", "warn",
               job_id=jid, clip_id=cid, tell=True)
        return False
    if not settings.allowed(ch["id"], "save"):
        os.remove(rep["path"])
        db.set_clip(cid, state="rejected", note="saving is switched off")
        return False
    lib = data_dir("library", ch["id"])
    stamp = time.strftime("%Y-%m-%d")
    final = lib / f"{stamp}_job{jid}_clip{cid}.mp4"
    shutil.move(rep["path"], final)
    thumb = lib / f"{stamp}_job{jid}_clip{cid}.jpg"
    if os.path.exists(rep["thumb"]):
        shutil.move(rep["thumb"], thumb)
    state = "awaiting_approval" if ch["permissions"].get("require_approval", True) else "ready"
    db.set_clip(cid, state=state, qa=result, path=str(final), thumb=str(thumb), duration=rep["duration"])
    db.log(f"Clip {cid} ready: \"{clip['title']}\" ({rep['duration']:.0f}s, {rep['layout']}, {rep['style']} captions).",
           job_id=jid, clip_id=cid)
    return True


def regenerate(clip_id):
    """Asks for a replacement for one clip: the next best moment from the same video (or, if
    there isn't one, the same moment again with a different caption style)."""
    c = db.clip(clip_id)
    if not c:
        raise KeyError("no such clip")
    job = db.job(c["job_id"])
    opts = job["options"]
    opts["regenerate"] = sorted(set(opts.get("regenerate", [])) | {clip_id})
    db.set_clip(clip_id, state="regenerate")
    db.set_job(job["id"], state="queued", options=opts, held=0, not_before=0, attempts=0)
    db.log(f"Making a replacement for clip {clip_id}.", job_id=job["id"], clip_id=clip_id)
    return f"Okay, I'll make a replacement for clip {clip_id}."

