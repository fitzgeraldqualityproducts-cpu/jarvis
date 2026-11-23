"""Phase 2 of the YouTube business module: narration and the local toolchain."""
import json
import sys

import numpy as np
import pytest

from test_studio_foundation import studio_home  # noqa: F401  (same isolated data folder)


def _speechy(seconds, rate=24000, gap_at=None, gap=0.0, lead=0.6):
    """Fake narration: tone bursts like syllables, silence at the start/end and an optional long gap."""
    t = np.arange(int(rate * seconds)) / rate
    x = 0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 4 * t) > -0.3)
    parts = [np.zeros(int(rate * lead)), x]
    if gap_at is not None:
        cut = int(rate * gap_at)
        parts = [np.zeros(int(rate * lead)), x[:cut], np.zeros(int(rate * gap)), x[cut:]]
    parts.append(np.zeros(int(rate * lead)))
    return np.concatenate(parts).astype(np.float32), rate


@pytest.fixture
def fake_voice(monkeypatch):
    from studio import guard, narration
    guard.register_built_ins()
    seen = {}

    def synth(text, voice, speed):
        seen.update(text=text, voice=voice, speed=speed)
        return _speechy(len(text.split()) / 2.6, gap_at=2.0, gap=2.0)     # ~156 words a minute
    monkeypatch.setattr(narration, "SYNTH", synth)
    return seen


TEXT = ("A piece of a neutron star the size of a sugar cube would weigh about a billion tons on Earth. "
        "Here's why. Gravity crushes its atoms until protons and electrons merge.")


def test_narration_is_trimmed_levelled_validated_and_recorded(fake_voice, tmp_path):
    from clipper import media
    from studio import config, db, narration
    config.set_values(voice="am_michael", voice_speed=1.1)
    rec = narration.narrate(TEXT, tmp_path / "vo.wav", video_id=5)
    assert fake_voice["voice"] == "am_michael" and fake_voice["speed"] == 1.1
    x, rate = media.read_wav(rec["path"])
    assert rate == 48000 and x.shape[1] == 1
    words = len(TEXT.split()) / 2.6
    assert words - 0.3 < rec["seconds"] < words + 1.0        # the 1.2 s of edges and the 2 s gap were shortened
    assert -17 <= rec["loudness"] <= -11 and rec["checks"]["ok"]
    row = db.one("SELECT * FROM voiceovers WHERE video_id=5")
    assert row["voice"] == "am_michael" and row["engine"] == "kokoro" and json.loads(row["checks"])["ok"]


def test_validation_rejects_bad_audio(tmp_path):
    from clipper import media
    from studio import narration
    silent = tmp_path / "s.wav"
    media.write_wav(silent, np.zeros(48000 * 5, np.float32), 48000)
    v = narration.validate(silent, TEXT)
    assert not v["ok"] and any("silent" in p for p in v["problems"])
    short = tmp_path / "cut.wav"
    x, r = _speechy(2.0, rate=48000, lead=0.1)
    media.write_wav(short, x, 48000)
    v = narration.validate(short, TEXT)                         # 30 words in 2 s: cut off or sped up
    assert not v["ok"] and any("words a minute" in p for p in v["problems"])
    assert not narration.validate(tmp_path / "nope.wav")["ok"]


def test_a_failing_voice_engine_trips_its_breaker(monkeypatch, tmp_path):
    from studio import config, guard, narration
    guard.register_built_ins()

    def broken(text, voice, speed):
        raise RuntimeError("onnx session failed")
    monkeypatch.setattr(narration, "SYNTH", broken)
    for _ in range(config.general()["breaker_failures"]):
        with pytest.raises(narration.NarrationError):
            narration.narrate(TEXT, tmp_path / "x.wav")
    with pytest.raises(narration.NarrationError, match="resting"):
        narration.narrate(TEXT, tmp_path / "x.wav")


def test_tidy_keeps_short_pauses():
    from studio import narration
    x, r = _speechy(3.0, rate=48000, gap_at=1.5, gap=0.3, lead=1.0)
    y = narration.tidy(x, r)
    assert abs(len(y) / r - (3.0 + 0.3 + 0.24)) < 0.15          # edges trimmed to 0.12 s, 0.3 s pause kept


def test_toolchain_check_is_all_free():
    from studio import toolchain
    out = toolchain.check()
    assert out["sqlite"]["ok"] and all(info["cost_usd"] == 0 for info in out.values())
    assert "ffmpeg" in out and "kokoro" in out


def test_test_short_renders_vertical_with_audio(fake_voice, tmp_path):
    from clipper import media
    from studio import toolchain
    res = toolchain.benchmark(tmp_path / "bench")
    assert res["ok"], res
    assert res["video"]["width"] == 1080 and res["video"]["height"] == 1920 and res["video"]["acodec"] == "aac"
