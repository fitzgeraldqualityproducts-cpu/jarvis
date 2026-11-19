"""A more human voice: Jarvis can whisper, laugh, get hyped or sound sarcastic.

His brain marks the moment with a tag, and this turns it into how he sounds, all on this computer
with his usual Kokoro voice (nothing to download, nothing to pay):

  [whisper]    quiet and breathy, for secrets and asides (his voice with the pitch taken out)
  [hyped]      faster, brighter, a touch higher and louder
  [sarcastic]  slower and a touch lower, deadpan
  [calm]       slower and softer;  [normal] goes back to his usual voice
  [laugh] [chuckle] [sigh]   the sound itself, in his own voice

A style tag lasts until the next one (or the end of his reply). Other voice engines just skip the
tags. "expressive": false in config.json turns it off."""
import json
import re
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
STYLES = {
    #            speed, pitch, gain
    "normal": (1.00, 1.00, 1.00),
    "hyped": (1.08, 1.05, 1.15),
    "sarcastic": (0.90, 0.96, 0.95),
    "calm": (0.92, 0.99, 0.85),
    "whisper": (0.95, 1.00, 1.00),
}
ALIASES = {"whispers": "whisper", "whispering": "whisper", "quietly": "whisper", "excited": "hyped", "hype": "hyped",
           "excitedly": "hyped", "sarcastically": "sarcastic", "deadpan": "sarcastic", "serious": "calm",
           "softly": "calm", "gently": "calm", "laughs": "laugh", "laughing": "laugh", "chuckles": "chuckle",
           "sighs": "sigh", "usual": "normal"}
SOUNDS = {"laugh": ("Ha ha ha!", "hyped"), "chuckle": ("Heh heh.", "normal"), "sigh": ("Haaah.", "whisper")}
TAG = re.compile(r"\[\s*([a-z]+)\s*\]", re.I)
PROMPT = ("You can colour how you sound with tags, sparingly: at most one or two in a reply, only when the moment "
          "really calls for it. Style tags go right before the words they apply to and last until the next one: "
          "[whisper] for a secret or a quiet aside, [hyped] for a big win, [sarcastic] for a playful roast, "
          "[calm] to settle down, [normal] to go back. Sound tags: [laugh], [chuckle], [sigh]. Never explain the tags.")


def enabled():
    try:
        return json.loads(CONFIG.read_text()).get("expressive", True)
    except Exception:
        return True


def _name(raw):
    n = raw.lower()
    n = ALIASES.get(n, n)
    return n if n in STYLES or n in SOUNDS else None


def strip(text):
    """The text without any tags (for the screen, logs and other voices)."""
    out = TAG.sub(lambda m: "" if _name(m.group(1)) else m.group(0), text or "")
    return re.sub(r"\s{2,}", " ", out).strip()


def has_tags(text):
    return any(_name(m.group(1)) for m in TAG.finditer(text or ""))


def parse(text, style="normal"):
    """[(kind, text, style)] where kind is "say" or "sound", plus the style in force at the end."""
    out, pos = [], 0
    for m in TAG.finditer(text):
        name = _name(m.group(1))
        if not name:
            continue
        before = text[pos:m.start()].strip()
        if before:
            out.append(("say", before, style))
        pos = m.end()
        if name in SOUNDS:
            out.append(("sound", name, style))
        else:
            style = name
    rest = text[pos:].strip()
    if rest:
        out.append(("say", rest, style))
    return out, style


# ---------------- turning a style into sound (numpy) ----------------

def _pitch(samples, factor):
    """Resampling: factor > 1 sounds higher (and a little quicker)."""
    import numpy as np
    if abs(factor - 1.0) < 0.005 or len(samples) < 2:
        return samples
    n = int(len(samples) / factor)
    return np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples).astype(np.float32)


def whisperize(samples, rate):
    """Keeps the shape of the words but throws away the pitch (random phase), which is what a
    whisper is: breath shaped into words. Low rumble is cut and it's kept fairly quiet."""
    import numpy as np
    n, hop = 512, 128
    win = np.hanning(n).astype(np.float32)
    x = np.concatenate([np.zeros(n, np.float32), samples.astype(np.float32), np.zeros(n, np.float32)])
    frames = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.fft.rfft(x[idx] * win, axis=1)
    mag = np.abs(spec)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    mag *= np.clip((freqs - 150) / 450, 0, 1)[None, :]            # whispers have no low end
    mag *= (1 + 0.6 * np.clip((freqs - 2000) / 4000, 0, 1))[None, :]  # and a bit more hiss
    rng = np.random.default_rng(7)
    phase = np.exp(2j * np.pi * rng.random(mag.shape))
    frames_out = np.fft.irfft(mag * phase, n=n, axis=1) * win
    y = np.zeros(len(x), np.float32)
    norm = np.zeros(len(x), np.float32)
    for i in range(frames):
        y[i * hop:i * hop + n] += frames_out[i]
        norm[i * hop:i * hop + n] += win ** 2
    y = (y / np.maximum(norm, 1e-3))[n:n + len(samples)]
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak * 0.32).astype(np.float32)


def _styled(text, style, voice):
    import numpy as np
    speed, pitch, gain = STYLES.get(style, STYLES["normal"])
    cfg = voice._cfg()
    base = float(cfg.get("kokoro_speed", 1.0))
    samples, rate = voice.kokoro_model().create(text, voice=cfg.get("kokoro_voice", "bm_george"),
                                                speed=base * speed / pitch, lang="en-gb")
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    samples = _pitch(samples, pitch)
    if style == "whisper":
        samples = whisperize(samples, rate)
    else:
        samples = np.clip(samples * gain, -1, 1)
    return samples, rate


def render(text, style, voice):
    """Speech for text with its tags, as one voice.Clip, using Kokoro. Returns (clip, style at the end)."""
    import numpy as np
    parts, end = parse(text, style)
    pieces, rate = [], 24000
    for kind, words, st in parts:
        if kind == "sound":
            words, st = SOUNDS[words]
        words = voice._speakable(words)
        if not words:
            continue
        samples, rate = _styled(words, st, voice)
        pieces += [voice._trim(samples, rate), np.zeros(int(0.08 * rate), np.float32)]
    if not pieces:
        return None, end
    return voice.Clip(np.concatenate(pieces), rate), end
