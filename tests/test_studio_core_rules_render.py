"""Connor's core rules on a whole Short (fake engines): the core look's end card, 1-2 sound effects, caption lead,
pattern interrupt, the QA checks and the output pack. Its own file, so the fixtures of the full-Short tests apply."""
import json
from pathlib import Path

from test_studio_better_shorts import _upgraded_runner, three  # noqa: F401
from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import BODY, FakeClaude, fakes  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401
from test_studio_core_rules import REAL_MODE


def test_a_whole_short_with_the_core_look(three, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import config, db, engines, rules
    from test_studio_video import _make
    monkeypatch.setattr(rules, "mode", REAL_MODE)
    real = config.general
    monkeypatch.setattr(config, "general", lambda: dict(real(), core_rules="advise", core_rules_look=True))
    seen, got = [], {}
    monkeypatch.setattr(engines, "RUNNER", _upgraded_runner(seen, got))
    config.set_values(outputs_dir=str(tmp_path / "D-Jarvis" / "studio"))
    engines.use(True, ["images", "look", "compose", "sharpen", "motion", "edit", "sfx"])
    vid, states = _make(three, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    meta = json.loads(db.video(vid)["meta"])
    rep = meta["render"]
    cr = rep["core_rules"]
    card = cr["end_card"]
    assert card and card["seconds"] >= rules.END_CARD[0] and got["endCard"] == card and got["loopBlend"] == 0
    assert abs(rep["duration"] - (card["start"] + card["seconds"])) < 0.05
    n_fx = int(rep["engines"]["sfx"].split(": ")[1].split()[0])
    assert 1 <= n_fx <= rules.SFX_MAX
    assert all(p["e"] <= card["start"] + 1e-6 for p in got["pages"])
    huds = [s["hud"] for s in got["scenes"] if s.get("hud")]
    assert huds and all(h.get("holdFor") == 3.0 for h in huds)
    names = {c["check_name"]: c for c in db.q("SELECT * FROM qa_checks WHERE video_id=?", vid)}
    assert "core rules: timing" in names and "phone check (quality gate)" in names
    assert "core rules: description" in names and "seamless loop" not in names
    assert "core rules: clean final frame" in names
    assert meta["upload"]["description"].count("Comment") >= 1
    folder = Path(db.video(vid)["path"]).parent
    md = (folder / "pack.md").read_text()
    for part in ("## Final script", "## Shot list", "end card", "## Titles", "## Thumbnail concept", "## Description",
                 "## Notes on the creative choices"):
        assert part in md, part
    assert json.loads(db.video(vid)["meta"])["pack"]["shots"][-1]["scene"] == "end card"
