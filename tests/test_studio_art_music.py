"""AI pictures (SDXL-Turbo), background music (ACE-Step) and the cinematic look from Connor's reference
video: each is used when switched on and working, and falls back on its own, so a Short is never lost."""
import json
from pathlib import Path

import numpy as np

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice, TEXT  # noqa: F401
from test_studio_video import _make, SCENES


def _picture(path, k):
    from PIL import Image
    y = np.linspace(0, 1, 896)[:, None, None]
    x = np.linspace(0, 1, 512)[None, :, None]
    img = np.concatenate([90 + 120 * y + 0 * x, 140 + 60 * x + 0 * y, 200 - 40 * y + 10 * k + 0 * x], axis=2)
    Image.fromarray(np.clip(img, 0, 255).astype("uint8")).save(path)


def _music(path, seconds):
    from clipper import media
    t = np.arange(int(48000 * seconds)) / 48000
    x = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 330 * t)
    media.write_wav(path, x.astype(np.float32), 48000)


def runner_for(seen, fail=()):
    from studio import engines

    def runner(tool, worker, payload, timeout):
        seen.append((tool, payload))
        if tool in fail:
            raise engines.EngineError(f"{tool} isn't installed")
        if tool == "sdxl":
            for k, im in enumerate(payload["images"]):
                _picture(im["out"], k)
            return {"images": [{"out": im["out"], "ok": True} for im in payload["images"]], "device": "RTX 2060"}
        if tool == "acestep":
            _music(payload["out"], payload["duration"])
            return {"path": payload["out"], "seconds": payload["duration"]}
        raise engines.EngineError(f"{tool} isn't installed")
    return runner


def test_cinematic_look_with_ai_pictures_and_music_passes_qa(fakes, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import config, db, engines, qa, video
    seen = []
    monkeypatch.setattr(engines, "RUNNER", runner_for(seen))
    config.set_values(outputs_dir=str(tmp_path / "D-Jarvis" / "studio"))
    engines.use(True, ["images", "music", "look"])
    vid, states = _make(fakes, fake_voice, monkeypatch, scenes=[dict(s, art="a beach seen from the sand") for s in SCENES])
    assert states[-1] == "ready", db.video(vid)["error"]
    meta = json.loads(db.video(vid)["meta"])
    used = meta["render"]["engines"]
    assert used["images"].startswith("SDXL-Turbo") and used["look"].startswith("cinematic")
    assert used["music"].startswith("ACE-Step") and "Manim" in used["visuals"]
    sd = [p for t, p in seen if t == "sdxl"][0]
    assert "a beach seen from the sand" in sd["images"][0]["prompt"] and "no text" in sd["images"][0]["prompt"]
    assert all(str(tmp_path / "D-Jarvis") in im["out"] for im in sd["images"])         # raw outputs on the big drive
    kinds = {a["kind"]: a for a in db.q("SELECT * FROM assets WHERE video_id=?", vid)}
    assert "SDXL-Turbo" in kinds["art:scene"]["license"] and "ACE-Step" in kinds["music"]["license"]
    assert not db.q("SELECT check_name FROM qa_checks WHERE video_id=? AND passed=0", vid)
    up = meta["upload"]
    assert up["containsSyntheticMedia"] and "AI-generated illustrations" in up["description"]
    assert "music is AI-generated" in up["description"]


def test_without_pictures_the_cinematic_look_falls_back_to_diagrams(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import config, db, engines
    monkeypatch.setattr(engines, "RUNNER", runner_for([], fail=("sdxl", "acestep")))
    engines.use(True, ["images", "music", "look"])
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    used = json.loads(db.video(vid)["meta"])["render"]["engines"]
    assert used["look"].startswith("diagram") and "weren't used" in used["images"] and used["music"].startswith("none")
    assert not json.loads(db.video(vid)["meta"])["upload"]["containsSyntheticMedia"]


def test_counter_counts_up_and_carries_on():
    from studio import video
    h = video.huds([{"type": "number", "value": "2,970", "unit": "t", "label": "Saturn V on the pad"},
                    {"type": "headline", "text": "Then what?"},
                    {"type": "number", "value": "2290", "unit": "t", "label": "first stage"},
                    {"type": "equation", "left": "2290 / 2970", "right": "0.77", "label": "of the rocket"}])
    assert h[0]["label"] == "SATURN V ON THE PAD" and h[0]["from"] == h[0]["num"] == 2970   # the hook doesn't move
    assert h[1]["value"] == h[0]["value"]                                                   # carried on
    assert h[2]["num"] == 2290 and h[2]["from"] == 0.0                                       # smaller: counts from 0
    assert h[3]["value"] == "0.77" and h[3]["dec"] == 2


def test_cinematic_remotion_props(fakes, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import engines
    got = {}
    base = runner_for([])

    def runner(tool, worker, payload, timeout):
        if tool == "remotion":
            got.update(payload["props"])
            from clipper import media
            media.run(["-y", "-f", "lavfi", "-i", f"color=c=0x0B1020:s=1080x1920:r=30:d={payload['props']['duration']}",
                       "-i", f"{payload['public']}/{payload['props']['audio']}", "-shortest", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", payload["out"]])
            return {"path": payload["out"]}
        return base(tool, worker, payload, timeout)
    monkeypatch.setattr(engines, "RUNNER", runner)
    engines.use(True, ["images", "look", "compose"])
    _make(fakes, fake_voice, monkeypatch)
    assert got["look"] == "hud" and all(s["bg"].startswith("bg_") for s in got["scenes"])
    assert got["scenes"][0]["title"] and got["scenes"][0]["hud"]
    assert not got["pages"][0]["text"] and all(p.get("text") for p in got["pages"][1:])   # hook = the title
    assert got["caption"]["size"] == 54


def test_ai_tools_need_an_nvidia_card_and_use_the_big_drive(studio_home, monkeypatch, tmp_path):  # noqa: F811
    import pytest
    from studio import config, engines
    monkeypatch.setattr(engines, "installed", lambda tool: True)
    monkeypatch.setattr(engines, "nvidia", lambda: None)
    with pytest.raises(engines.EngineError, match="NVIDIA"):
        engines.run("sdxl", "sdxl_worker", {})
    config.set_values(models_dir=str(tmp_path / "models"))
    assert engines.models_dir() == tmp_path / "models"
    assert engines.TOOL_FOR["music"] == "acestep" and engines.UPGRADED["look"] == "hud"
    assert "acestep" in engines.MODELS_ON_D and "sdxl" in engines.MODELS_ON_D


def test_music_mix_keeps_the_voice_on_top(tmp_path, fake_voice):  # noqa: F811
    from clipper import media
    from studio import music, narration
    narration.narrate(TEXT, tmp_path / "v.wav", record=False)
    _music(tmp_path / "m.wav", 40)
    dur = media.probe(str(tmp_path / "v.wav"))["duration"]
    music.mix(tmp_path / "v.wav", tmp_path / "m.wav", dur, tmp_path / "mix.wav")
    x, rate = media.read_wav(tmp_path / "mix.wav")
    assert rate == 48000 and abs(len(x) / rate - dur) < 0.2 and np.abs(x).max() <= 0.86
    assert music.caption({"pillar": "Space and Planets"}).startswith("instrumental") and "ambient" in music.caption(
        {"pillar": "Space and Planets"})
