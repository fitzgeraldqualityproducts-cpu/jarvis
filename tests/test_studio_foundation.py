"""Phase 1 of the YouTube business module: database, settings, guard, jobs, recovery, health."""
import json
import sys
import threading
import time

import pytest

from conftest import ROOT


@pytest.fixture(autouse=True)
def studio_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STUDIO_DATA", str(tmp_path / "studio_data"))
    from studio import config, db, jobs
    db.reset_connections()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"studio": {"tick_seconds": 0}}))
    monkeypatch.setattr(config, "CONFIG", cfg)
    monkeypatch.setattr(config, "PLATFORM", "darwin")
    jobs.STAGES.clear()
    jobs.IDLE.clear()
    jobs._stop.clear()
    yield tmp_path
    jobs._stop.set()
    jobs.STAGES.clear()
    db.reset_connections()


def _on(**more):
    from studio import config
    config.set_values(enabled=True, **more)


def test_repo_has_no_studio_data():
    assert not (ROOT / "studio_data").exists()          # tests never write into the real folder


def test_migrations_create_every_record_type_once():
    from studio import db
    c = db.conn()
    assert db.version() == len(db.MIGRATIONS)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("channel", "niche_research", "competitors", "pillars", "topics", "scripts", "sources", "voiceovers",
              "assets", "videos", "qa_checks", "publish_jobs", "published", "analytics", "eligibility", "tools",
              "errors", "recovery_events", "experiments", "decisions", "audit", "notices", "kv"):
        assert t in tables
    db.reset_connections()
    assert db.migrate(db.conn()) == len(db.MIGRATIONS)       # running again changes nothing


def test_off_by_default_and_mac_only():
    from studio import config, jobs
    assert config.enabled() is False
    jobs.register("researching", lambda v, ctx: "scripting")
    jobs.create(idem_key="a")
    assert jobs.tick() is None                               # off: nothing runs
    _on()
    assert config.enabled() is True
    config.PLATFORM = "win32"
    assert config.enabled() is False
    config.set_values(allow_windows=True)
    assert config.enabled() is True


def test_settings_keep_other_config_and_validate():
    from studio import config
    config.CONFIG.write_text(json.dumps({"voice": "bm_george", "studio": {"min_free_gb": "oops", "max_memory_pressure": 5}}))
    g = config.general()
    assert g["min_free_gb"] == config.DEFAULTS["min_free_gb"] and g["max_memory_pressure"] == 0.98
    config.set_values(enabled=True)
    assert json.loads(config.CONFIG.read_text())["voice"] == "bm_george"


def test_guard_allows_only_the_list_and_never_money_or_comments():
    from studio import config, db, guard
    assert guard.allow("youtube.upload", "video 1")
    for bad in ("youtube.post_comment", "youtube.reply_to_viewer", "purchase.credits", "adsense.read", "sponsor.contact",
                "affiliate.link", "billing.enable", "something.new"):
        with pytest.raises(guard.Forbidden):
            guard.allow(bad)
    config.set_values(actions=["youtube.post_comment"])       # a setting can't add actions
    with pytest.raises(guard.Forbidden):
        guard.allow("youtube.post_comment")
    rows = db.q("SELECT action, allowed FROM audit")
    assert {"action": "youtube.upload", "allowed": 1} in rows and {"action": "youtube.post_comment", "allowed": 0} in rows


def test_google_scopes_limited():
    from clipper import youtube
    from studio import guard
    assert guard.check_scopes(youtube.SCOPES)
    for extra in ("https://www.googleapis.com/auth/youtube.force-ssl", "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
                  "https://www.googleapis.com/auth/youtube"):
        with pytest.raises(guard.Forbidden):
            guard.check_scopes(list(youtube.SCOPES) + [extra])


def test_channel_lock_protects_existing_channel():
    from clipper import db as clip_db, settings as clip_settings
    from studio import guard
    clip_settings.ensure_default_channel()
    clip_db.run("UPDATE channels SET youtube_channel_id='UC_EXISTING', youtube_title='Connor' WHERE id='main'")
    with pytest.raises(guard.WrongChannel):
        guard.verify_destination("UC_NEW")                     # nothing locked yet
    with pytest.raises(guard.WrongChannel):
        guard.lock_channel("UC_EXISTING", "Connor")            # the existing channel can never be the business
    assert guard.lock_channel("UC_NEW", "Business")
    assert guard.lock_channel("UC_NEW", "Business")            # same channel again is fine
    with pytest.raises(guard.WrongChannel):
        guard.lock_channel("UC_OTHER", "Second")               # one channel only
    assert guard.verify_destination("UC_NEW")
    for wrong in ("UC_EXISTING", "UC_OTHER", None):
        with pytest.raises(guard.WrongChannel):
            guard.verify_destination(wrong)


def test_tool_registry_is_zero_cost_with_breakers():
    from studio import config, guard
    guard.register_built_ins()
    assert guard.tool_usable("kokoro") == (True, "")
    assert not guard.tool_usable("mystery")[0]
    with pytest.raises(guard.Forbidden):
        guard.register_tool("fancy-voice", "voice", "proprietary", False, True, cost_usd=5)
    with pytest.raises(guard.Forbidden):
        guard.register_tool("ElevenLabs free tier", "voice", "proprietary", False, True, cost_usd=0)
    for _ in range(config.general()["breaker_failures"]):
        guard.tool_result("kokoro", False, "model missing")
    ok, why = guard.tool_usable("kokoro")
    assert not ok and "resting" in why
    guard.tool_result("kokoro", True)
    assert guard.tool_usable("kokoro")[0]


def test_secrets_never_logged():
    from studio import db, guard
    text = 'Bearer ya29.a0AfH6SMBxyz123 {"refresh_token": "1//0gAbCdEfGhIjKlMn", "client_secret":"GOCSPX-secret"}'
    out = guard.redact(text)
    assert "ya29" not in out and "1//0g" not in out and "GOCSPX" not in out and out.count("[hidden]") >= 3
    guard.audit("token.refresh", True, text)
    assert "ya29" not in db.one("SELECT detail FROM audit")["detail"]


def test_job_runs_through_stages_and_is_idempotent():
    from studio import db, jobs
    _on()
    for a, b in zip(db.WORK_STATES, db.WORK_STATES[1:] + ["ready"]):
        jobs.register(a, lambda v, ctx, b=b: (ctx.beat(), b)[1])
    vid = jobs.create(topic_id=7)
    assert jobs.create(topic_id=7) == vid                      # the same topic never makes two jobs
    seen = [jobs.tick() for _ in range(6)]
    assert seen == db.WORK_STATES[1:] + ["ready"]
    assert jobs.tick() is None and db.video(vid)["state"] == "ready" and db.video(vid)["owner"] is None


def test_errors_are_classified_and_retried_the_right_way():
    import urllib.error
    from clipper.youtube import QuotaExceeded
    from studio import config, db, jobs
    _on(retry_base_seconds=10)
    calls = {"n": 0}

    def flaky(v, ctx):
        calls["n"] += 1
        raise urllib.error.URLError("network down")
    jobs.register("researching", flaky)
    vid = jobs.create(idem_key="net")
    assert jobs.tick() == "researching"
    v = db.video(vid)
    assert v["attempts"] == 1 and v["error_kind"] == "transient" and v["not_before"] > time.time() + 5
    assert jobs.tick() is None                                 # waiting for the retry
    assert jobs.backoff(1) == 10 and jobs.backoff(3) == 40 and jobs.backoff(50) == 6 * 3600
    for n in range(2, config.general()["max_attempts"] + 1):
        db.set_video(vid, not_before=0)
        state = jobs.tick()
    assert state == "failed" and calls["n"] == config.general()["max_attempts"]     # limited, not forever
    err = db.one("SELECT * FROM errors WHERE video_id=? ORDER BY id DESC", vid)
    assert err["recovery"] == "gave up" and err["remaining"].startswith("none")

    jobs.register("researching", lambda v, ctx: (_ for _ in ()).throw(QuotaExceeded("quota")))
    q = jobs.create(idem_key="quota")
    jobs.tick()
    assert db.video(q)["error_kind"] == "quota" and db.video(q)["attempts"] == 0
    jobs.register("researching", lambda v, ctx: (_ for _ in ()).throw(KeyError("title")))
    p = jobs.create(idem_key="bug")
    db.set_video(q, not_before=time.time() + 9999)
    assert jobs.tick() == "failed" and db.video(p)["error_kind"] == "permanent"
    assert jobs.classify(OSError(28, "No space left on device")) == "resource"


def test_auth_problem_tells_connor_once():
    from studio import db, health, jobs
    _on()
    jobs.register("researching", lambda v, ctx: (_ for _ in ()).throw(jobs.AuthProblem("token revoked")))
    jobs.create(idem_key="a")
    jobs.create(idem_key="b")
    jobs.tick()
    jobs.tick()
    said = health.news()
    assert len(said) == 1 and "Connect YouTube Business" in said[0]
    assert health.news() == []


def test_restart_mid_stage_resumes_from_last_finished_stage():
    from studio import db, jobs
    _on()
    jobs.register("researching", lambda v, ctx: "scripting")
    vid = jobs.create(idem_key="x")
    jobs.tick()
    db.set_video(vid, owner="99999", heartbeat=time.time())   # a crashed Jarvis owned it mid-script
    jobs.register("scripting", lambda v, ctx: "fact_checking")
    assert jobs.claim() is None                                # still looks alive: never run twice
    assert jobs.recover() == 1
    assert db.video(vid)["owner"] is None and db.video(vid)["stage_done"] == "researching"
    assert jobs.tick() == "fact_checking"
    assert db.one("SELECT * FROM recovery_events WHERE video_id=?", vid)["what"] == "restart"


def test_stalled_stage_is_taken_over():
    from studio import db, jobs
    _on(stall_minutes=1)
    jobs.register("researching", lambda v, ctx: "scripting")
    vid = jobs.create(idem_key="s")
    db.set_video(vid, owner=jobs._ident, heartbeat=time.time() - 120)
    assert jobs.claim()["id"] == vid
    assert db.one("SELECT what FROM recovery_events")["what"] == "stalled stage"


def test_shutdown_interrupts_safely():
    from studio import db, jobs
    _on()

    def slow(v, ctx):
        jobs._stop.set()                                       # Jarvis starts shutting down mid-stage
        ctx.beat()
        return "scripting"
    jobs.register("researching", slow)
    vid = jobs.create(idem_key="i")
    assert jobs.tick() == "researching"
    v = db.video(vid)
    assert v["attempts"] == 0 and v["error_kind"] == "interrupted" and v["owner"] is None


def test_worker_thread_starts_and_stops():
    from studio import db, jobs, status
    _on(tick_seconds=0.05)
    done = threading.Event()

    def stage(v, ctx):
        done.set()
        return "scripting"
    jobs.register("researching", stage)
    vid = jobs.create(idem_key="w")
    report = jobs.start()
    assert report["schema"] == len(db.MIGRATIONS)
    assert jobs.start() is False                               # never two workers
    assert done.wait(5)
    assert jobs.shutdown(5)
    assert db.video(vid)["state"] == "scripting"
    o = status.overview()
    assert o["operating_cost_usd"] == 0 and o["jobs"]["scripting"] == 1 and o["enabled"]
    assert "running" in status.say() and "$0" in status.say()


def test_low_disk_pauses_and_alerts():
    from studio import health, jobs
    _on(min_free_gb=10 ** 6)
    jobs.register("researching", lambda v, ctx: "scripting")
    jobs.create(idem_key="d")
    assert jobs.tick() is None and jobs.tick() is None
    said = health.news()
    assert len(said) == 1 and "disk" in said[0]


def test_waits_for_the_clipper(monkeypatch):
    from studio import health
    _on()
    monkeypatch.setattr(health, "clipper_busy", lambda: True)
    ok, why = health.ok_to_work()
    assert not ok and "clipper" in why
    assert health.ok_to_work(heavy=False)[0]


def test_damaged_database_is_repaired_at_startup():
    from studio import db, health
    db.conn()
    db.put("hello", 1)
    db.reset_connections()
    db.path().write_bytes(b"this is not a database" * 100)
    for s in ("-wal", "-shm"):
        p = db.path().with_name(db.path().name + s)
        if p.exists():
            p.unlink()
    report = health.startup()
    assert report["db"] != "ok" and report["schema"] == len(db.MIGRATIONS)
    assert db.one("SELECT what FROM recovery_events")["what"] == "database"


def test_updates_never_touch_studio_data():
    import updater
    assert updater._is_data("studio_data/studio.db") and updater._is_data("studio_data/renders/x.mp4")
