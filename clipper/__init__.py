"""Shorts Clipper: Jarvis's YouTube Shorts clipping agent.

It turns long videos you're allowed to use (your Twitch VODs, your own YouTube videos, files you
add, Creative Commons videos, creators who gave you permission) into finished vertical Shorts:

    discover -> authorize -> acquire -> transcribe -> find moments -> rank -> crop + edit
    -> captions -> titles -> quality check -> (approval) -> upload -> track -> learn

Modules (each one does one job and can be tested on its own):
    db          the clipper's own SQLite database (clipper_data/clipper.db): jobs, clips, uploads...
    settings    global limits (config.json "clipper") and per-channel settings + permissions
    sources     discovery, authorization rules and duplicate prevention
    acquire     getting the video file (local file, direct link, or yt-dlp for authorized sources)
    media       FFmpeg/PyAV helpers (uses a bundled FFmpeg if the computer has none)
    transcribe  speech to text with word timestamps (faster-whisper, or Parakeet via sherpa-onnx)
    moments     finds, scores, checks and ranks clip candidates (Claude through brain.py)
    crop        face detection/tracking and the 9:16 framing plan
    captions    caption styles and word-by-word rendering
    render      the editor: jump cuts, framing, captions, loudness, H.264/AAC 1080x1920
    metadata    titles, descriptions, hashtags
    qa          automated quality checks (and fixes)
    youtube     Google sign-in, uploads, status and analytics (official APIs only)
    jobs        persistent job queue, worker, scheduler, recovery, emergency stop
    learning    performance tracking and cautious scoring adjustments
    intents     turns what you say into clipper commands
    pipeline    runs one job's stages in order (in a child process, so Jarvis stays responsive)

Run it on its own with `python -m clipper --help`."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the Jarvis folder
MODELS = ROOT / "models"


def home():
    """Where the clipper keeps its database, jobs, clips and tokens (JARVIS_CLIPPER_DATA overrides,
    which the tests use)."""
    return Path(os.environ.get("JARVIS_CLIPPER_DATA") or ROOT / "clipper_data")


def data_dir(*parts):
    p = home().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
