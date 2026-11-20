"""The whole job, start to finish, with a fake transcriber (no speech model in CI) and fake Claude;
plus resume-after-interruption and the regenerate button."""
import json

from conftest import SCRIPT, make_video, make_words


def setup(monkeypatch, tmp_path):
    from clipper import llm, settings, sources, transcribe
    words = make_words(SCRIPT, start=0.3, wpm=170)
    calls = {"transcribe": 0}

    def fake_transcribe(path, progress=None, prompt="", language="en", eng=None):
        calls["transcribe"] += 1
        return transcribe.build(words, duration=words[-1]["e"] + 0.5)

    def fake_llm(prompt, timeout):
        if "checking Shorts clips" in prompt:
            return json.dumps([{"clip": i, "accurate": True, "standalone": True, "verdict": "keep"} for i in range(5)])
        if "metadata" in prompt:
            return json.dumps([{"clip": i, "titles": [f"Title {i}"], "description": "About money habits.",
                                "hashtags": ["#money", "#habits"], "keywords": ["money"], "pinned_comment": "Thoughts?"} for i in range(5)])
        good = {k: 8 for k in ("hook", "curiosity", "emotion", "education", "entertainment", "story", "surprise",
                               "standalone", "relevance", "retention")}
        return json.dumps([{"start_id": 2, "end_id": 4, "scores": good, "context_complete": 8, "hook_type": "bold_claim", "title": "x"},
                           {"start_id": 7, "end_id": 10, "scores": good, "context_complete": 8, "hook_type": "question", "title": "y"}])
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(llm, "RUNNER", fake_llm)
    settings.ensure_default_channel()
    settings.update_channel("main", clip_min=8, clip_max=25, clip_hard_max=30, min_score=5)
    src = make_video(tmp_path / "podcast.mp4", int(words[-1]["e"]) + 2)
    v, job, _ = sources.add(str(src), max_clips=2)
    return job, calls


def test_full_job(monkeypatch, tmp_path):
    from clipper import db, media, pipeline
    job, calls = setup(monkeypatch, tmp_path)
    assert pipeline.run_job(job["id"]) == pipeline.OK
    j = db.job(job["id"])
    assert j["state"] == "awaiting_approval", j
    clips = db.clips("job_id=?", job["id"])
    assert len(clips) == 2 and all(c["state"] == "awaiting_approval" for c in clips)
    for c in clips:
        info = media.probe(c["path"])
        assert (info["width"], info["height"]) == (1080, 1920) and c["qa"]["passed"]
        assert c["title"].startswith("Title") and "#shorts" in c["hashtags"]
    # running it again changes nothing (resume / idempotent) and doesn't re-transcribe
    db.set_job(job["id"], state="queued")
    assert pipeline.run_job(job["id"]) == pipeline.OK
    assert calls["transcribe"] == 1 and len(db.clips("job_id=?", job["id"])) == 2


def test_stop_mid_job_then_resume(monkeypatch, tmp_path):
    from clipper import db, pipeline, settings
    job, calls = setup(monkeypatch, tmp_path)
    settings.set_stopped(True)
    assert pipeline.run_job(job["id"]) == pipeline.STOPPED
    assert db.job(job["id"])["state"] == "queued"
    settings.set_stopped(False)
    assert pipeline.run_job(job["id"]) == pipeline.OK


def test_regenerate(monkeypatch, tmp_path):
    from clipper import db, pipeline
    job, _ = setup(monkeypatch, tmp_path)
    db.set_job(job["id"], max_clips=1)
    pipeline.run_job(job["id"])
    first = db.clips("job_id=?", job["id"])[0]
    pipeline.regenerate(first["id"])
    assert pipeline.run_job(job["id"]) == pipeline.OK
    rows = {c["id"]: c for c in db.clips("job_id=?", job["id"])}
    assert rows[first["id"]]["state"] == "rejected"
    assert any(c["state"] == "awaiting_approval" and c["id"] != first["id"] for c in rows.values())
