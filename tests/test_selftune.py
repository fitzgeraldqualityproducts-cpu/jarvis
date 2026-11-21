"""Clips no longer get stuck "rendering"; and the clipper learning from its mistakes and from Connor."""
import json
import os
import shutil
import time

from conftest import SCRIPT, make_video, make_words


# ---------- the "stuck on rendering" bug (no ffmpeg on PATH for yt-dlp) ----------

def test_ffmpeg_is_put_on_path_for_yt_dlp(monkeypatch, tmp_path):
    from clipper import acquire
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffmpeg") is None
    assert acquire.ffmpeg_on_path()
    found = shutil.which("ffmpeg")
    assert found and os.path.realpath(found) != found or os.path.isfile(found)


def test_section_falls_back_to_the_whole_video(monkeypatch, tmp_path):
    from clipper import acquire
    calls = []
    whole = make_video(tmp_path / "whole.mp4", 3)

    def fake(url, folder, opts, label):
        calls.append(label)
        if "download_ranges" in opts:
            raise acquire.AcquireError("download failed: ERROR: You have requested downloading the video partially, "
                                       "but ffmpeg is not installed. Aborting")
        return whole
    monkeypatch.setattr(acquire, "_download", fake)
    src = {"mode": "sections", "audio": "a.wav", "sections": {}, "url": "https://www.twitch.tv/videos/1"}
    assert acquire.section(src, tmp_path, 100, 130) == (str(whole), 0.0)
    assert acquire.section(src, tmp_path, 300, 330) == (str(whole), 0.0)
    assert calls == ["section_970_1330", "source"]            # the whole video is fetched only once


def _job(monkeypatch, tmp_path, n=2, review=None):
    from clipper import llm, settings, sources, transcribe
    words = make_words(SCRIPT, start=0.3, wpm=170)

    def fake_llm(prompt, timeout):
        if "checking YouTube Shorts clips" in prompt:
            return json.dumps([{"clip": i, "accurate": True, "standalone": True, "verdict": "keep",
                                "titles": [f"Title {i}"], "descriptions": ["About money."], "hashtags": ["#money"]}
                               for i in range(6)])
        good = {k: 8 for k in ("hook", "curiosity", "emotion", "entertainment", "story", "uniqueness", "clarity")}
        return json.dumps([{"start_id": 2, "end_id": 4, "scores": good, "context_complete": 8, "category": "story", "hook_type": "story"},
                           {"start_id": 7, "end_id": 10, "scores": good, "context_complete": 8, "category": "relatable", "hook_type": "question"}])
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: transcribe.build(words, duration=words[-1]["e"] + 0.5))
    monkeypatch.setattr(llm, "RUNNER", fake_llm)
    settings.ensure_default_channel()
    settings.update_channel("main", length_mode="custom", clip_min=6, clip_max=25, clip_hard_max=30, min_score=5)
    src = make_video(tmp_path / "podcast.mp4", int(words[-1]["e"]) + 2)
    v, job, _ = sources.add(str(src), max_clips=n)
    return job


def test_when_no_clip_can_be_made_the_job_says_so_and_nothing_is_left_rendering(monkeypatch, tmp_path):
    from clipper import acquire, db, pipeline
    job = _job(monkeypatch, tmp_path)
    real = acquire.section

    def broken(*a, **k):
        raise acquire.AcquireError("download failed: ffmpeg is not installed")
    monkeypatch.setattr(acquire, "section", broken)
    assert pipeline.run_job(job["id"]) == pipeline.RETRY
    j = db.job(job["id"])
    assert j["state"] == "queued" and "none of the clips could be made" in j["error"]
    assert not db.clips("job_id=? AND state='rendered'", job["id"])
    # once downloads work again, Retry makes the clips
    monkeypatch.setattr(acquire, "section", real)
    db.set_job(job["id"], not_before=0)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    clips = db.clips("job_id=? AND state='awaiting_approval'", job["id"])
    assert len(clips) == 2 and all(os.path.exists(c["path"]) for c in clips)


def test_clips_stuck_by_an_older_version_are_made_again(monkeypatch, tmp_path):
    from clipper import db, jobs, pipeline
    job = _job(monkeypatch, tmp_path, n=1)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    c = db.clips("job_id=?", job["id"])[0]
    db.set_clip(c["id"], state="rendered", path=None)                # what the old bug left behind
    db.set_job(job["id"], state="done")
    jobs.recover()
    assert db.job(job["id"])["state"] == "queued"
    assert pipeline.run_job(job["id"]) == pipeline.OK
    live = db.clips("job_id=? AND state='awaiting_approval'", job["id"])
    assert len(live) == 1 and os.path.exists(live[0]["path"])


def test_tool_decorator_allows_a_description_argument():
    import skills.clipper as sk
    assert callable(sk.edit_clip) and callable(sk.clipper_lessons)


# ---------- learning from Connor ----------

def _fake_clip(cid=None, **kw):
    from clipper import db
    row = dict(job_id=1, video_id=1, channel="main", rank=1, start=10.0, end=40.0, duration=30.0, score=70,
               hook_type="question", caption_style="bold", layout="track", state="awaiting_approval",
               title="t", description="d", extra={"category": "funny"})
    row.update(kw)
    return db.add_clip(**row)


def test_learns_style_switches_and_length_from_edits():
    from clipper import moments, selftune, settings
    settings.ensure_default_channel()
    for i in range(4):
        cid = _fake_clip()
        c = __import__("clipper").db.clip(cid)
        selftune.edited(c, {"caption_style": "cinematic", "zooms": False, "start": 12.0, "end": 32.0})
    lessons = settings.channel("main")["tuned"]["lessons"]
    assert lessons["caption_style"]["value"] == "cinematic"
    assert lessons["zooms"]["value"] is False
    assert lessons["length_scale"]["value"] < 0.85
    assert lessons["late_start"]["value"] is True
    ch = selftune.apply(moments.with_lengths(settings.channel("main"), "30_45"))
    assert ch["caption_style"] == "cinematic" and ch["zooms"] is False and ch["clip_max"] < 45
    assert any("hook" in line for line in ch["lessons"])
    # undo one: it goes and isn't learned again
    selftune.forget("main", "zooms")
    cid = _fake_clip()
    selftune.edited(__import__("clipper").db.clip(cid), {"zooms": False})
    assert "zooms" not in settings.channel("main")["tuned"]["lessons"]
    # his own setting wins over a lesson
    selftune.user_set("main", ["caption_style"])
    assert "caption_style" not in settings.channel("main")["tuned"]["lessons"]


def test_one_edit_teaches_nothing():
    from clipper import db, selftune, settings
    settings.ensure_default_channel()
    selftune.edited(db.clip(_fake_clip()), {"caption_style": "gaming"})
    assert not (settings.channel("main").get("tuned") or {}).get("lessons")


def test_learns_spellings_he_fixes_twice(monkeypatch):
    from clipper import db, selftune, settings
    settings.ensure_default_channel()
    monkeypatch.setattr(selftune, "_clip_words", lambda clip: [{"w": "conner", "s": 12.0, "e": 12.4}])
    for _ in range(2):
        selftune.edited(db.clip(_fake_clip()), {"words": {"12.000": "Connor"}})
    ch = selftune.apply(settings.channel("main"))
    assert ch["fixes"]["conner"] == "Connor" and "Connor" in ch["names"]


def test_learns_what_he_keeps_and_throws_away():
    from clipper import moments, publish, selftune, settings
    settings.ensure_default_channel()
    for _ in range(5):
        publish.reject(_fake_clip(extra={"category": "rant"}), "too slow to start")
        publish.approve(_fake_clip(extra={"category": "funny"}), upload=False)
    taste = selftune.preferences("main")
    assert taste["category"]["funny"] > 0 > taste["category"]["rant"]
    ch = selftune.apply(settings.channel("main"))
    assert any("too slow to start" in line for line in ch["lessons"])
    c = {"scores": {k: 6 for k in moments.CRITERIA}, "context_complete": 6, "duration": 30, "context_ok": True, "category": "funny"}
    r = dict(c, category="rant")
    assert moments.score(c, ch, taste) > moments.score(r, ch, taste)
    assert "keep funny" in selftune.report("main")


# ---------- learning from its own mistakes ----------

def test_a_failed_quality_check_is_retried_safer_and_remembered(monkeypatch, tmp_path):
    from clipper import db, pipeline, qa, selftune
    job = _job(monkeypatch, tmp_path, n=2)
    real = qa.check
    seen = []

    def picky(path, clip, rep, video, ch, plan=None, visible=None):
        r = real(path, clip, rep, video, ch, plan, visible)
        seen.append(plan["layout"])
        if plan["layout"] != "fit":                         # pretend the face got cut off
            r["checks"]["speaker_visible"] = {"ok": False, "critical": True, "value": 0.5, "detail": ""}
            r["critical"] = r["critical"] + ["speaker_visible"]
            r["passed"] = False
        return r
    monkeypatch.setattr(qa, "check", picky)
    monkeypatch.setattr(qa, "autofix", lambda *a: False)
    db.set_job(job["id"])
    from clipper import settings
    settings.update_channel("main", layout="full")       # centre crop, so there's a face to cut off
    assert pipeline.run_job(job["id"]) == pipeline.OK
    clips = db.clips("job_id=? AND state='awaiting_approval'", job["id"])
    assert len(clips) == 2
    assert seen.count("fit") == 2 and seen.count("track") == 1   # the second clip started with what worked
    assert selftune.job_fallback(job["id"]) == {"layout": "fit"}
    s = selftune.summary("main")
    assert s["qa_recovered"] == 1 and s["qa_fails"]["speaker_visible"] == 1


def test_crashes_in_clipper_code_are_recorded_for_self_repair(monkeypatch, tmp_path):
    from clipper import db, pipeline, render
    job = _job(monkeypatch, tmp_path, n=1)

    def crash(*a, **k):
        return {}["missing"]                                  # a KeyError inside the clipper
    monkeypatch.setattr(render, "render", crash)
    pipeline.run_job(job["id"])
    bugs = db.q("SELECT * FROM feedback WHERE kind='bug'")
    assert bugs and "KeyError" in json.loads(bugs[0]["detail"])["error"]


def test_self_repair_hands_the_crash_to_the_mechanic_once(monkeypatch):
    import sys
    import types
    from clipper import db, selftune
    asked = []
    fake = types.SimpleNamespace(enabled=lambda: True,
                                 fix=lambda *a, **k: asked.append(a) or {"fixed": True, "changed": ["clipper/render.py"]})
    monkeypatch.setitem(sys.modules, "repair", fake)
    selftune.record("bug", job_id=None, channel="main", where="clip", error="KeyError: 'x'", trace="clipper/render.py", sig="s1")
    selftune.repair_pending()
    selftune.repair_pending()
    assert len(asked) == 1 and asked[0][1] == "clipper"


def test_failures_are_sorted_by_cause():
    from clipper import selftune
    assert selftune.classify("download failed: Connection reset by peer") == "network"
    assert selftune.classify("there's hardly any speech or sound") == "no_speech"
    assert selftune.classify("TypeError: open() got an unexpected keyword argument") == "bug"


# ---------- speed ----------

def test_encoder_speed_follows_this_computer():
    from clipper import selftune, settings
    g = settings.general()
    assert selftune.preset(g) == "veryfast"                   # nothing measured yet
    for _ in range(10):
        selftune.speed("render", 40, 10)                      # 4 s of work per second of clip
    assert selftune.preset(g) == "superfast"
    assert selftune.preset(dict(g, x264_preset="medium")) == "medium"


def test_editor_rerender_reuses_the_face_analysis(monkeypatch, tmp_path):
    from clipper import crop, db, pipeline
    job = _job(monkeypatch, tmp_path, n=1)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    c = db.clips("job_id=?", job["id"])[0]
    calls = []
    real = crop.analyze
    monkeypatch.setattr(crop, "analyze", lambda *a, **k: calls.append(a) or real(*a, **k))
    pipeline.edit(c["id"], {"start": c["start"] + 1.0, "caption_style": "minimal"})
    assert pipeline.run_job(job["id"]) == pipeline.OK
    assert calls == [] and db.clip(c["id"])["caption_style"] == "minimal"


def test_dashboard_shows_and_undoes_lessons():
    from clipper import api, db, selftune, settings
    settings.ensure_default_channel()
    for _ in range(3):
        selftune.edited(db.clip(_fake_clip()), {"layout": "fit"})
    s = api.state()
    assert [l["key"] for l in s["tuning"]["lessons"]] == ["layout"]
    assert api.action({"action": "unlearn", "key": "layout"})["ok"]
    assert not api.state()["tuning"]["lessons"]
