"""Ears: wakes on a double clap or "Hey Jarvis", then listens for commands."""
import json
import re
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import speech_recognition as sr

import display

RATE = 16000
BLOCK = 640            # 40 ms
WAKE_CHUNK = 1280      # 80 ms, what the wake word model expects
CONFIG = Path(__file__).with_name("config.json")


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


# ---------- "Hey Jarvis" wake word (openWakeWord, runs offline) ----------

_model = None


def wake_model():
    """Returns the wake word model, or None if it's switched off or not installed."""
    global _model
    if _model is False:
        return None
    if _model is None:
        if not _cfg().get("wake_word", True):
            _model = False
            return None
        try:
            from openwakeword.model import Model
            _model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            print("(\"Hey Jarvis\" wake word is on)")
        except Exception as e:
            print(f"(wake word off, clap to wake: {e})")
            _model = False
            return None
    return _model


class Detector:
    """Feeds microphone blocks in; says when it hears a double clap or the wake word."""

    def __init__(self, clap_threshold, use_wake_word=True):
        self.threshold = clap_threshold
        self.claps = []
        self.last_peak = 0.0
        self.buffer = np.zeros(0, dtype=np.int16)
        self.model = wake_model() if use_wake_word else None
        self.wake_threshold = float(_cfg().get("wake_word_threshold", 0.5))
        if self.model:
            self.model.reset()

    def feed(self, block):
        """block: int16 samples. Returns 'clap', 'wake' or None."""
        peak = float(np.max(np.abs(block))) / 32768.0
        now = time.time()
        if peak > self.threshold and now - self.last_peak > 0.12:
            self.last_peak = now
            self.claps = [c for c in self.claps if now - c < 0.8] + [now]
            if len(self.claps) >= 2 and self.claps[-1] - self.claps[-2] >= 0.15:
                self.claps = []
                return "clap"
        if self.model:
            self.buffer = np.concatenate([self.buffer, block])
            while len(self.buffer) >= WAKE_CHUNK:
                chunk, self.buffer = self.buffer[:WAKE_CHUNK], self.buffer[WAKE_CHUNK:]
                scores = self.model.predict(chunk)
                if scores and max(scores.values()) >= self.wake_threshold:
                    self.model.reset()
                    self.buffer = np.zeros(0, dtype=np.int16)
                    return "wake"
        return None


def _muted():
    try:
        import mute
        return mute.MUTED.is_set()
    except Exception:
        return False


class MicStalled(Exception):
    """The mic stopped sending sound (on Windows: busy, asleep or unplugged)."""


_stall = {"told": False}


def read_block(stream, timeout=3.0):
    """The next ~40 ms of sound, without ever waiting forever: a plain stream.read() blocks for
    good when Windows stops delivering mic audio, which froze Jarvis. Raises MicStalled."""
    deadline = time.time() + timeout
    while True:
        try:
            if stream.read_available >= BLOCK:
                break
        except Exception:
            break                       # can't tell: just read
        if time.time() > deadline:
            if not _stall["told"]:
                _stall["told"] = True
                print("(the mic stopped sending sound; I'll keep reopening it)")
            raise MicStalled()
        time.sleep(0.005)
    block, _ = stream.read(BLOCK)
    if _stall["told"]:
        _stall["told"] = False
        print("(the mic is sending sound again)")
    return block


def wait_for_wake(clap_threshold=0.35, paused=None):
    """Blocks until a double clap ('clap') or "Hey Jarvis" ('wake').
    Returns None if paused() becomes true. If the mic stalls, it's reopened (with a pause)."""
    det = Detector(clap_threshold)
    while True:
        try:
            with sd.InputStream(samplerate=RATE, channels=1, blocksize=BLOCK, dtype="int16") as stream:
                while True:
                    if paused and paused():
                        return None
                    block = read_block(stream)
                    hit = det.feed(block[:, 0])
                    if hit:
                        return hit
        except MicStalled:
            for _ in range(20):         # wait 2 s before reopening, still honouring pause
                if paused and paused():
                    return None
                time.sleep(0.1)


def wait_for_double_clap(threshold=0.35):
    return wait_for_wake(threshold)


# ---------- recording a command ----------

_room = {"noise": 150.0}      # how loud the room was last time (used when carrying on mid-sentence)


def record_phrase(start_timeout=8, max_seconds=15, silence_seconds=0.9, preroll=None):
    """Wait up to start_timeout for speech, then record until a pause.
    Keeps half a second from before speech began so first words aren't clipped.
    preroll: audio of someone who's already talking (they talked over Jarvis), to carry on from.
    Returns int16 audio or None if nobody spoke."""
    pre_roll = int(0.5 * RATE / BLOCK)
    history, frames = [], []
    started, quiet, voiced = False, 0.0, 0.0
    noise_levels = []
    if preroll is not None and len(preroll):
        frames, started, voiced = [preroll.astype(np.int16)], True, 0.3
        noise_levels = [_room["noise"]] * 8
    if preroll is None:
        try:
            import echo
            wait = echo.gap()             # let his own voice die away first
        except Exception:
            wait = 0.0
        if wait:
            time.sleep(wait)
    with sd.InputStream(samplerate=RATE, channels=1, blocksize=BLOCK, dtype="int16") as stream:
        t0 = time.time()
        while True:
            if _muted():                       # muted mid-sentence: throw away what he heard
                frames, voiced = [], 0.0
                break
            try:
                block = read_block(stream)
            except MicStalled:
                break                          # the mic stalled: treat it as nothing heard
            block = block[:, 0].copy()
            level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            display.update(level=min(level / 6000, 1.0))
            if len(noise_levels) < 8:              # first ~0.3 s: measure the room
                noise_levels.append(level)
                history.append(block)
                continue
            noise = float(np.median(noise_levels))
            if not started:
                _room["noise"] = noise
            speaking = level > max(noise * 2.5, 300)
            if not started:
                history = (history + [block])[-pre_roll:]
                if speaking:
                    started = True
                    frames = list(history)
                elif time.time() - t0 > start_timeout:
                    break
                else:
                    noise_levels = (noise_levels + [level])[-30:]
                continue
            frames.append(block)
            if speaking:
                quiet = 0.0
                voiced += BLOCK / RATE
            else:
                quiet += BLOCK / RATE
            if quiet > silence_seconds or len(frames) * BLOCK / RATE > max_seconds:
                break
    display.update(level=0.0)
    if not frames or voiced < 0.2:
        return None
    return np.concatenate(frames)


# ---------- speech to text ----------

_whisper = None
_whisper_lock = __import__("threading").Lock()
JUNK = {"", "you", "thank you", "thank you.", "thanks for watching!", "bye.", ".", "uh", "um"}


LAST_AUDIO = {"audio": None}     # the last thing heard (hearing.py tests a bigger model on it)
HEARD = {"text": "", "confidence": 0.0, "unsure": False}   # how sure he was of the last thing heard


def whisper_model():
    """Loads the local Whisper model once (faster-whisper, or the bigger one hearing.py set up).
    None if not installed."""
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            if _cfg().get("speech_engine", "whisper") != "whisper":
                _whisper = False
            else:
                try:
                    import hearing
                    _whisper = hearing.load()
                    if _whisper:
                        print(f"(speech recognition: Whisper {_whisper.name}, on this computer)")
                except Exception as e:
                    print(f"(better hearing unavailable: {e})")
                    _whisper = None
                if not _whisper:
                    try:
                        from faster_whisper import WhisperModel
                        name = _cfg().get("whisper_model", "small.en")
                        name = "small.en" if name in ("auto", "") else name
                        _whisper = WhisperModel(name, device="cpu", compute_type="int8")
                        print(f"(speech recognition: Whisper {name}, on this computer)")
                    except Exception as e:
                        print(f"(Whisper unavailable, using Google speech: {e})")
                        _whisper = False
        return _whisper or None


def use_model(model, name):
    """Switch speech models while running (None goes back to the usual one)."""
    global _whisper
    with _whisper_lock:
        _whisper = model if model else None
    if model:
        print(f"(speech recognition: switched to Whisper {name})")
    else:
        whisper_model()


def _preload_speaker():
    try:
        import speaker
        if speaker.enrolled():
            speaker.model()
            print(f"(group chat mode: {'on' if speaker.group_mode() else 'off'}, I know {speaker.owner_name()}'s voice)")
    except Exception as e:
        print(f"(voice recognition unavailable: {e})")


def preload():
    """Load models in the background so the first command is quick."""
    __import__("threading").Thread(target=whisper_model, daemon=True).start()
    __import__("threading").Thread(target=_preload_speaker, daemon=True).start()


def _vocabulary():
    words = ["Jarvis", "Spotify", "YouTube", "Google", "remind me", "timer", "Shortcuts"]
    try:
        import hands
        words += hands.allowed_apps()
    except Exception:
        pass
    words += _cfg().get("extra_words", [])
    return "Commands for Jarvis, a voice assistant: " + ", ".join(dict.fromkeys(words)) + "."


def transcribe(audio):
    HEARD.update(text="", confidence=0.0, unsure=False)
    model = whisper_model()
    if model:
        segments, _ = model.transcribe(
            audio.astype(np.float32) / 32768.0, language="en", beam_size=5,
            initial_prompt=_vocabulary(), condition_on_previous_text=False,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400})
        kept = [seg for seg in segments if seg.no_speech_prob < 0.6]
        text = " ".join(seg.text for seg in kept).strip()
        if text.lower().strip(" .!?") in JUNK:
            return ""
        # How sure Whisper was: the average log-probability per token, weighted by length.
        weight = sum(max(len(seg.text), 1) for seg in kept) or 1
        conf = sum(getattr(seg, "avg_logprob", 0.0) * max(len(seg.text), 1) for seg in kept) / weight
        HEARD.update(text=text, confidence=round(conf, 2),
                     unsure=bool(text) and conf < float(_cfg().get("unsure_below", -0.8)))
        if HEARD["unsure"]:
            print(f"(not sure I heard that right: confidence {conf:.2f})")
        return text
    data = sr.AudioData(audio.tobytes(), RATE, 2)
    try:
        return _recognizer.recognize_google(data, language="en-GB")
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        print(f"(speech service error: {e})")
        return ""


_recognizer = sr.Recognizer()


SPEAKER = {"name": None, "owner": True, "score": 1.0}   # who said the last thing (group chat mode)


def _identify(audio):
    try:
        import speaker
        SPEAKER.update(speaker.identify(audio))
    except Exception as e:
        print(f"(voice check failed: {e})")      # leaves the safe guess made in listen()


def _strip_echo(text, own):
    """Drop his own last words from the start of what was heard after someone talked over him."""
    mine = set(re.findall(r"[a-z']+", own.lower()))
    words = text.split()
    n = 0
    while n < min(len(words), 6) and re.sub(r"[^a-z']", "", words[n].lower()) in mine:
        n += 1
    return " ".join(words[n:]) if n < len(words) else text


def listen(max_seconds=8, carry_on=None):
    """Returns what was said as text, or '' if nothing understood.
    Also works out who said it (ears.SPEAKER) when group chat mode is set up.
    carry_on: (audio, his words) from voice.take_barge(), when someone talked over him."""
    deadline = time.time() + max_seconds
    for _ in range(3):
        text = _listen_once(max_seconds, carry_on)
        if text != _ECHO or time.time() > deadline - 1:
            return "" if text == _ECHO else text
        max_seconds, carry_on = deadline - time.time(), None   # that was his own voice: keep listening
    return ""


_ECHO = "\x00echo"           # what _listen_once returns when it only heard Jarvis himself


def _listen_once(max_seconds, carry_on):
    preroll, own = carry_on if carry_on and carry_on[0] is not None else (None, "")
    audio = record_phrase(start_timeout=max_seconds, preroll=preroll)
    if audio is None:
        print("(no speech detected)")
        return ""
    display.update(status="thinking")
    LAST_AUDIO["audio"] = audio
    start = time.time()
    try:
        import speaker
        locked = speaker.group_mode()
    except Exception:
        locked = False
    SPEAKER.update(name=None, owner=not locked, score=0.0)   # if the check is slow, assume a guest
    who = __import__("threading").Thread(target=_identify, args=(audio,), daemon=True)
    who.start()                                   # runs alongside speech-to-text
    text = transcribe(audio)
    who.join(5)
    if own and text:
        text = _strip_echo(text, own)
    elif text:
        try:
            import echo
            cleaned = echo.clean(text)    # was that just his own voice coming back?
            if text and not cleaned:
                print(f"(heard {len(audio) / RATE:.1f}s, only my own voice: {text!r})")
                return _ECHO
            text = cleaned
        except Exception as e:
            print(f"(echo check failed: {e})")
    print(f"(heard {len(audio) / RATE:.1f}s, transcribed in {time.time() - start:.1f}s: {text!r})")
    return text
