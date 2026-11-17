"""Voice: how Jarvis speaks.

Engines, best first (set "voice_engine" in config.json, or leave on "auto"):
  elevenlabs  most lifelike; needs an ElevenLabs API key (setup_voice.py asks)
  kokoro      very natural British voice that runs on this computer, free and offline
  edge        Microsoft's online voice (the original)
  say         built-in macOS (or Windows) voice, last resort

Long replies are spoken sentence by sentence, so he starts talking sooner.
Saying "stop", "Jarvis" or "Hey Jarvis", or clapping twice, while he talks cuts him off
(set "interrupt": false in config.json to turn that off, "interrupt_words": false for just claps
and "Hey Jarvis")."""
import asyncio
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
MODELS = HERE / "models"
_speaking = threading.Lock()


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


# ---------------- text clean-up so it sounds natural ----------------

def _speakable(text):
    t = re.sub(r"[*_#`>]+", "", text)                      # stray markdown
    t = re.sub(r"(\d)\s*°(?:\s?F\b)?", r"\1 degrees", t)
    t = t.replace("&", " and ").replace(" w/ ", " with ")
    t = re.sub(r"\be\.g\.", "for example", t)
    t = re.sub(r"\bi\.e\.", "that is", t)
    return re.sub(r"\s+", " ", t).strip()


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    out, buf = [], ""
    for p in parts:                       # keep very short sentences together
        buf = f"{buf} {p}".strip()
        if len(buf) > 40:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out or [text]


def _tmp(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


# ---------------- engines: each turns text into an audio file ----------------

def _elevenlabs(text):
    cfg = _cfg()
    key = cfg.get("elevenlabs_api_key", "").strip()
    if not key:
        raise RuntimeError("no ElevenLabs key")
    voice_id = cfg.get("elevenlabs_voice_id", "JBFqnCBsd6RMkjVDRZzb")      # "George", warm British
    body = json.dumps({
        "text": text,
        "model_id": cfg.get("elevenlabs_model", "eleven_multilingual_v2"),
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True},
    }).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        data=body, headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    path = _tmp(".mp3")
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


_kokoro = None
_kokoro_lock = threading.Lock()


def kokoro_model():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
        return _kokoro


def _kokoro_tts(text):
    import numpy as np
    cfg = _cfg()
    samples, rate = kokoro_model().create(text, voice=cfg.get("kokoro_voice", "bm_george"),
                                          speed=float(cfg.get("kokoro_speed", 1.0)), lang="en-gb")
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    path = _tmp(".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


async def _edge_async(text, path):
    import edge_tts
    await edge_tts.Communicate(text, _cfg().get("voice", "en-GB-RyanNeural"), rate="-5%").save(path)


def _edge(text):
    path = _tmp(".mp3")
    asyncio.run(_edge_async(text, path))
    return path


def _say_cmd(text):
    if sys.platform == "win32":
        import win
        path = _tmp(".wav")
        win.speak_to_file(text, path)
        return path
    path = _tmp(".aiff")
    subprocess.run(["say", "-v", "Daniel", "-o", path, text], check=True)
    return path


ENGINES = {"elevenlabs": _elevenlabs, "kokoro": _kokoro_tts, "edge": _edge, "say": _say_cmd}
_broken = set()


def _order():
    choice = _cfg().get("voice_engine", "auto")
    order = ["elevenlabs", "kokoro", "edge", "say"]
    if choice in ENGINES:
        order.remove(choice)
        order.insert(0, choice)
    return [e for e in order if e not in _broken]


def synthesize(text):
    """Returns an audio file path for text, trying engines in order."""
    for name in _order():
        try:
            return ENGINES[name](text)
        except Exception as e:
            print(f"(voice engine {name} failed: {str(e)[:120]})")
            if name != "elevenlabs" or "401" in str(e) or "no ElevenLabs key" in str(e):
                _broken.add(name)          # don't keep retrying an engine that can't work
    raise RuntimeError("no voice engine worked")


def preload():
    """Warm up the local voice model (and the "stop" listener) in the background."""
    def go():
        if "kokoro" in _order()[:2]:
            try:
                kokoro_model()
                print("(voice: Kokoro ready)")
            except Exception as e:
                print(f"(Kokoro voice unavailable: {e})")
                _broken.add("kokoro")
        if _cfg().get("interrupt", True) and _cfg().get("interrupt_words", True):
            spotter_model()
    threading.Thread(target=go, daemon=True).start()


# ---------------- playback with interruption ----------------

STOP_WORDS = re.compile(r"\b(stop|jarvis|shut up|be quiet|hold on|that's enough)\b")
_spot = None
_spot_lock = threading.Lock()


def spotter_model():
    """A tiny, fast speech model used only to hear "stop" or "Jarvis" while he talks.
    Falls back to the main Whisper model. None if speech recognition isn't installed."""
    global _spot
    with _spot_lock:
        if _spot is None:
            _spot = False
            if _cfg().get("interrupt_words", True):
                try:
                    from faster_whisper import WhisperModel
                    _spot = WhisperModel(_cfg().get("interrupt_model", "tiny.en"), device="cpu",
                                         compute_type="int8", cpu_threads=2)
                    print("(interrupt words on: say stop or Jarvis to cut me off)")
                except Exception as e:
                    print(f"(fast interrupt model unavailable, using the main one: {str(e)[:120]})")
                    try:
                        import ears
                        _spot = ears.whisper_model() or False
                    except Exception:
                        _spot = False
        return _spot or None


class _Spotter:
    """Keeps the last 1.6 seconds of microphone sound and, a few times a second, checks it
    in the background for "stop" or "Jarvis". Words Jarvis is saying himself are ignored,
    since the mic also hears his own voice."""

    WINDOW, EVERY = 1.6, 0.35

    def __init__(self, own_text, rate):
        import numpy as np
        self.np, self.rate = np, rate
        # Don't hold up his speech if the model is still loading in the background.
        self.model = (_spot or None) if _spot is not None else \
            (spotter_model() if _cfg().get("interrupt_words", True) and not _spot_lock.locked() else None)
        self.ignore = set(STOP_WORDS.findall(own_text.lower()))
        self.audio = np.zeros(0, dtype=np.int16)
        self.last = 0.0
        self.busy = threading.Event()
        self.heard = threading.Event()

    def feed(self, block):
        if not self.model:
            return False
        import time
        keep = int(self.WINDOW * self.rate)
        self.audio = self.np.concatenate([self.audio, block])[-keep:]
        now = time.time()
        if not self.busy.is_set() and now - self.last >= self.EVERY and len(self.audio) >= keep // 2:
            self.last = now
            self.busy.set()
            threading.Thread(target=self._check, args=(self.audio.copy(),), daemon=True).start()
        return self.heard.is_set()

    def _check(self, audio):
        try:
            segs, _ = self.model.transcribe(audio.astype(self.np.float32) / 32768.0, language="en", beam_size=1,
                                            condition_on_previous_text=False, without_timestamps=True)
            text = " ".join(s.text for s in segs if s.no_speech_prob < 0.6).lower()
            words = set(STOP_WORDS.findall(text)) - self.ignore
            if words:
                print(f"(heard {', '.join(sorted(words))!r} while speaking)")
                self.heard.set()
        except Exception as e:
            print(f"(interrupt check failed: {str(e)[:120]})")
            self.model = None
        finally:
            self.busy.clear()


def _play(path, interruptible, stop, own_text=""):
    """Plays one file; returns True if the user interrupted."""
    if sys.platform == "win32":
        import win
        proc = win.play(path)
    else:
        proc = subprocess.Popen(["afplay", path])
    if not interruptible or not _cfg().get("interrupt", True):
        proc.wait()
        return False
    try:
        import sounddevice as sd
        import ears
        cfg = _cfg()
        det = ears.Detector(max(0.6, 1.5 * cfg.get("clap_threshold", 0.35)))   # louder than his own voice
        spotter = _Spotter(own_text, ears.RATE)
        with sd.InputStream(samplerate=ears.RATE, channels=1, blocksize=ears.BLOCK, dtype="int16") as stream:
            while proc.poll() is None:
                block, _ = stream.read(ears.BLOCK)
                if det.feed(block[:, 0]) or spotter.feed(block[:, 0]):
                    proc.terminate()
                    stop.set()
                    print("(interrupted)")
                    return True
    except Exception as e:
        print(f"(couldn't listen for interruptions: {e})")
        proc.wait()
    return False


def speaking():
    """True while Jarvis is talking."""
    return _speaking.locked()


def say(text, interruptible=True):
    """Speak text. Returns True if the user interrupted."""
    import display
    with _speaking:
        print(f"Jarvis: {text}")
        display.log("jarvis", text)
        display.update(status="speaking")
        chunks = _sentences(_speakable(text))
        files, stop = queue.Queue(), threading.Event()

        def produce():
            for chunk in chunks:
                if stop.is_set():
                    break
                try:
                    files.put(synthesize(chunk))
                except Exception as e:
                    print(f"(couldn't speak: {e})")
                    break
            files.put(None)

        threading.Thread(target=produce, daemon=True).start()
        interrupted = False
        while True:
            path = files.get()
            if path is None:
                break
            try:
                if not interrupted:
                    interrupted = _play(path, interruptible, stop, text)
            finally:
                os.remove(path)
        display.update(status="listening")
        return interrupted
