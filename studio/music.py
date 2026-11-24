"""Background music (ACE-Step 1.5, see studio/engines.py): one short instrumental piece per Short, matched
to its mood, mixed well under the narration. The voice always wins: the music ducks while words are spoken,
fades in at the start and out at the end (so the loop back to the first frame is smooth), and the mix is
limited so the loudness and peak checks still pass."""
import hashlib
from pathlib import Path

from studio import engines

LICENCE = "music made with ACE-Step 1.5 (MIT; its output may be used commercially)"
BASE = "instrumental, no vocals, background music for a science explainer, gentle, clear mix, loopable"
MOODS = {                                    # pillar words -> a mood; the default fits most science Shorts
    "space": "spacious ambient synth pads, soft arpeggio, sense of wonder, 85 bpm",
    "machine": "light electronic pulse, curious, playful plucks, 100 bpm",
    "body": "warm soft piano and strings, calm, 80 bpm",
    "earth": "organic ambient, soft marimba, airy pads, 90 bpm",
    "weather": "airy ambient, light percussion, 90 bpm",
}
DEFAULT_MOOD = "curious lo-fi electronic, soft synth pads, light beat, 90 bpm"
LEVEL = 0.16                                 # music level under the voice (about -16 dB)


def caption(topic):
    words = f"{(topic or {}).get('pillar', '')} {(topic or {}).get('title', '')}".lower()
    mood = next((m for k, m in MOODS.items() if k in words), DEFAULT_MOOD)
    return f"{BASE}, {mood}"


def bpm_of(text):
    import re
    m = re.search(r"(\d{2,3}) bpm", text)
    return int(m.group(1)) if m else None


def make(video_id, seconds, topic=None):
    """The raw music file for this Short (made once, then reused). Raises engines.EngineError."""
    cap = caption(topic)
    seconds = round(float(seconds) + 1.0, 1)
    seed = int(video_id) * 7 + 3
    folder = engines.outputs_dir("videos", str(video_id), "music")
    out = folder / f"music_{hashlib.sha1(f'{cap}|{seconds}|{seed}'.encode()).hexdigest()[:12]}.wav"
    if not out.exists():
        with engines.heavy("background music (ACE-Step)"):
            engines.run("acestep", "acestep_worker", {"caption": cap, "duration": max(10.0, seconds), "out": str(out),
                                                      "seed": seed, "bpm": bpm_of(cap)})
        if not out.exists():
            raise engines.EngineError("ACE-Step didn't write the music file")
    return out, cap


def mix(voice, music, end, out):
    """voice + music (ducked under speech, faded in/out, limited) -> out (48 kHz WAV)."""
    from clipper import media
    fade_out = max(0.0, end - 0.6)
    graph = (f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[v][key];"
             f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{end:.3f},asetpts=PTS-STARTPTS,"
             f"volume={LEVEL},afade=t=in:d=0.4,afade=t=out:st={fade_out:.3f}:d=0.6[m];"
             f"[m][key]sidechaincompress=threshold=0.03:ratio=4:attack=15:release=350[duck];"
             f"[v][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.84:level=false[out]")
    media.run(["-y", "-i", str(voice), "-i", str(music), "-filter_complex", graph, "-map", "[out]", "-ar", "48000",
               str(out)])
    return Path(out)
