"""Jarvis's own Claude Code calls run from an empty folder, so the repo's CLAUDE.md and
.claude/settings.json (notes and locks for coding sessions) never reach them."""
import json
from pathlib import Path

import brain

ROOT = Path(__file__).resolve().parent.parent


def test_calls_without_a_folder_use_the_quiet_room(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cwd"] = kw.get("cwd")
        class R: returncode, stdout, stderr = 0, "ok", ""
        return R()
    monkeypatch.setattr(brain.subprocess, "run", fake_run)
    brain._run_claude(["claude", "-p", "hi"], 5)
    room = Path(seen["cwd"])
    assert room == Path(brain.quiet_room()) and room.is_dir()
    assert not (room / "CLAUDE.md").exists()
    brain._run_claude(["claude", "-p", "hi"], 5, cwd="/somewhere")
    assert seen["cwd"] == "/somewhere"          # a folder given on purpose is kept


def test_lock_covers_every_guarded_file():
    from studio import guard
    asks = json.loads((ROOT / ".claude" / "settings.json").read_text())["permissions"]["ask"]
    for f in guard.GUARDED_FILES:
        assert f"Edit(**/{f})" in asks, f
