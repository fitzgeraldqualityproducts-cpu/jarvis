"""The viral-content upgrade: lengths, measured rejections, 0-100 scoring, diversity, the context
review, caption styles, filler cuts, audio enhancements, framing, the editor's re-render, picking
moments by hand, the transcript cache, delete/export/upload/link checks and Retry now."""
import io
import json
import time

import numpy as np
import pytest

from conftest import SCRIPT, make_video, make_words


def tr(words=None):
    from clipper import transcribe
    return transcribe.build(words or make_words(SCRIPT, gaps={6: 1.0}))


# ---------- finding moments ----------

def test_length_presets_and_custom():
    from clipper import moments, settings
    ch = settings.channel()
    assert moments.lengths(ch, "15_30")[:2] == (15.0, 30.0)
    assert moments.lengths(ch, "60_90")[:2] == (60.0, 90.0)
    lo, hi, hard = moments.lengths(ch, (20, 40))
    assert (lo, hi) == (20.0, 40.0) and hard >= hi
    custom = dict(ch, length_mode="custom", clip_min=11, clip_max=22, clip_hard_max=25)
    assert moments.lengths(custom) == (11.0, 22.0, 25.0)
    assert moments.with_lengths(ch, "30_45")["clip_max"] == 45.0
    assert moments.min_score({"min_score": 6}) == 60 and moments.min_score({"min_score": 55}) == 55


def test_mostly_silent_clip_is_rejected_with_a_reason():
    from clipper import moments, settings
    script = ["So.", "Well.", "Hmm.", "Yeah.", "Okay then.", "Right."]
    words = make_words(script, gaps={k: 4.0 for k in range(6)})
    t = tr(words)
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60)
    c = moments.finalize({"start_id": 0, "end_id": 5, "scores": {}}, t, ch)
    assert c is not None and c.get("reject") in ("mostly silence", "too much dead air")


def test_scores_are_0_to_100_with_parts():
    from clipper import moments, settings
    ch = settings.channel()
    hi = {"scores": {k: 9 for k in moments.CRITERIA}, "context_complete": 9, "duration": 30, "context_ok": True}
    lo = {"scores": {k: 3 for k in moments.CRITERIA}, "context_complete": 5, "duration": 30, "context_ok": True}
    a, b = moments.score(hi, ch), moments.score(lo, ch)
    assert 0 <= b < a <= 100 and a > 75
    assert set(hi["parts"]) >= {"hook", "entertainment", "context"} and hi["parts"]["hook"] == 90


def test_old_criteria_names_still_score():
    from clipper import moments, settings
    old = {"scores": {"hook": 8, "standalone": 8, "retention": 8, "surprise": 8, "education": 8}, "context_complete": 8,
           "duration": 30, "context_ok": True}
    assert moments.score(old, settings.channel()) > 50


def test_diversify_mixes_kinds_of_moment():
    from clipper import moments
    cands = [{"score": 90, "category": "funny"}, {"score": 88, "category": "funny"}, {"score": 85, "category": "story"}]
    out = moments.diversify(cands)
    assert [c["category"] for c in out[:2]] == ["funny", "story"]
    assert [c["score"] for c in cands] == [90, 88, 85]            # scores themselves aren't changed


def test_failed_claude_falls_back_to_rules_and_says_so(no_llm):
    from clipper import moments, settings
    ch = dict(settings.channel(), clip_min=8, clip_max=30, clip_hard_max=40, min_score=0)
    rep = {}
    cands, method = moments.find(tr(), ch, {"title": "t"}, max_clips=2, report=rep)
    assert method == "rules" and cands and rep.get("llm_error")


def test_review_moves_to_a_stronger_start_and_writes_metadata(monkeypatch):
    from clipper import llm, moments, settings
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60, min_score=0)
    good = {k: 8 for k in moments.CRITERIA}

    def fake(prompt, timeout):
        if "checking YouTube Shorts clips" in prompt:
            return json.dumps([{"clip": 0, "accurate": True, "standalone": True, "verdict": "keep", "better_start_id": 3,
                                "hook": 9, "titles": ["T1", "T2", "T3", "T4", "T5"], "descriptions": ["D1", "D2", "D3"],
                                "hashtags": ["#money"], "keywords": ["saving"], "hook_texts": ["One small habit"],
                                "pinned": ["What habit helped you?"]}])
        return json.dumps([{"start_id": 2, "end_id": 6, "scores": good, "context_complete": 9, "category": "educational",
                            "hook_type": "bold_claim", "title": "A"}])
    monkeypatch.setattr(llm, "RUNNER", fake)
    cands, method = moments.find(tr(), ch, {"title": "t"}, max_clips=1)
    assert method == "claude" and len(cands) == 1
    c = cands[0]
    assert c["verified"] and c["start_id"] == 3 and c["category"] == "educational"
    assert c["meta"]["titles"][:2] == ["T1", "T2"] and len(c["meta"]["descriptions"]) == 3


def test_metadata_fill_keeps_all_options():
    from clipper import metadata, settings
    c = {"title": "x", "transcript": "about saving money", "duration": 30}
    metadata.fill(c, {"titles": ["A", "B", "C", "D", "E", "F"], "descriptions": ["d1", "d2", "d3"], "hashtags": ["#a"],
                      "keywords": ["k"], "pinned": ["p1", "p2"], "hook_texts": ["h1"]}, {"title": "v"}, settings.channel())
    assert c["title"] == "A" and len(c["titles"]) == 5 and [d.split("\n")[0] for d in c["descriptions"]] == ["d1", "d2", "d3"]
    assert c["pinned_comment"] == "p1" and "#shorts" in c["hashtags"]


# ---------- captions ----------

def test_every_caption_style_renders_inside_the_safe_area():
    from clipper import captions, settings
    ch = settings.channel()
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25} for i, w in enumerate("I saved 4000 dollars in ONE month honestly".split())]
    for name in captions.STYLES:
        st = captions.resolve(name, ch)
        cap = captions.Captioner(words, st, keywords=captions.pick_keywords(words))
        frame = np.zeros((1920, 1080, 3), np.uint8)
        cap.draw(frame, 0.7)
        rows = np.where(frame.any(axis=(1, 2)))[0]
        assert len(rows), name                                   # something was drawn
        assert rows.max() < 1920 * 0.86, name                    # clear of YouTube's bottom buttons


def test_keywords_are_chosen_not_random():
    from clipper import captions
    words = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate("so we made 4000 dollars and it was the WORST idea ever okay".split())]
    k = captions.pick_keywords(words)
    picked = {words[i]["w"] for i in k} if k and isinstance(next(iter(k)), int) else set(k)
    assert any("4000" in str(p) for p in picked)
    assert len(k) <= max(1, len(words) // 6) + 1


def test_custom_caption_options_are_clamped():
    from clipper import captions, settings
    st = captions.resolve("bold", settings.channel(), {"size": 9999, "words_per_line": 50, "color": "#ff0000"})
    assert st["size"] <= 180 and st["words_per_line"] <= 6 and st["color"] == "#ff0000"


# ---------- editing ----------

def test_fillers_are_cut_and_caption_edits_apply():
    from clipper import render
    words = make_words(["So um I tracked every uh dollar"])
    start, end = words[0]["s"], words[-1]["e"]
    iv = render.keep_intervals(words, start, end, jump_cuts=False, fillers=True)
    assert len(iv) >= 3                                       # two fillers cut out
    out = render.output_words(words, iv, edits={f"{words[2]['s']:.3f}": "", f"{words[3]['s']:.3f}": "We"})
    texts = [w["w"] for w in out]
    assert "um" not in texts and "uh" not in texts and "I" not in texts and "We" in texts


def test_noise_reduction_and_ducked_music(tmp_path):
    from clipper import media, render, settings
    src = make_video(tmp_path / "v.mp4", 8)
    music = tmp_path / "music"
    music.mkdir()
    media.run(["-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=20", str(music / "song.wav")])
    ch = dict(settings.channel(), noise_reduction=True, music_folder=str(music), music_volume=0.08, sound_effects=True)
    rep = {}
    out = render.build_audio(str(src), 0.0, [(0.5, 3.0), (4.0, 7.0)], tmp_path, "c1", ch, sfx_at=[(1.0, "whoosh")], report=rep)
    info = media.probe(str(out))
    assert abs(info["duration"] - 5.5) < 0.3
    joined = " ".join(rep["enhancements"])
    assert "noise reduction" in joined and "ducked under speech" in joined and "sound effect" in joined


def test_framing_layouts(tmp_path):
    from clipper import crop
    src = make_video(tmp_path / "game.mp4", 6)
    an = crop.analyze(str(src), 0.0, 5.0)
    assert an["samples"] and any(s.get("motion") for s in an["samples"])
    hand = crop.plan(an, 5.0, src_w=1920, src_h=1080, manual={"x": 0.2, "zoom": 1.3})
    assert hand["manual"] and hand["zoom"] == 1.3 and hand["x"][0] >= hand["crop_w"] / 2
    assert crop.plan(an, 5.0, src_w=1920, src_h=1080, prefer="fit")["layout"] == "fit"
    game = crop.plan(an, 5.0, src_w=1920, src_h=1080, prefer="gameplay")
    assert game["layout"] in ("track", "gameplay") and game.get("x")
    assert crop.plan(an, 5.0, src_w=1080, src_h=1920)["layout"] == "scale"     # already vertical


# ---------- the whole job, the editor and the dashboard ----------

def _job(monkeypatch, tmp_path, n=2):
    from clipper import llm, settings, sources, transcribe
    words = make_words(SCRIPT, start=0.3, wpm=170)
    calls = {"transcribe": 0, "llm": 0}

    def fake_transcribe(path, progress=None, prompt="", language="en", eng=None):
        calls["transcribe"] += 1
        return transcribe.build(words, duration=words[-1]["e"] + 0.5)

    def fake_llm(prompt, timeout):
        calls["llm"] += 1
        if "checking YouTube Shorts clips" in prompt:
            return json.dumps([{"clip": i, "accurate": True, "standalone": True, "verdict": "keep", "hook": 8,
                                "titles": [f"Title {i}", f"Alt {i}"], "descriptions": ["About money habits."],
                                "hashtags": ["#money"], "keywords": ["money"], "pinned": ["Thoughts?"]} for i in range(6)])
        good = {k: 8 for k in ("hook", "curiosity", "emotion", "entertainment", "story", "uniqueness", "clarity",
                               "relevance", "replay", "shareability", "pacing", "payoff")}
        return json.dumps([{"start_id": 2, "end_id": 4, "scores": good, "context_complete": 8, "category": "educational", "title": "x"},
                           {"start_id": 7, "end_id": 10, "scores": good, "context_complete": 8, "category": "story", "title": "y"},
                           {"start_id": 5, "end_id": 6, "scores": dict(good, hook=6), "context_complete": 8, "category": "story", "title": "z"}])
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(llm, "RUNNER", fake_llm)
    settings.ensure_default_channel()
    settings.update_channel("main", length_mode="custom", clip_min=6, clip_max=25, clip_hard_max=30, min_score=5)
    src = make_video(tmp_path / "podcast.mp4", int(words[-1]["e"]) + 2)
    v, job, _ = sources.add(str(src), max_clips=n)
    return job, calls, src


def test_edit_pick_delete_export_and_steps(monkeypatch, tmp_path):
    from pathlib import Path
    from clipper import api, db, media, pipeline
    job, calls, _ = _job(monkeypatch, tmp_path, n=1)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    clip = db.clips("job_id=?", job["id"])[0]
    assert clip["title"] == "Title 0" and (clip["extra"] or {}).get("descriptions")
    steps = api.steps(db.job(job["id"]))
    assert [s["key"] for s in steps][:3] == ["acquiring", "audio", "transcribing"] or steps[0]["state"] == "done"
    assert all(s["state"] in ("done", "skipped") for s in steps)

    # the editor: a shorter clip with another caption style, a hand-set crop and a corrected word
    w0 = db.clip(clip["id"])
    msg = pipeline.edit(clip["id"], {"start": w0["start"] + 1.0, "end": w0["end"], "caption_style": "cinematic",
                                     "crop": {"x": 0.3, "zoom": 1.2}, "title": "My title"})
    assert "re-rendering" in msg.lower()
    assert db.clip(clip["id"])["title"] == "My title"            # saved straight away
    assert pipeline.run_job(job["id"]) == pipeline.OK
    c2 = db.clip(clip["id"])
    assert c2["caption_style"] == "cinematic" and (c2["extra"] or {}).get("version", 1) >= 2
    assert media.probe(c2["path"])["duration"] < clip["duration"] and Path(c2["path"]).exists()
    assert c2["path"] != clip["path"] and not Path(clip["path"]).exists()
    with pytest.raises(ValueError):
        pipeline.edit(clip["id"], {"caption_style": "nope"})

    # picking another moment by hand, without re-transcribing or re-asking Claude
    found = media.load(pipeline.folder_for(job["id"]) / "candidates.json")
    other = [c["rank"] for c in found["candidates"] if c["rank"] != clip.get("rank")]
    llm_before = calls["llm"]
    assert "making 1 clip" in pipeline.pick(job["id"], other[:1]).lower()
    assert pipeline.run_job(job["id"]) == pipeline.OK
    live = db.clips("job_id=? AND state NOT IN ('rejected','deleted')", job["id"])
    assert len(live) == 2 and calls["transcribe"] == 1 and calls["llm"] <= llm_before + 1

    # export, then delete
    monkeypatch.setattr(api, "export_dir", lambda: tmp_path / "exports")
    r = api.export([c["id"] for c in live])
    assert r["ok"] and len(list((tmp_path / "exports").glob("*.mp4"))) == 2
    assert "Deleted" in api.delete(live[0]["id"])
    assert db.clip(live[0]["id"])["state"] == "deleted" and not Path(live[0]["path"]).exists()
    s = api.state()
    assert all(c["id"] != live[0]["id"] for c in s["clips"])     # deleted clips leave the dashboard
    detail = api.job_detail(job["id"])
    assert detail["candidates"] and "rejected" in detail


def test_transcript_cache_is_reused_for_the_same_video(monkeypatch, tmp_path):
    from clipper import db, pipeline, sources
    job, calls, src = _job(monkeypatch, tmp_path, n=1)
    db.set_job(job["id"], max_clips=1)
    settings_ok = pipeline.run_job(job["id"]) == pipeline.OK
    assert settings_ok and calls["transcribe"] == 1
    db.run("DELETE FROM jobs")
    db.run("DELETE FROM videos")
    v, job2, _ = sources.add(str(src), max_clips=1)
    assert pipeline.run_job(job2["id"]) == pipeline.OK
    assert calls["transcribe"] == 1                               # second time: from the cache


def test_no_speech_fails_clearly(monkeypatch, tmp_path):
    from clipper import db, pipeline, settings, sources, transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: transcribe.build([], duration=10))
    settings.ensure_default_channel()
    src = make_video(tmp_path / "quiet.mp4", 6, tone_gaps=False)
    v, job, _ = sources.add(str(src), max_clips=1)
    pipeline.run_job(job["id"])
    j = db.job(job["id"])
    assert j["state"] == "failed" and "speech" in (j["error"] or "")


def test_failed_download_waits_and_retry_now(monkeypatch, tmp_path):
    from clipper import acquire, api, db, jobs, pipeline, settings, sources
    settings.ensure_default_channel()
    src = make_video(tmp_path / "v.mp4", 4)
    v, job, _ = sources.add(str(src), max_clips=1)

    def boom(*a, **k):
        raise RuntimeError("network went away")
    monkeypatch.setattr(acquire, "acquire", boom)
    pipeline.run_job(job["id"])
    j = db.job(job["id"])
    assert j["state"] == "queued" and "network" in j["error"] and j["not_before"] > time.time()
    assert api.state()["jobs"][0]["waiting_retry"]
    assert "try again now" in jobs.retry(job["id"])
    j = db.job(job["id"])
    assert j["not_before"] == 0 and not j["error"]


def test_link_checks():
    from clipper import api
    ok = api.check_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert ok["ok"] and ok["platform"] == "youtube" and ok["thumb"].startswith("https://i.ytimg.com/")
    assert api.check_link("https://www.twitch.tv/videos/123456789")["platform"] == "twitch"
    assert not api.check_link("not a link at all")["ok"]
    assert not api.check_link("")["ok"]


def test_uploads_are_validated(tmp_path):
    from clipper import api
    data = make_video(tmp_path / "up.mp4", 3).read_bytes()
    good = api.save_upload(io.BytesIO(data), len(data), "my stream.mp4")
    assert good["ok"], good
    assert not api.save_upload(io.BytesIO(b"x" * 100), 100, "notes.txt")["ok"]
    bad = api.save_upload(io.BytesIO(b"x" * 100), 100, "fake.mp4")
    assert not bad["ok"] and "can't read" in bad["message"]
    short = api.save_upload(io.BytesIO(data[:10]), len(data), "cut.mp4")
    assert not short["ok"]


def test_dashboard_actions_report_errors_instead_of_crashing():
    from clipper import api
    assert not api.action({"action": "rerender", "clip": 999, "edits": {"start": 1}})["ok"]
    assert not api.action({"action": "pick", "job": 999, "ranks": [1]})["ok"]
    assert not api.action({"action": "nonsense"})["ok"]


def test_a_failed_edit_keeps_the_previous_version(monkeypatch, tmp_path):
    from pathlib import Path
    from clipper import db, pipeline, render
    job, _, _ = _job(monkeypatch, tmp_path, n=1)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    before = db.clips("job_id=?", job["id"])[0]
    pipeline.edit(before["id"], {"start": before["start"] + 1.0, "caption_style": "gaming"})

    def broken(*a, **k):
        raise RuntimeError("encoder fell over")
    monkeypatch.setattr(render, "render", broken)
    pipeline.run_job(job["id"])
    after = db.clip(before["id"])
    assert after["start"] == before["start"] and after["path"] == before["path"] and Path(after["path"]).exists()
    assert (after["extra"] or {}).get("version", 1) == 1 and after["caption_style"] == before["caption_style"]
