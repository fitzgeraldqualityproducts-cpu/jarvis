"""YouTube, through Google's official APIs only (Data API v3 + Analytics API v2).

Sign-in: "Connect YouTube" (double-click) runs the standard Google sign-in for desktop apps:
your browser opens Google's own page, you pick the channel and press Allow, and Google sends the
answer back to this computer only (http://127.0.0.1, with PKCE). Jarvis never sees your password.
Tokens are stored per clipper channel in clipper_data/secrets/youtube_<channel>.json, readable only
by your user, refreshed automatically, and never logged or sent anywhere else.

What it needs once (free): a Google Cloud "OAuth client" of type Desktop app, with the YouTube Data
API v3 and YouTube Analytics API turned on. Its JSON file goes in clipper_data/secrets/google_client.json
(Connect YouTube asks for it). See README "Shorts Clipper" for click-by-click steps.

Heads-up from Google's rules: videos uploaded through a new app that Google hasn't audited are
locked to private. Private and unlisted work immediately; public publishing needs Google's free
API audit for your project. Jarvis notices the lock and tells you instead of pretending.

Uploads are resumable (8 MB pieces; a dropped connection or restart continues from the last piece),
retried with exponential backoff on network/5xx errors, and stop cleanly on quota errors. Every API
call is counted against the daily quota so Jarvis never burns through it."""
import base64
import datetime
import hashlib
import http.server
import json
import os
import secrets
import socket
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from clipper import data_dir, db, settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
COSTS = {"list": 1, "search": 100, "insert": 1600, "update": 50, "analytics": 0}   # Analytics has its own quota
CHUNK = 8 << 20


class YouTubeError(Exception):
    def __init__(self, msg, status=None, reason=None):
        super().__init__(msg)
        self.status, self.reason = status, reason


class QuotaExceeded(YouTubeError):
    pass


class NotConnected(YouTubeError):
    pass


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


# ---------- secrets ----------

def secrets_dir():
    d = data_dir("secrets")
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:
        pass
    return d


def _write_secret(path, obj):
    tmp = str(path) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def client():
    """The OAuth client (from Google Cloud). Environment variables override the file."""
    cid, sec = os.environ.get("JARVIS_GOOGLE_CLIENT_ID"), os.environ.get("JARVIS_GOOGLE_CLIENT_SECRET")
    if cid and sec:
        return {"client_id": cid, "client_secret": sec}
    p = secrets_dir() / "google_client.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    data = data.get("installed") or data.get("web") or data
    if not data.get("client_id"):
        return None
    return {"client_id": data["client_id"], "client_secret": data.get("client_secret", "")}


def save_client(path_or_json):
    """Stores the OAuth client JSON downloaded from Google Cloud (a file path or the JSON text)."""
    text = path_or_json
    if os.path.isfile(os.path.expanduser(str(path_or_json))):
        with open(os.path.expanduser(path_or_json), encoding="utf-8") as f:
            text = f.read()
    data = json.loads(text)
    inner = data.get("installed") or data.get("web") or data
    if not inner.get("client_id") or not inner.get("client_secret"):
        raise ValueError("that isn't a Google OAuth client file (no client_id/client_secret)")
    _write_secret(secrets_dir() / "google_client.json", data)
    return inner["client_id"]


def _token_path(channel):
    return secrets_dir() / f"youtube_{channel}.json"


def connected(channel="main"):
    return _token_path(channel).exists()


def _tokens(channel):
    p = _token_path(channel)
    if not p.exists():
        raise NotConnected("YouTube isn't connected yet. Double-click Connect YouTube in Jarvis's folder.")
    return json.loads(p.read_text())


# ---------- sign-in ----------

def sign_in(channel="main", open_browser=True, timeout=300, port=0):
    """The desktop sign-in flow (loopback redirect + PKCE). Returns the connected channel's title."""
    cl = client()
    if not cl:
        raise NotConnected("I need the Google OAuth client file first (see Connect YouTube).")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if (q.get("state") or [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            got["code"] = (q.get("code") or [None])[0]
            got["error"] = (q.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Jarvis is connected to YouTube. You can close this tab." if got["code"] else \
                "YouTube wasn't connected. You can close this tab and try again."
            self.wfile.write(f"<html><body style='font:20px system-ui;background:#05121a;color:#9ef;padding:40px'>"
                             f"{msg}</body></html>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cl["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    print("Opening Google's sign-in page. If it doesn't open, paste this into your browser:\n" + url)
    if open_browser:
        webbrowser.open(url)
    srv.timeout = 1
    end = time.time() + timeout
    while "code" not in got and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if not got.get("code"):
        raise YouTubeError(f"sign-in didn't finish ({got.get('error') or 'timed out'})")
    tok = _post_form(TOKEN_URL, {"code": got["code"], "client_id": cl["client_id"], "client_secret": cl["client_secret"],
                                 "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise YouTubeError("Google didn't give a refresh token; remove Jarvis's access at myaccount.google.com and try again")
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
    _write_secret(_token_path(channel), tok)
    me = identify(channel)
    return me.get("title") or "your channel"


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise YouTubeError(f"Google sign-in error {e.code}: {body}", e.code)


def access_token(channel="main"):
    tok = _tokens(channel)
    if time.time() < float(tok.get("expires_at", 0)):
        return tok["access_token"]
    cl = client()
    if not cl:
        raise NotConnected("the Google OAuth client file is missing")
    try:
        new = _post_form(TOKEN_URL, {"refresh_token": tok["refresh_token"], "client_id": cl["client_id"],
                                     "client_secret": cl["client_secret"], "grant_type": "refresh_token"})
    except YouTubeError as e:
        if e.status in (400, 401):
            raise NotConnected("Google signed Jarvis out of YouTube (that happens weekly while the Google app is in "
                               "'Testing'). Double-click Connect YouTube again.")
        raise
    tok.update(access_token=new["access_token"], expires_at=time.time() + float(new.get("expires_in", 3600)) - 60)
    if new.get("refresh_token"):
        tok["refresh_token"] = new["refresh_token"]
    _write_secret(_token_path(channel), tok)
    return tok["access_token"]


def disconnect(channel="main"):
    p = _token_path(channel)
    if p.exists():
        p.unlink()
    db.run("UPDATE channels SET youtube_channel_id=NULL, youtube_title=NULL WHERE id=?", channel)


# ---------- quota ----------

def _pacific_day():
    # YouTube's quota resets at midnight Pacific time
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


def quota_used():
    return int(db.get(f"quota_{_pacific_day()}", 0) or 0)


def quota_left():
    return int(settings.general()["youtube_daily_quota"]) - quota_used()


def _spend(kind):
    cost = int(settings.general()["youtube_upload_cost"]) if kind == "insert" else COSTS.get(kind, 1)
    if db.get(f"quota_out_{_pacific_day()}", False):
        raise QuotaExceeded("YouTube's daily API quota is used up; it resets at midnight Pacific time")
    if quota_used() + cost > int(settings.general()["youtube_daily_quota"]):
        raise QuotaExceeded("this would go over today's YouTube API quota; waiting until it resets")
    db.put(f"quota_{_pacific_day()}", quota_used() + cost)


# ---------- requests ----------

def call(channel, method, url, params=None, body=None, kind="list", headers=None, raw=False, data=None):
    _spend(kind)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"Authorization": f"Bearer {access_token(channel)}"}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json; charset=UTF-8"
    h.update(headers or {})
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ctx()) as r:
                return r if raw else json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            reason = _reason(text)
            if e.code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"):
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    db.put(f"quota_out_{_pacific_day()}", True)
                    raise QuotaExceeded("YouTube's daily API quota is used up", e.code, reason)
            if e.code in (500, 502, 503, 504) or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
                time.sleep(min(60, 2 ** attempt + secrets.randbelow(1000) / 1000))
                continue
            if e.code == 401 and attempt == 0:
                tok = _tokens(channel)
                tok["expires_at"] = 0
                _write_secret(_token_path(channel), tok)
                h["Authorization"] = f"Bearer {access_token(channel)}"
                continue
            raise YouTubeError(f"YouTube said {e.code} {reason or ''}: {text[:300]}", e.code, reason)
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            time.sleep(min(60, 2 ** attempt))
            last = e
            continue
    raise YouTubeError(f"YouTube couldn't be reached: {locals().get('last', 'repeated server errors')}")


def _reason(text):
    try:
        errs = json.loads(text).get("error", {})
        return (errs.get("errors") or [{}])[0].get("reason") or errs.get("status")
    except Exception:
        return None


def identify(channel="main"):
    """Which YouTube channel the sign-in belongs to; saved so clips can't go to the wrong one."""
    r = call(channel, "GET", f"{API}/channels", {"part": "snippet,contentDetails", "mine": "true"})
    items = r.get("items") or []
    if not items:
        raise YouTubeError("that Google account has no YouTube channel")
    it = items[0]
    db.run("UPDATE channels SET youtube_channel_id=?, youtube_title=? WHERE id=?", it["id"], it["snippet"]["title"], channel)
    db.log(f"Connected YouTube channel \"{it['snippet']['title']}\" to clipper channel {channel}.")
    return {"id": it["id"], "title": it["snippet"]["title"],
            "uploads": it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")}


# ---------- reading (discovery) ----------

def _parse_iso_duration(s):
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def video_info(channel, video_id):
    if not connected(channel):
        return None
    r = call(channel, "GET", f"{API}/videos", {"part": "snippet,contentDetails,status,statistics", "id": video_id})
    items = r.get("items") or []
    if not items:
        return {"availability": "private"}
    v = items[0]
    sn, st, stats = v["snippet"], v.get("status", {}), v.get("statistics", {})
    return {"title": sn.get("title"), "creator": sn.get("channelTitle"), "creator_id": sn.get("channelId"),
            "duration": _parse_iso_duration(v.get("contentDetails", {}).get("duration")),
            "license": st.get("license"), "views": int(stats.get("viewCount", 0) or 0),
            "likes": int(stats.get("likeCount", 0) or 0), "published_at": sn.get("publishedAt"),
            "description": (sn.get("description") or "")[:2000],
            "availability": "public" if st.get("privacyStatus") == "public" else st.get("privacyStatus"),
            "live_status": sn.get("liveBroadcastContent"), "url": f"https://www.youtube.com/watch?v={video_id}", "full": True}


def channel_videos(channel, yt_channel, limit=5):
    """Newest uploads of a YouTube channel (None if YouTube isn't connected)."""
    if not connected(channel):
        return None
    cid = yt_channel
    if not cid.startswith("UC"):
        r = call(channel, "GET", f"{API}/channels", {"part": "contentDetails", "forHandle": "@" + cid.lstrip("@")})
        items = r.get("items") or []
        if not items:
            return []
        cid = items[0]["id"]
    uploads = "UU" + cid[2:]
    r = call(channel, "GET", f"{API}/playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": limit})
    out = []
    for it in r.get("items", []):
        vid = it["contentDetails"]["videoId"]
        info = video_info(channel, vid) or {}
        out.append(dict(info, url=f"https://www.youtube.com/watch?v={vid}"))
    return out


def search_cc(channel, query, limit=5):
    """Niche search, long videos, Creative Commons licence only."""
    if not connected(channel):
        return []
    r = call(channel, "GET", f"{API}/search", {"part": "snippet", "q": query, "type": "video", "videoLicense": "creativeCommon",
                                               "videoDuration": "long", "maxResults": limit, "order": "relevance"}, kind="search")
    out = []
    for it in r.get("items", []):
        vid = it["id"]["videoId"]
        info = video_info(channel, vid) or {}
        if info.get("license") == "creativeCommon":
            out.append(dict(info, url=f"https://www.youtube.com/watch?v={vid}"))
    return out


# ---------- uploading ----------

def start_upload(channel, path, meta):
    """Opens a resumable upload session. Returns the session URL."""
    size = os.path.getsize(path)
    r = call(channel, "POST", UPLOAD, {"uploadType": "resumable", "part": "snippet,status"}, body=meta, kind="insert",
             headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}, raw=True)
    loc = r.headers.get("Location")
    if not loc:
        raise YouTubeError("YouTube didn't open an upload session")
    return loc


def _uploaded_so_far(session, size, token):
    req = urllib.request.Request(session, data=b"", method="PUT",
                                 headers={"Authorization": f"Bearer {token}", "Content-Range": f"bytes */{size}",
                                          "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r:
            return size, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 308:
            rng = e.headers.get("Range")
            return (int(rng.split("-")[1]) + 1 if rng else 0), None
        if e.code == 404:
            return None, None                         # session expired: start a new one
        raise YouTubeError(f"upload status check failed ({e.code})", e.code)


def send_file(channel, session, path, stop=lambda: False, progress=lambda f: None):
    """Sends the file in 8 MB pieces, resuming from whatever YouTube already has. Returns the video
    resource when done, or None if stopped (the session stays valid for resuming)."""
    size = os.path.getsize(path)
    token = access_token(channel)
    pos, done = _uploaded_so_far(session, size, token)
    if pos is None:
        raise YouTubeError("the upload session expired", 404, "sessionExpired")
    if done:
        return done
    tries = 0
    with open(path, "rb") as f:
        while pos < size:
            if stop():
                return None
            f.seek(pos)
            chunk = f.read(CHUNK)
            end = pos + len(chunk) - 1
            req = urllib.request.Request(session, data=chunk, method="PUT", headers={
                "Authorization": f"Bearer {access_token(channel)}", "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {pos}-{end}/{size}", "Content-Type": "video/mp4"})
            try:
                with urllib.request.urlopen(req, timeout=300, context=_ctx()) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code == 308:
                    rng = e.headers.get("Range")
                    pos = int(rng.split("-")[1]) + 1 if rng else 0
                    tries = 0
                    progress(pos / size)
                    continue
                if e.code in (500, 502, 503, 504) and tries < 6:
                    tries += 1
                    time.sleep(min(64, 2 ** tries))
                    pos, done = _uploaded_so_far(session, size, access_token(channel))
                    if done:
                        return done
                    if pos is None:
                        raise YouTubeError("the upload session expired", 404, "sessionExpired")
                    continue
                text = e.read().decode("utf-8", "replace")
                raise YouTubeError(f"upload failed {e.code}: {text[:300]}", e.code, _reason(text))
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                if tries >= 6:
                    raise YouTubeError("lost the connection to YouTube too many times")
                tries += 1
                time.sleep(min(64, 2 ** tries))
                pos, done = _uploaded_so_far(session, size, access_token(channel))
                if done:
                    return done
                if pos is None:
                    raise YouTubeError("the upload session expired", 404, "sessionExpired")
    raise YouTubeError("upload ended without YouTube confirming it")


def status(channel, video_ids):
    """{id: {privacy, upload, rejection, processing}} for uploaded videos."""
    out = {}
    for i in range(0, len(video_ids), 50):
        r = call(channel, "GET", f"{API}/videos", {"part": "status,processingDetails", "id": ",".join(video_ids[i:i + 50])})
        for v in r.get("items", []):
            st = v.get("status", {})
            out[v["id"]] = {"privacy": st.get("privacyStatus"), "upload": st.get("uploadStatus"),
                            "rejection": st.get("rejectionReason") or st.get("failureReason"),
                            "publish_at": st.get("publishAt"),
                            "processing": v.get("processingDetails", {}).get("processingStatus")}
    return out


def post_comment(channel, video_id, text):
    """Posts the suggested comment (you can pin it in YouTube Studio; the API can't pin)."""
    return call(channel, "POST", f"{API}/commentThreads", {"part": "snippet"}, kind="update",
                body={"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}})


# ---------- analytics ----------

def analytics(channel, video_ids, start="2020-01-01"):
    """Per-video totals from YouTube Analytics."""
    if not video_ids:
        return {}
    end = datetime.date.today().isoformat()
    metrics = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained"
    out = {}
    for i in range(0, len(video_ids), 200):
        r = call(channel, "GET", ANALYTICS, {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics,
                                             "dimensions": "video", "filters": "video==" + ",".join(video_ids[i:i + 200]),
                                             "maxResults": 200}, kind="analytics")
        cols = [h["name"] for h in r.get("columnHeaders", [])]
        for row in r.get("rows", []) or []:
            d = dict(zip(cols, row))
            out[d["video"]] = d
    return out


def retention(channel, video_id, start="2020-01-01"):
    """Audience retention curve: [(share of the video, share still watching)]."""
    r = call(channel, "GET", ANALYTICS, {"ids": "channel==MINE", "startDate": start, "endDate": datetime.date.today().isoformat(),
                                         "metrics": "audienceWatchRatio", "dimensions": "elapsedVideoTimeRatio",
                                         "filters": f"video=={video_id}"}, kind="analytics")
    return [(row[0], row[1]) for row in r.get("rows", []) or []]


def traffic(channel, video_id, start="2020-01-01"):
    r = call(channel, "GET", ANALYTICS, {"ids": "channel==MINE", "startDate": start, "endDate": datetime.date.today().isoformat(),
                                         "metrics": "views", "dimensions": "insightTrafficSourceType",
                                         "filters": f"video=={video_id}"}, kind="analytics")
    return {row[0]: row[1] for row in r.get("rows", []) or []}


def lock_note():
    return ("Google keeps videos uploaded by apps it hasn't audited set to private. To publish publicly from Jarvis, "
            "request Google's free YouTube API audit for your Cloud project (link in the README).")

