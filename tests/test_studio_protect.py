"""Phase 2 of the master directive (2026-11-24): verified backups, a guard that self-edits can't weaken,
a protected list that can't be emptied, honest "live" counts, and no comment endpoints anywhere."""
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from conftest import ROOT
from test_studio_foundation import studio_home  # noqa: F401


@pytest.fixture
def data(studio_home, monkeypatch, tmp_path):
    from studio import backup, config, db
    monkeypatch.setattr(config, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(backup, "ROOT", tmp_path)                       # memory.db lives here in tests
    db.insert("INSERT INTO topics(title, fingerprint, status) VALUES(?,?,?)", "Why is space cold?", "fp1", "idea")
    from studio import data_dir
    (data_dir("secrets") / "youtube_business.json").write_text('{"refresh_token": "RT-secret"}')
    c = sqlite3.connect(tmp_path / "memory.db")
    c.execute("CREATE TABLE facts(x)")
    c.execute("INSERT INTO facts VALUES (1)")
    c.commit()
    c.close()
    monkeypatch.setattr(backup, "root", lambda: (tmp_path / "backups", True))
    return tmp_path


# ---------- backups ----------

def test_backup_is_complete_and_verified(data):
    from studio import backup
    rep = backup.make("test")
    assert rep["ok"], rep["problems"]
    folder = Path(rep["folder"])
    assert not folder.name.endswith((".partial", ".failed"))
    assert {"studio.db", "memory.db", "config.json", "secrets/youtube_business.json"} <= set(rep["files"])
    man = json.loads((folder / "manifest.json").read_text())
    assert man["made_by"] == backup.MADE_BY
    assert man["files"]["studio.db"]["integrity"] == "ok" and man["files"]["studio.db"]["rows"]["topics"] == 1
    assert backup.verify(folder)["ok"]
    c = sqlite3.connect(folder / "studio.db")
    assert c.execute("SELECT title FROM topics").fetchone()[0] == "Why is space cold?"
    c.close()


def test_a_changed_backup_fails_verification(data):
    from studio import backup
    folder = Path(backup.make()["folder"])
    with open(folder / "config.json", "a") as f:
        f.write(" ")
    rep = backup.verify(folder)
    assert not rep["ok"] and any("config.json" in p for p in rep["problems"])
    (folder / "memory.db").unlink()
    assert any("memory.db: missing" in p for p in backup.verify(folder)["problems"])


def test_backups_never_print_secrets(data, capsys):
    from studio import __main__ as cli
    assert cli.main(["backup", "now"]) == 0
    assert cli.main(["backup", "plan"]) == 0
    out = capsys.readouterr().out
    assert "RT-secret" not in out and "verified" in out


def test_rotation_keeps_14_daily_and_8_weekly_and_only_its_own(data, monkeypatch):
    from studio import backup
    base = data / "backups"
    base.mkdir()
    day = 86400
    now = time.time()
    for i in range(70):                                     # one a day for 70 days
        d = base / f"b{i:03d}"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"made_by": backup.MADE_BY, "t": now - i * day, "files": {}}))
    mine_not = base / "connors-folder"
    mine_not.mkdir()
    (mine_not / "notes.txt").write_text("keep me")
    removed = backup.rotate(now)
    left = [d for d in base.iterdir() if d.name.startswith("b")]
    assert 14 <= len(left) <= 14 + 8
    assert (base / "b000").exists() and mine_not.exists()   # the newest, and folders it didn't make, always stay
    assert removed


def test_daily_backup_runs_once_a_day(data):
    from studio import backup
    assert backup.maybe_run()["ok"]
    assert backup.maybe_run() is None
    assert backup.status()["count"] == 1


def test_restore_refuses_while_jarvis_runs_and_keeps_a_safety_copy(data):
    from studio import backup, db
    good = Path(backup.make()["folder"])
    with pytest.raises(RuntimeError, match="Close Jarvis"):
        backup.restore(good, running=True)
    db.run("DELETE FROM topics")
    plan = {r["name"]: r for r in backup.plan(good)}
    assert plan["studio.db"]["changed_tables"]["topics"] == [0, 1]
    assert plan["config.json"]["auto"] is False             # settings and secrets only when named
    rep = backup.restore(good, running=False)
    assert "studio.db" in rep["restored"] and "config.json" not in rep["restored"]
    assert backup.verify(rep["safety_copy"])["ok"]
    db.reset_connections()
    assert db.one("SELECT COUNT(*) n FROM topics")["n"] == 1


def test_restore_refuses_a_bad_backup(data):
    from studio import backup
    folder = Path(backup.make()["folder"])
    (folder / "studio.db").write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="didn't pass"):
        backup.restore(folder, running=False)


def test_the_worker_backs_up_in_its_spare_time():
    from studio import backup, jobs
    import inspect
    assert "backup.maybe_run" in inspect.getsource(jobs.start)
    assert backup.maybe_run


# ---------- the guard ----------

def test_connors_channel_is_protected_in_the_code_whatever_the_settings():
    from studio import config, guard
    config.set_values(protected_channels=[])
    assert "UCamLvdxlcfg3YqISoj4jlcA" in guard.protected_channels()
    with pytest.raises(guard.WrongChannel):
        guard.lock_channel("UCamLvdxlcfg3YqISoj4jlcA", "Sauce")
    assert "UCIfBUEhiNl78bB_MoeE225A" not in guard.PERMANENT_PROTECTED     # the business channel never is


def test_upload_refused_for_protected_empty_list_or_protected_lock(monkeypatch):
    from studio import db, guard
    guard.lock_channel("UC_BIZ", "Business")
    monkeypatch.setattr(guard, "INTEGRITY", lambda: ("ok", []))
    assert guard.verify_destination("UC_BIZ")
    for wrong in ("UCamLvdxlcfg3YqISoj4jlcA", "UC_OTHER", "", None):
        with pytest.raises(guard.WrongChannel):
            guard.verify_destination(wrong)
    monkeypatch.setattr(guard, "protected_channels", lambda: set())
    with pytest.raises(guard.WrongChannel, match="empty"):
        guard.verify_destination("UC_BIZ")
    monkeypatch.setattr(guard, "protected_channels", lambda: {"UC_BIZ"})
    with pytest.raises(guard.WrongChannel, match="never-publish"):
        guard.verify_destination("UC_BIZ")
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='youtube.upload.destination' AND allowed=0")["n"] >= 6


def test_upload_refused_when_the_safety_code_was_changed_here(monkeypatch):
    from studio import guard
    guard.lock_channel("UC_BIZ", "Business")
    monkeypatch.setattr(guard, "INTEGRITY", lambda: ("changed", ["studio/guard.py"]))
    with pytest.raises(guard.WrongChannel, match="studio/guard.py"):
        guard.verify_destination("UC_BIZ")
    monkeypatch.setattr(guard, "INTEGRITY", lambda: ("unknown", []))       # no GitHub copy yet: not a stop
    assert guard.verify_destination("UC_BIZ")


def _repo(tmp_path):
    """A tiny git repo shaped like Jarvis, with an updater ref, for the real integrity check."""
    r = tmp_path / "repo"
    (r / "studio").mkdir(parents=True)
    for f in ("studio/guard.py", "selfedit.py"):
        (r / f).write_text("SAFE = True\n")
    g = lambda *a: subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  capture_output=True, text=True, check=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-q", "-m", "v1")
    (r / "selfedit.py").write_text("SAFE = True  # v2\n")
    g("commit", "-q", "-am", "v2")
    g("update-ref", "refs/remotes/updates/main", "HEAD")
    return r, g


def test_integrity_check_against_the_github_copy(tmp_path, monkeypatch):
    import updater
    from studio import guard
    r, g = _repo(tmp_path)
    monkeypatch.setattr(updater, "HERE", r)
    monkeypatch.setattr(guard, "GUARDED_FILES", ("studio/guard.py", "selfedit.py"))
    guard._integrity_cache.clear()
    assert guard.integrity(max_age=0) == ("ok", [])
    (r / "selfedit.py").write_text("SAFE = True\n")                       # an older GitHub version is fine
    assert guard.integrity(max_age=0) == ("ok", [])
    (r / "selfedit.py").write_bytes(b"SAFE = True  # v2\r\n")              # Windows line endings aren't a change
    assert guard.integrity(max_age=0) == ("ok", [])
    (r / "studio" / "guard.py").write_text("SAFE = False\n")              # a local change is not
    assert guard.integrity(max_age=0) == ("changed", ["studio/guard.py"])
    g("update-ref", "-d", "refs/remotes/updates/main")
    assert guard.integrity(max_age=0)[0] == "unknown"


def test_self_edits_can_never_change_the_safety_code(tmp_path, monkeypatch):
    import selfedit
    r, g = _repo(tmp_path)
    assert selfedit._check(r) is None or "channel-safety" not in (selfedit._check(r) or "")
    (r / "studio" / "guard.py").write_text("SAFE = False\n")
    assert selfedit.guarded_touched(r) == ["studio/guard.py"]
    problem = selfedit._check(r)
    assert problem and "channel-safety" in problem
    assert set(selfedit.GUARDED) == set(__import__("studio.guard", fromlist=["x"]).GUARDED_FILES)


def test_self_edits_cant_change_guarded_settings(tmp_path, monkeypatch):
    import selfedit
    monkeypatch.setattr(selfedit, "HERE", tmp_path)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"voice": "a", "studio": {"protected_channels": ["UC_X"], "publish_mode": "public",
                                                        "max_new_per_day": 3}}))
    before = selfedit.guarded_settings()
    cfg.write_text(json.dumps({"voice": "b", "studio": {"protected_channels": [], "publish_mode": "public",
                                                        "max_new_per_day": 9, "drafts": 2}}))
    fixed = selfedit.restore_guarded_settings(before)
    assert set(fixed) == {"protected_channels", "max_new_per_day"}
    now = json.loads(cfg.read_text())
    assert now["studio"]["protected_channels"] == ["UC_X"] and now["studio"]["max_new_per_day"] == 3
    assert now["voice"] == "b" and now["studio"]["drafts"] == 2            # other changes are kept
    assert selfedit.restore_guarded_settings(before) == []


def test_night_school_and_repair_use_the_same_check():
    import inspect

    import nightschool
    import repair
    assert "selfedit._check" in inspect.getsource(nightschool) and "selfedit._check" in inspect.getsource(repair)
    assert "restore_guarded_settings" in inspect.getsource(repair)


def test_no_comment_endpoint_anywhere_in_the_studio():
    for f in (ROOT / "studio").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "commentThreads" not in text and "youtube/v3/comments" not in text, f


# ---------- honest dashboard ----------

def test_a_private_upload_is_not_counted_as_live():
    from studio import dashboard, db
    for n, privacy in ((1, "private"), (2, "public")):
        vid = db.insert("INSERT INTO videos(idem_key, state, title, created, updated) VALUES(?,?,?,?,?)",
                        f"k{n}", "published", f"Short {n}", time.time(), time.time())
        db.insert("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy) VALUES(?,?,?,?,?)",
                  vid, f"yt{n}", "UC_BIZ", f"Short {n}", privacy)
    s = dashboard.state()
    assert s["live_count"] == 1 and s["private_count"] == 1
    texts = [a["text"] for a in s.get("activity") or []]
    if texts:
        assert any("uploaded (private)" in t for t in texts) and sum("went live" in t for t in texts) == 1


def test_hardware_readings_for_the_dashboard(monkeypatch):
    from studio import dashboard, health
    health._gpu_cache.clear()
    monkeypatch.setattr(health, "GPU_RUN", lambda: "NVIDIA GeForce RTX 2060, 1536, 6144, 37, 61")
    g = health.gpu(max_age=0)
    assert g == {"name": "NVIDIA GeForce RTX 2060", "used_gb": 1.5, "total_gb": 6.0, "util": 0.37, "temp_c": 61.0}
    r = health.resources()
    assert r["gpu"]["temp_c"] == 61.0 and r["drives"] and "load" in r
    s = dashboard.state()
    assert s["hardware"]["gpu"]["total_gb"] == 6.0 and s["backup"] == {"hours_ago": None}
    monkeypatch.setattr(health, "GPU_RUN", lambda: (_ for _ in ()).throw(RuntimeError("no card")))
    assert health.gpu(max_age=0) is None                                     # no card: nothing made up


def test_registry_lists_every_engine_at_zero_cost(capsys):
    from studio import __main__ as cli, engines, guard, registry
    assert registry.register_all() == len(registry.TOOLS)
    areas = {v[8] for v in registry.TOOLS.values() if v[8]}
    assert areas == set(engines.UPGRADED) - {"look", "edit"}  # every optional engine ("look" and "edit" are own code)
    for r in registry.rows():
        assert r["registered"] and r["cost_usd"] == 0 and r["fallback"] and r["source"]
    assert all(not any(b in n for b in guard.BANNED_TOOLS) for n in registry.TOOLS)
    cli.main(["registry"])
    out = capsys.readouterr().out
    assert "| sdxl-turbo |" in out and "| pexels |" in out and "$0" in out


def test_quality_score_is_weighted_and_never_guesses():
    from studio import qa
    best = qa.quality({"lufs": -14, "loop_diff": 0, "timing": 1.0, "speech_start": 0, "hook": 100, "originality": 10,
                       "confidence": 1.0})
    assert best["score"] == 100.0 and not best["missing"]
    mid = qa.quality({"lufs": -16, "loop_diff": 7, "timing": 0.925, "speech_start": 0.175, "hook": 50,
                      "originality": 7.5, "confidence": 0.5})
    assert mid["score"] == 50.0
    part = qa.quality({"lufs": -14, "hook": None})
    assert part["score"] == 100.0 and "hook" in part["missing"] and "loop" in part["missing"]
    assert qa.quality({})["score"] is None


def test_policy_watch_tells_connor_and_changes_nothing(monkeypatch):
    from studio import config, db, health, policy
    base = "Creators can apply when they reach 1,000 subscribers and 4,000 watch hours in the last 12 months. " * 8
    pages = {u: base for u in policy.PAGES.values()}
    before = json.dumps(config.general(), sort_keys=True, default=str)
    first = policy.check(read=lambda u: pages[u])
    assert {r["state"] for r in first} == {"first"} and not health.peek_news(5)
    assert {r["state"] for r in policy.check(read=lambda u: pages[u])} == {"same"}
    url = policy.PAGES["Partner Program eligibility"]
    pages[url] = base.replace("4,000 watch hours", "8,000 watch hours")[:300] + \
        " From February 2027 new channels need 8,000 watch hours or 20 million Shorts views to join. " * 6
    rows = {r["url"]: r for r in policy.check(read=lambda u: pages[u])}
    assert rows[url]["state"] == "changed" and any("8,000" in s for s in rows[url]["new"])
    msgs = [m for _, m in health.peek_news(5)]
    assert any("Partner Program eligibility" in m and "haven't changed anything" in m for m in msgs)
    assert json.dumps(config.general(), sort_keys=True, default=str) == before        # no setting touched
    pages[url] = ""
    assert {r["url"]: r for r in policy.check(read=lambda u: pages[u])}[url]["state"] == "unreadable"
    assert db.get("policy_pages")[url]["text"]                                        # the last good copy is kept


def test_policy_watch_runs_weekly(monkeypatch):
    from studio import config, policy
    calls = []
    monkeypatch.setattr(config, "enabled", lambda: True)
    monkeypatch.setattr(policy, "check", lambda now=None: calls.append(now) or [])
    t = time.time()
    policy.maybe_check(t)
    assert policy.maybe_check(t + 3600) is None and len(calls) == 1
    policy.maybe_check(t + policy.EVERY + 1)
    assert len(calls) == 2


def test_experiments_only_run_when_started_and_only_suggest(monkeypatch):
    from studio import config, db, experiments, health
    assert experiments.instruction({"id": 1}) == ""                       # nothing runs unless Connor starts it
    experiments.start("hook_style")
    with pytest.raises(experiments.ExperimentError):
        experiments.start("length")                                        # one variable at a time
    arms = [experiments.instruction({"id": t}) for t in range(1, 13)]
    assert sum('arm "question"' in a for a in arms) == 6 and sum('arm "number"' in a for a in arms) == 6
    assert experiments.instruction({"id": 1}) == arms[0]                  # assigned once
    assert experiments.evaluate(index={})["state"] == "waiting"
    assign = experiments.running()["result"]["assign"]
    idx = {f"yt{t}": {"topic_id": t, "index": (70 + t % 3 if assign[str(t)] == "number" else 45 + t % 3)}
           for t in range(1, 13)}
    before = json.dumps(config.general(), sort_keys=True, default=str)
    res = experiments.evaluate(index=idx)
    assert res["state"] == "clear" and res["winner"] == "number"
    assert json.dumps(config.general(), sort_keys=True, default=str) == before   # nothing changed by itself
    assert any("only a suggestion" in m for _, m in health.peek_news(5))
    assert experiments.running() is None and experiments.instruction({"id": 99}) == ""


def test_experiment_with_no_clear_difference_changes_nothing():
    from studio import experiments
    experiments.start("length")
    for t in range(1, 13):
        experiments.instruction({"id": t})
    idx = {f"yt{t}": {"topic_id": t, "index": 50 + (t % 5)} for t in range(1, 13)}
    assert experiments.evaluate(index=idx)["state"] == "no difference"


def test_the_writer_gets_the_arm(monkeypatch):
    import inspect
    from studio import script
    src = inspect.getsource(script.write)
    assert "experiments.instruction(topic)" in src and "if not feedback" in src
