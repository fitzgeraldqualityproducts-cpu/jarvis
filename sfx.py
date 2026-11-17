"""Sound effects for "take a blinker", made from real recordings.

The clips come from the ESC-50 sound dataset (github.com/karolpiczak/ESC-50,
Creative Commons clips from freesound.org). They are downloaded once into sounds/.
"""
import json
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SOUNDS = HERE / "sounds"
BASE = "https://raw.githubusercontent.com/karolpiczak/ESC-50/master/audio/"
# Real recordings to choose from: (file, start, end, play backwards?)
BREATHS = [
    ("2-54962-A-23.wav", 0.05, 2.55, True),
    ("2-54961-A-23.wav", 0.62, 3.45, True),
    ("5-238492-A-23.wav", 0.00, 2.40, True),
    ("2-98392-A-23.wav", 0.00, 2.30, True),
]
COUGHS = [
    ("5-207681-A-24.wav", 0.15, 3.40),
    ("2-87799-A-24.wav", 0.20, 3.45),
    ("2-108017-A-24.wav", 0.20, 4.35),
    ("2-87412-A-24.wav", 0.15, 2.30),
    ("5-184871-A-24.wav", 0.00, 2.30),
    ("2-87795-A-24.wav", 0.00, 1.95),
    ("4-154443-A-24.wav", 0.00, 5.00),
    ("5-204604-A-24.wav", 0.00, 5.00),
]
CONFIG = HERE / "config.json"
RATE = 44100


def _clip(name, start, end):
    path = SOUNDS / name
    if not path.exists():
        SOUNDS.mkdir(exist_ok=True)
        print(f"(downloading sound {name})")
        tmp = path.with_suffix(".part")
        try:   # the Mac's curl uses the system certificates, which Python sometimes lacks
            subprocess.run(["curl", "-fsSL", "-m", "60", "-o", str(tmp), BASE + name], check=True)
        except Exception:
            urllib.request.urlretrieve(BASE + name, tmp)
        tmp.rename(path)
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64) / 32768
        if w.getnchannels() == 2:
            x = x.reshape(-1, 2).mean(1)
        rate = w.getframerate()
    return x[int(start * rate):int(end * rate)]


def _stretch(x, seconds, grain=4096):
    """Make a noisy sound (like a breath) longer without changing its pitch."""
    out_len = int(seconds * RATE)
    hop_out = grain // 4
    ratio = (len(x) - grain) / max(1, out_len - grain)
    win = np.hanning(grain)
    out = np.zeros(out_len + grain)
    norm = np.zeros(out_len + grain)
    rng = np.random.default_rng(3)
    pos = 0
    while pos < out_len:
        src = int(pos * ratio + rng.integers(-grain // 8, grain // 8))
        src = min(max(src, 0), len(x) - grain)
        out[pos:pos + grain] += x[src:src + grain] * win
        norm[pos:pos + grain] += win
        pos += hop_out
    return (out / np.maximum(norm, 1e-3))[:out_len]


def _fade(x, fin=0.15, fout=0.25):
    a, b = int(fin * RATE), int(fout * RATE)
    x = x.copy()
    x[:a] *= np.linspace(0, 1, a)
    x[-b:] *= np.linspace(1, 0, b)
    return x


def _write(samples, path):
    samples = samples / (np.max(np.abs(samples)) + 1e-9) * 0.95
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((samples * 32767).astype(np.int16).tobytes())


def _choice():
    try:
        b, c = json.loads(CONFIG.read_text()).get("blinker", [0, 0])
    except Exception:
        b, c = 0, 0
    return b % len(BREATHS), c % len(COUGHS)


def change(part="both"):
    """Switch to the next breath and/or cough. Returns (breath number, cough number), counting from 1."""
    b, c = _choice()
    if part in ("breath", "both"):
        b = (b + 1) % len(BREATHS)
    if part in ("cough", "both"):
        c = (c + 1) % len(COUGHS)
    try:
        cfg = json.loads(CONFIG.read_text())
    except Exception:
        cfg = {}
    cfg["blinker"] = [b, c]
    CONFIG.write_text(json.dumps(cfg, indent=2))
    return b + 1, c + 1


def _trim(x, level=0.03):
    loud = np.where(np.abs(x) > level * np.max(np.abs(x)))[0]
    if not len(loud):
        return x
    return x[max(0, loud[0] - int(0.02 * RATE)):loud[-1] + int(0.15 * RATE)]


def blinker_file():
    b, c = _choice()
    name, start, end, backwards = BREATHS[b]
    cname, cstart, cend = COUGHS[c]
    out = SOUNDS / f"blinker-{name[:-4]}-{cname[:-4]}.wav"   # one cached file per combination
    if out.exists():
        return out
    breath = _clip(name, start, end)
    breath = _stretch(breath[::-1] if backwards else breath, 5.0)
    # Soften the quiet start so the whole drag is audible, keeping its natural build.
    k = int(0.25 * RATE)
    env = np.sqrt(np.convolve(breath ** 2, np.ones(k) / k, mode="same")) + 1e-3
    breath = breath / env ** 0.6
    breath = _fade(breath, 0.25, 0.08)
    breath = breath / (np.max(np.abs(breath)) + 1e-9) * 0.35
    cough = _fade(_trim(_clip(cname, cstart, cend)), 0.005, 0.3)
    cough = cough / (np.max(np.abs(cough)) + 1e-9)
    hold = np.zeros(int(0.6 * RATE))
    _write(np.concatenate([breath, hold, cough]), out)
    return out


def blinker():
    try:
        path = blinker_file()
    except Exception as e:
        print(f"(blinker sound failed: {e})")
        return False
    if sys.platform == "win32":
        import win
        win.play_wait(path)
    else:
        subprocess.run(["afplay", str(path)])
    return True


if __name__ == "__main__":
    blinker()
