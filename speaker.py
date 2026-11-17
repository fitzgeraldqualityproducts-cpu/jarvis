"""Speaker: tells Connor's voice apart from his friends' (group chat mode).

Everything runs on this computer. A small voice model turns a few seconds of speech
into a "voiceprint" (a list of numbers). Teach Jarvis your voice once with the
"Teach Jarvis My Voice" file, and after that every command is compared with it.

Saved in voiceprints.json next to this file:
  {"owner": "Connor", "threshold": 0.55, "people": {"Connor": {"vec": [...], "n": 6}, ...}}
"""
import json
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PRINTS = HERE / "voiceprints.json"
CONFIG = HERE / "config.json"
MODEL = HERE / "models" / "speaker-wespeaker-resnet34.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34_LM.onnx")
RATE = 16000
DEFAULT_THRESHOLD = 0.55

_lock = threading.Lock()
_extractor = None
LAST = {"name": None, "owner": True, "score": 0.0, "t": 0.0}   # who spoke last, for jarvis.py


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def load():
    try:
        return json.loads(PRINTS.read_text())
    except Exception:
        return {"owner": None, "people": {}}


def save(data):
    PRINTS.write_text(json.dumps(data, indent=1))


def owner_name():
    return load().get("owner") or "Connor"


def enrolled():
    data = load()
    return bool(data.get("owner") and data["owner"] in data.get("people", {}))


def group_mode():
    """True when Jarvis should check who's talking (after the voice setup, unless switched off)."""
    return enrolled() and _cfg().get("group_mode", True) and model() is not None


# ---------- the voice model ----------

def download_model(show=print):
    if MODEL.exists() and MODEL.stat().st_size > 20_000_000:
        return True
    MODEL.parent.mkdir(exist_ok=True)
    tmp = MODEL.with_suffix(".part")
    show("Downloading the voice recognition model (about 26 MB)...")
    try:
        try:
            import certifi
            import ssl
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "jarvis-assistant"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        tmp.replace(MODEL)
        return True
    except Exception as e:
        show(f"Couldn't download the voice model: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def model():
    """Returns the speaker model, or None if it isn't installed."""
    global _extractor
    with _lock:
        if _extractor is None:
            try:
                import sherpa_onnx
                if not MODEL.exists():
                    raise FileNotFoundError("run Teach Jarvis My Voice first")
                cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=1)
                _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            except Exception as e:
                print(f"(voice recognition off: {e})")
                _extractor = False
        return _extractor or None


def _speech_only(audio):
    """Drops the quiet parts so the voiceprint is made from speech, not room noise."""
    a = audio.astype(np.float32)
    block = 320                                    # 20 ms
    n = len(a) // block
    if n < 5:
        return a
    rms = np.sqrt(np.mean(a[:n * block].reshape(n, block) ** 2, axis=1))
    floor = max(float(np.percentile(rms, 20)) * 2.0, 250.0)
    keep = rms > floor
    keep = keep | np.roll(keep, 1) | np.roll(keep, -1)   # keep word edges
    voiced = a[:n * block].reshape(n, block)[keep].reshape(-1)
    return voiced if len(voiced) >= RATE // 2 else a


def embed(audio):
    """int16 (or float) audio at 16 kHz -> unit-length voiceprint, or None."""
    ex = model()
    if ex is None or audio is None or len(audio) < RATE // 3:
        return None
    a = _speech_only(audio)
    if a.dtype != np.float32 or np.max(np.abs(a)) > 1.5:
        a = a / 32768.0
    with _lock:
        s = ex.create_stream()
        s.accept_waveform(RATE, a.astype(np.float32))
        s.input_finished()
        if not ex.is_ready(s):
            return None
        v = np.array(ex.compute(s), dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm else None


# ---------- enrolling and recognising ----------

def enroll(name, clips, owner=False):
    """Make a voiceprint for `name` from a few recordings. Returns (ok, message)."""
    vecs = [v for v in (embed(c) for c in clips) if v is not None]
    if len(vecs) < 2:
        return False, "I couldn't hear enough speech. Try again a bit closer to the microphone."
    mean = np.mean(vecs, axis=0)
    mean /= np.linalg.norm(mean)
    data = load()
    data.setdefault("people", {})[name] = {"vec": [round(float(x), 5) for x in mean], "n": len(vecs),
                                           "added": time.strftime("%Y-%m-%d")}
    if owner:
        data["owner"] = name
        # How alike his own recordings are tells us how strict to be.
        loo = []
        for i, v in enumerate(vecs):
            rest = np.mean([w for j, w in enumerate(vecs) if j != i], axis=0)
            loo.append(float(v @ (rest / np.linalg.norm(rest))))
        data["threshold"] = round(min(max(min(loo) - 0.12, 0.40), 0.62), 3)
        data["self_match"] = round(float(np.mean(loo)), 3)
    save(data)
    return True, f"Got it. I'll know {name}'s voice now."


def remove(name):
    data = load()
    if name in data.get("people", {}) and name != data.get("owner"):
        del data["people"][name]
        save(data)
        return True
    return False


def friends():
    data = load()
    return [n for n in data.get("people", {}) if n != data.get("owner")]


def identify(audio):
    """Who said this? Returns {"name", "owner", "score"}. When voice recognition isn't set up
    everyone counts as the owner, so Jarvis works just like before."""
    if not group_mode():
        return {"name": None, "owner": True, "score": 1.0}
    v = embed(audio)
    data = load()
    owner = data["owner"]
    if v is None:                               # too short to tell: trust the last speaker briefly
        recent = time.time() - LAST["t"] < 45
        return {"name": LAST["name"] if recent else None, "owner": bool(recent and LAST["owner"]), "score": 0.0}
    scores = {n: float(v @ np.array(p["vec"], dtype=np.float32)) for n, p in data["people"].items()}
    best = max(scores, key=scores.get)
    threshold = float(_cfg().get("voice_threshold", data.get("threshold", DEFAULT_THRESHOLD)))
    me = scores[owner]
    # A little benefit of the doubt if Connor was the one talking a moment ago.
    if LAST["owner"] and time.time() - LAST["t"] < 30:
        threshold -= 0.05
    is_owner = me >= threshold and (best == owner or me >= scores[best] - 0.02)
    if is_owner:
        name = owner
    elif best != owner and scores[best] >= threshold:
        name = best                             # a friend Jarvis knows by voice
    else:
        name = None                             # someone new
    print(f"(voice: {name or 'guest'}, {owner} match {me:.2f}, needs {threshold:.2f})")
    if is_owner and me >= threshold + 0.15:
        _learn(owner, v)
    LAST.update(name=name, owner=is_owner, score=me, t=time.time())
    return {"name": name, "owner": is_owner, "score": me}


def _learn(name, v, rate=0.03):
    """Slowly adapt Connor's voiceprint when Jarvis is sure it's him (colds, new mic, etc.)."""
    try:
        data = load()
        p = data["people"][name]
        mixed = (1 - rate) * np.array(p["vec"], dtype=np.float32) + rate * v
        p["vec"] = [round(float(x), 5) for x in mixed / np.linalg.norm(mixed)]
        p["learned"] = p.get("learned", 0) + 1
        save(data)
    except Exception as e:
        print(f"(couldn't update voiceprint: {e})")
