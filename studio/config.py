"""Studio settings (config.json "studio") and the permanent rules.

Settings are things Connor may change. RULES are not settings: they are Connor's fixed project
requirements, enforced in guard.py, and no config value, voice command or Claude answer can loosen
them. Changing them means changing this file in a reviewed update."""
import json
import sys

from studio import ROOT

CONFIG = ROOT / "config.json"
PLATFORM = sys.platform

# Connor's permanent project rules (2026-11-23). guard.py enforces them.
RULES = {
    "budget_usd": 0,                    # nothing may ever cost money
    "max_channels": 1,                  # one dedicated channel, never the existing one
    "content": "shorts",                # YouTube Shorts only
    "market": "US", "language": "en",
    "never": ("purchase", "subscribe", "paid_trial", "paid_api", "billing", "money_account", "adsense",
              "withdraw", "transfer", "spend_revenue", "sponsor", "affiliate", "comment", "reply_to_viewer"),
}

DEFAULTS = {
    "enabled": False,                   # the whole module is off until Connor turns it on
    "allow_windows": False,             # the business runs on the Mac
    "min_free_gb": 6,                   # production pauses below this much free disk
    "max_disk_gb": 15,                  # studio_data/ size limit
    "max_memory_pressure": 0.85,        # pause heavy work above this share of memory in use
    "max_attempts": 4,                  # tries per stage before a job fails
    "retry_base_seconds": 60,           # first retry wait (doubles each time, max 6 h)
    "breaker_failures": 3,              # failures in a row that open a tool's circuit breaker
    "breaker_cooldown_minutes": 30,
    "stall_minutes": 45,                # a stage with no heartbeat for this long is retried
    "claude_calls_per_day": 60,         # cap on Claude Code calls (Connor's plan is shared with Jarvis)
    "tick_seconds": 30,                 # how often the worker looks for work
    "notify_cooldown_minutes": 180,     # the same alert is spoken at most this often
    "voice": "am_michael",              # the channel's Kokoro voice (US English), picked by Connor 2026-11-23; keep it once videos are public
    "voice_speed": 1.05,
}


def _read():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def general():
    s = dict(DEFAULTS)
    s.update((_read().get("studio") or {}))
    return validate(s)


def validate(s):
    out = dict(DEFAULTS)
    for k, v in s.items():
        if k not in DEFAULTS:
            out[k] = v                  # later phases add keys; unknown keys are kept, not trusted
            continue
        base = DEFAULTS[k]
        try:
            out[k] = bool(v) if isinstance(base, bool) else type(base)(v)
        except (TypeError, ValueError):
            out[k] = base
    for k in ("min_free_gb", "max_disk_gb", "max_attempts", "retry_base_seconds", "breaker_failures",
              "stall_minutes", "tick_seconds", "claude_calls_per_day"):
        if out[k] < 0:
            out[k] = DEFAULTS[k]
    out["max_memory_pressure"] = min(max(out["max_memory_pressure"], 0.3), 0.98)
    return out


def set_values(**changes):
    """Saves studio settings in config.json without touching anything else in it."""
    data = _read()
    s = dict(data.get("studio") or {})
    s.update(changes)
    data["studio"] = s
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CONFIG)
    return general()


def enabled():
    s = general()
    if not s["enabled"]:
        return False
    return PLATFORM != "win32" or s["allow_windows"]
