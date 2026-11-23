"""Narration: the channel's voice, made on this Mac with Kokoro (free, Apache-2.0, no internet).

narrate(text, out) makes a finished voiceover WAV (48 kHz mono):
    Kokoro (the channel's fixed US voice and speed) -> trim leading/trailing silence
    -> shorten long pauses -> loudness to -14 LUFS, true peak under -1.5 dBTP -> validate
validate(path, text) checks it exists, has the right length for the words, isn't silent or
clipped, and has the expected loudness. A voiceover that fails validation is never used.

The voice and model version are saved with every voiceover, so a later voice change is deliberate
(config "studio": {"voice": ...}) and old videos stay traceable. Jarvis's own voice (bm_george) is
not affected: the studio only borrows the loaded Kokoro model."""
import hashlib
import os
import sys
import time
from pathlib import Path

import numpy as np

from studio import ROOT, config, db, guard

RATE = 48000
TARGET_LUFS = -14.0
VOICES = ("af_heart", "am_michael", "am_puck", "af_bella", "am_fenrir")   # US English voices offered to Connor


class NarrationError(Exception):
    pass


def settings():
    g = config.general()
    return {"voice": g.get("voice") or "af_heart", "speed": float(g.get("voice_speed") or 1.05)}


def _models():
    return ROOT / "models" / "kokoro-v1.0.onnx", ROOT / "models" / "voices-v1.0.bin"


def model_version():
    """A short fingerprint of the installed Kokoro files (size + first MB), saved with each voiceover."""
    h = hashlib.sha256()
    for p in _models():
        if not p.exists():
            return "missing"
        h.update(str(p.stat().st_size).encode())
        with open(p, "rb") as f:
            h.update(f.read(1 << 20))
    return "kokoro-v1.0:" + h.hexdigest()[:12]


_model = None


def _kokoro():
    global _model
    if _model is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            import voice as jarvis_voice                 # share Jarvis's loaded model (saves ~300 MB of memory)
            _model = jarvis_voice.kokoro_model()
        except Exception:
            from kokoro_onnx import Kokoro
            onnx, voices = _models()
            if not onnx.exists():
                raise NarrationError("Kokoro's model files aren't in the models folder")
            _model = Kokoro(str(onnx), str(voices))
    return _model


SYNTH = None           # tests swap this for a fake: (text, voice, speed) -> (samples, rate)


def _synth(text, voice, speed):
    if SYNTH:
        return SYNTH(text, voice, speed)
    return _kokoro().create(text, voice=voice, speed=speed, lang="en-us")


def _resample(x, rate, to=RATE):
    if rate == to or not len(x):
        return x.astype(np.float32)
    n = int(round(len(x) * to / rate))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def tidy(samples, rate, max_pause=0.45, edge=0.12):
    """Trims silence at both ends (keeping `edge` s) and shortens pauses longer than max_pause."""
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    hop = int(rate * 0.02)
    k = len(x) // hop
    if k < 3:
        return x
    rms = np.sqrt(np.mean(x[:k * hop].reshape(k, hop) ** 2, axis=1) + 1e-12)
    loud = rms > max(rms.max() * 0.03, 1e-4)
    if not loud.any():
        return x[:0]
    first, last = np.argmax(loud), k - 1 - np.argmax(loud[::-1])
    keep, quiet = [], 0
    for i in range(first, last + 1):
        quiet = 0 if loud[i] else quiet + 1
        if quiet * 0.02 <= max_pause:
            keep.append(i)
    body = np.concatenate([x[i * hop:(i + 1) * hop] for i in keep])
    pad = np.zeros(int(rate * edge), dtype=np.float32)
    return np.concatenate([pad, body, pad])


def _loudnorm(src, out):
    from clipper import media
    media.run(["-y", "-i", src, "-af", f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11,aresample={RATE}",
               "-ar", RATE, "-ac", 1, out])


def narrate(text, out, video_id=None):
    """Makes and validates the voiceover. Returns its record (dict)."""
    text = " ".join(str(text).split())
    if not text:
        raise NarrationError("there's no script to read")
    guard.allow("media.local", f"narration for video {video_id}", record=False)
    ok, why = guard.tool_usable("kokoro")
    if not ok:
        raise NarrationError(why)
    s = settings()
    t0 = time.time()
    try:
        samples, rate = _synth(text, s["voice"], s["speed"])
    except Exception as e:
        guard.tool_result("kokoro", False, str(e))
        raise NarrationError(f"Kokoro couldn't read the script: {e}")
    guard.tool_result("kokoro", True)
    from clipper import media
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.wav")
    media.write_wav(raw, tidy(_resample(np.asarray(samples).reshape(-1), rate), RATE), RATE)
    try:
        _loudnorm(raw, out)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    checks = validate(out, text)
    rec = {"video_id": video_id, "path": str(out), "engine": "kokoro", "voice": s["voice"], "speed": s["speed"],
           "model_version": model_version(), "seconds": checks["seconds"], "loudness": checks["lufs"],
           "checks": checks, "render_seconds": round(time.time() - t0, 1)}
    if not checks["ok"]:
        raise NarrationError("the voiceover failed its checks: " + "; ".join(checks["problems"]))
    import json
    db.insert("INSERT INTO voiceovers(video_id, path, engine, voice, model_version, seconds, loudness, checks, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", video_id, str(out), "kokoro", s["voice"], rec["model_version"],
              rec["seconds"], rec["loudness"], json.dumps(checks), time.time())
    return rec


def validate(path, text=""):
    """{ok, problems, seconds, lufs, peak, wpm}. Words-per-minute catches cut-off or stretched audio."""
    from clipper import media
    problems = []
    if not os.path.isfile(str(path)) or os.path.getsize(str(path)) < 1000:
        return {"ok": False, "problems": ["the audio file is missing or empty"], "seconds": 0, "lufs": None, "peak": None, "wpm": 0}
    x, rate = media.read_wav(path)
    seconds = len(x) / float(rate)
    lufs, peak = media.loudness(path)
    words = len(str(text).split())
    wpm = words / (seconds / 60.0) if seconds and words else 0
    if seconds < 1.0:
        problems.append(f"only {seconds:.1f} s long")
    if lufs is None or lufs < -24 or lufs > -9:
        problems.append(f"loudness {lufs} LUFS is outside -24 to -9")
    if peak is not None and peak > -0.5:
        problems.append(f"peak {peak} dBFS is too close to clipping")
    if words >= 8 and not (110 <= wpm <= 230):
        problems.append(f"{wpm:.0f} words a minute (normal speech is 110-230), so the audio is probably cut off or stretched")
    if np.sqrt(np.mean(x ** 2)) < 1e-3:
        problems.append("the audio is silent")
    return {"ok": not problems, "problems": problems, "seconds": round(seconds, 2),
            "lufs": lufs, "peak": peak, "wpm": round(wpm)}
