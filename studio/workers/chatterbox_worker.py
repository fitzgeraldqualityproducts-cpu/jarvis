"""Chatterbox narration (runs inside tools/chatterbox). In: {"text", "out", "reference"?, "exaggeration",
"cfg_weight", "seed"}. Out: {"path", "rate", "seconds", "device", "sentences"}.

Reads the script one sentence at a time (Chatterbox is best on short passages), with a short pause
between sentences, then writes one mono WAV. With "reference" (a few seconds of the channel's Kokoro
voice) it speaks in that voice, so the channel sounds the same, just more natural."""
import json
import re
import sys
import time

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))


def sentences(text, limit=260):
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if p.strip()]
    out = []
    for p in parts:                       # very long sentences are split at commas
        while len(p) > limit and "," in p[:limit]:
            k = p[:limit].rfind(",")
            out.append(p[:k + 1].strip())
            p = p[k + 1:].strip()
        out.append(p)
    return out


try:
    import numpy as np
    import torch
    from chatterbox.tts import ChatterboxTTS
    torch.manual_seed(int(job.get("seed", 7)))
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.time()
    try:
        model = ChatterboxTTS.from_pretrained(device=device)
    except Exception:
        device = "cpu"
        model = ChatterboxTTS.from_pretrained(device=device)
    kw = {"exaggeration": float(job.get("exaggeration", 0.5)), "cfg_weight": float(job.get("cfg_weight", 0.5))}
    if job.get("reference"):
        model.prepare_conditionals(job["reference"], exaggeration=kw["exaggeration"])
    rate = model.sr
    gap = np.zeros(int(rate * 0.18), dtype=np.float32)
    chunks = []
    for s in sentences(job["text"]):
        with torch.inference_mode():
            wav = model.generate(s, **kw)
        chunks.append(wav.squeeze(0).detach().cpu().numpy().astype(np.float32))
        chunks.append(gap)
    audio = np.concatenate(chunks[:-1]) if chunks else np.zeros(1, dtype=np.float32)
    import wave                           # 16-bit PCM: the format Jarvis's own WAV reader understands
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(job["out"], "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    json.dump({"path": job["out"], "rate": rate, "seconds": round(len(audio) / rate, 2), "device": device,
               "sentences": len(chunks) // 2, "took": round(time.time() - t0, 1)}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
