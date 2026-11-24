"""The studio's permission layer. Every outside action goes through allow() first.

  actions   a short allow-list. Anything not on it is refused, including everything in
            config.RULES["never"] (money, purchases, sponsors, affiliates, comments...), whatever
            a setting, a voice command or a Claude answer asks for.
  scopes    only the three Google permissions the business needs; a request for any other
            (for example comment posting or revenue data) is refused before sign-in.
  channel   the studio is locked to ONE YouTube channel id, set once when Connor connects the new
            channel. Every upload checks the signed-in channel against the lock, and also against
            the protected list (Connor's existing channel), and refuses on any mismatch.
  $0 tools  every tool the studio uses is registered with its licence and cost; anything that
            costs money, or has no registration, can't be used. Each tool has a circuit breaker.
  audit     sensitive actions are recorded in the audit table, with secrets removed."""
import json
import re
import time

from studio import config, db


class Forbidden(Exception):
    """An action the studio is never allowed to take."""


class WrongChannel(Forbidden):
    pass


ACTIONS = {
    "youtube.read_channel": "read which channel the sign-in belongs to",
    "youtube.read_public": "read public channel and video statistics for research",
    "youtube.upload": "upload a finished, validated Short to the locked channel",
    "youtube.set_video": "set title, description, privacy or schedule of the studio's own video",
    "youtube.set_thumbnail": "set the cover image of the studio's own uploaded Short",
    "youtube.read_video": "read the processing status of the studio's own video",
    "youtube.analytics": "read the locked channel's own analytics (no revenue)",
    "youtube.playlist": "make the studio's series playlists and add its own public Shorts to them",
    "claude.ask": "ask Claude Code on Connor's plan, with no file or shell tools",
    "web.read": "read a public web page or open-data API for research and fact-checks",
    "media.local": "make audio, images and video on this Mac",
    "drive.backup": "copy finished Shorts and reports to Connor's own Google Drive (only files Jarvis made)",
}

SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly",
          # playlists (Connor approved 2026-11-23). Google's playlist permission also covers comments;
          # comment actions stay refused by the never-list above, whatever the permission allows.
          "https://www.googleapis.com/auth/youtube")

# Never registered as tools, whatever their price page says today.
BANNED_TOOLS = ("elevenlabs", "openai", "anthropic-api", "midjourney", "runway", "pictory", "invideo",
                "synthesia", "heygen", "shutterstock", "getty", "epidemic", "artlist", "pytrends")

_SECRET = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]{10,}|(?i:bearer)\s+[\w.-]{10,}|"
                     r"(\"?(?:access_token|refresh_token|client_secret|id_token|code_verifier)\"?\s*[:=]\s*\"?)[^\",\s}]+)")


def redact(text):
    """The text with OAuth tokens and secrets replaced by [hidden]."""
    def fix(m):
        return (m.group(2) or "") + "[hidden]"
    return _SECRET.sub(fix, str(text))


def audit(action, allowed, detail=""):
    if not isinstance(detail, str):
        detail = json.dumps(detail, default=str)
    db.insert("INSERT INTO audit(t, action, allowed, detail) VALUES(?,?,?,?)",
              time.time(), action, 1 if allowed else 0, redact(detail)[:2000])


def _never_word(action):
    a = action.lower()
    return next((w for w in config.RULES["never"] if w in a), None)


def allow(action, detail="", record=True):
    """Raises Forbidden unless the action is on the allow-list. Returns True."""
    bad = _never_word(action)
    if bad or action not in ACTIONS:
        audit(action, False, detail)
        why = f"it involves {bad.replace('_', ' ')}, which Connor ruled out" if bad else "it isn't on the studio's allow-list"
        raise Forbidden(f"The studio can't do \"{action}\": {why}.")
    if record:
        audit(action, True, detail)
    return True


def check_scopes(scopes):
    extra = [s for s in scopes if s not in SCOPES]
    if extra:
        audit("google.scopes", False, {"refused": extra})
        raise Forbidden("The studio only asks Google for upload, read-only channel, read-only analytics and "
                        f"playlist permission; refused: {', '.join(extra)}")
    return True


# ---------- the channel lock ----------

# Connor's own, existing channel (Sauce, @sauce2010). Written into the code so that no setting, sign-in
# or reset can ever take it off the never-publish list. The business channel is never on this list.
PERMANENT_PROTECTED = ("UCamLvdxlcfg3YqISoj4jlcA",)


def protected_channels():
    """Channel ids the studio must never publish to: Connor's existing channel(s). The permanent list
    above, plus config "studio": {"protected_channels": [...]} and the clipper's connected channels."""
    ids = set(PERMANENT_PROTECTED) | set(config.general().get("protected_channels") or [])
    try:
        from clipper import db as clip_db
        if clip_db.path().exists():
            ids.update(r["youtube_channel_id"] for r in clip_db.q("SELECT youtube_channel_id FROM channels")
                       if r["youtube_channel_id"])
    except Exception:
        pass
    return ids


def locked_channel():
    r = db.one("SELECT youtube_channel_id, title FROM channel WHERE id=1")
    return r if r and r["youtube_channel_id"] else None


def lock_channel(youtube_channel_id, title=""):
    """Saves the dedicated channel once. Refuses a protected channel or a different second channel."""
    if not youtube_channel_id:
        raise WrongChannel("No channel id was given.")
    if youtube_channel_id in protected_channels():
        audit("channel.lock", False, {"channel": youtube_channel_id, "why": "protected"})
        raise WrongChannel(f"\"{title or youtube_channel_id}\" is your existing channel. The business needs its own "
                           "new channel; sign in with the new channel instead.")
    cur = locked_channel()
    if cur and cur["youtube_channel_id"] != youtube_channel_id:
        audit("channel.lock", False, {"channel": youtube_channel_id, "locked": cur["youtube_channel_id"]})
        raise WrongChannel(f"The studio is already locked to \"{cur['title']}\". It only ever runs one channel.")
    db.run("INSERT INTO channel(id, youtube_channel_id, title, locked_at, updated) VALUES(1,?,?,?,?) "
           "ON CONFLICT(id) DO UPDATE SET youtube_channel_id=excluded.youtube_channel_id, title=excluded.title, "
           "locked_at=COALESCE(channel.locked_at, excluded.locked_at), updated=excluded.updated",
           youtube_channel_id, title, time.time(), time.time())
    audit("channel.lock", True, {"channel": youtube_channel_id, "title": title})
    return True


def verify_destination(signed_in_channel_id):
    """Called right before every upload with the channel the sign-in belongs to right now."""
    cur = locked_channel()
    if not cur:
        raise WrongChannel("The studio has no dedicated channel connected yet, so nothing can be uploaded.")
    protected = protected_channels()
    if not protected:
        audit("youtube.upload.destination", False, {"why": "empty protected list"})
        raise WrongChannel("The never-publish list is empty, which should be impossible, so I stopped the upload.")
    if cur["youtube_channel_id"] in protected:
        audit("youtube.upload.destination", False, {"locked": cur["youtube_channel_id"], "why": "lock is protected"})
        raise WrongChannel("The locked channel is on the never-publish list, so I stopped the upload.")
    state, changed = integrity()
    if state == "changed":
        audit("youtube.upload.destination", False, {"why": "guard files changed", "files": changed})
        raise WrongChannel("The channel-safety code on this computer differs from Jarvis's GitHub copy ("
                           + ", ".join(changed) + "), so uploads are stopped until it matches again.")
    if not signed_in_channel_id or signed_in_channel_id in protected or signed_in_channel_id != cur["youtube_channel_id"]:
        audit("youtube.upload.destination", False, {"signed_in": signed_in_channel_id, "locked": cur["youtube_channel_id"]})
        raise WrongChannel("The signed-in YouTube channel isn't the business channel, so I stopped the upload.")
    return True


# ---------- the safety code itself ----------

# The files that decide where Shorts may go and what the studio may do. Jarvis's self-editing (voice
# edits, night school, self-repair) may never change them (selfedit._check refuses), and before every
# upload they must match a version that came from GitHub.
GUARDED_FILES = ("studio/guard.py", "studio/publish.py", "studio/ytauth.py", "studio/config.py",
                 "studio/backup.py", "selfedit.py", "nightschool.py", "repair.py")
INTEGRITY = None            # tests swap this: () -> (state, changed files)
_integrity_cache = {}


def integrity(max_age=600):
    """("ok" | "changed" | "unknown", [changed files]). "ok": every guarded file is exactly a version from
    the GitHub branch the updater installs (its tip or one of its last 40 changes). "unknown": there's no
    git or no GitHub copy yet to compare with (a fresh zip install), which doesn't stop uploads."""
    if INTEGRITY:
        return INTEGRITY()
    hit = _integrity_cache.get("v")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    res = _integrity_now()
    _integrity_cache["v"] = (time.time(), res)
    return res


def _blob(data):
    """git's id for a file's content, with Windows line endings counted as plain ones (a copy unpacked
    from the install zip may have them; that's not a change)."""
    import hashlib
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _integrity_now():
    try:
        import updater
        ref = updater.REMOTE_REF
        if updater._git("rev-parse", "--verify", "-q", ref).returncode != 0:
            return "unknown", []
        files = [f for f in GUARDED_FILES if (updater.HERE / f).exists()]
        local = {f: _blob((updater.HERE / f).read_bytes()) for f in files}
        known = {f: set() for f in files}
        tip = updater._git("ls-tree", ref, "--", *files)
        for line in tip.stdout.splitlines():
            meta, _, path = line.partition("\t")
            if path in known:
                known[path].add(meta.split()[2])
        hist = updater._git("log", "-n", "40", "--raw", "--no-abbrev", "--format=", ref, "--", *files)
        for line in hist.stdout.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if path in known and len(parts) >= 4:
                known[path].add(parts[3])
        changed = [f for f in files if local.get(f) not in known[f]]
        return ("changed" if changed else "ok"), changed
    except Exception:
        return "unknown", []


# ---------- the $0 tool registry ----------

def register_tool(name, purpose, license, local, needs_key=False, cost_usd=0, limits="", status="ok"):
    low = name.lower()
    if any(b in low for b in BANNED_TOOLS) or float(cost_usd or 0) != 0:
        audit("tool.register", False, {"tool": name, "cost_usd": cost_usd})
        raise Forbidden(f"{name} can't be used: the studio's budget is $0.")
    db.run("INSERT INTO tools(name, purpose, license, local, needs_key, cost_usd, limits, verified, status) "
           "VALUES(?,?,?,?,?,0,?,?,?) ON CONFLICT(name) DO UPDATE SET purpose=excluded.purpose, license=excluded.license, "
           "local=excluded.local, needs_key=excluded.needs_key, limits=excluded.limits, verified=excluded.verified, "
           "status=excluded.status", name, purpose, license, 1 if local else 0, 1 if needs_key else 0, limits,
           time.time(), status)
    return True


def tool_usable(name):
    """(usable, reason). Unregistered tools and tools with an open circuit breaker aren't usable."""
    t = db.one("SELECT * FROM tools WHERE name=?", name)
    if not t:
        return False, f"{name} isn't a registered $0 tool"
    if t["cost_usd"]:
        return False, f"{name} costs money"
    if t["open_until"] and t["open_until"] > time.time():
        return False, f"{name} failed {t['fails']} times in a row; resting until {time.strftime('%H:%M', time.localtime(t['open_until']))}"
    return True, ""


def tool_result(name, ok, error=""):
    """Feeds the circuit breaker: enough failures in a row rest the tool for a while."""
    g = config.general()
    if ok:
        db.run("UPDATE tools SET fails=0, open_until=0, last_error=NULL WHERE name=?", name)
        return
    t = db.one("SELECT fails FROM tools WHERE name=?", name) or {"fails": 0}
    fails = int(t["fails"] or 0) + 1
    until = time.time() + 60 * g["breaker_cooldown_minutes"] if fails >= g["breaker_failures"] else 0
    db.run("UPDATE tools SET fails=?, open_until=?, last_error=? WHERE name=?", fails, until, redact(error)[:500], name)


# Registered at start-up. Phase 2 re-verifies each against the installed files.
BUILT_IN_TOOLS = [
    ("claude-code", "research, scripts, fact-checks, metadata", "Anthropic consumer terms (Connor's existing plan)", False, False,
     "plan usage limits shared with Jarvis; no API key"),
    ("kokoro", "narration", "Apache-2.0 weights, MIT kokoro-onnx", True, False, "none"),
    ("faster-whisper", "word timing for captions", "MIT", True, False, "none"),
    ("ffmpeg", "editing, encoding, loudness", "LGPL/GPL (imageio-ffmpeg build)", True, False, "none"),
    ("pillow", "original graphics", "MIT-CMU", True, False, "none"),
    ("sqlite", "database", "public domain", True, False, "none"),
    ("youtube-data-api", "upload, channel check, public research", "Google APIs terms", False, True,
     "100 uploads + 100 searches + 10,000 units a day; no billing account"),
    ("youtube-analytics-api", "the channel's own analytics", "Google APIs terms", False, True, "free quota; no revenue scope"),
    ("wikipedia-api", "fact-check sources", "free API; text CC BY-SA, cited not copied", False, False, "polite rate"),
    ("wikimedia-commons", "licensed images", "per-file licence, recorded", False, False, "polite rate"),
    ("chatterbox", "expressive narration in the channel's voice (optional)", "MIT (Resemble AI)", True, False,
     "needs ~3 GB free memory; Kokoro is the fallback"),
    ("whisperx", "forced alignment of caption words (optional)", "BSD-2-Clause", True, False, "none"),
    ("manim", "animated numbers, equations and charts (optional)", "MIT (Manim Community)", True, False, "none"),
    ("remotion", "animated captions and motion (optional)", "Remotion licence: free for individuals and companies "
     "of up to 3 people", True, False, "Connor uses it as one person; no paid company licence needed"),
    ("google-drive-api", "backups of finished Shorts", "Google APIs terms (free; Connor's free 15 GB)", False, True,
     "drive.file permission only: sees just the files Jarvis made"),
]


def register_built_ins():
    for name, purpose, lic, local, key, limits in BUILT_IN_TOOLS:
        register_tool(name, purpose, lic, local, key, 0, limits)
