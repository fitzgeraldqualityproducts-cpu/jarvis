"""`python -m studio test` (2026-11-27): a held test Short in one process. The studio is paused for the run and
resumed in a finally, and a test whose window died un-pauses the studio by itself (tests 33 and 36 left it paused
for 2.5 h when the Claude plan limit cut the session off)."""
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def test_the_test_command_holds_the_short_and_resumes_even_on_failure(studio_home, monkeypatch):  # noqa: F811
    from studio import __main__ as cli, db, jobs
    seen = {}

    def fake_video(argv):
        seen["argv"], seen["paused"] = argv, jobs.paused()
        vid = db.insert("INSERT INTO videos(state, title, created, updated) VALUES(?,?,?,?)", "rendering", "t",
                        time.time(), time.time())
        raise RuntimeError(f"render died on {vid}")
    monkeypatch.setattr(cli, "_video", fake_video)
    with pytest.raises(RuntimeError):
        cli._test(["test", "--footage"])
    assert seen["paused"] and seen["argv"] == ["video", "--new-looks", "--footage"]
    assert not jobs.paused() and db.get("test_pause") is None
    v = db.one("SELECT * FROM videos ORDER BY id DESC LIMIT 1")
    assert v["state"] == "rejected" and v["error"] == cli.TEST_HOLD


def test_a_studio_paused_before_the_test_stays_paused(studio_home, monkeypatch):  # noqa: F811
    from studio import __main__ as cli, jobs
    jobs.pause()
    monkeypatch.setattr(cli, "_video", lambda argv: 0)
    assert cli._test(["test"]) == 0
    assert jobs.paused()


def test_a_dead_test_run_unpauses_the_studio_by_itself(studio_home):  # noqa: F811
    from studio import db, jobs
    jobs.pause()
    db.put("test_pause", {"t": time.time(), "beat": time.time(), "was_paused": False})
    assert jobs.paused()                                    # still alive: stays paused
    db.put("test_pause", {"t": 0, "beat": time.time() - jobs.TEST_STALE - 5, "was_paused": False})
    assert not jobs.paused() and db.get("test_pause") is None
    jobs.pause()                                            # Connor's own pause is never lifted
    assert jobs.paused()
    db.put("test_pause", {"t": 0, "beat": 0, "was_paused": True})
    assert jobs.paused()


def test_a_topic_gets_a_fresh_script_inside_the_same_pause(studio_home, monkeypatch):  # noqa: F811
    from studio import __main__ as cli, db, jobs
    calls = []
    old = db.insert("INSERT INTO videos(idem_key, state, title, created, updated) VALUES(?,?,?,?,?)", "topic-69",
                    "rejected", "old try", time.time(), time.time())

    def fake_script(argv):
        calls.append(("script", argv, jobs.paused()))
        db.insert("INSERT INTO videos(idem_key, state, title, created, updated) VALUES(?,?,?,?,?)",
                  f"test-69-{time.time()}", "rendering", "fresh", time.time(), time.time())
        return 0

    def fake_video(argv):
        calls.append(("video", argv, jobs.paused()))
        return 0
    monkeypatch.setattr(cli, "_script", fake_script)
    monkeypatch.setattr(cli, "_video", fake_video)
    assert cli._test(["test", "--topic", "69", "--footage"]) == 0
    new = db.one("SELECT id FROM videos ORDER BY id DESC LIMIT 1")["id"]
    assert calls[0] == ("script", ["script", "--topic", "69", "--fresh"], True)
    assert calls[1] == ("video", ["video", "--new-looks", "--video", str(new), "--footage"], True)
    assert new != old and db.video(new)["state"] == "rejected" and not jobs.paused()
    assert db.video(old)["error"] is None                    # the earlier video is left as it was


def test_fresh_makes_a_new_video_for_an_idea_tried_before(studio_home):  # noqa: F811
    from studio import jobs
    a = jobs.create(7, title="x")
    assert jobs.create(7, title="x") == a                     # the normal key reuses the video
    assert jobs.create(7, idem_key="test-7-1", title="x") != a
