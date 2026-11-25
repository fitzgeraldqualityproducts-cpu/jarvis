"""Shared test setup: every test gets its own empty clipper data folder and a fake Claude."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def clipper_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_CLIPPER_DATA", str(tmp_path / "clipper_data"))
    monkeypatch.delenv("JARVIS_CLIPPER_PARENT", raising=False)
    monkeypatch.setenv("JARVIS_BUDGET_FILE", str(tmp_path / "claude-budget.json"))
    monkeypatch.setenv("JARVIS_NO_LOCAL_BRAIN", "1")       # never talks to a real Ollama while testing
    from clipper import db, settings
    db.reset_connections()
    cfg = tmp_path / "config.json"
    cfg.write_text('{"twitch_channel": "connortv"}')
    monkeypatch.setattr(settings, "CONFIG", cfg)
    from clipper import caption_fix

    def no_model(name):
        raise RuntimeError("no speech model in tests")
    monkeypatch.setattr(caption_fix, "_model", no_model)     # never downloads a model while testing
    from clipper import media
    monkeypatch.setattr(media, "NVENC_TEST", lambda: False)  # encode on the processor unless a test says otherwise
    monkeypatch.setattr(media, "_NVENC", [])
    from studio import footage
    monkeypatch.setattr(footage, "NASA_GET", lambda url: {"collection": {"items": []}})   # never asks NASA while testing
    from studio import topics

    def no_web(url):
        raise OSError("no reference sources in tests")
    monkeypatch.setattr(topics, "API", no_web)               # nor Wikipedia or arXiv
    from studio import trends
    monkeypatch.setattr(trends, "EMBED", lambda text: None)    # no meaning model while testing
    from studio import finalreview
    monkeypatch.setattr(finalreview, "mode", lambda: "off")   # no extra Claude call in other tests' Shorts
    from studio import art

    def no_clip(images, texts):
        raise RuntimeError("no OpenCLIP in tests")
    monkeypatch.setattr(art, "CLIP_SCORE", no_clip)          # pictures aren't scored unless a test fakes it
    from studio import script
    monkeypatch.setattr(script, "number_check_on", lambda: False)   # other tests' fake Claude answers stay as they are
    monkeypatch.setattr(script, "lookup_on", lambda: False)         # (test_studio_make_sense switches these back on)
    monkeypatch.setattr(script, "polish_on", lambda: False)
    from studio import obsidian
    monkeypatch.setattr(obsidian, "vault", lambda: None)     # never writes into a real Obsidian vault
    yield tmp_path
    db.reset_connections()


@pytest.fixture
def no_llm(monkeypatch):
    from clipper import llm

    def fail(prompt, timeout):
        raise llm.LLMError("no Claude in tests")
    monkeypatch.setattr(llm, "RUNNER", fail)


def make_words(script, start=0.5, wpm=150, gaps=None):
    """Fake word timings for a script: list of sentences. gaps: {sentence index: extra pause}."""
    words, t = [], start
    per = 60.0 / wpm
    for k, sentence in enumerate(script):
        for w in sentence.split():
            words.append({"w": w, "s": round(t, 3), "e": round(t + per * 0.8, 3), "p": 0.95})
            t += per
        t += 0.25 + (gaps or {}).get(k, 0.0)
    return words


SCRIPT = [
    "Welcome back to the show everybody.",
    "Today we are talking about money and habits.",
    "Here's the thing most people get wrong about saving money.",
    "They think it starts with a big income but it starts with one small habit.",
    "I was broke three years ago with forty dollars in the bank.",
    "And then I started tracking every single dollar I spent for thirty days.",
    "That one habit changed everything for me and my family.",
    "What was the hardest part of doing that?",
    "Honestly the hardest part was being honest with myself about where it went.",
    "Most of it went to food delivery and subscriptions I never used.",
    "So I cancelled seven subscriptions in one afternoon.",
    "Anyway let's take a quick break and come back with your questions.",
]


def make_video(path, seconds=20, size="1920x1080", tone_gaps=True):
    """A test video: moving test pattern + speech-like beeps (sound with pauses)."""
    from clipper import media
    expr = "0.4*sin(2*PI*440*t)*gt(mod(t\\,3)\\,0.6)" if tone_gaps else "0.4*sin(2*PI*440*t)"
    media.run(["-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
               "-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:d={seconds}",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    return path
