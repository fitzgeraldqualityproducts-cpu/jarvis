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
from pathlib import Path

from clipper import acquire, caption_fix, captions, crop, data_dir, db, media, metadata, moments, presets, qa, render, selftune, settings, transcribe

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
    _mark(job_id, state, progress=0.0, detail=detail)
    return _progress(job_id, state)


def _mark(job_id, state, **more):
    """Moves the job to a step and records real start/end times for the progress page (a step that
    repeats, like rendering each clip, keeps its first start time)."""
    j = db.job(job_id)
    opts = j["options"] if j else {}
    stages = opts.setdefault("stages", {})
    now = time.time()
    for k, v in stages.items():                     # the step before this one is finished
        if k != state and not v.get("end"):
            v["end"] = now
    if state in stages:
        stages[state].pop("end", None)
    else:
        stages[state] = {"start": now}
    db.set_job(job_id, state=state, heartbeat=now, options=opts, **more)


def _finish_stages(job_id):
    j = db.job(job_id)
    if not j:
        return
    opts = j["options"]
    for v in (opts.get("stages") or {}).values():
        v.setdefault("end", time.time())
    db.set_job(job_id, options=opts)


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
    ch = configure(ch, job.get("options") or {})
    folder = folder_for(job_id)
    try:
        return _run(job, video, ch, folder)
    except Stop:
        db.set_job(job_id, state="queued", detail="paused (emergency stop or pause)", pid=None)
        db.log("Stopped safely; it'll carry on when you resume.", job_id=job_id)
        return STOPPED
    except (Fatal, acquire.NotPermitted) as e:
        selftune.record("job_failed", job_id=job_id, channel=job["channel"], why=selftune.classify(e), error=str(e)[:300])
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
        selftune.bug("job", job_id=job_id)
        return _retry(job_id, (job.get("attempts") or 0) + 1, e)


def _retry(job_id, attempts, e):
    limit = int(settings.general().get("max_attempts", 3))
    detail = f"{type(e).__name__}: {e}"[:500]
    if attempts >= limit:
        selftune.record("job_failed", job_id=job_id, why=selftune.classify(detail), error=detail[:300])
        db.set_job(job_id, state="failed", attempts=attempts, error=detail, finished=time.time(), pid=None)
        db.log(f"Job failed after {attempts} tries: {detail}", "error", job_id=job_id, tell=True)
        return FATAL
    wait = 60 * (4 ** (attempts - 1))                  # 1 min, 4 min, 16 min...
    db.set_job(job_id, state="queued", attempts=attempts, error=detail, not_before=time.time() + wait, pid=None,
               detail=f"will retry in {wait // 60} min")
    db.log(f"Problem ({detail}); retrying in {wait // 60} minutes (try {attempts + 1} of {limit}).", "warn", job_id=job_id)
    return RETRY


def configure(ch, opts):
    """The settings one job runs with: the channel's, then its preset (the video's own choice, else the
    channel's default), the clip length (the video's choice, else the channel's, else the preset's),
    then what the clipper has learned from you."""
    ch = presets.apply(ch, opts.get("preset"))
    length = opts.get("length")
    if isinstance(length, list):
        length = tuple(length)
    if not length and ch.get("length_mode", "auto") == "auto":
        length = presets.length_for(ch["preset"])
    return selftune.apply(moments.with_lengths(ch, length))


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
    tr = media.load(tpath) or _cached(video, "transcript.json")
    if tr and not tpath.exists():
        media.dump(tr, tpath)
        db.log("Reused the transcript from the last time this video was processed.", job_id=jid)
    if not tr:
        p = _stage(jid, "transcribing", "listening to every word")
        names = ", ".join(ch.get("names") or [])
        t0 = time.time()
        tr = transcribe.transcribe(src["audio"], p, prompt=f"Names: {names}." if names else "", language=ch.get("language", "en"))
        selftune.speed("transcribe", time.time() - t0, float(tr.get("duration") or video.get("duration") or 0))
        media.dump(tr, tpath)
        _cache(video, "transcript.json", tr)
        db.log(f"Transcribed {len(tr['words'])} words ({tr['engine']}, {tr['speakers']} voice"
               f"{'s' if tr['speakers'] != 1 else ''}).", job_id=jid)
    if len(tr["words"]) < 40 and not tr.get("loud"):
        raise Fatal("there's hardly any speech or sound worth clipping in this video, so I made no clips")
    if not tr["sentences"]:
        raise Fatal("I couldn't find any speech in this video to build clips around")

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
        learned = selftune.merge_prefs(learned, selftune.preferences(ch["id"]))   # plus what Connor keeps / throws away
        rep = {}
        cands, method = moments.find(tr, ch, video, max_clips=job["max_clips"] * 2, learned=learned, progress=p, report=rep)
        _stage(jid, "selecting", f"ranking {len(cands)} moments")
        top = cands[:int(job["max_clips"]) + 2]
        metadata.generate(top, video, ch)                    # usually free: the context check wrote them
        for c in cands[len(top):]:
            metadata.fill(c, c.get("meta") or {}, video, ch)  # titles for the rest when they're picked
        found = {"method": method, "candidates": cands, "rejected": rep.get("rejected", [])[:60],
                 "llm_error": rep.get("llm_error"), "made": time.time(), "lengths": [ch["clip_min"], ch["clip_max"], ch["clip_hard_max"]]}
        media.dump(found, cpath)
        db.log(f"Found {len(cands)} strong moment{'s' if len(cands) != 1 else ''} ({method})"
               + (f", turned down {len(found['rejected'])}" if found["rejected"] else "") + ".", job_id=jid)
    cands = found["candidates"]
    for n, c in enumerate(cands):
        c.setdefault("rank", n + 1)
    opts = db.job(jid)["options"]
    if opts.get("rerender"):                          # the editor asked for changes to existing clips
        done = _rerender_all(jid, video, ch, folder, src, tr, opts["rerender"], g)
        _finish_stages(jid)
        _end(jid, video, src, done, "updated")
        return OK
    if not cands:
        _finish_stages(jid)
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
    _finish_stages(jid)
    _end(jid, video, src, made, "new")
    return OK


def _end(jid, video, src, made, kind):
    waiting = db.one("SELECT COUNT(*) n FROM clips WHERE job_id=? AND state='awaiting_approval'", jid)["n"]
    state = "awaiting_approval" if waiting else "done"
    opts = db.job(jid)["options"]
    for k in ("rerender", "pick"):
        opts.pop(k, None)
    db.set_job(jid, state=state, progress=1.0, finished=time.time(), pid=None, options=opts,
               detail=f"{made} clip{'s' if made != 1 else ''} {'ready' if kind == 'new' else 'updated'}", error=None)
    try:
        from clipper import status
        status.record_speed(jid)
    except Exception:
        pass
    if made and kind == "new":
        db.log(f"{made} new clip{'s' if made != 1 else ''} from \"{str(video['title'])[:60]}\" "
               + ("waiting for your OK." if waiting else "saved."), job_id=jid, tell=True)
    elif made:
        db.log(f"Updated {made} clip{'s' if made != 1 else ''} with your edits.", job_id=jid, tell=True)
    if src.get("mode") == "sections":                 # the downloaded pieces aren't needed any more
        for path in (src.get("sections") or {}).values():
            try:
                os.remove(path)
            except OSError:
                pass


def _render_all(jid, video, ch, folder, src, tr, cands, want, g):
    """Renders moments until the job has `want` good clips (skipping ones already made), plus any
    moments picked by hand on the dashboard."""
    opts = db.job(jid)["options"]
    regen = set(opts.get("regenerate", []))
    picks = [int(r) for r in opts.get("pick", [])]
    for rid in regen:
        old = db.clip(rid)
        if old and old["job_id"] == jid:
            db.set_clip(rid, state="rejected", note="replaced by a regenerated clip")
    # a clip left half-made (the render stopped or crashed before it finished) is made again
    db.run("DELETE FROM clips WHERE job_id=? AND (path IS NULL OR path='') AND (state='rendered' OR "
           "(state='qa_failed' AND note LIKE 'the render failed%'))", jid)
    existing = db.clips("job_id=?", jid, limit=500)
    used = {c["rank"] for c in existing}
    good = [c for c in existing if c["state"] not in ("qa_failed", "rejected", "regenerate", "deleted")]
    picked = [c for c in cands if c["rank"] in picks and c["rank"] not in used]
    todo = picked + [c for c in cands if c["rank"] not in used and c["rank"] not in picks]
    need = max(want - len(good), 0) + len(picked)
    total, made, k = max(1, min(need, len(todo))), 0, 0
    errors = []
    for c in todo:
        if need <= 0:
            break
        p = _stage(jid, "editing", f"framing clip {k + 1} of {total}")
        try:
            ok = _make_clip(jid, video, ch, folder, src, tr, c, g, p, min(k, total - 1), total)
        except (Stop, Fatal, acquire.NotPermitted):
            raise
        except Exception as e:                       # one bad clip never sinks the whole job
            traceback.print_exc()
            selftune.bug("clip", job_id=jid)
            db.log(f"Couldn't make the clip at {c['start']:.0f}s ({type(e).__name__}: {str(e)[:200]}); moving on.",
                   "warn", job_id=jid)
            errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            ok = False
        if ok:
            made += 1
            need -= 1
        k += 1
    if errors and not made and not good:
        # nothing could be made at all: that's a failure to show (and retry), not "0 clips ready"
        raise RuntimeError(f"none of the clips could be made ({errors[-1]})")
    if regen:
        if need > 0:
            db.log("There were no other strong moments left in that video for a replacement.", job_id=jid, tell=True)
        opts = db.job(jid)["options"]
        opts.pop("regenerate", None)
        db.set_job(jid, options=opts)
    return made


def _rerender_all(jid, video, ch, folder, src, tr, edits_by_clip, g):
    made, items = 0, list(edits_by_clip.items())
    for k, (cid, edits) in enumerate(items):
        clip = db.clip(int(cid))
        if not clip or clip["job_id"] != jid:
            continue
        p = _stage(jid, "editing", f"applying your edits to clip {cid}")
        try:
            if _make_clip(jid, video, ch, folder, src, tr, None, g, p, k, len(items), existing=clip, edits=edits):
                made += 1
        except (Stop, Fatal, acquire.NotPermitted):
            raise
        except Exception as e:
            traceback.print_exc()
            selftune.bug("edit", job_id=jid, clip_id=int(cid))
            db.log(f"Couldn't apply the edits to clip {cid}: {type(e).__name__}: {str(e)[:200]}", "warn", job_id=jid, clip_id=cid, tell=True)
    return made


def _analysis(folder, path, start, dur, progress):
    """Face/motion analysis for a stretch of the source, reused when an edit only changes things
    inside a stretch already analysed (re-renders from the editor skip the slow part)."""
    store = Path(folder) / "analysis"
    store.mkdir(exist_ok=True)
    for f in sorted(store.glob("*.json")):
        a = media.load(f) or {}
        if a.get("path") == str(path) and a["start"] <= start + 0.02 and a["end"] >= start + dur - 0.02:
            shift = start - a["start"]
            samples = [dict(x, t=round(x["t"] - shift, 3)) for x in a["analysis"]["samples"] if -0.01 <= x["t"] - shift <= dur + 0.01]
            if samples:
                return dict(a["analysis"], samples=samples)
    t0 = time.time()
    an = crop.analyze(path, start, dur, progress)
    selftune.speed("analyze", time.time() - t0, dur)
    media.dump({"path": str(path), "start": start, "end": start + dur, "analysis": an}, store / f"{start:.2f}_{start + dur:.2f}.json")
    return an


def snap(tr, start, end):
    """The editor's start/end (seconds) moved to the nearest word edges, so no word is cut in half."""
    words = tr["words"]
    inside = [w for w in words if w["e"] > start + 0.05 and w["s"] < end - 0.05]
    if not inside:
        return round(start, 3), round(end, 3)
    first, last = inside[0], inside[-1]
    i0, i1 = words.index(first), words.index(last)
    prev_end = words[i0 - 1]["e"] if i0 > 0 else 0.0
    next_start = words[i1 + 1]["s"] if i1 + 1 < len(words) else last["e"] + 1.0
    s = max(prev_end + 0.02, min(start, first["s"]) if start <= first["s"] else first["s"] - 0.12, 0.0)
    e = min(next_start - 0.03, max(end, last["e"] + 0.05) if end >= last["e"] else last["e"] + 0.3)
    return round(s, 3), round(e, 3)


def _make_clip(jid, video, ch, folder, src, tr, c, g, p, k, total, existing=None, edits=None):
    """Frames, captions, renders and checks one clip. existing + edits = the editor's changes to a
    clip that's already made (it's re-rendered in place, same clip number)."""
    from clipper import learning
    edits = edits or {}
    if existing:
        cid = existing["id"]
        extra = dict(existing.get("extra") or {})
        start, end = float(existing["start"]), float(existing["end"])
        if edits.get("start") is not None or edits.get("end") is not None:
            start, end = snap(tr, float(edits.get("start", start)), float(edits.get("end", end)))
        if end - start < 3:
            raise ValueError("the clip would be shorter than 3 seconds")
        text = " ".join(w["w"] for w in caption_fix.words_for(tr["words"], folder, start, end) if start <= w["s"] <= end)
        changes = {"start": start, "end": end, "duration": round(end - start, 3), "transcript": text}
        for key in ("title", "description", "pinned_comment"):
            if edits.get(key):
                changes[key] = str(edits[key])[:4800]
        restore = {k2: existing[k2] for k2 in ("start", "end", "duration", "transcript", "layout", "caption_style")}
        restore["extra"] = dict(existing.get("extra") or {})
        db.set_clip(cid, **changes)
        extra["edits"] = {**(extra.get("edits") or {}), **{k2: v for k2, v in edits.items() if k2 not in ("title", "description")}}
        next_version = int(extra.get("version", 1)) + 1     # saved only once the new file is in place
        db.set_clip(cid, extra=extra)
        clip = db.clip(cid)
        c = dict(extra, **{k2: clip[k2] for k2 in ("start", "end", "duration", "transcript", "hook", "hook_type", "title")})
        c["hook_text"] = edits.get("hook_text", extra.get("hook_text", "")) if "hook_text" in edits else extra.get("hook_text", "")
    else:
        if not (c.get("meta") or {}).get("titles") and (c.get("method") == "claude" or c.get("needs_titles")) \
                and not c.get("titles_done") and llm_ok():
            metadata.generate([c], video, ch)            # a hand-picked moment from further down the list
            c["titles_done"] = True
        extra = {k2: c.get(k2) for k2 in ("category", "descriptions", "hook_texts", "pinned_ideas", "highlight", "parts",
                                          "measures", "check", "notes", "similar", "hook_text", "keywords", "method", "caption_fixes",
                                          "strongest", "strongest_at", "audience", "weaknesses", "manual")
                 if c.get(k2) is not None}
        extra["version"] = next_version = 1
        extra["preset"] = ch.get("preset", "balanced")
        restore = None
        cid = db.add_clip(job_id=jid, video_id=video["id"], channel=ch["id"], rank=c["rank"], start=c["start"], end=c["end"],
                          duration=c["duration"], score=c["score"], scores=c["scores"], hook=c.get("hook"),
                          hook_type=c.get("hook_type"), reason=c.get("reason"), transcript=c["transcript"],
                          topic=c.get("topic"), title=c.get("title"), titles=c.get("titles", []),
                          description=c.get("description"), hashtags=c.get("hashtags", []), tags=c.get("tags", []),
                          category_id=c.get("category_id"), audience=c.get("audience"), pinned_comment=c.get("pinned_comment"),
                          state="rendered", note="; ".join(c.get("notes", []))[:300], extra=extra)
    clip = db.clip(cid)
    clip["keywords"], clip["hook_text"] = c.get("keywords", []), c.get("hook_text", "")
    clip["highlight"] = c.get("highlight") or []
    clip["edits"] = (clip.get("extra") or {}).get("edits") or {}
    if not existing:
        clip["fingerprint"] = qa.fingerprint(clip, video["id"])
        db.set_clip(cid, fingerprint=clip["fingerprint"])
        dup = qa.duplicate_of(clip)
        if dup:
            db.set_clip(cid, state="rejected", note=f"same moment as clip {dup}")
            return False
    try:
        path, offset = acquire.section(src, folder, clip["start"], clip["end"])
        info = media.probe(path)
        dur = clip["end"] - clip["start"]
        analysis = _analysis(folder, path, max(0.0, clip["start"] - offset), dur,
                             lambda f, d="": p((k + f * 0.15) / total, f"clip {k + 1}/{total}: {d}"))
        cap_note = ""
        if not (clip["edits"].get("words") and not caption_fix.covered(folder, clip["start"], clip["end"])):
            # (word corrections made in the editor are tied to the words they were made on, so a clip
            # with some keeps those words)
            p((k + 0.16) / total, f"clip {k + 1}/{total}: double-checking every caption word")
            cap_note = caption_fix.refine(tr, folder, src.get("audio"), clip["start"], clip["end"], ch,
                                          log=lambda m: db.log(m, "warn", job_id=jid, clip_id=cid))
        words = caption_fix.words_for(tr["words"], folder, clip["start"], clip["end"])
        if cap_note:
            db.set_clip(cid, transcript=" ".join(w["w"] for w in words if clip["start"] <= w["s"] <= clip["end"])[:20000])
        ws = [dict(w, s=w["s"] - clip["start"], e=w["e"] - clip["start"]) for w in words if clip["start"] <= w["s"] <= clip["end"]]
        learned = learning.preferences(ch["id"])
        fb = {} if existing else dict(selftune.job_fallback(jid))     # what fixed an earlier clip of this video
        g2 = dict(g, x264_preset=selftune.preset(g))
        first_fail = None
        for attempt in range(2):
            e = dict(fb, **clip["edits"])
            if fb.get("caption"):
                e["caption"] = {**fb["caption"], **(clip["edits"].get("caption") or {})}
            manual = e.get("crop") if isinstance(e.get("crop"), dict) else None
            layout_pref = e.get("layout") or (ch.get("layout") if ch.get("layout") not in (None, "auto") else learned.get("layout"))
            plan = crop.plan(analysis, dur, ws, info["width"], info["height"], prefer=layout_pref, manual=manual)
            if manual and manual.get("zoom") and plan.get("layout") == "track" and not plan.get("manual"):
                plan["zoom"] = float(manual["zoom"])
            media.dump(plan, folder / f"plan_{cid}.json")
            run_ch = dict(ch, **{k2: e[k2] for k2 in ("jump_cuts", "remove_fillers", "zooms", "punch_ins", "edit_intensity", "noise_reduction",
                                                      "music_volume", "sound_effects", "hook_overlay", "color_correction", "sharpen")
                                 if k2 in e})
            if e.get("music") is False:
                run_ch["music_folder"] = ""
            run_ch["fixes"] = {**(ch.get("fixes") or {}), **((clip.get("extra") or {}).get("caption_fixes") or {})}
            style_name = e.get("caption_style") or captions.choose_style(ch, c.get("caption_style"), learned, c.get("category"))
            style = captions.resolve(style_name, ch, e.get("caption") or {})
            db.set_clip(cid, layout=plan["layout"] if not plan.get("gameplay") else "gameplay", caption_style=style["name"])
            p((k + 0.15) / total, f"rendering clip {k + 1}/{total}" + (" (safer settings)" if attempt else ""))
            _mark(jid, "rendering")
            t0 = time.time()
            rep = render.render(folder, clip, words, plan, (path, offset), run_ch, style, g2,
                                progress=lambda f, d="": p((k + 0.15 + f * 0.75) / total, f"clip {k + 1}/{total}: {d}"))
            selftune.speed("render", time.time() - t0, rep["duration"])
            _mark(jid, "quality_check")
            visible = crop.visible_share(plan, analysis)
            qa_ch = ch if not existing else dict(ch, clip_min=min(float(ch["clip_min"]), 5.0),
                                                 clip_hard_max=max(float(ch["clip_hard_max"]), min(178.0, rep["duration"])))
            result = qa.check(rep["path"], db.clip(cid), rep, video, qa_ch, plan, visible)
            if not result["passed"] and qa.autofix(rep["path"], result):
                result = qa.check(rep["path"], db.clip(cid), rep, video, qa_ch, plan, visible)
                result["autofixed"] = True
            if result["passed"] or attempt or existing:
                break
            more = selftune.fallback_for(result)
            if not more or all(fb.get(k2) == v for k2, v in more.items()):
                break
            first_fail = result                       # learn from the mistake: one more try, safer
            db.log(f"Clip {cid} failed the quality check ({qa.describe(result)}); trying once more with a safer setting.",
                   "warn", job_id=jid, clip_id=cid)
            try:
                os.remove(rep["path"])
            except OSError:
                pass
            fb = dict(fb, **more)
        if result["passed"] and first_fail:
            selftune.remember_fallback(jid, fb)
            selftune.record("qa_recovered", clip_id=cid, job_id=jid, checks=first_fail["critical"], fix=fb)
            db.log(f"The safer setting worked for clip {cid}; the rest of this video's clips will use it from the start.",
                   job_id=jid, clip_id=cid)
        elif not result["passed"]:
            selftune.record("qa_failed", clip_id=cid, job_id=jid, critical=result["critical"], layout=plan["layout"],
                            edit=bool(existing))
        if cap_note:
            rep.setdefault("enhancements", []).append(cap_note)
        db.merge_extra(cid, enhancements=rep.get("enhancements", []), render={k2: rep.get(k2) for k2 in
                       ("fps", "cuts", "punches", "picture", "noise_floor_db", "speech_db", "caption_pages")}, plan_reason=plan.get("reason"))
        if not result["passed"]:
            report = qa.save_report(folder, cid, result)
            if existing:                                  # keep the old version rather than lose the clip
                os.remove(rep["path"])
                db.set_clip(cid, **restore)
                db.log(f"Your edits to clip {cid} didn't pass the quality check ({qa.describe(result)}), so I kept the "
                       f"previous version.", "warn", job_id=jid, clip_id=cid, tell=True)
                return False
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
        tail = f"_v{next_version}" if next_version > 1 else ""
        final = lib / f"{stamp}_job{jid}_clip{cid}{tail}.mp4"
        shutil.move(rep["path"], final)
        thumb = lib / f"{stamp}_job{jid}_clip{cid}{tail}.jpg"
        if os.path.exists(rep["thumb"]):
            shutil.move(rep["thumb"], thumb)
        if existing:
            for old in (existing.get("path"), existing.get("thumb")):
                if old and os.path.exists(old) and old not in (str(final), str(thumb)):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            keep = existing["state"] if existing["state"] in ("awaiting_approval", "approved", "ready") else \
                ("awaiting_approval" if ch["permissions"].get("require_approval", True) else "ready")
            if existing.get("youtube_id"):
                keep = existing["state"]                  # already on YouTube: the new file stays local
            db.set_clip(cid, state=keep, qa=result, path=str(final), thumb=str(thumb), duration=rep["duration"], approved=0
                        if keep == "awaiting_approval" else existing.get("approved", 0))
            db.merge_extra(cid, version=next_version)
            db.log(f"Clip {cid} updated with your edits ({rep['duration']:.0f}s).", job_id=jid, clip_id=cid)
            return True
        state = "awaiting_approval" if ch["permissions"].get("require_approval", True) else "ready"
        db.set_clip(cid, state=state, qa=result, path=str(final), thumb=str(thumb), duration=rep["duration"])
        db.log(f"Clip {cid} ready: \"{clip['title']}\" ({rep['duration']:.0f}s, {rep['layout']}, {rep['style']} captions).",
               job_id=jid, clip_id=cid)
        return True
    except Exception as err:
        if restore:                                   # a failed edit leaves the clip as it was
            db.set_clip(cid, **restore)
        elif not isinstance(err, (Stop, Fatal)):      # never leave a clip "rendering" forever
            db.set_clip(cid, state="qa_failed", note=f"the render failed: {type(err).__name__}: {str(err)[:200]}")
        raise


def edit(clip_id, edits):
    """The editor's changes (new start/end, captions, layout, crop, sound...) are applied by
    re-rendering the clip in the background. Returns what Jarvis says."""
    c = db.clip(int(clip_id))
    if not c:
        raise KeyError("no such clip")
    if c["state"] in ("uploading",):
        return "That clip is uploading right now; try again when it's done."
    clean = _clean_edits(edits)
    quick = {k: clean.pop(k) for k in ("title", "description", "pinned_comment") if k in clean}
    if quick:
        db.set_clip(int(clip_id), **{k: str(v)[:4800] for k, v in quick.items()})
    if not clean:
        return "Saved." if quick else "Nothing to change."
    if quick:
        clean.update(quick)
    from clipper import selftune
    selftune.edited(c, clean)
    job = db.job(c["job_id"])
    opts = job["options"]
    opts.setdefault("rerender", {})[str(clip_id)] = clean
    db.set_job(job["id"], state="queued", options=opts, held=0, not_before=0, attempts=0, error=None)
    db.log(f"Re-rendering clip {clip_id} with your edits.", job_id=job["id"], clip_id=int(clip_id))
    return f"Okay, I'm re-rendering clip {clip_id} with your changes. It'll take about a minute."


EDIT_KEYS = {"start": float, "end": float, "caption_style": str, "caption": dict, "layout": str, "crop": dict,
             "words": dict, "hook_text": str, "jump_cuts": bool, "remove_fillers": bool, "zooms": bool, "punch_ins": bool,
             "noise_reduction": object, "music": bool, "music_volume": float, "sound_effects": bool, "hook_overlay": bool,
             "color_correction": object, "sharpen": object, "title": str, "description": str, "pinned_comment": str,
             "edit_intensity": str}


def _clean_edits(edits):
    out = {}
    for k, v in (edits or {}).items():
        if k not in EDIT_KEYS or v is None:
            continue
        kind = EDIT_KEYS[k]
        if kind is float:
            out[k] = float(v)
        elif kind is bool:
            out[k] = bool(v)
        elif kind is dict:
            if not isinstance(v, dict):
                raise ValueError(f"{k} must be a set of values")
            out[k] = v
        elif kind is str:
            out[k] = str(v)[:4800]
        else:
            out[k] = v if v in (True, False, "auto") else "auto"
    if "caption_style" in out and out["caption_style"] not in captions.STYLES:
        raise ValueError("unknown caption style")
    if "edit_intensity" in out and out["edit_intensity"] not in settings.INTENSITIES:
        raise ValueError("editing intensity must be subtle, normal or strong")
    if "layout" in out and out["layout"] not in settings.LAYOUTS:
        raise ValueError("unknown layout")
    if "crop" in out:
        cr = out["crop"]
        out["crop"] = {k: max(0.0, min(1.0, float(cr[k]))) if k in ("x", "y") else max(1.0, min(2.0, float(cr[k])))
                       for k in ("x", "y", "zoom") if cr.get(k) is not None}
        if cr.get("layout") in ("fit", "split", "stack"):
            out["crop"]["layout"] = cr["layout"]
    if "music_volume" in out:
        out["music_volume"] = max(0.0, min(0.3, out["music_volume"]))
    if "start" in out and "end" in out and out["end"] - out["start"] < 3:
        raise ValueError("a clip needs to be at least 3 seconds long")
    return out


def pick(job_id, ranks):
    """Makes clips from moments chosen by hand on the dashboard (by their rank in the candidate list)."""
    job = db.job(int(job_id))
    if not job:
        raise KeyError("no such job")
    found = media.load(folder_for(job["id"]) / "candidates.json") or {}
    have = {c.get("rank") for c in found.get("candidates", [])}
    ranks = [int(r) for r in ranks if int(r) in have]
    if not ranks:
        return "I couldn't find those moments in that video's list."
    made = {c["rank"] for c in db.clips("job_id=? AND state NOT IN ('rejected','regenerate','deleted')", job["id"], limit=500)}
    ranks = [r for r in ranks if r not in made]
    if not ranks:
        return "Those moments are already clips."
    opts = job["options"]
    opts["pick"] = sorted(set(opts.get("pick", [])) | set(ranks))
    db.set_job(job["id"], state="queued", options=opts, held=0, not_before=0, attempts=0, error=None)
    db.log(f"Making {len(ranks)} more clip{'s' if len(ranks) != 1 else ''} you picked.", job_id=job["id"])
    return f"Okay, making {len(ranks)} clip{'s' if len(ranks) != 1 else ''} from the moments you picked."


def add_range(job_id, start_id, end_id):
    """A clip you chose yourself from the transcript (sentence start_id..end_id, inclusive): it's
    snapped to whole words, measured and scored like any other moment, added to the video's list and
    made. Your boundaries are kept (no extending for context), up to 180 seconds."""
    job = db.job(int(job_id))
    if not job:
        raise KeyError("no such job")
    folder = folder_for(job["id"])
    tr = media.load(folder / "transcript.json")
    found = media.load(folder / "candidates.json")
    if not tr or found is None:
        return "That video hasn't been transcribed yet, so there's nothing to pick from."
    a, b = sorted((int(start_id), int(end_id)))
    sents = tr["sentences"]
    if not (0 <= a <= b < len(sents)):
        return "Those sentences aren't in this video's transcript."
    if sents[b]["e"] - sents[a]["s"] > 180:
        return "That's longer than 3 minutes; pick a shorter stretch."
    if sents[b]["e"] - sents[a]["s"] < 3:
        return "That's under 3 seconds; pick a bit more."
    ch = configure(settings.channel(job["channel"]), job["options"])
    loose = dict(ch, clip_min=3.0, clip_hard_max=180.0)
    text = " ".join(x["text"] for x in sents[a:b + 1])
    c = moments.finalize({"start_id": a, "end_id": b, "start_locked": True, "end_locked": True,
                          "title": sents[a]["text"][:68], "hook": sents[a]["text"],
                          "reason": "picked by you from the transcript", "context_complete": 7.0}, tr, loose)
    if c is None:
        return "I couldn't make a clip from those sentences."
    c.pop("reject", None)
    c["context_ok"], c["manual"], c["method"] = True, True, "manual"
    c["score"] = moments.score(c, ch)
    cands = found.get("candidates") or []
    for k in cands:
        if abs(k["start"] - c["start"]) < 0.5 and abs(k["end"] - c["end"]) < 0.5:
            return pick(job["id"], [k["rank"]])
    c["rank"] = max([k.get("rank", 0) for k in cands] + [0]) + 1
    metadata.fill(c, {}, db.video(job["video_id"]) or {}, ch)
    c["needs_titles"] = True
    found["candidates"] = cands + [c]
    media.dump(found, folder / "candidates.json")
    db.log(f"Added your own moment ({c['duration']:.0f}s, \"{text[:50]}\").", job_id=job["id"])
    return pick(job["id"], [c["rank"]])


def llm_ok():
    from clipper import llm
    try:
        return llm.available()
    except Exception:
        return False


def _cache_key(video):
    import hashlib
    if video.get("platform") == "file" and video.get("path") and os.path.exists(video["path"]):
        st = os.stat(video["path"])
        raw = f"file:{os.path.abspath(video['path'])}:{st.st_size}:{int(st.st_mtime)}"
    else:
        raw = f"{video.get('platform')}:{video.get('external_id')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _cached(video, name):
    try:
        return media.load(data_dir("cache", _cache_key(video)) / name)
    except Exception:
        return None


def _cache(video, name, value):
    try:
        media.dump(value, data_dir("cache", _cache_key(video)) / name)
    except Exception as e:
        print(f"(clipper: couldn't cache {name}: {e})")


def regenerate(clip_id):
    """Asks for a replacement for one clip: the next best moment from the same video (or, if
    there isn't one, the same moment again with a different caption style)."""
    c = db.clip(clip_id)
    if not c:
        raise KeyError("no such clip")
    job = db.job(c["job_id"])
    opts = job["options"]
    opts["regenerate"] = sorted(set(opts.get("regenerate", [])) | {clip_id})
    from clipper import selftune
    selftune.record("regenerated", clip_id=clip_id)
    db.set_clip(clip_id, state="regenerate")
    db.set_job(job["id"], state="queued", options=opts, held=0, not_before=0, attempts=0)
    db.log(f"Making a replacement for clip {clip_id}.", job_id=job["id"], clip_id=clip_id)
    return f"Okay, I'll make a replacement for clip {clip_id}."

