"""Reading PUBLIC YouTube data for research, through the official YouTube Data API v3 only.

Credential (read-only, free): a Google Cloud API key saved with save_key() in
studio_data/secrets/youtube_api_key (readable only by Connor's user, never logged). If there's no
key, a channel Connor already connected to the clipper is used for read-only calls. Nothing here
can write to any channel.

Quota: calls are counted in the same daily buckets as the clipper (clipper.youtube._spend):
search.list has its own 100-a-day bucket; the rest cost 1 unit of 10,000."""
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from studio import data_dir, guard

API = "https://www.googleapis.com/youtube/v3"


class NoCredential(Exception):
    pass


def _key_path():
    return data_dir("secrets") / "youtube_api_key"


def save_key(key):
    key = key.strip()
    if not key.startswith("AIza") or len(key) < 30:
        raise ValueError("that doesn't look like a Google API key (they start with AIza)")
    p = _key_path()
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    os.chmod(str(p), stat.S_IRUSR | stat.S_IWUSR)
    guard.audit("youtube.api_key.saved", True, "read-only research key saved")
    return True


def _auth():
    p = _key_path()
    if p.exists() and p.read_text().strip():
        return {"key": p.read_text().strip()}, {}
    try:
        from clipper import db as clip_db, youtube
        for r in clip_db.q("SELECT id FROM channels"):
            if youtube.connected(r["id"]):
                return {}, {"Authorization": f"Bearer {youtube.access_token(r['id'])}"}
    except Exception:
        pass
    raise NoCredential("no YouTube research key yet (double-click \"Research YouTube Niche\" to add one)")


def available():
    try:
        _auth()
        return True
    except NoCredential:
        return False


FETCH = None        # tests swap this: (url) -> dict


def get(resource, params, kind="list"):
    guard.allow("youtube.read_public", f"{resource} {params.get('q', '')}".strip(), record=False)
    from clipper import youtube
    youtube._spend(kind)
    q, headers = _auth()
    url = f"{API}/{resource}?" + urllib.parse.urlencode({**params, **q})
    if FETCH:
        return FETCH(url)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=youtube._ctx()) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 403 and "quota" in body.lower():
                raise youtube.QuotaExceeded("YouTube's daily API quota is used up")
            if e.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(guard.redact(f"YouTube API {e.code}: {body[:300]}"))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def search_shorts(query, days=30, order="viewCount", limit=50):
    """Recent short videos for a query (US, English). [{video_id, channel_id, title, published}]"""
    r = get("search", {"part": "snippet", "q": query, "type": "video", "videoDuration": "short",
                       "regionCode": "US", "relevanceLanguage": "en", "order": order, "maxResults": min(limit, 50),
                       "publishedAfter": _iso(time.time() - days * 86400), "safeSearch": "moderate"}, kind="search")
    return [{"video_id": it["id"]["videoId"], "channel_id": it["snippet"]["channelId"],
             "title": it["snippet"]["title"], "published": it["snippet"]["publishedAt"]}
            for it in r.get("items", []) if it.get("id", {}).get("videoId")]


def _parse_duration(s):
    from clipper import youtube
    return youtube._parse_iso_duration(s)


def videos(ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = get("videos", {"part": "statistics,contentDetails,snippet", "id": ",".join(ids[i:i + 50])})
        for it in r.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"views": int(st.get("viewCount", 0) or 0),
                             "likes": int(st["likeCount"]) if "likeCount" in st else None,
                             "comments": int(st["commentCount"]) if "commentCount" in st else None,
                             "seconds": _parse_duration(it.get("contentDetails", {}).get("duration", "PT0S")),
                             "channel_id": it["snippet"]["channelId"], "title": it["snippet"]["title"],
                             "published": it["snippet"]["publishedAt"]}
    return out


def channels(ids):
    out = {}
    for i in range(0, len(ids), 50):
        r = get("channels", {"part": "statistics,snippet,contentDetails", "id": ",".join(ids[i:i + 50])})
        for it in r.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"title": it["snippet"]["title"], "created": it["snippet"].get("publishedAt"),
                             "country": it["snippet"].get("country"),
                             "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0),
                             "views": int(st.get("viewCount", 0) or 0), "videos": int(st.get("videoCount", 0) or 0),
                             "uploads": it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")}
    return out


def recent_uploads(playlist, limit=30):
    r = get("playlistItems", {"part": "contentDetails", "playlistId": playlist, "maxResults": min(limit, 50)})
    return [it["contentDetails"]["videoId"] for it in r.get("items", [])]
