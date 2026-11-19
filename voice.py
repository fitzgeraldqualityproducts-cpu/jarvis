"""Voice: how Jarvis speaks.

Engines, best first (set "voice_engine" in config.json, or leave on "auto"):
  elevenlabs  most lifelike; needs an ElevenLabs API key (setup_voice.py asks)
  kokoro      very natural British voice that runs on this computer, free and offline
  edge        Microsoft's online voice (the original)
  say         built-in macOS (or Windows) voice, last resort

Long replies are spoken a phrase at a time, so he starts talking sooner. Answers from the brain
are spoken while they're still being written (say_stream), so he starts within a second or two.
The Kokoro voice is made in memory (on Windows it plays straight from there), the microphone that
listens for "stop" stays open the whole time instead of reopening for every sentence, and the
next phrase is made while he's still saying the current one, so there are no gaps.
Saying "stop", "Jarvis" or "Hey Jarvis", or clapping twice, while he talks cuts him off
(set "interrupt": false in config.json to turn that off, "interrupt_words": false for just claps
and "Hey Jarvis"). In a conversation you can also just start talking over him: he stops and
listens to what you're saying ("barge_in": false turns that off)."""
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
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
MODELS = HERE / "models"
_speaking = threading.Lock()
QUIET = set()                   # threads running a skill for the brain: they mustn't talk over his reply
SAID = []                       # everything he's said lately (jarvis.py uses it to notice when he couldn't help)
CONVERSING = threading.Event()  # set by jarvis.py during a spoken conversation: talking over him is allowed then
_barge = {}                     # what was heard when someone talked over him, for ears.listen to carry on from
LAST_START = {}                 # how long the last reply took to start playing (for the self-check)
_mirrors = []                   # (wanted, send): other places his voice also goes, e.g. a Discord call


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


_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")


_PAUSE = re.compile(r"[,;:]\s+|\s+(?:-|–|—)\s+")


def _chunks(pieces):
    """Turns text arriving in pieces into speakable chunks. The first comes out as soon as
    there's a short phrase (up to the first comma or full stop), so he can start talking
    straight away; the next is short too (so it's ready before the first ends), then the
    rest come a sentence or so at a time."""
    buf, need, n = "", 12, 0
    for piece in pieces:
        buf += piece
        while True:
            cut = next((m.end() for m in _END.finditer(buf) if len(buf[:m.start()].strip()) >= need), None)
            if n < 2:                                 # the first two phrases: a comma is enough
                comma = next((m.end() for m in _PAUSE.finditer(buf)
                              if len(buf[:m.start()].strip()) >= (10, 25)[n]), None)
                if comma is not None and (cut is None or comma < cut):
                    cut = comma
            if cut is None and len(buf) > 160:        # a long sentence with no full stop yet
                i = buf.rfind(", ", 60)
                cut = i + 2 if i > 0 else None
            if cut is None:
                break
            chunk, buf = buf[:cut].strip(), buf[cut:]
            if chunk:
                yield chunk
                need, n = 40, n + 1
    if buf.strip():
        yield buf.strip()


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


class Clip:
    """Speech kept in memory (float samples, -1..1, mono) so it can play without a file."""

    def __init__(self, samples, rate):
        import numpy as np
        self.samples = _trim(np.asarray(samples, dtype=np.float32).reshape(-1), rate)
        self.rate = int(rate)

    def seconds(self):
        return len(self.samples) / self.rate

    def to_file(self):
        import numpy as np
        pcm = (np.clip(self.samples, -1, 1) * 32767).astype(np.int16)
        path = _tmp(".wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(pcm.tobytes())
        return path


def _trim(samples, rate):
    """Cuts long silence off the ends (keeps a natural little pause)."""
    import numpy as np
    loud = np.flatnonzero(np.abs(samples) > 0.01)
    if not len(loud):
        return samples
    pad = int(0.06 * rate)
    return samples[max(0, loud[0] - pad):loud[-1] + int(0.12 * rate)]


def _kokoro_clip(text):
    cfg = _cfg()
    samples, rate = kokoro_model().create(text, voice=cfg.get("kokoro_voice", "bm_george"),
                                          speed=float(cfg.get("kokoro_speed", 1.0)), lang="en-gb")
    return Clip(samples, rate)


def _kokoro_tts(text):
    return _kokoro_clip(text).to_file()


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
IN_MEMORY = {"kokoro": _kokoro_clip}      # engines that can skip the sound file
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
    return _make(text, ENGINES)


LAST_ENGINE = ""


def _make(text, table):
    global LAST_ENGINE
    for name in _order():
        try:
            audio = (table.get(name) or ENGINES[name])(text)
            LAST_ENGINE = name
            return audio
        except Exception as e:
            print(f"(voice engine {name} failed: {str(e)[:120]})")
            if name != "elevenlabs" or "401" in str(e) or "no ElevenLabs key" in str(e):
                _broken.add(name)          # don't keep retrying an engine that can't work
    raise RuntimeError("no voice engine worked")


def synthesize_audio(text):
    """Like synthesize, but Kokoro's voice comes back as a Clip in memory (quicker)."""
    if not _cfg().get("fast_voice", True):
        return synthesize(text)
    return _make(text, IN_MEMORY)


def add_mirror(wanted, send):
    """Also send everything he says somewhere else: send(audio file path) is called for each
    phrase while wanted() is true (the file is deleted afterwards)."""
    _mirrors.append((wanted, send))


def _mirror(audio):
    for wanted, send in list(_mirrors):
        try:
            if not wanted():
                continue
            path = audio.to_file() if isinstance(audio, Clip) else audio
            try:
                send(path)
            finally:
                if path is not audio:
                    _remove(path)
        except Exception as e:
            print(f"(couldn't mirror my voice: {e})")


def _remove(audio):
    if isinstance(audio, str):
        try:
            os.remove(audio)
        except OSError:
            pass


def preload():
    """Warm up the local voice model (and the "stop" listener) in the background."""
    def go():
        if "kokoro" in _order()[:2]:
            try:
                kokoro_model().create("Ready.", voice=_cfg().get("kokoro_voice", "bm_george"), lang="en-gb")
                print("(voice: Kokoro ready)")         # (that first tiny line warms it up)
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
        self.own = own_text if callable(own_text) else (lambda: own_text)
        self.audio = np.zeros(0, dtype=np.int16)
        self.last = 0.0
        self.busy = threading.Event()
        self.heard = threading.Event()
        self.barge = CONVERSING.is_set() and _cfg().get("barge_in", True)
        self.strikes = 0
        self.barge_audio = None

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
            own = self.own().lower()
            words = set(STOP_WORDS.findall(text)) - set(STOP_WORDS.findall(own))
            if words:
                print(f"(heard {', '.join(sorted(words))!r} while speaking)")
                self.heard.set()
                return
            if self.barge:
                new = _new_words(text, own)
                self.strikes = self.strikes + 1 if new else 0
                if self.strikes >= 2:              # twice in a row: someone really is talking
                    print(f"(someone talked over me: {text.strip()!r})")
                    self.barge_audio = audio
                    self.heard.set()
        except Exception as e:
            print(f"(interrupt check failed: {str(e)[:120]})")
            self.model = None
        finally:
            self.busy.clear()


_FILLER = {"you", "thank", "thanks", "the", "a", "uh", "um", "hmm", "oh", "so", "and", "i", "it", "is", "bye",
           "watching", "for", "to", "of", "okay", "ok", "yeah", "music", "applause", "laughter"}


def _new_words(heard, own):
    """The words in what the mic heard that aren't his own (echo) or noise, if it looks like
    a person talking: at least two of them, and most of what was heard."""
    words = re.findall(r"[a-z']+", heard)
    mine = set(re.findall(r"[a-z']+", own))
    new = [w for w in words if w not in mine and w not in _FILLER]
    return new if len(new) >= 2 and len(new) >= 0.6 * len(words) else []


def clear_barge():
    _barge.clear()


def take_barge():
    """(audio, his own words) if someone just talked over him, else (None, ""). Clears it."""
    audio, own = _barge.pop("audio", None), _barge.pop("own", "")
    return audio, own


class _ClipPlayer:
    """Plays a Clip from memory through the speaker. Works like the afplay process:
    poll() is None while playing, wait() blocks until done, terminate() stops it."""

    def __init__(self, clip):
        import sounddevice as sd
        self._sd, self._data, self._pos = sd, clip.samples.reshape(-1, 1), 0
        self._done = threading.Event()
        device = None
        if sys.platform == "win32":
            import win
            device = win.output_device()          # "Choose Jarvis Speaker"
        self._stream = sd.OutputStream(samplerate=clip.rate, channels=1, dtype="float32", device=device,
                                       callback=self._fill, finished_callback=self._done.set)
        self._stream.start()

    def _fill(self, out, frames, _time, _status):
        chunk = self._data[self._pos:self._pos + frames]
        out[:len(chunk)] = chunk
        self._pos += frames
        if len(chunk) < frames:
            out[len(chunk):] = 0
            raise self._sd.CallbackStop

    def poll(self):
        return 0 if self._done.is_set() else None

    def wait(self):
        self._done.wait(max(5.0, len(self._data) / 8000))
        self.close()

    def terminate(self):
        try:
            self._stream.abort()
        except Exception:
            pass
        self._done.set()

    def close(self):
        try:
            self._stream.close()
        except Exception:
            pass


def _start(audio):
    """Starts playing a Clip or a sound file; returns something with poll/wait/terminate."""
    if isinstance(audio, Clip):
        # On a Mac, afplay follows whichever speaker is picked right now (e.g. AirPods put in
        # mid-run), so the clip goes through a quick file there.
        if sys.platform != "darwin" or _cfg().get("mac_memory_playback", False):
            try:
                return _ClipPlayer(audio)
            except Exception as e:
                print(f"(couldn't play from memory, using a file: {str(e)[:120]})")
        path = audio.to_file()
        player = _start(path)
        threading.Thread(target=lambda: (player.wait(), _remove(path)), daemon=True).start()
        return player
    if sys.platform == "win32":
        import win
        return win.play(audio)
    return subprocess.Popen(["afplay", audio])


class _Watch:
    """Listens to the microphone the whole time he's talking (opened once, not per phrase),
    for a double clap, "stop"/"Jarvis", or someone talking over him."""

    def __init__(self, own_text):
        import sounddevice as sd
        import ears
        self.ears = ears
        cfg = _cfg()
        self.det = ears.Detector(max(0.6, 1.5 * cfg.get("clap_threshold", 0.35)))   # louder than his own voice
        self.spotter = _Spotter(own_text, ears.RATE)
        self.stream = sd.InputStream(samplerate=ears.RATE, channels=1, blocksize=ears.BLOCK, dtype="int16")
        self.stream.start()

    def heard(self):
        """Reads the next bit of sound (about 40 ms); True if he should stop."""
        block, _ = self.stream.read(self.ears.BLOCK)
        return bool(self.det.feed(block[:, 0]) or self.spotter.feed(block[:, 0]))

    def close(self):
        try:
            self.stream.close()
        except Exception:
            pass


def _play(audio, interruptible, stop, own_text="", watch=None):
    """Plays one phrase; returns True if the user interrupted."""
    proc = _start(audio)
    if not interruptible or not _cfg().get("interrupt", True):
        proc.wait()
        return False
    try:
        if watch is None:
            watch = _Watch(own_text)
            own = True
        else:
            own = False
        try:
            while proc.poll() is None:
                if watch.heard():
                    proc.terminate()
                    stop.set()
                    if watch.spotter.barge_audio is not None:
                        _barge.update(audio=watch.spotter.audio.copy(), own=watch.spotter.own())
                    print("(interrupted)")
                    return True
        finally:
            if own:
                watch.close()
    except Exception as e:
        print(f"(couldn't listen for interruptions: {e})")
        proc.wait()
    finally:
        if isinstance(proc, _ClipPlayer):
            proc.close()
    return False


def speaking():
    """True while Jarvis is talking."""
    return _speaking.locked()


def say(text, interruptible=True):
    """Speak text. Returns True if the user interrupted.
    text can also be a brain answer that's still arriving (see say_stream)."""
    if threading.get_ident() in QUIET:
        print(f"(skill wanted to say: {text if isinstance(text, str) else '...'})")
        return False
    if not isinstance(text, str):
        return say_stream(text, interruptible)
    import display
    import expressive
    shown = expressive.strip(text)
    print(f"Jarvis: {shown}")
    display.log("jarvis", shown)
    return _speak(_chunks([_speakable(text)]), interruptible, shown)


def say_stream(pieces, interruptible=True):
    """Speak text as it's being written (e.g. brain.think_stream), starting with the first
    sentence. If it's slow to start (say, a web search), he says "one sec" first.
    Returns True if the user interrupted."""
    return _speak(_chunks(_hold_on(pieces, float(_cfg().get("hold_on_after", 3.5)))), interruptible, None,
                  source=pieces)


def _hold_on(pieces, after):
    q = queue.Queue()

    def feed():
        try:
            for piece in pieces:
                q.put(piece)
        except Exception as e:
            print(f"(brain stream failed: {e})")
        finally:
            q.put(None)
    threading.Thread(target=feed, daemon=True).start()
    waiting = after > 0
    while True:
        try:
            piece = q.get(timeout=after) if waiting else q.get()
        except queue.Empty:
            import personality
            waiting = False
            yield "\n" + personality.line("hold_on") + "\n"
            continue
        if piece is None:
            return
        if piece.strip():
            waiting = False
        yield piece


def _drain(files):
    """Deletes audio made after he was cut off."""
    while True:
        audio = files.get()
        if audio is None:
            return
        _remove(audio)


def _speak(chunks, interruptible, text, source=None):
    import display
    with _speaking:
        if _barge.get("audio") is not None and CONVERSING.is_set():
            return True                 # he was just talked over: let them finish instead of talking again
        files, stop = queue.Queue(), threading.Event()
        said = []
        began = time.time()

        def own():
            return text or " ".join(said)

        def produce():
            import expressive
            finished, style = False, "normal"
            try:
                for chunk in chunks:
                    if stop.is_set():
                        break
                    plain = expressive.strip(chunk)
                    said.append(plain)
                    audio = None
                    if (expressive.has_tags(chunk) or style != "normal") and expressive.enabled() and \
                            _cfg().get("fast_voice", True) and _order()[:1] == ["kokoro"]:
                        try:                       # whisper, laugh, hyped, sarcastic (expressive.py)
                            audio, style = expressive.render(chunk, style, sys.modules[__name__])
                        except Exception as e:
                            print(f"(expressive voice failed, plain voice instead: {e})")
                            audio = None
                        if audio is None and not plain:
                            continue
                    spoken = _speakable(plain)
                    if audio is None and not spoken:
                        continue
                    try:
                        if audio is None:
                            audio = synthesize_audio(spoken)
                    except Exception as e:
                        print(f"(couldn't speak: {e})")
                        break
                    if stop.is_set():
                        _remove(audio)
                        break
                    if _mirrors:
                        _mirror(audio)
                    files.put(audio)
                else:
                    finished = True
            except Exception as e:
                print(f"(couldn't keep talking: {e})")
            finally:
                if stop.is_set() or not finished:
                    cancel = getattr(source, "cancel", None)
                    if cancel:
                        cancel()                   # stop the brain too if he stopped talking early
                files.put(None)

        threading.Thread(target=produce, daemon=True).start()
        interrupted, started, watch = False, False, None
        try:
            while True:
                audio = files.get()
                if audio is None:
                    break
                try:
                    if not started:
                        started = True
                        LAST_START.update(seconds=round(time.time() - began, 2), at=time.time())
                        display.update(status="speaking")
                        if interruptible and _cfg().get("interrupt", True):
                            try:
                                watch = _Watch(own)
                            except Exception as e:
                                print(f"(couldn't listen for interruptions: {e})")
                    if not interrupted:
                        interrupted = _play(audio, interruptible and watch is not None, stop, own, watch)
                finally:
                    _remove(audio)
                if interrupted:
                    cancel = getattr(source, "cancel", None)
                    if cancel:
                        cancel()
                    threading.Thread(target=_drain, args=(files,), daemon=True).start()
                    break
        finally:
            if watch:
                watch.close()
        full = own()
        if text is None:
            print(f"Jarvis: {full}" + (" (cut off)" if interrupted else ""))
            if full:
                display.log("jarvis", full)
        SAID.append(full)
        del SAID[:-20]
        display.update(status="listening")
        return interrupted
