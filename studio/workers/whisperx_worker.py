"""Forced alignment with whisperX (runs inside tools/whisperx). In: {"audio", "words": [script words]}.
Out: {"words": [{"w", "s", "e"}]} with one entry per script word (s/e null where it couldn't be placed).

The script is known exactly, so nothing is transcribed: whisperX's phoneme model lines the known words
up with the audio. Numbers are aligned as the narrator says them ("2,970" -> "two thousand nine
hundred seventy") and mapped back onto the written word."""
import json
import re
import sys

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))


def spoken(word):
    from num2words import num2words
    w = word.strip()
    m = re.fullmatch(r"[^\w]*([\d,]*\.?\d+)([^\w]*)(%?)[^\w]*", w)
    if m and any(c.isdigit() for c in m.group(1)):
        n = m.group(1).replace(",", "")
        try:
            v = float(n) if "." in n else int(n)
            s = num2words(v)
            if m.group(3):
                s += " percent"
            return re.sub(r"[^a-z' ]", " ", s.lower()).split()
        except Exception:
            pass
    t = re.sub(r"[^A-Za-z']", " ", w.replace("%", " percent")).lower().split()
    return t or [w]


try:
    import whisperx
    audio = whisperx.load_audio(job["audio"])
    dur = len(audio) / 16000.0
    words = job["words"]
    pieces, owner = [], []
    for i, w in enumerate(words):
        for p in spoken(w):
            pieces.append(p)
            owner.append(i)
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"        # an NVIDIA card (Windows PC), else the processor
    model, meta = whisperx.load_align_model(language_code="en", device=device)
    seg = [{"start": 0.0, "end": dur, "text": " ".join(pieces)}]
    res = whisperx.align(seg, model, meta, audio, device, return_char_alignments=False)
    got = [w for s in res.get("segments", []) for w in s.get("words", [])]
    out = [{"w": w, "s": None, "e": None} for w in words]
    for k, g in enumerate(got[:len(owner)]):
        i = owner[k]
        if g.get("start") is None:
            continue
        if out[i]["s"] is None:
            out[i]["s"] = round(float(g["start"]), 3)
        out[i]["e"] = round(float(g.get("end", g["start"])), 3)
    json.dump({"words": out, "aligned": sum(1 for o in out if o["s"] is not None), "device": device}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
