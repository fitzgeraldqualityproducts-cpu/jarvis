"""Ears: wakes on a double clap or "Hey Jarvis", then listens for commands."""
import json
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


def wait_for_wake(clap_threshold=0.35, paused=None):
    """Blocks until a double clap ('clap') or "Hey Jarvis" ('wake').
    Returns None if paused() becomes true."""
    det = Detector(clap_threshold)
    with sd.InputStream(samplerate=RATE, channels=1, blocksize=BLOCK, dtype="int16") as stream:
        while True:
            if paused and paused():
                return None
            block, _ = stream.read(BLOCK)
            hit = det.feed(block[:, 0])
            if hit:
                return hit


def wait_for_double_clap(threshold=0.35):
    return wait_for_wake(threshold)


# ---------- recording a command ----------

def record_phrase(start_timeout=8, max_seconds=15, silence_seconds=0.9):
    """Wait up to start_timeout for speech, then record until a pause.
    Keeps half a second from before speech began so first words aren't clipped.
    Returns int16 audio or None if nobody spoke."""
    pre_roll = int(0.5 * RATE / BLOCK)
    history, frames = [], []
    started, quiet, voiced = False, 0.0, 0.0
    noise_levels = []
    with sd.InputStream(samplerate=RATE, channels=1, blocksize=BLOCK, dtype="int16") as stream:
        t0 = time.time()
        while True:
            block, _ = stream.read(BLOCK)
            block = block[:, 0].copy()
            level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            display.update(level=min(level / 6000, 1.0))
            if len(noise_levels) < 8:              # first ~0.3 s: measure the room
                noise_levels.append(level)
                history.append(block)
                continue
            noise = float(np.median(noise_levels))
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


def whisper_model():
    """Loads the local Whisper model once (faster-whisper). None if not installed."""
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            if _cfg().get("speech_engine", "whisper") != "whisper":
                _whisper = False
            else:
                try:
                    from faster_whisper import WhisperModel
                    name = _cfg().get("whisper_model", "small.en")
                    _whisper = WhisperModel(name, device="cpu", compute_type="int8")
                    print(f"(speech recognition: Whisper {name}, on this computer)")
                except Exception as e:
                    print(f"(Whisper unavailable, using Google speech: {e})")
                    _whisper = False
        return _whisper or None


def preload():
    """Load models in the background so the first command is quick."""
    __import__("threading").Thread(target=whisper_model, daemon=True).start()


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
    model = whisper_model()
    if model:
        segments, _ = model.transcribe(
            audio.astype(np.float32) / 32768.0, language="en", beam_size=5,
            initial_prompt=_vocabulary(), condition_on_previous_text=False,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400})
        text = " ".join(seg.text for seg in segments if seg.no_speech_prob < 0.6).strip()
        if text.lower().strip(" .!?") in JUNK:
            return ""
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


def listen(max_seconds=8):
    """Returns what was said as text, or '' if nothing understood."""
    audio = record_phrase(start_timeout=max_seconds)
    if audio is None:
        print("(no speech detected)")
        return ""
    display.update(status="thinking")
    start = time.time()
    text = transcribe(audio)
    print(f"(heard {len(audio) / RATE:.1f}s, transcribed in {time.time() - start:.1f}s: {text!r})")
    return text
