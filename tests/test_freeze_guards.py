"""Jarvis must never freeze waiting on a stalled mic or speaker (the PC froze this way on 2026-11-24)."""
import sys
import threading
import time
import types

import numpy as np
import pytest


@pytest.fixture
def ears(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=None))
    monkeypatch.setitem(sys.modules, "speech_recognition", types.SimpleNamespace(Recognizer=lambda: None, UnknownValueError=Exception, RequestError=Exception))
    sys.modules.pop("ears", None)
    import ears as e
    yield e
    sys.modules.pop("ears", None)


class DeadStream:
    """A mic stream that opened fine but never sends any sound."""
    read_available = 0

    def read(self, n):
        raise AssertionError("a blocking read on a dead mic would hang forever")


class LiveStream:
    read_available = 10_000

    def read(self, n):
        return np.zeros((n, 1), np.int16), False


def test_read_block_gives_up_on_a_dead_mic(ears):
    t = time.time()
    with pytest.raises(ears.MicStalled):
        ears.read_block(DeadStream(), timeout=0.2)
    assert time.time() - t < 2


def test_read_block_reads_a_live_mic(ears):
    assert ears.read_block(LiveStream()).shape == (ears.BLOCK, 1)


class StuckPlayer:
    """A phrase whose sound device stopped: it never finishes on its own."""
    seconds = 0.1

    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def wait(self):
        return 0

    def terminate(self):
        self.stopped = True


def test_stalled_speaker_cannot_hang_speech(monkeypatch):
    import voice
    monkeypatch.setattr(voice, "_deadline", lambda proc: time.time() + 0.3)
    p, stop = StuckPlayer(), threading.Event()
    t = time.time()
    assert voice._wait(p, stop) is True
    assert p.stopped and stop.is_set() and time.time() - t < 3


def test_watch_stops_listening_when_mic_stalls(monkeypatch):
    import voice
    w = voice._Watch.__new__(voice._Watch)
    w.stream, w.ears = DeadStream(), types.SimpleNamespace(BLOCK=640)
    w.last, w.dead = time.time() - voice.MIC_STALL_SECONDS - 1, False
    assert w.heard() is False and w.dead
    assert w.heard() is False           # and keeps not blocking
