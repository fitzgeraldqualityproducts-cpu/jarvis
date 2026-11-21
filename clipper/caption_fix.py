"""More accurate captions.

The whole video is transcribed with a fast model so finding moments stays quick. That model mishears
things (on Connor's VOD, the same slang came out as "hoods" in one line and "huzz" the next, line
endings were dropped, and about 1 in 7 words were guesses), and its word timings drift: a word after
a pause often "starts" during the silence, so its caption shows up before it's said.

So before a clip is rendered, just that clip's audio is listened to again:
  1. with a more accurate model (large-v3-turbo by default, free and on this computer; downloaded
     once, about 1.6 GB), with a little audio either side so first and last words aren't clipped,
     and told the channel's names, its learned spellings and what was said just before;
  2. made-up text is dropped (Whisper "hears" words in music and silence, and can loop a phrase);
  3. every word's start and end is moved to where its sound actually is, so captions appear as the
     word is said, never early, and no two words overlap;
  4. if the second listen disagrees wildly with the first (a sign it went wrong), the first is kept.

The result is saved beside the transcript (captions.json), so re-renders and the editor use the
same words and timings. Settings: caption_model ("turbo", any faster-whisper model name, or "off").
"""
import difflib
import re
import time
import wave
from pathlib import Path

import numpy as np

from clipper import media, settings

PAD_BEFORE, PAD_AFTER = 1.5, 1.0
FRAME = 0.01                                   # 10 ms loudness frames for word timing

_models = {}


# ---------- reading only the clip's audio ----------

def read_window(path, start, end):
    """(mono float32 samples, rate) for start..end seconds of a WAV, without reading the whole file
    (a stream's audio can be hundreds of MB)."""
    with wave.open(str(path), "rb") as w:
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = max(0, min(n, int(start * rate)))
        b = max(a, min(n, int(end * rate)))
        w.setpos(a)
        data = np.frombuffer(w.readframes(b - a), dtype=np.int16).astype(np.float32) / 32768.0
    data = data.reshape(-1, ch).mean(axis=1) if ch > 1 else data
    return data, rate


def _to16k(audio, rate):
    if rate == 16000 or not len(audio):
        return audio.astype(np.float32)
    return np.interp(np.arange(0, len(audio), rate / 16000.0), np.arange(len(audio)), audio).astype(np.float32)


# ---------- the second listen ----------

def model_name(g=None):
    g = g or settings.general()
    name = str(g.get("caption_model", "turbo") or "off").strip()
    return "" if name.lower() in ("off", "none", "false", "0") else name


def _room_for(name, folder):
    """The careful model is downloaded once (about 1.6 GB); never fill the disk to do it."""
    from clipper import acquire, transcribe
    short = "large-v3-turbo" if name == "turbo" else name
    if transcribe._whisper_available(short):
        return
    free = acquire.free_gb(folder)
    need = float(settings.general().get("min_free_gb", 4)) + 2.0
    if free < need:
        raise RuntimeError(f"the careful caption model needs about 1.6 GB to download once and only "
                           f"{free:.1f} GB is free")


def _model(name):
    if name not in _models:
        from faster_whisper import WhisperModel
        from clipper import transcribe
        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=transcribe._threads())
    return _models[name]


def listen(audio16, name, prompt="", hotwords="", language="en"):
    """Words (times from the start of audio16) and segments from a careful listen."""
    model = _model(name)
    segments, _ = model.transcribe(
        audio16, word_timestamps=True, vad_filter=True, beam_size=5, best_of=5,
        initial_prompt=prompt or None, hotwords=hotwords or None,
        condition_on_previous_text=True,         # a short clip: context keeps spellings consistent
        language=(language or "en").split("-")[0])
    words, segs = [], []
    for k, seg in enumerate(segments):
        segs.append({"s": seg.start, "e": seg.end, "lp": seg.avg_logprob, "ns": seg.no_speech_prob,
                     "cr": getattr(seg, "compression_ratio", 1.0)})
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": float(w.start), "e": float(w.end), "p": round(float(w.probability), 3), "seg": k})
    return words, segs


# ---------- cleaning ----------

def drop_made_up(words, segs):
    """Removes text Whisper invents: whole segments it was unsure were speech at all (its own
    no-speech test), unsure guesses inside those, and loops of the same phrase."""
    out = []
    for w in words:
        sg = segs[w["seg"]] if segs and 0 <= w.get("seg", -1) < len(segs) else None
        if sg:
            if sg["ns"] > 0.6 and sg["lp"] < -1.0:
                continue                       # Whisper's own rule for "this wasn't speech"
            if sg.get("cr", 1.0) > 2.4:
                continue                       # a looping, repetitive segment
            if sg["ns"] > 0.5 and w.get("p", 1) < 0.15:
                continue                       # a wild guess in something that's probably not speech
        out.append(w)
    return collapse_loops(out)


def _bare(w):
    return re.sub(r"[^\w']", "", w["w"].lower())


def collapse_loops(words, max_n=4, keep=2):
    """A 2-4 word phrase repeated over and over ("thank you thank you thank you thank you ...") is a
    Whisper loop; keeps the first `keep` times. Single words repeated ("no no no") are left alone:
    people really say that."""
    out = list(words)
    i = 0
    while i < len(out):
        for n in range(2, max_n + 1):
            phrase = [_bare(w) for w in out[i:i + n]]
            if len(phrase) < n or not all(phrase):
                continue
            reps = 1
            while [_bare(w) for w in out[i + reps * n:i + (reps + 1) * n]] == phrase:
                reps += 1
            if reps > max(keep, 3):
                del out[i + keep * n:i + reps * n]
                break
        i += 1
    return out


def _frames_db(audio, rate):
    hop = max(1, int(rate * FRAME))
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(audio[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def expected_length(text):
    """About how long a word takes to say, generously (used to spot stretched timings)."""
    letters = len(re.sub(r"[^\w]", "", text))
    return min(1.6, max(0.45, 0.18 + 0.1 * letters))


def tighten(words, audio=None, rate=16000, offset=0.0):
    """Word timings that match the sound: a stretched word is trimmed to where it's actually loud
    (its start moves off the silence before it, its end off the silence after), words never overlap,
    and every word lasts long enough to be read. offset = the time of audio[0]."""
    words = sorted((dict(w) for w in words), key=lambda w: w["s"])
    db = _frames_db(audio, rate) if audio is not None and len(audio) else np.zeros(0)
    floor = float(np.percentile(db, 10)) if len(db) else 0.0
    for w in words:
        w["s"], w["e"] = float(w["s"]), float(max(w["e"], w["s"]))
        if len(db) and w["e"] - w["s"] > expected_length(w["w"]):
            a = max(0, int((w["s"] - offset) / FRAME))
            b = min(len(db), int((w["e"] - offset) / FRAME) + 1)
            seg = db[a:b]
            if len(seg) > 3:
                peak = float(seg.max())
                if peak - floor > 12:            # the word is clearly louder than the background
                    on = np.nonzero(seg > max(floor + 6, peak - 18))[0]
                    if len(on):
                        s2 = offset + (a + on[0]) * FRAME - 0.04
                        e2 = offset + (a + on[-1] + 1) * FRAME + 0.06
                        w["s"], w["e"] = max(w["s"], s2), min(w["e"], max(e2, s2 + 0.12))
        if w["e"] - w["s"] > 2 * expected_length(w["w"]):       # still stretched: keep it reasonable
            w["e"] = w["s"] + 2 * expected_length(w["w"])
    for i, w in enumerate(words):
        if i + 1 < len(words) and w["e"] > words[i + 1]["s"]:
            w["e"] = max(w["s"] + 0.05, words[i + 1]["s"])
        w["e"] = max(w["e"], w["s"] + 0.08)
        w["s"], w["e"] = round(w["s"], 3), round(w["e"], 3)
    return words


def agreement(a, b):
    """0..1, how much two word lists say the same thing."""
    x, y = [_bare(w) for w in a], [_bare(w) for w in b]
    if not x and not y:
        return 1.0
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()


# ---------- combining the two listens ----------

def _styled(word, like, case=True, tail=True):
    """`word`'s text written the way the first transcript writes things: capital where it had one,
    and its punctuation (the careful model sometimes writes everything lowercase, unpunctuated)."""
    text = word["w"]
    ref = like["w"]
    core = text.strip(".,!?;:\"'")
    if not core:
        return ref
    if case and ref[:1].isupper() and core[:1].islower():
        core = core[:1].upper() + core[1:]
    if core.lower() == "i" or core.lower().startswith("i'"):
        core = "I" + core[1:]
    end = re.search(r"[.,!?;:…\"')\]]*$", ref).group(0) if tail else re.search(r"[.,!?;:…\"')\]]*$", text).group(0)
    return core + end


def _add_new(out, n, nxt, sure):
    """Adds a word only the careful listen heard, if it was sure and it fits in time."""
    after = out[-1]["s"] + 0.1 if out else -1e9
    if n.get("p", 0) >= max(sure, 0.6) and n["s"] >= after and n["e"] <= nxt + 0.05:
        if out and out[-1]["e"] > n["s"]:
            out[-1]["e"] = n["s"]                            # the word before was stretched over this one
        out.append(dict(n, _n=True))


def merge(old, new, sure=0.5):
    """The best of both listens, word by word (aligned by what was said):
    - words both heard keep the first transcript's spelling, punctuation and timing;
    - where they disagree, the more confident one wins (the careful listen needs a clear margin);
    - words only the first heard stay if it was fairly sure of them;
    - words only the careful listen heard (endings the first one dropped) are added when it was sure
      and they fit in time between the words around them; otherwise they're left out."""
    ob, nb = [_bare(w) for w in old], [_bare(w) for w in new]
    out = []
    ops = difflib.SequenceMatcher(None, ob, nb, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        olds, news = old[i1:i2], new[j1:j2]
        nxt = old[i2]["s"] if i2 < len(old) else 1e9
        if tag == "equal":
            for o, n in zip(olds, news):
                out.append(dict(o, p=round(max(o.get("p", 0.9), n.get("p", 0.9)), 3)))
        elif tag == "delete":
            out += [dict(o) for o in olds if o.get("p", 0.9) >= sure]
        elif tag == "insert":
            for n in news:
                _add_new(out, n, nxt, sure)
        else:                                                    # replace
            po = sum(o.get("p", 0.9) for o in olds) / len(olds)
            pn = sum(n.get("p", 0.9) for n in news) / len(news)
            wins = pn >= po + 0.15 and pn >= sure
            same_slots = len(olds) == len(news) and all(abs(n["s"] - o["s"]) < 0.6 for o, n in zip(olds, news))
            in_span = all(olds[0]["s"] - 0.3 <= n["s"] and n["e"] <= olds[-1]["e"] + 0.3 for n in news)
            if wins and same_slots:
                for o, n in zip(olds, news):                     # same slots: the first one's timing
                    out.append(dict(o, w=_styled(n, o), p=n.get("p", 0.9), _n=True))
            elif wins and in_span and (not out or news[0]["s"] >= out[-1]["s"] + 0.1):
                for k, n in enumerate(news):
                    like = olds[0] if k == 0 else olds[-1]
                    out.append(dict(n, w=_styled(n, like, case=k == 0, tail=k == len(news) - 1), _n=True))
            elif not same_slots and not in_span:
                # the two listens put different words at different times: keep the first one's sure
                # words, and add the careful one's sure words where nothing else was heard
                for x in sorted([dict(o, _o=True) for o in olds if o.get("p", 0.9) >= sure or not wins] +
                                [dict(n) for n in news], key=lambda x: x["s"]):
                    if x.pop("_o", False):
                        out.append(x)
                    elif not any(o["s"] < x["e"] - 0.05 and o["e"] > x["s"] + 0.05 for o in olds):
                        _add_new(out, x, nxt, sure)
            else:
                out += [dict(o) for o in olds]
    vocab = {b: o["w"].strip(".,!?;:\"'") for o, b in zip(old, ob) if len(b) >= 4}
    for k, w in enumerate(out):
        if not w.pop("_n", False):
            continue
        b = _bare(w)
        if b not in vocab and len(b) >= 4:                      # one spelling per clip ("huzz", not "hudz")
            near = difflib.get_close_matches(b, list(vocab), n=1, cutoff=0.74)
            if near and near[0][0] == b[0]:
                w["w"] = vocab[near[0]] + re.search(r"[.,!?;:…\"')\]]*$", w["w"]).group(0)
        if k == 0 or re.search(r"[.!?…][\"')\]]*$", out[k - 1]["w"]):
            w["w"] = w["w"][:1].upper() + w["w"][1:]          # a sentence starts with a capital
        if _bare(w) == "i" or _bare(w).startswith("i'"):
            w["w"] = "I" + w["w"][1:]
    for w in out:                                               # loud emphasis from the first pass
        if "emph" not in w:
            w["emph"] = any(o["s"] < w["e"] and o["e"] > w["s"] and o.get("emph") for o in old)
    return out


# ---------- spelling the clip's caption fixes ----------

def clean_fixes(fixes, text):
    """Spelling fixes the reviewer suggested for one clip ({heard: meant}), kept only when they're
    single words actually in the clip, a short replacement each (the prompt says never to reword)."""
    out = {}
    have = {re.sub(r"[^\w']", "", t.lower()) for t in str(text).split()}
    for heard, meant in (fixes or {}).items() if isinstance(fixes, dict) else []:
        heard, meant = str(heard).strip(), str(meant).strip()
        if not heard or not meant or heard.lower() == meant.lower() or len(heard) > 30 or len(meant) > 30:
            continue
        if len(heard.split()) != 1 or len(meant.split()) > 2 or heard.lower().strip(".,!?\"'") not in have:
            continue
        out[heard.strip(".,!?\"'")] = meant.strip(".,!?\"'")
        if len(out) >= 8:
            break
    return out


# ---------- per clip ----------

def _store(folder):
    return Path(folder) / "captions.json"


def windows(folder):
    return (media.load(_store(folder)) or {}).get("windows", [])


def words_for(tr_words, folder, start=None, end=None):
    """The transcript's words with every refined stretch swapped in (all of them, or only those
    overlapping start..end)."""
    out = list(tr_words)
    for win in windows(folder):
        if start is not None and (win["e"] < start or win["s"] > end):
            continue
        out = [w for w in out if not (win["s"] <= w["s"] <= win["e"])] + win["words"]
    return sorted(out, key=lambda w: w["s"])


def covered(folder, start, end):
    """A careful listen of start..end is saved already (one that failed to run is tried again)."""
    return any(w["s"] <= start + 0.01 and w["e"] >= end - 0.01 and not w.get("retry") for w in windows(folder))


def refine(tr, folder, audio_path, start, end, channel, log=lambda m: None):
    """Listens to start..end again and saves better words for it. Returns a short note of what
    changed (for the clip's enhancements) or "" if nothing was done."""
    name = model_name()
    if not name or not audio_path or not Path(audio_path).exists() or covered(folder, start, end):
        return ""
    lo, hi = max(0.0, start - PAD_BEFORE), end + PAD_AFTER
    old = [w for w in tr["words"] if start - 0.05 <= w["s"] <= end]
    if not old:
        return ""
    audio, rate = read_window(audio_path, lo, hi)
    if len(audio) < rate:
        return ""
    names = [str(n) for n in (channel.get("names") or [])]
    spell = [str(v) for v in (channel.get("fixes") or {}).values()]
    before = " ".join(w["w"] for w in tr["words"] if start - 20 <= w["s"] < start)[-300:]
    vocab = ", ".join(dict.fromkeys(names + spell))[:300]
    prompt = (f"Names and words: {vocab}. " if vocab else "") + before
    t0 = time.time()
    retry = False
    try:
        _room_for(name, folder)
        got, segs = listen(_to16k(audio, rate), name, prompt=prompt.strip(), hotwords=vocab,
                           language=channel.get("language", "en"))
        try:
            from clipper import selftune
            selftune.speed("caption_refine", time.time() - t0, hi - lo)
        except Exception:
            pass
    except Exception as e:                      # no model and no internet, or not enough memory
        log(f"Couldn't listen to the clip again for captions ({str(e)[:120]}); fixed the timings of the first "
            f"transcript instead.")
        got, segs, retry = [], [], True
    new = drop_made_up(got, segs)
    for w in new:
        w["s"], w["e"] = w["s"] + lo, w["e"] + lo
        w.pop("seg", None)
    new = [w for w in new if start - 0.05 <= w["s"] <= end]
    score = agreement(old, new) if new else 0.0
    if not new or score < 0.45 or not (0.6 <= len(new) / len(old) <= 1.6):
        if not retry:
            log(f"The second listen didn't agree with the first ({score:.0%} the same), so I kept the first.")
        new, used = [dict(w) for w in old], "first"
    else:
        used = name
        new = merge(old, new)
    new = tighten(new, audio, rate, offset=lo)
    changed = sum(1 for op in difflib.SequenceMatcher(None, [_bare(w) for w in old], [_bare(w) for w in new],
                                                     autojunk=False).get_opcodes() if op[0] != "equal")
    data = media.load(_store(folder)) or {"windows": []}
    data["windows"] = [w for w in data["windows"] if not (w["s"] >= start - 0.01 and w["e"] <= end + 0.01)]
    data["windows"].append({"s": round(start - 0.05, 3), "e": round(end, 3), "model": used, "words": new,
                            "agreement": round(score, 3), "made": time.time(), "retry": retry})
    media.dump(data, _store(folder))
    if used == "first":
        return "caption timings matched to the voice"
    return (f"captions checked by a second, more careful listen ({changed} correction{'s' if changed != 1 else ''})"
            if changed else "captions double-checked by a second listen")
