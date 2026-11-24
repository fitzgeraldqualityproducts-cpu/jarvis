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


REF_LINE = ("Here's a number that sounds wrong at first. Let's check it together, one step at a time, "
            "with real figures you can test yourself.")


def reference_clip(s=None):
    """A short clip of the channel's Kokoro voice, made once per voice setting. Chatterbox copies it, so
    the upgraded narration keeps the channel's voice."""
    from studio import data_dir
    s = s or settings()
    path = data_dir("brand") / f"voice_ref_{s['voice']}_{s['speed']:.2f}.wav"
    if not path.exists() or path.stat().st_size < 1000:
        from clipper import media
        samples, rate = _synth(REF_LINE, s["voice"], s["speed"])
        media.write_wav(path, tidy(_resample(np.asarray(samples).reshape(-1), rate), RATE), RATE)
    return path


def read_any_wav(path):
    """(samples float32 (n, channels), rate) from a 16-bit PCM or a 32-bit float WAV (format 3, which
    Python's wave module refuses and torchaudio writes by default)."""
    from clipper import media
    import struct
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise NarrationError(f"{Path(path).name} isn't a WAV file")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = list(struct.unpack("<HHIIHH", chunk[:16]))
            if fmt[0] == 0xFFFE and len(chunk) >= 26:    # WAVE_FORMAT_EXTENSIBLE: the real kind is in the GUID
                fmt[0] = struct.unpack("<H", chunk[24:26])[0]
        elif cid == b"data":
            body = chunk
        pos += 8 + size + (size & 1)
    if not fmt or body is None:
        raise NarrationError(f"{Path(path).name} has no audio in it")
    kind, ch, rate, _, _, bits = fmt
    if kind == 3 and bits == 32:
        x = np.frombuffer(body, dtype="<f4").astype(np.float32)
    elif kind == 1 and bits == 16:
        return media.read_wav(path)
    else:
        raise NarrationError(f"{Path(path).name} is in an audio format Jarvis can't read ({kind}, {bits}-bit)")
    return np.clip(x, -1, 1).reshape(-1, ch), int(rate)


# How the narrator leans on each beat of the script (studio/script.py BEATS): more feeling on the payoff (the
# surprising result) and the re-hook, a short pause just before them. Chatterbox speeds up when it's more
# expressive, so the guidance (cfg) drops a little there to keep the pace calm.
DELIVERY = {"hook": {"exaggeration": 0.1, "pause_before": 0.0},
            "rehook": {"exaggeration": 0.12, "cfg": -0.1, "pause_before": 0.3},
            "payoff": {"exaggeration": 0.22, "cfg": -0.15, "pause_before": 0.42}}
GAP = 0.18


def segments(text, video_id=None, base_ex=0.5, base_cfg=0.5):
    """The script as [{text, exaggeration, cfg_weight, pause_before, beat}] when the passed script's lines (with
    their beats) make up exactly this text; else None (the whole text is read evenly, as before)."""
    if video_id is None:
        return None
    try:
        import json
        from studio import script
        row = script.latest(video_id, "passed")
        lines = json.loads(row["structure"] or "{}").get("lines") or [] if row else []
        texts = [" ".join(str(ln.get("text", "")).split()) for ln in lines]
        if texts:
            texts[0] = " ".join(str(row["hook"]).split())
        if not texts or " ".join(texts) != " ".join(str(text).split()):
            return None
        out = []
        for ln, t in zip(lines, texts):
            d = DELIVERY.get(str(ln.get("beat") or ""), {})
            out.append({"text": t, "beat": ln.get("beat") or "",
                        "exaggeration": round(min(0.9, base_ex + d.get("exaggeration", 0.0)), 2),
                        "cfg_weight": round(max(0.2, base_cfg + d.get("cfg", 0.0)), 2),
                        "pause_before": d.get("pause_before", 0.0)})
        return out if any(o["beat"] in DELIVERY for o in out) else None
    except Exception:
        return None


def _chatterbox(text, s, out, video_id=None):
    """(samples, rate, info) from Chatterbox in the channel's voice. Raises engines.EngineError."""
    from clipper import media
    from studio import engines
    g = config.general()
    raw = Path(out).with_suffix(".cb.wav")
    ex, cfg = float(g.get("chatterbox_exaggeration") or 0.5), float(g.get("chatterbox_cfg_weight") or 0.5)
    job = {"text": text, "out": str(raw), "reference": str(reference_clip(s)), "exaggeration": ex, "cfg_weight": cfg,
           "seed": 7, "gap": GAP}
    segs = segments(text, video_id, ex, cfg) if g.get("expressive_delivery", True) else None
    if segs:
        job["segments"] = segs
    with engines.heavy("Chatterbox narration"):
        info = engines.run("chatterbox", "chatterbox_worker", job)
    info["delivery"] = "by beat" if segs else "even"
    try:
        x, rate = read_any_wav(raw)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    return x, rate, info


def narrate(text, out, video_id=None, engine=None, record=True):
    """Makes and validates the voiceover. Returns its record (dict). engine "chatterbox" uses Chatterbox
    when it's installed and works, else Kokoro (the reason is kept in the record); record=False makes a
    sample without saving it in the database."""
    text = " ".join(str(text).split())
    if not text:
        raise NarrationError("there's no script to read")
    guard.allow("media.local", f"narration for video {video_id}", record=False)
    ok, why = guard.tool_usable("kokoro")
    if not ok:
        raise NarrationError(why)
    s = settings()
    from studio import engines
    engine = engine or engines.settings()["voice"]
    from clipper import media
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fallback = None
    if engine == "chatterbox":
        t0 = time.time()
        try:
            samples, rate, info = _chatterbox(text, s, out, video_id)
            rec = _finish(samples, rate, out, text, video_id, s, "chatterbox", "chatterbox-tts==0.1.7 (channel voice "
                          f"{s['voice']})", t0)
            rec["engine_info"] = {k: info.get(k) for k in ("device", "sentences", "took", "delivery")}
            if rec["checks"]["ok"]:
                return _save(rec, record)
            fallback = "Chatterbox's audio failed its checks: " + "; ".join(rec["checks"]["problems"])
        except Exception as e:
            fallback = str(e)[:300]
    t0 = time.time()
    try:
        samples, rate = _synth(text, s["voice"], s["speed"])
    except Exception as e:
        guard.tool_result("kokoro", False, str(e))
        raise NarrationError(f"Kokoro couldn't read the script: {e}")
    guard.tool_result("kokoro", True)
    rec = _finish(samples, rate, out, text, video_id, s, "kokoro", model_version(), t0)
    if fallback:
        rec["fallback"] = f"used Kokoro because {fallback}"
    if not rec["checks"]["ok"]:
        raise NarrationError("the voiceover failed its checks: " + "; ".join(rec["checks"]["problems"]))
    return _save(rec, record)


def _finish(samples, rate, out, text, video_id, s, engine, version, t0):
    from clipper import media
    raw = Path(out).with_suffix(".raw.wav")
    media.write_wav(raw, tidy(_resample(np.asarray(samples).reshape(-1), rate), RATE), RATE)
    try:
        _loudnorm(raw, out)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    checks = validate(out, text)
    return {"video_id": video_id, "path": str(out), "engine": engine, "voice": s["voice"], "speed": s["speed"],
            "model_version": version, "seconds": checks["seconds"], "loudness": checks["lufs"],
            "checks": checks, "render_seconds": round(time.time() - t0, 1)}


def _save(rec, record=True):
    if record:
        import json
        db.insert("INSERT INTO voiceovers(video_id, path, engine, voice, model_version, seconds, loudness, checks, created) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", rec["video_id"], rec["path"], rec["engine"], rec["voice"],
                  rec["model_version"], rec["seconds"], rec["loudness"], json.dumps(rec["checks"]), time.time())
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
