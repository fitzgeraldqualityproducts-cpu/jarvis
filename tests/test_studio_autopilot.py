"""Phase 11: the autopilot keeps one Short in production, halts on repeated failures, and work
survives a restart mid-render and mid-upload."""
import json
import os
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401


@pytest.fixture
def line(monkeypatch):
    from studio import config, db, guard, topics
    monkeypatch.setattr(config, "enabled", lambda: True)
    guard.lock_channel("UC_BIZ", "HussUniversity")
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    monkeypatch.setattr(topics, "pool", lambda now=None: [{"id": 1}])
    return db


def _count(db, state=None):
    return db.one("SELECT COUNT(*) n FROM videos" + (" WHERE state=?" if state else ""), *([state] if state else []))["n"]


def test_starts_one_short_at_a_time(line):
    from studio import autopilot
    vid = autopilot.maybe_make()
    assert vid and _count(line) == 1
    assert autopilot.maybe_make() is None                          # one is already being made


def test_manual_mode_keeps_just_one_waiting(line):
    from studio import autopilot
    vid = autopilot.maybe_make()
    line.set_video(vid, state="ready")
    assert autopilot.maybe_make() is None                          # manual: buffer of 1 is full
    from studio import config
    config.set_values(publish_mode="private")
    assert autopilot.maybe_make()                                  # private/public: buffer of 2


def test_daily_limit_and_off_switch(line):
    from studio import autopilot, config
    config.set_values(max_new_per_day=1, publish_mode="private", ready_buffer=5)
    vid = autopilot.maybe_make()
    line.set_video(vid, state="ready")
    assert autopilot.maybe_make() is None                          # today's limit
    config.set_values(max_new_per_day=5, autopilot=False)
    assert autopilot.maybe_make() is None                          # turned off


def test_halts_after_failures_in_a_row_and_resumes(line):
    from studio import autopilot, config, health
    config.set_values(publish_mode="private", ready_buffer=9, max_new_per_day=9)
    for _ in range(3):
        vid = autopilot.maybe_make()
        line.set_video(vid, state="failed")
    assert autopilot.maybe_make() is None and autopilot.halted()
    assert any("stopped making new YouTube Shorts" in m for _, m in health.peek_news(5))
    autopilot.resume()
    assert autopilot.halted() is None
    assert autopilot.failure_streak() == 0                         # old failures don't re-halt it at once
    assert autopilot.maybe_make() and autopilot.halted() is None


def test_waits_without_ideas_or_channel(line, monkeypatch):
    from studio import autopilot, topics
    monkeypatch.setattr(topics, "pool", lambda now=None: [])
    assert autopilot.maybe_make() is None
    line.run("DELETE FROM channel")
    monkeypatch.setattr(topics, "pool", lambda now=None: [{"id": 1}])
    assert autopilot.maybe_make() is None


def test_keep_awake_runs_caffeinate_only_on_mac(monkeypatch):
    from studio import autopilot
    started = []

    class P:
        def __init__(self, args, **kw):
            started.append(args)

        def terminate(self):
            started.append("stopped")

        def wait(self, t):
            pass
    monkeypatch.setattr(autopilot.subprocess, "Popen", P)
    monkeypatch.setattr(autopilot.sys, "platform", "darwin")
    with autopilot.keep_awake():
        assert started == [["caffeinate", "-i"]]
    assert started[-1] == "stopped"
    started.clear()
    monkeypatch.setattr(autopilot.sys, "platform", "win32")
    with autopilot.keep_awake():
        pass
    assert started == []


def test_restart_mid_render_resumes_the_same_stage(line):
    """Drill: Jarvis dies while a Short is rendering; after the restart it carries on from rendering."""
    from studio import jobs
    calls = []

    def rendering(v, ctx):
        calls.append(v["id"])
        return "validating"
    jobs.register("rendering", rendering)
    vid = jobs.create(idem_key="drill-render")
    line.set_video(vid, state="rendering", owner="99999-dead", heartbeat=time.time())
    assert jobs.recover() == 1
    assert jobs.tick() == "validating" and calls == [vid]
    assert line.one("SELECT what FROM recovery_events WHERE video_id=?", vid)["what"] == "restart"


def test_restart_mid_upload_resumes_without_a_second_upload(line, monkeypatch):
    """Drill: Jarvis stops halfway through an upload; after the restart the same session continues."""
    from clipper import youtube
    from studio import jobs, pipeline, publish, ytauth
    from test_studio_publish import FakeYouTube, _finished_video
    fake = FakeYouTube()
    monkeypatch.setattr(publish, "HTTP", fake)
    monkeypatch.setattr(publish, "CHUNK", 256 * 1024)
    monkeypatch.setattr(ytauth, "OPEN", fake.open)
    youtube._write_secret(ytauth._path(), {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3000})
    pipeline.register()
    vid = _finished_video(line)
    line.set_video(vid, state="uploading", meta=json.dumps(dict(json.loads(line.video(vid)["meta"]),
                                                                  publish_plan={"privacy": "private", "publish_at": None})))
    sent = {"n": 0}
    real = publish._send

    def stop_after_one(session, path, size, progress, stop):
        return real(session, path, size, progress, lambda: (sent.__setitem__("n", sent["n"] + 1) or sent["n"] > 1))
    monkeypatch.setattr(publish, "_send", stop_after_one)
    state = jobs.tick()
    assert state == "uploading" and len(fake.posts) == 1           # interrupted, left in place
    monkeypatch.setattr(publish, "_send", real)
    line.set_video(vid, owner="99999-dead", not_before=0)
    jobs.recover()
    assert jobs.tick() == "processing"
    assert len(fake.posts) == 1 and publish.job(vid)["youtube_id"] == "yt1"   # same session, one upload
