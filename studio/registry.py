"""The studio's software registry: every tool it can use, with version, purpose, licence, where it comes
from, whether it runs here or is an outside service, its cost (always $0), the hardware it needs and what
the studio falls back to without it. `python -m studio registry` prints it with each tool's live state.

guard.py's $0 registry (the tools table) is what's enforced; this module describes it fully and keeps both
in step: every entry here is registered there at start-up, so nothing the studio uses is unregistered."""
from studio import engines, guard

# name: (version, purpose, licence, source, local, needs_key, hardware, fallback, engine area or None)
TOOLS = {
    "claude-code": ("Connor's installed CLI", "research, scripts, fact-checks, metadata",
                    "Anthropic consumer terms (Connor's existing plan)", "https://claude.com/claude-code", False, False,
                    "none (runs in the cloud on Connor's plan)", "the local brain for small jobs; otherwise the Short waits",
                    None),
    "kokoro": ("kokoro-onnx (pinned in requirements)", "narration", "Apache-2.0 weights, MIT kokoro-onnx",
               "https://github.com/thewh1teagle/kokoro-onnx", True, False, "CPU", "none: it is the fallback", None),
    "chatterbox": ("chatterbox-tts 0.1.7", "expressive narration", "MIT (Resemble AI)",
                   "https://github.com/resemble-ai/chatterbox", True, False, "3 GB free memory; NVIDIA card if present",
                   "kokoro", "voice"),
    "faster-whisper": ("pinned in requirements", "word timing for captions", "MIT",
                       "https://github.com/SYSTRAN/faster-whisper", True, False, "CPU", "none: it is the fallback", None),
    "whisperx": ("whisperx 3.8.6", "forced alignment of caption words", "BSD-2-Clause",
                 "https://github.com/m-bain/whisperX", True, False, "1.5 GB free memory", "faster-whisper", "align"),
    "manim": ("Manim Community (conda-forge)", "animated numbers, equations and charts", "MIT",
              "https://www.manim.community", True, False, "1 GB free memory", "Pillow diagrams", "visuals"),
    "remotion": ("pinned in studio/remotion/package.json", "animated captions and motion",
                 "Remotion licence: free for individuals and companies of up to 3 people", "https://www.remotion.dev",
                 True, False, "1.5 GB free memory, Node.js", "the classic ffmpeg compose", "compose"),
    "sdxl-turbo": ("diffusers 0.32.2, stabilityai/sdxl-turbo", "an AI picture behind each scene",
                   "Stability AI Community License (free under $1M a year in revenue)",
                   "https://huggingface.co/stabilityai/sdxl-turbo", True, False, "NVIDIA card, 3 GB free memory",
                   "code-drawn scenes", "images"),
    "ace-step": (f"ACE-Step 1.5 @ {engines.ACESTEP_COMMIT[:10]}", "background music", "MIT (output usable commercially)",
                 "https://github.com/ACE-Step/ACE-Step-1.5", True, False, "NVIDIA card, 4 GB free memory, 8 GB commit",
                 "no music", "music"),
    "real-esrgan": ("RealESRGAN_x4plus weights (64 MB)", "pictures made 4x sharper", "BSD-3-Clause",
                    "https://github.com/xinntao/Real-ESRGAN", True, False, "NVIDIA card (runs in the sdxl environment)",
                    "the picture as made", "sharpen"),
    "depth-anything-v2": ("Depth-Anything-V2-Small (95 MB)", "depth maps for camera moves", "Apache-2.0",
                          "https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf", True, False,
                          "NVIDIA card (runs in the sdxl environment)", "a flat zoom", "motion"),
    "pexels": ("Pexels API v1", "stock footage for some scenes", "Pexels License (free, commercial use allowed)",
               "https://www.pexels.com/api/", False, True, "internet", "the AI picture", "footage"),
    "ffmpeg": ("imageio-ffmpeg build", "editing, encoding, loudness", "LGPL/GPL", "https://ffmpeg.org", True, False,
               "CPU", "none: required", None),
    "pillow": ("pinned in requirements", "original graphics, covers", "MIT-CMU", "https://python-pillow.org", True,
               False, "CPU", "none: required", None),
    "numpy-sfx": ("studio/sound.py (Jarvis's own code)", "sound effects", "Jarvis's own (no third-party sounds)",
                  "this repository", True, False, "CPU", "no sound effects", "sfx"),
    "sqlite": ("Python's built-in", "database", "public domain", "https://sqlite.org", True, False, "disk",
               "none: required", None),
    "youtube-data-api": ("v3", "upload, channel check, public research", "Google APIs terms",
                         "https://developers.google.com/youtube/v3", False, True, "internet",
                         "nothing is uploaded; the Short waits", None),
    "youtube-analytics-api": ("v2", "the channel's own analytics (no revenue)", "Google APIs terms",
                              "https://developers.google.com/youtube/analytics", False, True, "internet",
                              "Data API counts only", None),
    "wikipedia-api": ("MediaWiki API", "fact-check sources", "free API; text CC BY-SA, cited not copied",
                      "https://www.mediawiki.org/wiki/API", False, False, "internet", "other listed sources", None),
    "wikimedia-commons": ("MediaWiki API", "licensed images", "per-file licence, recorded",
                          "https://commons.wikimedia.org", False, False, "internet", "code-drawn scenes", None),
    "google-drive-api": ("v3, drive.file scope", "backups of finished Shorts", "Google APIs terms (free 15 GB)",
                         "https://developers.google.com/drive", False, True, "internet",
                         "the copy on D: (and the daily local backups)", None),
}


def register_all():
    """Registers every tool in guard's $0 registry (cost 0; guard refuses anything else)."""
    for name, (ver, purpose, lic, src, local, key, hw, fb, area) in TOOLS.items():
        guard.register_tool(name, purpose, lic, local, key, 0, f"{hw}; fallback: {fb}")
    return len(TOOLS)


def rows():
    """Every tool with its live state: registered, cost, and (for an optional engine) whether it's switched on."""
    from studio import db
    s = engines.settings()
    out = []
    for name, (ver, purpose, lic, src, local, key, hw, fb, area) in TOOLS.items():
        t = db.one("SELECT cost_usd, status, fails, open_until FROM tools WHERE name=?", name)
        on = None if not area else s.get(area) == engines.UPGRADED.get(area)
        out.append({"name": name, "version": ver, "purpose": purpose, "license": lic, "source": src,
                    "where": "local" if local else "external", "needs_key": key, "cost_usd": 0 if not t else t["cost_usd"],
                    "hardware": hw, "fallback": fb, "area": area, "on": on, "registered": bool(t),
                    "usable": guard.tool_usable(name)[0] if t else False})
    return out


def text():
    out = ["| Tool | Version | Purpose | Licence | Local / external | Cost | Hardware | Fallback | State |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows():
        state = ("on" if r["on"] else "off") if r["on"] is not None else "core"
        if not r["usable"]:
            state += ", not usable now"
        out.append(f"| {r['name']} | {r['version']} | {r['purpose']} | {r['license']} | {r['where']}"
                   f"{' (free key)' if r['needs_key'] else ''} | ${r['cost_usd']:.0f} | {r['hardware']} | {r['fallback']} | {state} |")
    return "\n".join(out) + "\n"
