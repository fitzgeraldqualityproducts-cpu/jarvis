"""The upgraded video tools (Chatterbox, whisperX, Manim, Remotion): each one is used when switched on and
working, and each falls back to the classic way on its own, so a missing or broken tool never stops a Short."""
import json
import shutil

import numpy as np
import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice, TEXT  # noqa: F401
from test_studio_video import _make, SCENES


def test_defaults_are_classic_and_use_switches(studio_home):  # noqa: F811
    from studio import config, engines
    assert engines.settings() == engines.DEFAULTS
    assert engines.settings({"voice": "chatterbox"})["voice"] == "chatterbox"
    engines.use(True)
    assert config.general()["engines"] == engines.UPGRADED
    engines.use(False, ["voice"])
    assert engines.settings()["voice"] == "kokoro" and engines.settings()["visuals"] == "manim"


def test_missing_tool_is_an_engine_error(studio_home, monkeypatch):  # noqa: F811
    from studio import engines
    monkeypatch.setattr(engines, "TOOLS", engines.TOOLS / "nowhere")
    with pytest.raises(engines.EngineError, match="isn't installed"):
        engines.run("manim", "manim_worker", {})


def test_heavy_jobs_take_turns(studio_home):  # noqa: F811
    import threading
    from studio import engines
    order = []

    def job(name):
        with engines.heavy(name):
            order.append(name + " in")
            import time
            time.sleep(0.2)
            order.append(name + " out")
    a = threading.Thread(target=job, args=("a",))
    a.start()
    import time
    time.sleep(0.05)
    job("b")
    a.join()
    assert order == ["a in", "a out", "b in", "b out"]


def test_chatterbox_voice_with_kokoro_fallback(tmp_path, monkeypatch, fake_voice):  # noqa: F811
    from clipper import media
    from studio import engines, narration
    from test_studio_toolchain import _speechy
    calls = []

    def runner(tool, worker, payload, timeout):
        calls.append((tool, payload))
        x, rate = _speechy(len(payload["text"].split()) / 2.6, gap_at=2.0, gap=0.3)
        media.write_wav(payload["out"], np.asarray(x, dtype=np.float32), rate)
        return {"path": payload["out"], "device": "cpu", "sentences": 2, "took": 1.0}
    monkeypatch.setattr(engines, "RUNNER", runner)
    rec = narration.narrate(TEXT, tmp_path / "a.wav", engine="chatterbox")
    assert rec["engine"] == "chatterbox" and rec["checks"]["ok"] and not rec.get("fallback")
    assert calls[0][0] == "chatterbox" and calls[0][1]["reference"].endswith(".wav")    # the channel's own voice

    def broken(tool, worker, payload, timeout):
        raise engines.EngineError("chatterbox needs about 3.0 GB of free memory and only 1.2 GB is free")
    monkeypatch.setattr(engines, "RUNNER", broken)
    rec = narration.narrate(TEXT, tmp_path / "b.wav", engine="chatterbox")
    assert rec["engine"] == "kokoro" and "free memory" in rec["fallback"]


def test_sample_narration_saves_nothing(tmp_path, fake_voice):  # noqa: F811
    from studio import db, narration
    narration.narrate(TEXT, tmp_path / "a.wav", record=False)
    assert not db.q("SELECT 1 FROM voiceovers")


def test_whisperx_alignment_is_used_then_falls_back(monkeypatch, studio_home):  # noqa: F811
    from studio import engines, video
    text = "The Saturn V weighed 2,970 tonnes."
    aligned = [{"w": w, "s": 0.3 * i, "e": 0.3 * i + 0.25} for i, w in enumerate(text.split())]
    aligned[4]["s"] = None                                       # a word it couldn't place
    monkeypatch.setattr(engines, "RUNNER", lambda tool, worker, payload, timeout: {"words": aligned})
    monkeypatch.setattr(video, "TRANSCRIBE", lambda a: [])
    words, info = video.word_times("x.wav", text, align="whisperx")
    assert info["method"] == "whisperX forced alignment" and [w["w"] for w in words] == text.split()
    assert words[1]["s"] == 0.3 and words[3]["s"] <= words[4]["s"] <= words[5]["s"]

    def broken(*a):
        raise engines.EngineError("whisperx isn't installed")
    monkeypatch.setattr(engines, "RUNNER", broken)
    heard = [{"w": w, "s": 0.2 * i, "e": 0.2 * i + 0.1} for i, w in enumerate(text.split())]
    monkeypatch.setattr(video, "TRANSCRIBE", lambda a: heard)
    words, info = video.word_times("x.wav", text, align="whisperx")
    assert info["method"] == "whisper matched to the script" and "isn't installed" in info["note"]


def _fake_manim(payload):
    """Writes a short clip and a last frame for every scene, like the real worker."""
    import cv2
    out = []
    for sc in payload["scenes"]:
        still = cv2.imread(sc["out"].replace("manim_", "scene_").replace(".mp4", ".png"))
        wr = cv2.VideoWriter(sc["out"], cv2.VideoWriter_fourcc(*"mp4v"), 30, (1080, 1920))
        for f in range(12):
            wr.write(cv2.convertScaleAbs(still, alpha=0.5 + f / 24))
        wr.release()
        hold = sc["out"].replace(".mp4", "_last.png")
        cv2.imwrite(hold, still)
        out.append({"out": sc["out"], "hold": hold, "ok": True, "seconds": 0.4})
    return {"clips": out}


def test_upgraded_short_passes_qa_with_manim_and_falls_back_from_remotion(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import db, engines
    seen = []

    def runner(tool, worker, payload, timeout):
        seen.append(tool)
        if tool == "manim":
            assert payload["scenes"] and all(s["spec"]["type"] != "nonsense" for s in payload["scenes"])
            return _fake_manim(payload)
        raise engines.EngineError(f"{tool} isn't installed")
    monkeypatch.setattr(engines, "RUNNER", runner)
    engines.use(True)
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    rep = json.loads(db.video(vid)["meta"])["render"]
    assert rep["engines"]["visuals"].startswith("Manim: ") and "Remotion wasn't used" in rep["engines"]["compose"]
    assert "whisperX wasn't used" in rep["engines"]["align"]
    assert {"manim", "remotion", "whisperx", "chatterbox"} <= set(seen)
    kinds = [a["kind"] for a in db.q("SELECT kind FROM assets WHERE video_id=?", vid)]
    assert any(k.startswith("animation:") for k in kinds)
    assert not db.q("SELECT check_name FROM qa_checks WHERE video_id=? AND passed=0", vid)
    hook = [s for s in seen if s == "manim"]
    assert hook                                                    # (the hook scene itself is never animated)


def test_remotion_output_is_used_and_reported(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import db, engines, video
    props_seen = {}

    def runner(tool, worker, payload, timeout):
        if tool == "remotion":
            props_seen.update(payload["props"])
            from clipper import media
            # stand in for Remotion: a valid 1080x1920 H.264/AAC file of the right length
            media.run(["-y", "-f", "lavfi", "-i", f"color=c=0x0B1020:s=1080x1920:r=30:d={payload['props']['duration']}",
                       "-i", f"{payload['public']}/{payload['props']['audio']}", "-shortest", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", payload["out"]])
            return {"path": payload["out"]}
        raise engines.EngineError(f"{tool} isn't installed")
    monkeypatch.setattr(engines, "RUNNER", runner)
    engines.use(True, ["compose"])
    vid, states = _make(fakes, fake_voice, monkeypatch)
    rep = json.loads(db.video(vid)["meta"])["render"]
    assert rep["engines"]["compose"].startswith("Remotion")
    assert props_seen["scenes"][0]["start"] == 0 and "video" not in props_seen["scenes"][0]
    assert props_seen["pages"] and rep["caption_words_shown"] == rep["words"]
    assert all(c[1] + c[3] <= 1920 * 0.8 and c[0] >= 40 and c[0] + c[2] <= 1080 - 120 for c in rep["caption_boxes"])


def test_memory_need_is_halved_under_normal_mac_pressure(studio_home, monkeypatch):  # noqa: F811
    from studio import engines
    monkeypatch.setattr(engines, "free_gb", lambda: 1.6)
    monkeypatch.setattr(engines, "_pressure_normal", lambda: True)
    assert engines.enough_memory("chatterbox")[0]                     # needs 1.5 GB when pressure is normal
    monkeypatch.setattr(engines, "_pressure_normal", lambda: False)
    assert not engines.enough_memory("chatterbox")[0]                 # 3 GB under pressure


def test_tools_find_an_ffmpeg_command(studio_home, monkeypatch, tmp_path):  # noqa: F811
    import os
    from studio import engines
    monkeypatch.setattr(engines, "TOOLS", tmp_path / "tools")
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    b = engines._tool_bin()
    assert (b / "ffmpeg").exists() and os.access(b / "ffmpeg", os.X_OK)
    assert any("setuptools" in p for p in engines.SPECS["chatterbox"]["pip"])   # perth needs pkg_resources


def test_chatterbox_float_wav_is_read(tmp_path):
    """torchaudio writes 32-bit float WAVs (format 3), which Python's wave module refuses: on the Mac
    that threw Chatterbox's narration away. Both float and 16-bit PCM files must read."""
    import struct
    from clipper import media
    from studio import narration
    x = (0.5 * np.sin(np.linspace(0, 200, 24000))).astype("<f4")
    for ext in (False, True):
        body = x.tobytes()
        if ext:
            fmt = struct.pack("<HHIIHHHHIH14s", 0xFFFE, 1, 24000, 96000, 4, 32, 22, 32, 4, 3, b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x008\x9bq")
        else:
            fmt = struct.pack("<HHIIHH", 3, 1, 24000, 96000, 4, 32)
        data = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(body)) + body
        p = tmp_path / f"f{ext}.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(data)) + data)
        y, rate = narration.read_any_wav(p)
        assert rate == 24000 and y.shape == (24000, 1) and abs(float(y[100, 0]) - float(x[100])) < 1e-6
    media.write_wav(tmp_path / "pcm.wav", x, 24000)
    y, rate = narration.read_any_wav(tmp_path / "pcm.wav")
    assert rate == 24000 and abs(float(y[100, 0]) - float(x[100])) < 1e-3
    src = (narration.ROOT / "studio" / "workers" / "chatterbox_worker.py").read_text()
    assert "torchaudio.save" not in src and "setsampwidth(2)" in src


def test_music_is_skipped_clearly_when_windows_memory_is_short(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import engines
    py = tmp_path / "py"
    py.write_text("")
    monkeypatch.setattr(engines, "python", lambda tool: py)
    monkeypatch.setattr(engines, "nvidia", lambda: {"name": "RTX 2060", "vram_gb": 6})
    monkeypatch.setattr(engines, "free_gb", lambda: 9.0)
    monkeypatch.setattr(engines, "commit_free_gb", lambda: 7.0)
    with pytest.raises(engines.EngineError, match="page file"):
        engines.run("acestep", "acestep_worker", {})


def test_tool_temp_files_go_to_the_big_drive(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import engines
    monkeypatch.setattr(engines, "_big_drive", lambda: tmp_path / "D")
    env = engines._temp_env({})
    assert env["TEMP"] == env["TMP"] == str(tmp_path / "D" / "temp") and (tmp_path / "D" / "temp").is_dir()
    monkeypatch.setattr(engines, "_big_drive", lambda: None)
    assert engines.temp_dir() is None and engines._temp_env({}) == {}
