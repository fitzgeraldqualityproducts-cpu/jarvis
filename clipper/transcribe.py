"""Speech to text with word-level timestamps, done on this computer (free, private).

Engines:
  whisper   faster-whisper (already used for Jarvis's hearing), small.en by default
  parakeet  NVIDIA Parakeet TDT through sherpa-onnx (also already installed): very accurate English
            with punctuation, downloaded on first use (about 480 MB) if chosen
  mlx       the same Whisper model run on the Apple Silicon graphics chip (mlx-whisper, already
            installed on the Mac): several times faster than the CPU, same words. Only on Apple
            Silicon Macs; anything going wrong falls back to faster-whisper on the CPU.
"auto" uses whisper when its model is on the computer, else parakeet. mlx is only used when chosen
(transcriber: "mlx"): measured on Connor's M1 it was 1.6x faster but its words differed from the
CPU's in about 1 in 5 places, and it slowed right down while a clip was rendering.

The transcript (transcript.json) has:
  words      [{w, s, e, p, emph}]   every word with start/end seconds, confidence, loud emphasis
  sentences  [{id, s, e, text, i0, i1, speaker, question, pause_after}]
  speakers   how many voices were found (from Jarvis's voiceprint model, when installed)
  quality    speech ratio and confidence, used to judge audio/speaker quality
Timestamps are kept exactly as the engine gives them; clip edges are snapped to these words."""
import os
import platform
import re
import sys
import tarfile
import urllib.request

import numpy as np

from clipper import MODELS, media, settings

PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
PARAKEET_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{PARAKEET}.tar.bz2"
TERMINAL = re.compile(r"[.!?…][\"')\]]*$")

_models = {}


# ---------- engines ----------

def _whisper_available(name):
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    if os.path.isdir(name):
        return True
    from pathlib import Path
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    short = name.split("/")[-1]
    return any(cache.glob(f"models--*faster-whisper-{short}*")) or any((MODELS / "whisper").glob(f"*{short}*"))


def mlx_available():
    """An Apple Silicon Mac with mlx-whisper installed (and the graphics chip not switched off)."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    if not settings.general().get("gpu_transcribe", True):
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


def mlx_repo(name):
    """The mlx-community copy of a Whisper model ("small.en" -> mlx-community/whisper-small.en-mlx)."""
    name = str(name or "small.en").strip()
    if "/" in name or os.path.isdir(name):
        return name
    if name in ("turbo", "large-v3-turbo"):
        return "mlx-community/whisper-large-v3-turbo"
    if name in ("large", "large-v3"):
        return "mlx-community/whisper-large-v3-mlx"
    return f"mlx-community/whisper-{name}-mlx"


def engine():
    g = settings.general()
    choice = g.get("transcriber", "auto")
    if choice in ("whisper", "parakeet", "mlx"):
        return choice if choice != "mlx" or mlx_available() else "whisper"
    if _whisper_available(g.get("whisper_model", "small.en")):
        return "whisper"
    if (MODELS / PARAKEET).is_dir():
        return "parakeet"
    return "whisper"            # downloads small.en on first use


def _threads():
    return max(2, (os.cpu_count() or 4) // 2)


def _whisper_words(audio_path, duration, progress, prompt, language):
    from faster_whisper import WhisperModel
    name = settings.general().get("whisper_model", "small.en")
    if language and language != "en" and name.endswith(".en"):
        name = name[:-3]
    key = ("whisper", name)
    if key not in _models:
        _models[key] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=_threads())
    model = _models[key]
    # Hand Whisper the samples, not the file: reading files itself needs a newer PyAV than some
    # computers have ("open() got an unexpected keyword argument 'metadata_errors'").
    samples, rate = media.read_wav(audio_path)
    audio = samples.mean(axis=1) if samples.ndim > 1 else samples
    if rate != 16000:
        audio = np.interp(np.arange(0, len(audio), rate / 16000.0), np.arange(len(audio)), audio).astype(np.float32)
    segments, info = model.transcribe(audio.astype(np.float32), word_timestamps=True, vad_filter=True, beam_size=5,
                                      initial_prompt=prompt or None, condition_on_previous_text=False,
                                      language=None if not language else language.split("-")[0])
    words, segs = [], []
    for seg in segments:
        segs.append({"s": seg.start, "e": seg.end, "lp": seg.avg_logprob, "ns": seg.no_speech_prob,
                     "cr": getattr(seg, "compression_ratio", 1.0)})
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3),
                              "seg": len(segs) - 1})
        if duration:
            progress(min(0.99, seg.end / duration), "transcribing")
    return words, segs, getattr(info, "language", language or "en")


def _speech_regions(audio, rate=16000):
    """Where there's speech (Silero VAD, the same detector faster-whisper uses), as (start, end)
    seconds. Whisper only listens to these, exactly like vad_filter=True on the CPU."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        ts = get_speech_timestamps(audio, VadOptions())
        return [(t["start"] / rate, t["end"] / rate) for t in ts]
    except Exception as e:
        print(f"(clipper: speech detection skipped: {e})")
        return [(0.0, len(audio) / rate)]


def _batches(regions, longest=600.0):
    """Speech regions grouped so each group has about ten minutes of speech (for progress, and so a
    long stream never has to fit on the graphics chip at once)."""
    out, cur, have = [], [], 0.0
    for a, b in regions:
        if cur and have + (b - a) > longest:
            out.append(cur)
            cur, have = [], 0.0
        cur.append((a, b))
        have += b - a
    if cur:
        out.append(cur)
    return out


def _joined(audio, regions, rate=16000):
    """The speech regions back to back (silence left out, as faster-whisper does), plus a map
    from a time in the joined audio back to the original time."""
    parts, table, at = [], [], 0.0
    for a, b in regions:
        i0, i1 = int(a * rate), int(b * rate)
        if i1 <= i0:
            continue
        parts.append(audio[i0:i1])
        table.append((at, a))
        at += (i1 - i0) / rate
    joined = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def back(t):
        lo, hi = 0, len(table) - 1
        while lo < hi:                               # the last region starting at or before t
            mid = (lo + hi + 1) // 2
            if table[mid][0] <= t:
                lo = mid
            else:
                hi = mid - 1
        j0, o0 = table[lo] if table else (0.0, 0.0)
        return o0 + (t - j0)
    return joined, back


def _audio16(audio_path):
    samples, rate = media.read_wav(audio_path)
    audio = samples.mean(axis=1) if samples.ndim > 1 else samples
    if rate != 16000:
        audio = np.interp(np.arange(0, len(audio), rate / 16000.0), np.arange(len(audio)), audio).astype(np.float32)
    return audio.astype(np.float32)


def _mlx_words(audio_path, duration, progress, prompt, language):
    import mlx_whisper
    name = settings.general().get("whisper_model", "small.en")
    if language and language != "en" and name.endswith(".en"):
        name = name[:-3]
    repo = mlx_repo(name)
    audio = _audio16(audio_path)
    lang = None if not language else language.split("-")[0]
    words, segs = [], []
    batches = _batches(_speech_regions(audio))
    for k, regions in enumerate(batches):
        piece, back = _joined(audio, regions)
        if len(piece) < 1600:
            continue
        out = mlx_whisper.transcribe(piece, path_or_hf_repo=repo, word_timestamps=True, verbose=None,
                                     condition_on_previous_text=False, initial_prompt=prompt or None,
                                     language=lang)
        for seg in out.get("segments") or []:
            segs.append({"s": round(back(seg["start"]), 3), "e": round(back(seg["end"]), 3),
                         "lp": seg.get("avg_logprob", 0.0), "ns": seg.get("no_speech_prob", 0.0),
                         "cr": seg.get("compression_ratio", 1.0)})
            for w in seg.get("words") or []:
                text = str(w.get("word", "")).strip()
                if text:
                    s0, e0 = back(w["start"]), back(w["end"])
                    e0 = min(e0, s0 + 2.0)          # a word that straddles a removed silence
                    words.append({"w": text, "s": round(s0, 3), "e": round(max(e0, s0 + 0.02), 3),
                                  "p": round(float(w.get("probability", 0.9)), 3), "seg": len(segs) - 1})
        progress(min(0.99, (k + 1) / len(batches)), "transcribing")
        lang = lang or out.get("language")
    return words, segs, lang or "en"


def _parakeet_model():
    if "parakeet" not in _models:
        folder = MODELS / PARAKEET
        if not folder.is_dir():
            download_parakeet()
        import sherpa_onnx
        _models["parakeet"] = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"),
            num_threads=_threads(), model_type="nemo_transducer")
    return _models["parakeet"]


def download_parakeet(show=print):
    MODELS.mkdir(exist_ok=True)
    tmp = MODELS / (PARAKEET + ".tar.bz2.part")
    show("(clipper: downloading the Parakeet speech model, about 480 MB)")
    req = urllib.request.Request(PARAKEET_URL, headers={"User-Agent": "jarvis-clipper"})
    try:
        import ssl
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    with tarfile.open(tmp) as t:
        for m in t.getmembers():
            if m.name.startswith(PARAKEET) and ".." not in m.name and not m.issym() and not m.islnk():
                t.extract(m, MODELS)
    tmp.unlink()


def _chunks(samples, rate, longest=25.0):
    """Cuts long audio at quiet moments into pieces of at most `longest` seconds."""
    hop = int(rate * 0.05)
    n = len(samples) // hop
    if n == 0:
        return [(0, len(samples))]
    rms = np.sqrt(np.mean(samples[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    out, start = [], 0
    max_len = int(longest * rate)
    while start < len(samples):
        if len(samples) - start <= max_len:
            out.append((start, len(samples)))
            break
        lo, hi = (start + int(max_len * 0.6)) // hop, (start + max_len) // hop
        cut = (lo + int(np.argmin(rms[lo:hi]))) * hop if hi > lo else start + max_len
        out.append((start, cut))
        start = cut
    return out


def _parakeet_words(audio_path, duration, progress):
    rec = _parakeet_model()
    samples, rate = media.read_wav(audio_path)
    mono = samples[:, 0]
    words = []
    pieces = _chunks(mono, rate)
    for k, (a, b) in enumerate(pieces):
        piece = mono[a:b]
        if len(piece) < rate * 0.3 or float(np.sqrt(np.mean(piece ** 2))) < 0.003:
            continue
        s = rec.create_stream()
        s.accept_waveform(rate, piece)
        rec.decode_stream(s)
        r = s.result
        off = a / rate
        cur = None
        toks, times = list(r.tokens), list(r.timestamps)
        for i, (tok, t) in enumerate(zip(toks, times)):
            starts_word = tok.startswith((" ", "▁")) or cur is None
            clean = tok.replace("▁", " ")
            nxt = times[i + 1] if i + 1 < len(times) else None
            if starts_word and clean.strip() and not re.fullmatch(r"[.,!?;:'\"]+", clean.strip()):
                if cur:
                    words.append(cur)
                cur = {"w": clean.strip(), "s": round(off + t, 3), "e": round(off + (nxt if nxt else t + 0.3), 3), "p": 0.9}
            elif cur is not None:
                cur["w"] += clean.strip() if not clean.startswith(" ") else clean
                cur["e"] = round(off + (nxt if nxt else t + 0.25), 3)
        if cur:
            words.append(cur)
        progress((k + 1) / len(pieces) * 0.99, "transcribing")
    for i, w in enumerate(words):                    # a word ends before the next one starts
        w["w"] = w["w"].strip()
        if i + 1 < len(words):
            w["e"] = min(w["e"], words[i + 1]["s"])
        # Parakeet gives when each word starts; a word lasts about as long as it is to say,
        # so pauses after it stay visible (jump cuts need them)
        w["e"] = round(min(w["e"], w["s"] + max(0.2, 0.075 * len(w["w"]) + 0.12), w["s"] + 1.2), 3)
    return [w for w in words if w["w"]], [], "en"


# ---------- building the transcript ----------

def transcribe(audio_path, progress=lambda f, d="": None, prompt="", language="en", eng=None):
    samples, rate = media.read_wav(audio_path)
    duration = len(samples) / rate
    eng = eng or engine()
    if eng == "parakeet":
        words, segs, lang = _parakeet_words(audio_path, duration, progress)
    elif eng == "mlx":
        try:
            words, segs, lang = _mlx_words(audio_path, duration, progress, prompt, language)
        except Exception as e:                     # never lose a job to the fast path
            print(f"(clipper: graphics-chip transcription failed ({type(e).__name__}: {str(e)[:160]}); using the CPU)")
            eng = "whisper"
            words, segs, lang = _whisper_words(audio_path, duration, progress, prompt, language)
    else:
        words, segs, lang = _whisper_words(audio_path, duration, progress, prompt, language)
    words = clean(words, segs, samples, rate)
    return build(words, samples, rate, duration, segs=segs, language=lang, engine=eng)


def clean(words, segs, samples=None, rate=16000):
    """Drops text Whisper made up (in music, silence or a loop) and moves each word's timing onto its
    actual sound, so captions appear as it's said (see caption_fix)."""
    from clipper import caption_fix
    words = caption_fix.drop_made_up(words, segs)
    mono = None
    if samples is not None and len(samples):
        mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    words = caption_fix.tighten(words, mono, rate)
    for w in words:
        w.pop("seg", None)
    return words


def build(words, samples=None, rate=16000, duration=None, segs=(), language="en", engine="test"):
    """Turns a word list into the full transcript (sentences, pauses, emphasis, speakers, quality)."""
    words = [dict(w) for w in words if str(w.get("w", "")).strip()]
    for w in words:
        w["s"], w["e"] = float(w["s"]), float(max(w["e"], w["s"] + 0.02))
    duration = duration or (words[-1]["e"] if words else 0.0)
    if samples is not None and len(words):
        _emphasis(words, samples, rate)
    sentences = sentences_from(words)
    speakers = 1
    if samples is not None and len(sentences) >= 4:
        try:
            speakers = _diarize(sentences, samples, rate)
        except Exception as e:
            print(f"(clipper: speaker detection skipped: {e})")
    speech = sum(w["e"] - w["s"] for w in words)
    probs = [w.get("p", 0.9) for w in words]
    quality = {"speech_ratio": round(speech / duration, 3) if duration else 0.0,
               "confidence": round(float(np.mean(probs)), 3) if probs else 0.0,
               "words_per_minute": round(len(words) / (duration / 60), 1) if duration else 0.0}
    loud = []
    if samples is not None:
        loud = _loud_moments(samples, rate)
    return {"language": language, "engine": engine, "duration": round(duration, 3), "words": words,
            "sentences": sentences, "speakers": speakers, "quality": quality, "loud": loud,
            "segments": [dict(s) for s in segs][:5000]}


def sentences_from(words, max_len=28.0):
    """Sentence boundaries: punctuation, long pauses (> 1.2 s), or very long run-ons."""
    out, start = [], 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        gap = (words[i + 1]["s"] - w["e"]) if not last else 99.0
        long_ = w["e"] - words[start]["s"] > max_len and gap > 0.25
        if last or TERMINAL.search(w["w"]) or gap > 1.2 or long_:
            text = " ".join(x["w"] for x in words[start:i + 1])
            out.append({"id": len(out), "s": words[start]["s"], "e": w["e"], "text": text, "i0": start, "i1": i,
                        "question": text.rstrip().endswith("?"), "pause_after": round(min(gap, 99.0), 2),
                        "speaker": 0})
            start = i + 1
    return out


def _emphasis(words, samples, rate):
    """Marks words said noticeably louder than the speaker's usual level (for captions + scoring)."""
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    levels = []
    for w in words:
        a, b = int(w["s"] * rate), int(w["e"] * rate)
        seg = mono[a:b]
        levels.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
    lv = np.array(levels)
    if not len(lv) or lv.max() <= 0:
        return
    med = np.median(lv[lv > 0]) if (lv > 0).any() else 1.0
    for w, v in zip(words, lv):
        w["emph"] = bool(v > med * 1.8)


def _loud_moments(samples, rate):
    """Spikes in loudness (laughing, shouting, hype): [(t, dB over the median)], for Twitch especially."""
    db = media.energy(samples, rate, 0.5)
    if len(db) < 20:
        return []
    med = float(np.median(db))
    out = []
    for i in np.argsort(db)[::-1][:60]:
        if db[i] - med < 10:
            break
        t = i * 0.5
        if all(abs(t - x) > 20 for x, _ in out):
            out.append((round(t, 1), round(float(db[i] - med), 1)))
    return sorted(out)


def _diarize(sentences, samples, rate):
    """Who's talking: a voiceprint for each sentence (Jarvis's speaker model), grouped by similarity."""
    import sys
    from clipper import ROOT
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import speaker
    if not speaker.MODEL.exists() and not speaker.download_model(show=lambda *_: None):
        return 1
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    embs, idx = [], []
    for k, s in enumerate(sentences):
        if s["e"] - s["s"] < 1.0:
            continue
        seg = (mono[int(s["s"] * rate):int(s["e"] * rate)] * 32767).astype(np.int16)
        v = speaker.embed(seg if rate == 16000 else seg)
        if v is not None:
            embs.append(np.asarray(v, dtype=np.float32))
            idx.append(k)
    if len(embs) < 3:
        return 1
    labels = cluster(np.stack(embs))
    for k, lab in zip(idx, labels):
        sentences[k]["speaker"] = int(lab)
    for k, s in enumerate(sentences):                  # short sentences: same as the one before
        if k not in idx and k > 0:
            s["speaker"] = sentences[k - 1]["speaker"]
    return int(max(labels) + 1)


def cluster(embs, same=0.55, min_share=0.04):
    """Groups unit-length voiceprints into speakers. Returns a label per row."""
    embs = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
    cents, counts, labels = [], [], []
    for v in embs:
        if cents:
            sims = np.array([float(v @ (c / np.linalg.norm(c))) for c in cents])
            j = int(np.argmax(sims))
            if sims[j] >= same:
                cents[j] = cents[j] + v
                counts[j] += 1
                labels.append(j)
                continue
        cents.append(v.copy())
        counts.append(1)
        labels.append(len(cents) - 1)
    total = len(labels)
    keep = [j for j, n in enumerate(counts) if n / total >= min_share] or [int(np.argmax(counts))]
    remap = {}
    for j in range(len(cents)):
        if j in keep:
            remap[j] = keep.index(j)
        else:
            c = cents[j] / np.linalg.norm(cents[j])
            remap[j] = int(np.argmax([float(c @ (cents[x] / np.linalg.norm(cents[x]))) for x in keep]))
    return [remap[j] for j in labels]
