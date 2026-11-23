"""Fixes from the 2026-11-23 review: snapshots never hold Connor's data, one shared Claude budget,
and the clipper and the studio never render at the same time."""
import subprocess
import sys
import types

import pytest


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          capture_output=True, text=True)


@pytest.fixture
def jarvis_folder(tmp_path, monkeypatch):
    import selfedit
    folder = tmp_path / "jarvis"
    folder.mkdir()
    monkeypatch.setattr(selfedit, "HERE", folder)
    monkeypatch.setattr(selfedit, "_git", lambda *a: _git(folder, *a))
    return folder


def test_rolling_back_never_deletes_the_youtube_sign_in_or_settings(jarvis_folder):
    import selfedit
    f = jarvis_folder
    (f / "jarvis.py").write_text("print('v1')\n")
    selfedit.ensure_repo()
    before = _git(f, "rev-parse", "HEAD").stdout.strip()
    (f / "studio_data" / "secrets").mkdir(parents=True)
    (f / "studio_data" / "secrets" / "youtube_business.json").write_text("{}")
    (f / "studio_data" / "brand").mkdir()
    (f / "studio_data" / "brand" / "logo.png").write_bytes(b"png")
    (f / "config.json").write_text('{"a": 1}')
    (f / "clip.mp4").write_bytes(b"video")
    selfedit.ensure_repo()                                   # a later snapshot (a voice edit, a repair...)
    tracked = _git(f, "ls-files").stdout.split()
    assert not [p for p in tracked if p.startswith("studio_data/") or p in ("config.json", "clip.mp4")]
    _git(f, "reset", "-q", "--hard", before)                 # undoing an update
    assert (f / "studio_data" / "secrets" / "youtube_business.json").exists()
    assert (f / "studio_data" / "brand" / "logo.png").exists()
    assert (f / "config.json").read_text() == '{"a": 1}'
    assert (f / "clip.mp4").exists()


def test_files_tracked_by_older_versions_are_let_go_but_kept_on_disk(jarvis_folder):
    import selfedit
    f = jarvis_folder
    (f / "studio_data" / "secrets").mkdir(parents=True)
    (f / "studio_data" / "secrets" / "youtube_business.json").write_text("{}")
    (f / "config.json").write_text("{}")
    _git(f, "init", "-q")
    _git(f, "add", "-A")
    _git(f, "commit", "-q", "-m", "old snapshot that tracked everything")
    selfedit.ensure_repo()
    assert _git(f, "ls-files").stdout.split() == [".gitignore"]
    assert (f / "studio_data" / "secrets" / "youtube_business.json").exists() and (f / "config.json").exists()


def test_background_calls_share_one_daily_limit(monkeypatch):
    import brain
    import budget
    monkeypatch.setattr(budget, "per_day", lambda: 2)
    calls = []
    monkeypatch.setattr(brain, "run_claude", lambda cmd, timeout, cwd=None: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "ok", ""))
    outs = [brain.run_background(["claude", "-p", "hi"], 10, who="clipper") for _ in range(3)]
    assert [o.returncode for o in outs] == [0, 0, 1] and len(calls) == 2
    assert "today's 2 Claude calls" in outs[2].stderr
    assert budget.today()["by"] == {"clipper": 2}


def test_a_plan_limit_pauses_background_work_for_an_hour():
    import budget
    assert budget.check()[0]
    assert not budget.saw("some ordinary error")
    assert budget.saw("Claude AI usage limit reached|1700000000")
    ok, why = budget.check()
    assert not ok and "limit was reached" in why


def test_the_clipper_waits_while_the_studio_is_making_a_short(monkeypatch, tmp_path):
    from clipper import jobs, sources
    from conftest import make_video
    sources.add(str(make_video(tmp_path / "a.mp4", 6)))
    fake = types.SimpleNamespace(busy=lambda: True)
    monkeypatch.setitem(sys.modules, "studio.jobs", fake)
    assert jobs.claim() is None                              # the studio is rendering: wait
    fake.busy = lambda: False
    assert jobs.claim() is not None
