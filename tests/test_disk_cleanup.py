"""Disk clean-up: clears old downloads, temp files and rejected clips, never unreviewed or approved clips."""
import os
import time

import pytest


def _old(folder):
    """Files the clean-up may touch are at least an hour old (newer ones may be in use)."""
    t = time.time() - 7200
    for f in folder.iterdir():
        os.utime(f, (t, t))


def _job(state, finished_days_ago, video_path=None):
    from clipper import data_dir, db
    now = time.time()
    db.run("INSERT INTO videos (channel, platform, external_id, path, title) VALUES ('main','file',?,?,?)",
           f"v{now}{state}{finished_days_ago}", video_path, "a video")
    vid = db.one("SELECT MAX(id) n FROM videos")["n"]
    db.run("INSERT INTO jobs (video_id, channel, state, created, updated, finished) VALUES (?,?,?,?,?,?)",
           vid, "main", state, now, now, (now - finished_days_ago * 86400) if finished_days_ago is not None else None)
    jid = db.one("SELECT MAX(id) n FROM jobs")["n"]
    folder = data_dir("jobs", str(jid))
    (folder / "audio.wav").write_bytes(b"a" * 4000)
    (folder / "transcript.json").write_text("{}")
    (folder / "job.log").write_text("log")
    _old(folder)
    return jid, vid, folder


def _clip(jid, vid, state, days_ago=0, youtube_id=None):
    from clipper import data_dir, db
    lib = data_dir("library", "main")
    cid = db.add_clip(job_id=jid, video_id=vid, channel="main", rank=1, start=0, end=10, duration=10, score=50,
                      scores={}, transcript="hi", state=state)
    p = lib / f"clip{cid}.mp4"
    p.write_bytes(b"c" * 3000)
    db.set_clip(cid, path=str(p), thumb=None, youtube_id=youtube_id)
    db.run("UPDATE clips SET updated=? WHERE id=?", time.time() - days_ago * 86400, cid)
    return cid, p


@pytest.fixture
def setup():
    from clipper import settings
    settings.ensure_default_channel()


def test_never_deletes_unreviewed_approved_or_published_clips(setup):
    from clipper import disk
    jid, vid, _ = _job("awaiting_approval", 30)
    keep = [_clip(jid, vid, s, days_ago=30)[1] for s in ("awaiting_approval", "approved", "ready", "rendered")]
    on_yt = _clip(jid, vid, "rejected", days_ago=30, youtube_id="abc")[1]
    disk.clean(urgent=True, by="test")
    for p in keep + [on_yt]:
        assert p.exists(), p


def test_rejected_clips_cleared_after_grace(setup):
    from clipper import disk
    jid, vid, _ = _job("done", 0)
    fresh = _clip(jid, vid, "rejected", days_ago=0)[1]
    old = _clip(jid, vid, "rejected", days_ago=8)[1]
    failed = _clip(jid, vid, "qa_failed", days_ago=8)[1]
    disk.clean(by="test")
    assert fresh.exists() and not old.exists() and not failed.exists()
    disk.clean(urgent=True, by="test")
    assert fresh.exists()                    # still within the 1-day undo window, even on "clean up now"


def test_sources_rules(setup):
    from clipper import disk
    j_new, _, f_new = _job("done", 0)                       # finished today, nothing to review
    j_rev, v_rev, f_rev = _job("awaiting_approval", 1)       # clips still to review
    _clip(j_rev, v_rev, "awaiting_approval")
    j_old, _, f_old = _job("done", 5)
    j_run, _, f_run = _job("rendering", None)
    (f_run / "clip_1.part.mp4").write_bytes(b"x" * 100)
    (f_new / "audio_src.mp4.part").write_bytes(b"x" * 100)
    (f_new / "fresh.part").write_bytes(b"x" * 100)          # being written right now
    _old(f_run)
    for f in (f_new / "audio_src.mp4.part",):
        os.utime(f, (time.time() - 7200,) * 2)

    disk.clean(by="test")                                   # the 6-hourly one
    assert not (f_old / "audio.wav").exists()
    assert (f_new / "audio.wav").exists() and (f_rev / "audio.wav").exists()
    assert not (f_new / "audio_src.mp4.part").exists()      # temp files of a stopped job go at once
    assert (f_new / "fresh.part").exists()
    assert (f_run / "clip_1.part.mp4").exists() and (f_run / "audio.wav").exists()
    assert (f_old / "transcript.json").exists() and (f_old / "job.log").exists()

    r = disk.clean(urgent=True, by="test")                  # "clean up now"
    assert not (f_new / "audio.wav").exists()
    assert (f_rev / "audio.wav").exists()                   # kept so those clips can still be edited
    assert r["kept_review"] == 1
    assert (f_run / "audio.wav").exists()
    assert "kept the videos for 1 project" in disk.say_clean(r)


def test_own_video_file_never_deleted(setup, tmp_path):
    from clipper import data_dir, db, disk, media
    jid, _, folder = _job("done", 30)
    own = folder / "my_upload.mp4"
    own.write_bytes(b"v" * 500)
    media.dump({"mode": "full", "downloaded": False, "video": str(own)}, folder / "source.json")
    _old(folder)
    disk.clean(urgent=True, by="test")
    assert own.exists() and not (folder / "audio.wav").exists()


def test_old_venv_backup_and_big_log(setup, tmp_path, monkeypatch):
    from clipper import disk
    root = tmp_path / "jarvis"
    (root / ".venv").mkdir(parents=True)
    old = root / ".venv-old-20200101000000"
    old.mkdir()
    (old / "lib.py").write_bytes(b"x" * 1000)
    new = root / f".venv-old-{time.strftime('%Y%m%d%H%M%S')}"
    new.mkdir()
    log = root / "jarvis.log"
    log.write_bytes(b"line\n" * (disk.LOG_MAX // 5 + 10))
    monkeypatch.setattr(disk, "ROOT", root)
    r = disk.clean(by="test")
    assert not old.exists() and new.exists()
    assert log.stat().st_size <= disk.LOG_KEEP + 100
    assert r["parts"]["backups"] == 1000 and "logs" in r["parts"]


def test_low_disk_triggers_urgent_clean(setup, monkeypatch):
    from clipper import acquire, db, disk, jobs
    _, _, f = _job("done", 0)
    monkeypatch.setattr(acquire, "free_gb", lambda p: 1.0)
    assert jobs.claim() is None                              # still too full after cleaning
    assert not (f / "audio.wav").exists()
    assert db.get("disk_last_clean")["by"] == "low_disk"


def test_report_and_voice(setup):
    from clipper import control, disk, intents
    _job("done", 0)
    r = disk.report(fresh=True)
    assert r["can_free_gb"] >= 0 and r["free_gb"] > 0 and "parts" in r
    assert intents.parse("jarvis free up some disk space") == ("disk_clean", {})
    assert intents.parse("how much disk space do i have") == ("disk", {})
    assert intents.parse("clean up my desktop") is None
    said, _ = control.run("disk_clean", {})
    assert "free" in said
    said, _ = control.run("disk", {})
    assert "GB free" in said


def test_dashboard_action(setup):
    from clipper import api
    _, _, f = _job("done", 0)
    out = api.action({"action": "disk_clean"})
    assert out["ok"] and not (f / "audio.wav").exists() and "disk" in out
    assert "disk" in api.state()
