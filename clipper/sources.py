"""Finding videos, deciding whether they may be clipped, and never doing one twice.

Ways in:
  - a link or file you give Jarvis ("clip this podcast", the dashboard, `python -m clipper add`)
  - the inbox folder (clipper_data/inbox): drop video files in and they're picked up
  - approved sources, checked every few hours:
      twitch_channel   your own Twitch VODs (added automatically from stream mode's channel)
      youtube_channel  a YouTube channel (yours, or a creator who gave you permission)
      youtube_search   a niche search, Creative Commons videos only (through the official API)
      folder           any folder of your own videos

Authorization (nothing is processed unless one of these is true; the reason is saved):
  - a file or folder you added yourself
  - your own Twitch channel's VODs
  - your own YouTube channel (the one you connected)
  - a Creative Commons (CC BY) YouTube video (credit is added to the description)
  - a channel or video you marked "I have permission" (your say-so is recorded)
  - a link you paste or say yourself (that's your say-so too, so it starts right away)
  - anything else, unless the "ask_permission" setting is on (it's off: everything starts right away)
Protected/members-only videos are always refused, and live streams wait for their VOD.
Anything else waits in "awaiting authorization" until you approve it or drop it.
DRM, private, members-only or sub-only videos are never touched."""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from clipper import data_dir, db, settings

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts"}


class NotAllowed(Exception):
    """The source isn't authorized for clipping."""


# ---------- recognising links ----------

def classify(text):
    """-> (platform, external_id, url). platform: youtube | twitch | url | file."""
    s = str(text).strip().strip("<>\"'")
    p = os.path.expanduser(s)
    if os.path.isfile(p):
        return "file", fingerprint(p), str(Path(p).resolve())
    if not re.match(r"https?://", s, re.I):
        if re.match(r"(www\.|m\.)?(youtube\.com|youtu\.be|twitch\.tv)/", s, re.I):
            s = "https://" + s
        else:
            raise ValueError("that isn't a link or a file I can find")
    u = urlparse(s)
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in ("youtube.com", "youtu.be", "music.youtube.com"):
        vid = None
        if host == "youtu.be":
            vid = u.path.strip("/").split("/")[0]
        elif u.path.startswith(("/shorts/", "/live/", "/embed/")):
            vid = u.path.split("/")[2]
        else:
            vid = (parse_qs(u.query).get("v") or [None])[0]
        if not vid or not re.fullmatch(r"[\w-]{11}", vid):
            raise ValueError("that YouTube link isn't a single video")
        return "youtube", vid, f"https://www.youtube.com/watch?v={vid}"
    if host.endswith("twitch.tv"):
        m = re.match(r"/videos/(\d+)", u.path) or re.match(r"/[\w]+/v(?:ideo)?/(\d+)", u.path)
        if m:
            return "twitch", "v" + m.group(1), f"https://www.twitch.tv/videos/{m.group(1)}"
        m = re.match(r"/([\w]+)/clip/([\w-]+)", u.path)
        if m or host == "clips.twitch.tv":
            slug = m.group(2) if m else u.path.strip("/")
            return "twitch", "clip:" + slug, s
        raise ValueError("that Twitch link isn't a VOD or clip (it should look like twitch.tv/videos/123)")
    return "url", hashlib.sha1(s.encode()).hexdigest()[:16], s


def fingerprint(path):
    """Same file = same id, even if it's renamed or moved (size + start and end bytes)."""
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    return h.hexdigest()[:20]


# ---------- metadata (no downloading) ----------

def lookup(platform, url, channel="main"):
    """Title, creator, duration, licence... for a link, without downloading the video.
    YouTube uses the official API when YouTube is connected; otherwise yt-dlp reads the page."""
    if platform == "youtube":
        try:
            from clipper import youtube
            info = youtube.video_info(channel, url.rsplit("=", 1)[-1])
            if info:
                return info
        except Exception as e:
            print(f"(clipper: YouTube API lookup failed, trying yt-dlp: {e})")
    if platform in ("youtube", "twitch", "url"):
        from clipper import acquire
        return acquire.info(url)
    return {}


# ---------- authorization ----------

def _own_twitch(channel):
    names = {settings.jarvis_config().get("twitch_channel", "")}
    names |= {s["value"] for s in db.q("SELECT value FROM sources WHERE channel=? AND kind='twitch_channel' "
                                       "AND authorized=1 AND auth_basis LIKE 'your own%'", channel)}
    return {str(n).strip().lstrip("#").lower() for n in names if n}


def authorize_check(channel, platform, info, added_by_owner=False, source=None):
    """-> (status, basis). status: authorized | awaiting_authorization | rejected."""
    info = info or {}
    if info.get("drm") or info.get("availability") in ("subscriber_only", "premium_only", "needs_auth", "private"):
        return "rejected", "protected or members-only video (never processed)"
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "awaiting_authorization", "still live; waiting for the VOD"
    if platform == "file":
        return ("authorized", "a file you added yourself") if added_by_owner or source else \
            ("awaiting_authorization", "a file nobody vouched for")
    if source and source.get("authorized"):
        return "authorized", source.get("auth_basis") or "an approved source"
    creator_id = str(info.get("creator_id") or "").lower()
    creator = str(info.get("creator") or "").lower()
    if platform == "twitch":
        own = _own_twitch(channel)
        if creator_id in own or creator in own:
            return "authorized", "your own Twitch VOD"
    if platform == "youtube":
        ch = settings.channel(channel)
        if ch.get("youtube_channel_id") and creator_id == str(ch["youtube_channel_id"]).lower():
            return "authorized", "your own YouTube channel"
        if str(info.get("license", "")).lower() in ("creativecommon", "creative commons attribution license (reuse allowed)"):
            return "authorized", "Creative Commons (CC BY): credit added"
    for s in db.q("SELECT * FROM sources WHERE channel=? AND authorized=1 AND enabled=1", channel):
        v = s["value"].lower()
        if s["kind"] in ("youtube_channel", "twitch_channel", "creator") and v and v in (creator_id, creator):
            return "authorized", s["auth_basis"] or "an approved creator"
    if added_by_owner and not source:
        return "authorized", "a link you added yourself"     # your own paste is your say-so: starts right away
    if not settings.general().get("ask_permission"):
        return "authorized", "you chose to clip without asking first"
    return "awaiting_authorization", "not yours, not Creative Commons, and no permission recorded"


# ---------- adding videos ----------

def add(text, channel="main", added_by_owner=True, max_clips=None, priority=5, options=None, source=None,
        queue=True):
    """Adds a video (link or file). Returns (video, job_or_None, message). Never adds the same video
    twice, and never makes a second job for a video that already has one."""
    settings.ensure_default_channel()
    platform, ext_id, url = classify(text)
    existing = db.one("SELECT * FROM videos WHERE channel=? AND platform=? AND external_id=?", channel, platform, ext_id)
    if existing:
        job = db.one("SELECT * FROM jobs WHERE video_id=? AND state NOT IN ('failed','cancelled') ORDER BY id DESC",
                     existing["id"])
        if job and job["state"] == "awaiting_authorization" and added_by_owner and not source:
            grant(existing["id"], "a link you added yourself")
            return db.video(existing["id"]), db.job(job["id"]), \
                f"Got it. \"{_short(existing['title'])}\" is queued (job {job['id']})."
        if job:
            return existing, job, f"I already have that one (job {job['id']}, {job['state'].replace('_', ' ')})."
        if existing["auth_status"] != "authorized" and added_by_owner and platform == "file":
            db.run("UPDATE videos SET auth_status='authorized', auth_basis='a file you added yourself' WHERE id=?",
                   existing["id"])
            existing["auth_status"] = "authorized"
    info = {}
    if platform == "file":
        info = {"title": Path(url).stem.replace("_", " "), "creator": "you", "creator_id": "owner"}
        try:
            from clipper import media
            info["duration"] = media.probe(url)["duration"]
        except Exception as e:
            raise ValueError(f"I can't read that file as a video: {e}")
    else:
        info = lookup(platform, url, channel)
    status, basis = authorize_check(channel, platform, info, added_by_owner=added_by_owner, source=source)
    limit = settings.general()["max_video_hours"] * 3600
    if info.get("duration") and info["duration"] > limit:
        status, basis = "rejected", f"longer than {settings.general()['max_video_hours']} hours"
    if existing:
        vid = existing["id"]
        if existing["auth_status"] != "authorized":
            db.run("UPDATE videos SET auth_status=?, auth_basis=? WHERE id=?", status, basis, vid)
    else:
        vid = db.run("INSERT INTO videos(channel, platform, external_id, url, path, title, creator, creator_id, "
                     "duration, published_at, license, auth_status, auth_basis, discovery_score, source_id, info, "
                     "discovered) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     channel, platform, ext_id, url, url if platform == "file" else None,
                     info.get("title") or url, info.get("creator"), info.get("creator_id"), info.get("duration"),
                     info.get("published_at"), info.get("license"), status, basis, score_video(info, channel),
                     source["id"] if source else None, json.dumps({k: v for k, v in info.items() if k != "raw"}),
                     time.time())
    video = db.video(vid)
    if status == "rejected":
        db.log(f"Skipped \"{video['title']}\": {basis}.", "warn")
        return video, None, f"I can't clip that one: {basis}."
    if not queue:
        return video, None, "Saved."
    job = make_job(video, max_clips=max_clips, priority=priority, options=options)
    if status != "authorized":
        db.log(f"\"{video['title']}\" needs your OK before I clip it ({basis}).", "warn", job_id=job["id"], tell=True)
        return video, job, (f"I found \"{_short(video['title'])}\", but it isn't yours and isn't Creative Commons, "
                            "so I need you to confirm you have permission before I clip it.")
    return video, job, f"Got it. \"{_short(video['title'])}\" is queued (job {job['id']})."


def _short(title, n=60):
    title = str(title or "that video")
    return title if len(title) <= n else title[:n - 1].rsplit(" ", 1)[0] + "..."


def make_job(video, max_clips=None, priority=5, options=None):
    existing = db.one("SELECT * FROM jobs WHERE video_id=? AND state NOT IN ('failed','cancelled')", video["id"])
    if existing:
        return existing
    ch = settings.channel(video["channel"])
    state = "queued" if video["auth_status"] == "authorized" else "awaiting_authorization"
    now = time.time()
    jid = db.run("INSERT INTO jobs(video_id, channel, state, priority, max_clips, options, created, updated) "
                 "VALUES(?,?,?,?,?,?,?,?)", video["id"], video["channel"], state, priority,
                 int(max_clips or ch["max_clips_per_video"]), json.dumps(options or {}), now, now)
    db.log(f"New job for \"{_short(video['title'])}\" ({state.replace('_', ' ')}).", job_id=jid)
    return db.one("SELECT * FROM jobs WHERE id=?", jid)


def release_waiting():
    """With ask_permission off, projects still waiting for an OK start (live streams keep waiting)."""
    if settings.general().get("ask_permission"):
        return 0
    n = 0
    for v in db.q("SELECT * FROM videos WHERE auth_status='awaiting_authorization'"):
        if "still live" in (v.get("auth_basis") or ""):
            continue
        grant(v["id"], "you chose to clip without asking first")
        n += 1
    return n


def grant(video_id, basis="you confirmed you have permission"):
    """The owner says this video may be clipped."""
    v = db.video(video_id)
    if not v:
        raise KeyError("no such video")
    db.run("UPDATE videos SET auth_status='authorized', auth_basis=? WHERE id=?", basis, video_id)
    db.run("UPDATE jobs SET state='queued', updated=? WHERE video_id=? AND state='awaiting_authorization'",
           time.time(), video_id)
    db.log(f"Authorized \"{_short(v['title'])}\": {basis}.")
    return f"Okay, \"{_short(v['title'])}\" is approved and queued."


def add_source(kind, value, channel="main", authorized=False, basis=None, label=None):
    kinds = ("twitch_channel", "youtube_channel", "youtube_search", "folder", "creator")
    if kind not in kinds:
        raise ValueError(f"source kind must be one of {', '.join(kinds)}")
    value = str(value).strip()
    if kind == "twitch_channel":
        value = re.sub(r"^(https?://)?(www\.)?twitch\.tv/", "", value).strip("/").lower()
    if kind == "folder":
        value = str(Path(os.path.expanduser(value)).resolve())
        authorized, basis = True, basis or "a folder of your own videos"
    if kind == "youtube_search":
        authorized, basis = True, "search limited to Creative Commons videos"
    if authorized and not basis:
        basis = "you confirmed you have permission"
    db.run("INSERT INTO sources(channel, kind, value, label, authorized, auth_basis, created) VALUES(?,?,?,?,?,?,?) "
           "ON CONFLICT(channel, kind, value) DO UPDATE SET authorized=excluded.authorized, "
           "auth_basis=excluded.auth_basis, enabled=1", channel, kind, value, label or value, int(bool(authorized)),
           basis, time.time())
    db.log(f"Added source {kind} {value}" + (f" ({basis})" if authorized else " (not authorized yet)"))
    return db.one("SELECT * FROM sources WHERE channel=? AND kind=? AND value=?", channel, kind, value)


# ---------- discovery ----------

def score_video(info, channel="main"):
    """0-10: how promising a long video looks for Shorts, before downloading it."""
    if not info:
        return 5.0
    s = 5.0
    d = float(info.get("duration") or 0)
    if d:
        s += 1.5 if 15 * 60 <= d <= 4 * 3600 else (0.5 if d >= 5 * 60 else -2.0)
    views = float(info.get("views") or 0)
    if views:
        import math
        s += min(2.0, max(-1.0, (math.log10(views + 1) - 3) * 0.6))
    likes = float(info.get("likes") or 0)
    if views and likes:
        s += min(1.0, likes / views * 25)
    text = f"{info.get('title', '')} {info.get('description', '')[:500]}".lower()
    niches = [n.lower() for n in settings.channel(channel).get("niches", [])]
    words = {"podcasts": ["podcast", "episode", "interview", "conversation"], "gaming": ["stream", "gameplay", "vod"],
             "business and entrepreneurship": ["business", "startup", "founder"], "motivation": ["motivat", "mindset"],
             "technology and ai": ["ai ", "tech", "software"], "finance and investing": ["invest", "money", "stock"]}
    for n in niches:
        if any(w in text for w in words.get(n, [n])):
            s += 0.8
            break
    return round(max(0.0, min(10.0, s)), 2)


def scan_inbox(channel="main"):
    """Video files dropped into clipper_data/inbox (or an approved folder) become jobs."""
    added = []
    folders = [(data_dir("inbox"), None)]
    folders += [(Path(s["value"]), s) for s in db.q("SELECT * FROM sources WHERE channel=? AND kind='folder' AND enabled=1", channel)]
    for folder, src in folders:
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in VIDEO_EXT or f.name.startswith("."):
                continue
            try:
                if time.time() - f.stat().st_mtime < 60:      # still being copied in
                    continue
                if db.one("SELECT id FROM videos WHERE channel=? AND platform='file' AND external_id=?",
                          channel, fingerprint(str(f))):
                    continue
                v, job, msg = add(str(f), channel, added_by_owner=True, source=src, priority=4)
                if job:
                    added.append(v)
            except Exception as e:
                db.log(f"Couldn't add {f.name}: {e}", "warn")
    return added


def discover(channel="main", limit_per_source=5):
    """Checks every approved source for new videos. Returns the new videos found."""
    if not settings.allowed(channel, "discover"):
        return []
    found = scan_inbox(channel)
    auto = settings.allowed(channel, "auto_process")
    for s in db.q("SELECT * FROM sources WHERE channel=? AND enabled=1 AND kind!='folder'", channel):
        try:
            items = _list_source(channel, s, limit_per_source)
        except Exception as e:
            db.log(f"Couldn't check {s['label'] or s['value']}: {e}", "warn")
            continue
        db.run("UPDATE sources SET last_checked=? WHERE id=?", time.time(), s["id"])
        for item in items:
            try:
                platform, ext_id, url = classify(item["url"])
            except ValueError:
                continue
            if db.one("SELECT id FROM videos WHERE channel=? AND platform=? AND external_id=?", channel, platform, ext_id):
                continue
            info = item if item.get("full") else (lookup(platform, url, channel) or item)
            if info.get("duration") and float(info["duration"]) < 4 * 60:
                continue                       # already short
            status, basis = authorize_check(channel, platform, info, source=s if s["authorized"] else None)
            vid = db.run("INSERT OR IGNORE INTO videos(channel, platform, external_id, url, title, creator, creator_id, "
                         "duration, published_at, license, auth_status, auth_basis, discovery_score, source_id, info, "
                         "discovered) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", channel, platform, ext_id, url,
                         info.get("title") or url, info.get("creator"), info.get("creator_id"), info.get("duration"),
                         info.get("published_at"), info.get("license"), status, basis, score_video(info, channel),
                         s["id"], json.dumps({k: v for k, v in info.items() if k != "raw"}), time.time())
            v = db.video(vid)
            found.append(v)
            if status == "authorized" and auto and v["discovery_score"] >= 4:
                make_job(v, priority=3)
            elif status == "awaiting_authorization":
                db.log(f"Found \"{_short(v['title'])}\" from {s['label']}; it needs your OK.", "info")
    if found:
        db.log(f"Discovery found {len(found)} new video{'s' if len(found) != 1 else ''}.")
    return found


def _list_source(channel, s, limit):
    kind, value = s["kind"], s["value"]
    from clipper import acquire
    if kind == "twitch_channel":
        return acquire.list_playlist(f"https://www.twitch.tv/{value}/videos?filter=archives&sort=time", limit)
    if kind == "youtube_channel":
        try:
            from clipper import youtube
            items = youtube.channel_videos(channel, value, limit)
            if items is not None:
                return items
        except Exception as e:
            print(f"(clipper: YouTube API listing failed, trying yt-dlp: {e})")
        url = value if value.startswith("http") else (
            f"https://www.youtube.com/channel/{value}/videos" if value.startswith("UC") else
            f"https://www.youtube.com/@{value.lstrip('@')}/videos")
        return acquire.list_playlist(url, limit)
    if kind == "youtube_search":
        from clipper import youtube
        return youtube.search_cc(channel, value, limit) or []
    return []


def latest_twitch_vod(channel="main"):
    """Your most recent Twitch VOD (for "clip my last stream")."""
    own = sorted(_own_twitch(channel))
    if not own:
        return None
    from clipper import acquire
    items = acquire.list_playlist(f"https://www.twitch.tv/{own[0]}/videos?filter=archives&sort=time", 1)
    return items[0]["url"] if items else None
