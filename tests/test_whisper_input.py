"""Whisper gets the audio as samples, so it never opens files itself with PyAV (older PyAV
versions don't accept the options newer faster-whisper passes when it does)."""
import sys
import types

import numpy as np


def test_whisper_gets_samples_not_a_path(tmp_path, monkeypatch):
    from clipper import media, transcribe
    seen = {}

    class Word:
        def __init__(self, w, s, e):
            self.word, self.start, self.end, self.probability = w, s, e, 0.9

    class Seg:
        start, end, avg_logprob, no_speech_prob = 0.0, 1.0, -0.2, 0.01
        words = [Word(" hello", 0.1, 0.4), Word(" there.", 0.5, 0.9)]

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kw):
            seen["audio"] = audio
            return iter([Seg()]), types.SimpleNamespace(language="en", duration=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(transcribe, "_models", {})
    wav = tmp_path / "a.wav"
    media.write_wav(wav, np.zeros((16000, 1), np.float32), 16000)
    out = transcribe._whisper_words(wav, 1.0, lambda *a: None, "", "en")
    assert isinstance(seen["audio"], np.ndarray) and seen["audio"].dtype == np.float32 and seen["audio"].ndim == 1
    assert len(seen["audio"]) == 16000
    words = out[0] if isinstance(out, tuple) else out
    assert "hello" in str(words)
