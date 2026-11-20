"""Job persistence, states, duplicate prevention, retries, recovery, emergency stop."""
import time

from conftest import make_video


def test_job_persists_across_restart(tmp_path):
    from clipper import db, jobs, sources
    v, job, msg = sources.add(str(make_video(tmp_path / "a.mp4", 6)))
    assert job["state"] == "queued" and "queued" in msg
    db.reset_connections()                                   # "restart"
    assert db.job(job["id"])["state"] == "queued"
    jid = jobs.claim()
    assert jid == job["id"] and db.job(jid)["state"] == "acquiring"
    db.set_job(jid, state="rendering")
    db.reset_connections()
    assert jobs.recover() == 1                                # interrupted -> back in the queue
    assert db.job(jid)["state"] == "queued"


def test_no_duplicate_videos_or_jobs(tmp_path):
    from clipper import db, sources
    f = make_video(tmp_path / "a.mp4", 6)
    v1, j1, _ = sources.add(str(f))
    copy = tmp_path / "renamed.mp4"
    copy.write_bytes(f.read_bytes())
    v2, j2, msg = sources.add(str(copy))                      # same file under a new name
    assert v1["id"] == v2["id"] and j1["id"] == j2["id"] and "already" in msg
    assert db.one("SELECT COUNT(*) n FROM jobs")["n"] == 1


def test_emergency_stop_blocks_claims_and_resume(tmp_path):
    from clipper import jobs, settings, sources
    sources.add(str(make_video(tmp_path / "a.mp4", 6)))
    jobs.emergency_stop()
    assert settings.stopped() and jobs.claim() is None
    jobs.resume()
    assert jobs.claim() is not None


def test_retry_backoff_and_limit():
    from clipper import db, pipeline, settings
    jid = db.run("INSERT INTO jobs(video_id, channel, state, created, updated) VALUES(1,'main','acquiring',?,?)", time.time(), time.time())
    assert pipeline._retry(jid, 1, RuntimeError("net down")) == pipeline.RETRY
    j = db.job(jid)
    assert j["state"] == "queued" and j["not_before"] > time.time() + 30
    limit = settings.general()["max_attempts"]
    assert pipeline._retry(jid, limit, RuntimeError("still down")) == pipeline.FATAL
    assert db.job(jid)["state"] == "failed"


def test_priority_order(tmp_path):
    from clipper import jobs, sources
    _, low, _ = sources.add(str(make_video(tmp_path / "a.mp4", 5)), priority=1)
    _, high, _ = sources.add(str(make_video(tmp_path / "b.mp4", 6)), priority=9)
    assert jobs.claim() == high["id"]


def test_bad_job_state_rejected():
    import pytest
    from clipper import db
    jid = db.run("INSERT INTO jobs(video_id, channel, state) VALUES(1,'main','queued')")
    with pytest.raises(ValueError):
        db.set_job(jid, state="flying")
