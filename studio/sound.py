"""Sound effects for the Shorts, made right here from noise and tones (numpy), so there's nothing to download and
no licence question: every sound is Jarvis's own.

  whoosh   filtered noise that sweeps up then down, on each cut
  tick     a tiny click, a few per second while the counter counts
  pop      a short bright click when the counter lands
  riser    a short swell of rising noise into the re-hook
  hit      a low thump with a little noise, on the payoff

track(cues, seconds) places them on the edit's cues (studio/edit.py) and mix() lays them under the narration
(and the music), well below the voice so the words always win."""
from pathlib import Path

import numpy as np

RATE = 48000
LEVELS = {"whoosh": 0.16, "tick": 0.06, "pop": 0.12, "riser": 0.12, "hit": 0.32}   # peak, before the mix
TICK_EVERY = 0.06
TICKS_FOR = 1.4


def _rng(seed):
    return np.random.default_rng(seed)


def _norm(x, peak):
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return (x / m * peak).astype(np.float32) if m > 0 else x.astype(np.float32)


def _lowpass(x, cutoff):
    """A one-pole low-pass whose cutoff (Hz) can change per sample."""
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=np.float32), x.shape)
    a = 1.0 - np.exp(-2 * np.pi * cutoff / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def whoosh(seconds=0.45, seed=1):
    n = int(RATE * seconds)
    t = np.linspace(0, 1, n, dtype=np.float32)
    noise = _rng(seed).standard_normal(n).astype(np.float32)
    sweep = 300 + 5200 * np.sin(np.pi * t) ** 2                     # the filter opens and closes
    y = _lowpass(noise, sweep) - _lowpass(noise, sweep * 0.25)      # a band, so it's airy, not hissy
    env = np.clip(np.sin(np.pi * t), 0, 1) ** 1.6
    return _norm(y * env, 1.0)


def tick(seed=2):
    n = int(RATE * 0.018)
    t = np.arange(n) / RATE
    y = np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 320) + 0.3 * _rng(seed).standard_normal(n) * np.exp(-t * 900)
    return _norm(y, 1.0)


def pop(seed=3):
    n = int(RATE * 0.07)
    t = np.arange(n) / RATE
    f = 1400 * np.exp(-t * 30) + 500
    y = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 70)
    return _norm(y, 1.0)


def riser(seconds=0.7, seed=4):
    n = int(RATE * seconds)
    t = np.linspace(0, 1, n, dtype=np.float32)
    noise = _rng(seed).standard_normal(n).astype(np.float32)
    y = _lowpass(noise, 400 + 6000 * t ** 2) * (t ** 2.2)
    y[-int(RATE * 0.02):] *= np.linspace(1, 0, int(RATE * 0.02))   # ends cleanly on the cut
    return _norm(y, 1.0)


def hit(seed=5):
    n = int(RATE * 0.6)
    t = np.arange(n) / RATE
    f = 55 + 70 * np.exp(-t * 18)
    body = np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 6.5)
    crack = _lowpass(_rng(seed).standard_normal(n).astype(np.float32), 2500) * np.exp(-t * 40)
    return _norm(body + 0.5 * crack, 1.0)


def _put(buf, x, t, level):
    i = int(round(t * RATE))
    if i >= len(buf) or i + len(x) <= 0:
        return
    if i < 0:
        x, i = x[-i:], 0
    j = min(len(buf), i + len(x))
    buf[i:j] += x[:j - i] * level


def track(cues, seconds, levels=None):
    """A mono float32 track (48 kHz) with every cue's sound in place, or None when there are no cues."""
    if not cues:
        return None
    lv = dict(LEVELS, **(levels or {}))
    buf = np.zeros(int(RATE * (seconds + 0.1)), dtype=np.float32)
    w, tk, pp, rs, ht = whoosh(), tick(), pop(), riser(), hit()
    for c in cues:
        t, kind = float(c["t"]), c["kind"]
        if kind in ("cut", "jump"):
            if kind == "jump":
                continue                                    # the jump zoom is silent: the voice carries it
            fx = c.get("sfx") or "whoosh"                  # the storyboard can pick the cut's sound
            if fx == "none":
                continue
            if fx == "pop":
                _put(buf, pp, t, lv["pop"])
            elif fx == "tick":
                _put(buf, tk, t, lv["tick"])
            elif fx == "rise":
                _put(buf, rs, t - len(rs) / RATE, lv["riser"] * 0.7)
            else:
                _put(buf, w, t - len(w) / RATE * 0.55, lv["whoosh"])   # the whoosh peaks just as the cut lands
        elif kind == "count":
            # the counter eases out (fast, then slow), so the ticks start quick and spread out, and stop
            # well before the number lands (at most about 1.4 s of them)
            until = min(float(c.get("until") or t + 0.6), t + TICKS_FOR)
            x, k = t, 0
            while x < until - 0.05:
                _put(buf, tk, x, lv["tick"] * (0.6 + 0.4 * (k % 2)))
                x += TICK_EVERY * (1 + 1.6 * (x - t) / max(0.1, until - t))
                k += 1
        elif kind == "pop":
            _put(buf, pp, t, lv["pop"])
        elif kind == "rehook":
            _put(buf, rs, t - len(rs) / RATE, lv["riser"])
        elif kind == "payoff":
            _put(buf, ht, t, lv["hit"])
    return buf if np.any(buf) else None


def mix(voice, sfx, out, seconds):
    """voice (the narration, maybe with music) + the effects track -> out (48 kHz WAV). The voice stays on top:
    the effects sit well under it and a limiter catches any peak."""
    from clipper import media
    raw = Path(out).with_suffix(".sfx.wav")
    media.write_wav(raw, np.clip(sfx, -1, 1), RATE)
    try:
        graph = ("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[v];"
                 "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[s];"
                 f"[v][s]amix=inputs=2:duration=first:normalize=0,atrim=0:{seconds:.3f},"
                 "alimiter=limit=0.84:level=false[out]")
        media.run(["-y", "-i", str(voice), "-i", str(raw), "-filter_complex", graph, "-map", "[out]", "-ar", "48000",
                   str(out)])
    finally:
        try:
            raw.unlink()
        except OSError:
            pass
    return Path(out)
