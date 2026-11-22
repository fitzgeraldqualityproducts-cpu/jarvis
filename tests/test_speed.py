"""Speed-ups that must not change the result: transcripts on the Mac's graphics chip (same words and
times as the CPU path would give), and clip sections downloaded ahead in the background."""
import sys
import threading
import time
import types

import numpy as np


def _wav(tmp_path, seconds=20):
    from clipper import media
    p = tmp_path / "a.wav"
    media.write_wav(p, np.zeros(16000 * seconds, dtype=np.float32), 16000)
    return p


def test_joined_speech_maps_back_to_the_original_times():
    from clipper import transcribe
    audio = np.arange(16000 * 20, dtype=np.float32)
    joined, back = transcribe._joined(audio, [(1.0, 3.0), (10.0, 12.5)])
    assert len(joined) == 16000 * 4.5
    assert back(0.5) == 1.5 and back(2.0) == 10.0 and abs(back(3.0) - 11.0) < 1e-9
    assert transcribe._batches([(0, 300), (300, 500), (600, 900)], longest=600) == [[(0, 300), (300, 500)], [(600, 900)]]


def _fake_mlx(monkeypatch, fn):
    mod = types.ModuleType("mlx_whisper")
    mod.transcribe = fn
    monkeypatch.setitem(sys.modules, "mlx_whisper", mod)
    return mod


def test_graphics_chip_transcript_has_original_times(monkeypatch, tmp_path):
    from clipper import transcribe
    seen = {}

    def fake(audio, **kw):
        seen.update(kw, n=len(audio))
        return {"language": "en", "segments": [
            {"start": 0.2, "end": 3.4, "avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.2,
             "words": [{"word": " Hello", "start": 0.5, "end": 0.9, "probability": 0.95},
                       {"word": " there.", "start": 2.5, "end": 2.9, "probability": 0.9}]}]}
    _fake_mlx(monkeypatch, fake)
    monkeypatch.setattr(transcribe, "_speech_regions", lambda audio, rate=16000: [(1.0, 3.0), (10.0, 12.0)])
    words, segs, lang = transcribe._mlx_words(str(_wav(tmp_path)), 20.0, lambda f, d="": None, "Names: Connor.", "en")
    assert [(w["w"], w["s"], w["e"]) for w in words] == [("Hello", 1.5, 1.9), ("there.", 10.5, 10.9)]
    assert segs[0]["s"] == 1.2 and lang == "en"
    assert seen["path_or_hf_repo"] == "mlx-community/whisper-small.en-mlx" and seen["word_timestamps"]
    assert seen["initial_prompt"] == "Names: Connor." and not seen["condition_on_previous_text"]
    assert seen["n"] == 16000 * 4                      # only the speech is listened to


def test_engine_picks_the_graphics_chip_only_on_apple_silicon(monkeypatch):
    from clipper import settings, transcribe
    _fake_mlx(monkeypatch, lambda *a, **k: {})
    monkeypatch.setattr(transcribe.sys, "platform", "darwin")
    monkeypatch.setattr(transcribe.platform, "machine", lambda: "arm64")
    assert transcribe.engine() == "mlx"
    settings.set_global(gpu_transcribe=False)
    assert transcribe.engine() != "mlx"
    settings.set_global(gpu_transcribe=True)
    monkeypatch.setattr(transcribe.platform, "machine", lambda: "x86_64")
    assert transcribe.engine() != "mlx"
    assert transcribe.mlx_repo("turbo") == "mlx-community/whisper-large-v3-turbo"


def test_graphics_chip_trouble_falls_back_to_the_cpu(monkeypatch, tmp_path):
    from clipper import transcribe

    def broken(audio, **kw):
        raise RuntimeError("Metal out of memory")
    _fake_mlx(monkeypatch, broken)
    monkeypatch.setattr(transcribe, "_speech_regions", lambda audio, rate=16000: [(0.0, 5.0)])
    cpu = [{"w": "fine", "s": 1.0, "e": 1.4, "p": 0.9, "seg": 0}]
    monkeypatch.setattr(transcribe, "_whisper_words", lambda *a: (list(cpu), [{"s": 1, "e": 2, "lp": 0, "ns": 0, "cr": 1}], "en"))
    tr = transcribe.transcribe(str(_wav(tmp_path)), eng="mlx")
    assert tr["engine"] == "whisper" and [w["w"] for w in tr["words"]] == ["fine"]


def test_sections_download_ahead_once_each(monkeypatch, tmp_path):
    from clipper import acquire
    calls, gate = [], threading.Event()

    def fake(source, folder, s, e, key):
        calls.append(key)
        gate.wait(5)
        p = tmp_path / f"section_{key}.mp4"
        p.write_bytes(b"x")
        with acquire._lock:
            source.setdefault("sections", {})[key] = str(p)
        return str(p)
    monkeypatch.setattr(acquire, "_fetch", fake)
    src = {"mode": "sections", "audio": "a.wav", "sections": {}, "url": "u"}
    assert acquire.prefetch(src, tmp_path, [(100, 130), (300, 330), (500, 520)]) == 3
    assert acquire.prefetch(src, tmp_path, [(100, 130)]) == 0          # already on its way
    time.sleep(0.2)
    assert len(calls) == 2                                              # two at a time
    gate.set()
    path, off = acquire.section(src, tmp_path, 300, 330)
    assert path.endswith("section_2970_3330.mp4") and off == 297.0
    acquire.section(src, tmp_path, 100, 130)
    acquire.section(src, tmp_path, 500, 520)
    assert sorted(calls) == ["2970_3330", "4970_5230", "970_1330"]                 # never twice
    assert not acquire._pending
