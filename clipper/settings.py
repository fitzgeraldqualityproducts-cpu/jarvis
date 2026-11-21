"""Clipper settings.

Global limits live in config.json under "clipper" (so they travel with Jarvis's other settings).
Each YouTube channel the clipper works for has its own settings in the database (channels table):
niche, branding, caption style, clip lengths, permissions, publishing schedule, metadata defaults.
Nothing about one channel (its sources, tokens, clips, analytics) is ever used for another.

Permissions (per channel) are separate switches, and the risky ones start OFF:
    discover        look for new videos from approved sources            on
    process         download/transcribe/analyze authorized videos        on
    render          make clips                                           on
    save            keep finished clips in the library                   on
    upload_private  upload clips to YouTube as private (or unlisted)      off until YouTube is connected
    publish_public  make clips public automatically                      OFF
    schedule_public schedule clips to go public later                    OFF
    delete_files    delete old source files to save disk space            on
    require_approval  clips wait for your OK before any upload            on
"""
import copy
import json

from clipper import ROOT, db

CONFIG = ROOT / "config.json"

GLOBAL_DEFAULTS = {
    "enabled": True,              # the background worker runs inside Jarvis
    "max_parallel_jobs": 1,       # jobs at once (video work is heavy; one keeps Jarvis snappy)
    "max_disk_gb": 25,            # clipper_data may not grow past this
    "min_free_gb": 4,             # never fill the disk below this
    "keep_sources_days": 3,       # delete downloaded source videos this long after a job finishes
    "keep_failed_days": 14,
    "max_attempts": 3,            # retries for a failing stage (with growing waits), then it fails
    "stage_timeout_minutes": 240, # a stage with no progress for this long is restarted
    "whisper_model": "small.en",  # faster-whisper model for transcripts (or "parakeet")
    "transcriber": "auto",        # auto | whisper | parakeet
    "llm_model": "",              # Claude model for analysis ("" = Claude Code's default)
    "fps": 30,
    "x264_preset": "veryfast",
    "crf": 20,
    "allow_platform_download": True,   # yt-dlp, only ever for AUTHORIZED YouTube/Twitch sources
    "max_video_hours": 12,
    "youtube_daily_quota": 10000,      # Google's free daily API quota
    "youtube_upload_cost": 1600,       # units one upload costs (Google's published price)
    "discovery_every_hours": 6,
    "analytics_every_hours": 24,
}

PERMISSION_DEFAULTS = {
    "discover": True, "process": True, "render": True, "save": True,
    "upload_private": False, "publish_public": False, "schedule_public": False,
    "delete_files": True, "require_approval": True, "auto_process": True,
}

CHANNEL_DEFAULTS = {
    "name": "My Shorts channel",
    "niches": ["podcasts", "gaming", "entertainment"],
    "audience": "general",
    "language": "en",
    "brand": {"handle": "", "voice": "energetic, honest, never clickbait", "color": "#22d3ee",
              "highlight": "#facc15", "watermark": False},
    "caption_style": "auto",          # auto or one of STYLES (see captions.py)
    "caption_overrides": {},          # font, size, color, active, keyword, stroke, shadow, y, animation, words_per_line
    "hook_overlay": True,             # short accurate title text over the first seconds
    "layout": "auto",                 # auto | track | podcast | split | stack | gameplay | fit | full
    "jump_cuts": True,                # remove dead air between words (silence removal)
    "max_gap": 0.55,                  # pauses longer than this (seconds) get tightened
    "remove_fillers": True,           # "um"/"uh" on their own are cut from the sound and the captions
    "zooms": True,                    # a gentle settle at the start and alternate framing on jump cuts
    "punch_ins": True,                # a quick, subtle push-in on emphasised words and big reactions
    "transitions": True,              # a short push on speaker switches instead of a hard jump
    "noise_reduction": "auto",        # auto (only when the sound is noisy) | True | False
    "color_correction": "auto",       # auto (only when the picture is dull/flat) | True | False
    "sharpen": "auto",                # auto (only when a small video is enlarged) | True | False
    "music_folder": "",               # licensed background music only (empty = none)
    "music_volume": 0.08,             # music level under speech (0-0.3); it ducks further while someone talks
    "sound_effects": False,           # soft whoosh/pop on the opening text and punch-ins (made by the clipper, no licences)
    "length_mode": "auto",            # auto | 15_30 | 30_45 | 45_60 | 60_90 | custom (clip_min/clip_max below)
    "clip_min": 20, "clip_max": 60, "clip_hard_max": 90,
    "max_clips_per_video": 5,
    "min_score": 55,                  # clips scoring under this (out of 100) are dropped
    "priorities": {},                 # e.g. {"hook": 2.0} = weigh opening hooks double
    "names": [],                      # names/terms Whisper should spell right
    "fixes": {},                      # word fixes for captions, e.g. {"jarvis": "JARVIS"}
    "privacy": "private",             # private | unlisted | scheduled | public  (permissions still apply)
    "publish_times": ["12:00", "18:00"],
    "max_uploads_per_day": 3,
    "hashtags_always": ["#shorts"],
    "category_id": "24",              # YouTube category (24 = Entertainment, 22 = People & Blogs...)
    "credit_sources": True,           # add "Source: ..." for videos that aren't yours
    "permissions": PERMISSION_DEFAULTS,
    "learned": {},                    # filled in by learning.py
}

STYLES = ["clean", "bold", "minimal", "gaming", "podcast", "highlight", "pop", "cinematic", "kinetic", "educational", "premium"]
LAYOUTS = ["auto", "track", "podcast", "split", "stack", "gameplay", "fit", "full"]
LENGTH_MODES = ["auto", "15_30", "30_45", "45_60", "60_90", "custom"]


def _deep(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def general():
    try:
        cfg = json.loads(CONFIG.read_text())
    except Exception:
        cfg = {}
    return _deep(GLOBAL_DEFAULTS, cfg.get("clipper") or {})


def set_global(**changes):
    try:
        cfg = json.loads(CONFIG.read_text())
    except Exception:
        cfg = {}
    cfg.setdefault("clipper", {}).update(changes)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def jarvis_config():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def ensure_default_channel():
    """There's always at least one channel ("main"). Your Twitch channel (from stream mode) is added
    as an approved source for it, since VODs of your own streams are yours to clip."""
    import time
    if not db.one("SELECT id FROM channels WHERE id='main'"):
        db.run("INSERT OR IGNORE INTO channels(id, name, settings, created) VALUES('main', ?, '{}', ?)",
               CHANNEL_DEFAULTS["name"], time.time())
    twitch = str(jarvis_config().get("twitch_channel", "")).strip().lstrip("#").lower()
    if twitch:
        db.run("INSERT OR IGNORE INTO sources(channel, kind, value, label, authorized, auth_basis, created) "
               "VALUES('main', 'twitch_channel', ?, ?, 1, 'your own Twitch channel', ?)",
               twitch, f"twitch.tv/{twitch}", time.time())


def channels():
    ensure_default_channel()
    return [c["id"] for c in db.q("SELECT id FROM channels ORDER BY created")]


def channel(cid="main"):
    """A channel's full settings (defaults filled in)."""
    ensure_default_channel()
    row = db.one("SELECT * FROM channels WHERE id=?", cid)
    if not row:
        raise KeyError(f"no clipper channel called {cid}")
    s = _deep(CHANNEL_DEFAULTS, db.loads(row["settings"]))
    s["id"] = cid
    s["youtube_channel_id"] = row["youtube_channel_id"]
    s["youtube_title"] = row["youtube_title"]
    if row["name"] and s.get("name") == CHANNEL_DEFAULTS["name"]:
        s["name"] = row["name"]
    return s


def update_channel(cid="main", **changes):
    """Changes some of a channel's settings. Nested dicts (permissions, brand...) are merged."""
    ensure_default_channel()
    row = db.one("SELECT settings FROM channels WHERE id=?", cid)
    if not row:
        raise KeyError(f"no clipper channel called {cid}")
    current = db.loads(row["settings"])
    for k, v in changes.items():
        if k not in CHANNEL_DEFAULTS:
            raise KeyError(f"unknown channel setting {k}")
        current[k] = _deep(current.get(k, {}), v) if isinstance(v, dict) else v
    _validate(current)
    db.run("UPDATE channels SET settings=? WHERE id=?", json.dumps(current), cid)
    return channel(cid)


def add_channel(cid, name):
    import re
    import time
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", cid or ""):
        raise ValueError("channel ids are short lowercase words, like 'gaming'")
    db.run("INSERT OR IGNORE INTO channels(id, name, settings, created) VALUES(?, ?, ?, ?)",
           cid, name, json.dumps({"name": name}), time.time())
    return channel(cid)


def _validate(s):
    if s.get("caption_style", "auto") not in STYLES + ["auto"]:
        raise ValueError(f"caption style must be one of {', '.join(STYLES)} or auto")
    if s.get("layout", "auto") not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
    if s.get("length_mode", "auto") not in LENGTH_MODES:
        raise ValueError(f"clip length must be one of {', '.join(LENGTH_MODES)}")
    if "music_volume" in s and not (0 <= float(s["music_volume"]) <= 0.3):
        raise ValueError("music volume must be between 0 and 0.3 so it never drowns out speech")
    if "min_score" in s and not (0 <= float(s["min_score"]) <= 100):
        raise ValueError("the minimum score is out of 100")
    if s.get("privacy", "private") not in ("draft", "private", "unlisted", "scheduled", "public"):
        raise ValueError("privacy must be draft, private, unlisted, scheduled or public")
    lo, hi = float(s.get("clip_min", 20)), float(s.get("clip_max", 60))
    if not (5 <= lo < hi <= 180):
        raise ValueError("clip lengths must be between 5 and 180 seconds, min below max")
    if "max_clips_per_video" in s and not (1 <= int(s["max_clips_per_video"]) <= 30):
        raise ValueError("clips per video must be 1-30")


def allowed(cid, permission):
    return bool(channel(cid)["permissions"].get(permission, PERMISSION_DEFAULTS.get(permission, False)))


# ---------- the two global switches ----------

def stopped():
    """Emergency stop: no new jobs, no uploads, until you say resume."""
    return bool(db.get("emergency_stop", False))


def paused():
    return bool(db.get("paused", False))


def set_stopped(flag):
    db.put("emergency_stop", bool(flag))
    db.log("EMERGENCY STOP: no new jobs or uploads until you resume." if flag else "Emergency stop lifted.",
           "warn" if flag else "info")


def set_paused(flag):
    db.put("paused", bool(flag))
    db.log("Clipping paused." if flag else "Clipping resumed.")
