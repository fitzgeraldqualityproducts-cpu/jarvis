"""Clipping presets (they change the prompt, the scoring, the length and the editing), the
explanation each moment carries (strongest line, audience, weak spots), the 90-180 s length and
making a clip from your own transcript selection."""
import json

import pytest

from conftest import SCRIPT, make_video, make_words


def test_every_preset_is_valid_and_saved():
    from clipper import presets, settings
    assert set(presets.NAMES) == set(settings.PRESET_NAMES)
    for name in presets.NAMES:
        assert settings.update_channel("main", preset=name)["preset"] == name
    with pytest.raises(ValueError):
        settings.update_channel("main", preset="nope")
    with pytest.raises(ValueError):
        settings.update_channel("main", edit_intensity="wild")


def test_presets_change_scoring_for_real():
    from clipper import moments, presets, settings
    ch = settings.channel()
    funny = {"scores": dict({k: 6 for k in moments.CRITERIA}, entertainment=9, replay=8, clarity=5), "category": "funny",
             "context_complete": 7, "duration": 25, "context_ok": True}
    lesson = {"scores": dict({k: 6 for k in moments.CRITERIA}, clarity=9, relevance=9, payoff=8, entertainment=4),
              "category": "educational", "context_complete": 8, "duration": 50, "context_ok": True}
    edu, ent = presets.apply(ch, "educational"), presets.apply(ch, "entertainment")
    assert moments.score(dict(lesson), edu) > moments.score(dict(funny), edu)
    assert moments.score(dict(funny), ent) > moments.score(dict(lesson), ent)
    c = dict(funny)
    moments.score(c, ent)
    assert c["parts"]["preset"] > 0                                   # shown on the dashboard
    bal = dict(funny)
    moments.score(bal, presets.apply(ch, "balanced"))
    assert bal["parts"]["preset"] == 0


def test_preset_fills_editing_only_where_you_left_defaults():
    from clipper import presets, settings
    ch = settings.channel()
    v = presets.apply(ch, "viral")
    assert v["edit_intensity"] == "strong" and v["max_gap"] == 0.4 and "VIRAL" in v["preset_focus"]
    pod = presets.apply(ch, "podcast")
    assert pod["preset_style"] == "podcast" and pod["punch_ins"] is False
    mine = settings.update_channel("main", caption_style="gaming", max_gap=0.9)
    pod = presets.apply(mine, "podcast")
    assert "preset_style" not in pod and pod["max_gap"] == 0.9     # your own choices win
    from clipper import captions
    assert captions.choose_style(presets.apply(settings.channel(), "educational")) == "gaming"
    fresh = dict(settings.channel(), caption_style="auto")
    assert captions.choose_style(presets.apply(fresh, "educational")) == "educational"


def test_job_lengths_preset_then_channel_then_video():
    from clipper import pipeline, settings
    ch = settings.channel()
    story = pipeline.configure(ch, {"preset": "storytelling"})
    assert (story["clip_min"], story["clip_max"], story["clip_hard_max"]) == (40.0, 90.0, 120.0)
    assert pipeline.configure(ch, {"preset": "storytelling", "length": "15_30"})["clip_max"] == 30.0
    custom = settings.update_channel("main", length_mode="custom", clip_min=10, clip_max=20, clip_hard_max=25)
    assert pipeline.configure(custom, {"preset": "storytelling"})["clip_max"] == 20.0
    long = pipeline.configure(ch, {"length": "90_180"})
    assert (long["clip_min"], long["clip_max"], long["clip_hard_max"]) == (90.0, 180.0, 180.0)
    assert settings.update_channel("main", length_mode="90_180")["length_mode"] == "90_180"


def test_preset_focus_reaches_the_prompt(monkeypatch):
    from clipper import llm, moments, presets, settings, transcribe
    seen = []

    def fake(prompt, timeout):
        seen.append(prompt)
        return "[]"
    monkeypatch.setattr(llm, "RUNNER", fake)
    tr = transcribe.build(make_words(SCRIPT))
    ch = moments.with_lengths(presets.apply(settings.channel(), "educational"), (6, 30))
    moments.find(tr, ch, {"title": "t"}, max_clips=2)
    assert seen and "PRESET: EDUCATIONAL" in seen[0] and '"weakness"' in seen[0]


def test_moments_explain_themselves():
    from clipper import moments, settings, transcribe
    tr = transcribe.build(make_words(SCRIPT, gaps={2: 2.5, 3: 2.5, 4: 2.5}))
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60)
    c = moments.finalize({"start_id": 2, "end_id": 6, "scores": {}, "audience": "people saving money",
                          "strongest": "That one habit changed everything for me and my family.",
                          "weakness": "a slow middle"}, tr, ch)
    assert c["audience"] == "people saving money"
    assert c["strongest_at"] is not None and 0 < c["strongest_at"] < c["duration"]
    assert c["weaknesses"][0] == "a slow middle" and any("pauses" in w for w in c["weaknesses"])


def test_hook_optimisation_can_be_switched_off(monkeypatch):
    from clipper import llm, moments, settings, transcribe
    tr = transcribe.build(make_words(SCRIPT))
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60)
    base = moments.finalize({"start_id": 1, "end_id": 6, "scores": {}}, tr, ch)
    monkeypatch.setattr(llm, "RUNNER", lambda p, t: json.dumps([{"clip": 0, "accurate": True, "standalone": True,
                                                                 "verdict": "keep", "better_start_id": 2, "hook": 7}]))
    assert moments.verify([dict(base)], tr, ch)[0]["start_id"] == 2
    off = moments.verify([dict(base)], tr, dict(ch, hook_optimization=False))
    assert off[0]["start_id"] == 1


def test_intensity_changes_zoom_and_punch_spacing():
    from clipper import render
    words = [{"s": t, "emph": True} for t in (1.5, 3.0, 4.5, 6.0, 9.0)]
    plan = {"layout": "track", "shots": []}
    iv = [(0.0, 12.0)]
    sub = render.plan_punches(words, plan, {"edit_intensity": "subtle", "punch_ins": True}, iv)
    strong = render.plan_punches(words, plan, {"edit_intensity": "strong", "punch_ins": True}, iv)
    assert len(strong) > len(sub) and strong[0][1] > sub[0][1]
    z_sub = render.zoom_at(0.1, iv, plan, {"edit_intensity": "subtle", "zooms": True})
    z_strong = render.zoom_at(0.1, iv, plan, {"edit_intensity": "strong", "zooms": True})
    assert 1.0 < z_sub < z_strong


def _job(monkeypatch, tmp_path):
    from clipper import llm, settings, sources, transcribe
    words = make_words(SCRIPT, start=0.3, wpm=170)

    def fake_transcribe(path, progress=None, prompt="", language="en", eng=None):
        return transcribe.build(words, duration=words[-1]["e"] + 0.5)

    def fake_llm(prompt, timeout):
        if "checking YouTube Shorts clips" in prompt:
            return json.dumps([{"clip": i, "accurate": True, "standalone": True, "verdict": "keep", "hook": 8,
                                "titles": [f"Title {i}"], "descriptions": ["About money."]} for i in range(6)])
        if "Write YouTube Shorts metadata" in prompt:
            return json.dumps([{"clip": 0, "titles": ["My own moment"], "descriptions": ["Mine."]}])
        good = {k: 8 for k in ("hook", "curiosity", "emotion", "entertainment", "story", "uniqueness", "clarity",
                               "relevance", "replay", "shareability", "pacing", "payoff")}
        return json.dumps([{"start_id": 2, "end_id": 4, "scores": good, "context_complete": 8, "category": "educational",
                            "title": "x", "strongest": "They think it starts with a big income", "audience": "savers"}])
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(llm, "RUNNER", fake_llm)
    settings.ensure_default_channel()
    settings.update_channel("main", length_mode="custom", clip_min=6, clip_max=25, clip_hard_max=30, min_score=5)
    src = make_video(tmp_path / "talk.mp4", int(words[-1]["e"]) + 2)
    v, job, _ = sources.add(str(src), max_clips=1, options={"preset": "educational"})
    return job


def test_make_a_clip_from_the_transcript(monkeypatch, tmp_path):
    from clipper import api, db, media, pipeline
    job = _job(monkeypatch, tmp_path)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    first = db.clips("job_id=?", job["id"])[0]
    assert (first["extra"] or {}).get("preset") == "educational" and (first["extra"] or {}).get("audience") == "savers"
    detail = api.job_detail(job["id"])
    assert detail["candidates"][0].get("strongest") and detail["candidates"][0].get("weaknesses") is not None

    t = api.action({"action": "transcript", "job": job["id"]})
    assert t["ok"] and len(t["sentences"]) == len(SCRIPT) and t["has_source"]
    assert t["sentences"][7]["t"].startswith("What was the hardest part")
    assert api.source_file(job["id"])

    r = api.action({"action": "make_range", "job": job["id"], "start_id": 9, "end_id": 7})   # either order
    assert r["ok"] and "making 1 clip" in r["message"].lower()
    found = media.load(pipeline.folder_for(job["id"]) / "candidates.json")
    mine = [c for c in found["candidates"] if c.get("manual")]
    assert len(mine) == 1 and (mine[0]["start_id"], mine[0]["end_id"]) == (7, 9)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    clip = [c for c in db.clips("job_id=?", job["id"]) if (c["extra"] or {}).get("manual")][0]
    assert clip["path"] and clip["title"] == "My own moment"
    assert abs(clip["start"] - found["candidates"][-1]["start"]) < 0.01

    # asking again for the same stretch doesn't make a second copy of the moment
    again = api.action({"action": "make_range", "job": job["id"], "start_id": 7, "end_id": 9})
    assert "already" in again["message"].lower()
    bad = api.action({"action": "make_range", "job": job["id"], "start_id": 0, "end_id": 99})
    assert "aren't in" in bad["message"]


# ---------- phase 2: variations and version history ----------

def test_variations_are_new_clips_and_versions_can_be_restored(monkeypatch, tmp_path):
    from pathlib import Path
    from clipper import api, db, pipeline
    job = _job(monkeypatch, tmp_path)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    orig = db.clips("job_id=?", job["id"])[0]
    r = api.action({"action": "variants", "clip": orig["id"], "kinds": ["tight", "captions", "crop", "ending", "copy", "bogus"]})
    assert r["ok"] and len(r["clips"]) in (4, 5)
    pending = [db.clip(i) for i in r["clips"]]
    assert all(c["state"] == "variant_pending" and not c["path"] for c in pending)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    after = [db.clip(i) for i in r["clips"]]
    assert all(c["path"] and Path(c["path"]).exists() and c["state"] == "awaiting_approval" for c in after), \
        [(c["state"], c["note"]) for c in after]
    kinds = {(c["extra"] or {})["variant"]: c for c in after}
    assert kinds["captions"]["caption_style"] != orig["caption_style"]
    assert kinds["tight"]["extra"]["edits"]["max_gap"] == 0.35
    if "ending" in kinds:
        assert kinds["ending"]["end"] > orig["end"]
    o2 = db.clip(orig["id"])
    assert o2["path"] == orig["path"] and Path(o2["path"]).exists()          # the original never changes
    d = api.clip_detail(orig["id"])
    assert len(d["family"]) == len(after) + 1 and d["family"][0]["label"] == "Original"
    assert api.job_detail(job["id"])["candidates"][0]["clip_id"] == orig["id"]

    # re-render the original: the old file goes to its history, and you can go back to it
    pipeline.edit(orig["id"], {"caption_style": "minimal"})
    assert pipeline.run_job(job["id"]) == pipeline.OK
    o3 = db.clip(orig["id"])
    hist = o3["extra"]["history"]
    assert o3["extra"]["version"] == 2 and len(hist) == 1 and Path(hist[0]["path"]).exists()
    assert api.media_path("media", orig["id"], "1") == hist[0]["path"]
    assert "back to version 1" in api.action({"action": "restore", "clip": orig["id"], "version": 1})["message"]
    o4 = db.clip(orig["id"])
    assert o4["extra"]["version"] == 1 and o4["caption_style"] == orig["caption_style"] and Path(o4["path"]).exists()
    assert [h["version"] for h in o4["extra"]["history"]] == [2]
    # deleting the clip removes its earlier versions too
    files = [h["path"] for h in o4["extra"]["history"]]
    assert "Deleted" in api.delete(orig["id"])
    assert not any(Path(f).exists() for f in files)


def test_history_keeps_only_the_last_few(monkeypatch, tmp_path):
    from clipper import pipeline
    made = []
    for n in range(4):
        f = tmp_path / f"v{n}.mp4"
        f.write_bytes(b"x")
        made.append(f)
    hist = []
    for n, f in enumerate(made):
        existing = {"id": 9, "path": str(f), "thumb": None, "start": 0, "end": 5, "duration": 5,
                    "extra": {"version": n + 1, "history": hist}}
        hist = pipeline.archive(existing, None, "main")
    assert [h["version"] for h in hist] == [3, 4]
    assert sum(1 for f in made if f.exists()) == 0
    from pathlib import Path
    assert sum(1 for h in hist if Path(h["path"]).exists()) == 2


def test_hook_variant_starts_on_the_strongest_line():
    from clipper import pipeline, transcribe
    tr = transcribe.build(make_words(SCRIPT))
    s = tr["sentences"]
    c = {"start": s[2]["s"], "end": s[6]["e"], "extra": {"strongest_at": s[3]["s"] - s[2]["s"] + 0.2}, "caption_style": "bold",
         "layout": "track"}
    edits, _ = pipeline._variant_edits(c, "hook", tr)
    assert edits == {"start": s[3]["s"]}
    assert pipeline._variant_edits(c, "captions", tr)[0]["caption_style"] == "clean"
    assert pipeline._variant_edits(c, "crop", tr)[0]["layout"] == "fit"
    assert pipeline._variant_edits(dict(c, end=s[-1]["e"]), "ending", tr)[0] is None


# ---------- phase 3: batch queue, export presets ----------

def test_batch_adds_each_video_once(tmp_path):
    from clipper import api, db
    a = make_video(tmp_path / "one.mp4", 4)
    b = make_video(tmp_path / "two.mp4", 6)
    r = api.action({"action": "add_many", "urls": f"{a}\n{b}\n{a}\n\n", "preset": "podcast", "count": 2})
    assert r["ok"] and len(r["results"]) == 2 and all(x["ok"] for x in r["results"])
    jobs = db.q("SELECT * FROM jobs ORDER BY id")
    assert len(jobs) == 2 and all(db.loads(j["options"]).get("preset") == "podcast" for j in jobs)
    again = api.action({"action": "add_many", "urls": [str(a), "not a link at all"]})
    assert len(db.q("SELECT id FROM jobs")) == 2 and "Not added" in again["message"]


def test_export_presets_copy_or_shrink_and_verify(monkeypatch, tmp_path):
    from clipper import api, db, media
    src = make_video(tmp_path / "clip.mp4", 3, size="1080x1920")
    cid = db.add_clip(job_id=1, video_id=1, channel="main", rank=1, start=0, end=3, duration=200, score=70,
                      title="My clip", state="ready", path=str(src))
    monkeypatch.setattr(api, "export_dir", lambda: tmp_path / "out")
    full = api.action({"action": "export", "clips": [cid], "preset": "reels"})
    assert full["ok"] and "longer than Instagram Reels allows" in full["message"]
    assert (tmp_path / "out" / "My clip (clip 1) [Reels].mp4").stat().st_size == src.stat().st_size
    small = api.action({"action": "export", "clips": [cid], "preset": "small"})
    assert small["ok"], small["message"]
    info = media.probe(str(tmp_path / "out" / "My clip (clip 1) [720p].mp4"))
    assert (info["width"], info["height"]) == (720, 1280)
    assert {x["id"] for x in api.export_presets()} >= {"shorts", "tiktok", "reels", "small"}
