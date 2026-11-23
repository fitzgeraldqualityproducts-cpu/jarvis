"""Publishing (Phase 8): the official YouTube Data API only, to the locked business channel only.

Modes (config "studio" "publish_mode"):
  manual   (default) finished Shorts wait; Connor uploads a PRIVATE test with "Upload Test Video".
  private  the worker uploads each finished Short as private (nobody sees it) for Connor to review.
  public   the worker uploads on a schedule: private with publishAt, and YouTube makes it public at
           that time. Only set by Connor himself (python -m studio publish-mode public, typed confirmation).

upload()      checks the destination channel, then a resumable upload (8 MB pieces). The session URL is
              saved before any bytes are sent, so a crash or restart resumes the same upload instead of
              starting a second one. Each video carries a private tag (jv-<id>-<key>) so an upload
              whose answer was lost can be found again (reconcile) instead of being repeated.
verify()      reads the video back from YouTube: right channel, processing finished, privacy as expected.
next_slot()   when the next Short should go out (spacing and weekly pace from config; quality first:
              a Short only exists here if it passed QA)."""
import datetime
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from studio import config, db, guard, jobs

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024
MODES = ("manual", "private", "public")
DEFAULTS = {"publish_mode": "manual", "posts_per_week": 3, "min_hours_between": 40, "publish_hour": 17}

HTTP = None       # tests swap this: (method, url, headers, data) -> (status, headers_dict, body_bytes)


class UploadError(jobs.Transient):
    pass


class SessionExpired(UploadError):
    pass


def settings():
    g = config.general()
    return {k: g.get(k, v) for k, v in DEFAULTS.items()}


def mode():
    m = settings()["publish_mode"]
    return m if m in MODES else "manual"


# ---------- HTTP ----------

def _http(method, url, headers, data=None, timeout=120):
    if HTTP:
        return HTTP(method, url, headers, data)
    from clipper import youtube
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=youtube._ctx()) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def _auth():
    from studio import ytauth
    return {"Authorization": f"Bearer {ytauth.access_token()}"}


def _json(body):
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return {}


def _reason(body):
    try:
        return _json(body)["error"]["errors"][0]["reason"]
    except (KeyError, IndexError, TypeError):
        return ""


def _check(status, body, what):
    if status in (200, 201):
        return _json(body)
    reason = _reason(body)
    if status == 401:
        raise jobs.AuthProblem("Google signed the business channel out. Double-click Connect YouTube Business again.")
    if status == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"):
        raise jobs.QuotaWait(f"YouTube's limit for today is reached ({reason})", _after_pacific_midnight())
    if status in (500, 502, 503, 504, 429):
        raise UploadError(f"{what}: YouTube is busy ({status})")
    raise jobs.Permanent(guard.redact(f"{what}: YouTube said {status} {reason}: {body[:300]!r}"))


def _after_pacific_midnight():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-8)))
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
    return nxt.timestamp()


def _get(path, params, kind="list"):
    from clipper import youtube
    youtube._spend(kind)
    status, _, body = _http("GET", f"{API}/{path}?" + urllib.parse.urlencode(params), _auth())
    return _check(status, body, path)


# ---------- the queue ----------

def job(video_id):
    r = db.one("SELECT * FROM publish_jobs WHERE video_id=?", video_id)
    return dict(r) if r else None


def _set(video_id, **cols):
    cols["updated"] = time.time()
    db.run(f"UPDATE publish_jobs SET {', '.join(f'{k}=?' for k in cols)} WHERE video_id=?", *cols.values(), video_id)


def tag(video_id):
    v = db.video(video_id)
    return f"jv-{video_id}-{(v['idem_key'] or 'x')[-12:]}".replace(" ", "")


def body(video_id, privacy, publish_at=None):
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    up = meta.get("upload")
    if not up:
        raise jobs.Permanent("the video has no upload details (it hasn't passed QA)")
    st = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(up.get("madeForKids")),
          "containsSyntheticMedia": bool(up.get("containsSyntheticMedia")), "embeddable": True,
          "license": "youtube"}
    if publish_at:
        st["privacyStatus"] = "private"
        st["publishAt"] = datetime.datetime.fromtimestamp(publish_at, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"snippet": {"title": up["title"][:100], "description": up["description"][:4900],
                        "tags": (up.get("tags") or [])[:12] + [tag(video_id)], "categoryId": up.get("categoryId", "27"),
                        "defaultLanguage": up.get("defaultLanguage", "en"),
                        "defaultAudioLanguage": up.get("defaultAudioLanguage", "en-US")},
            "status": st}


def _destination():
    """The signed-in channel must be the locked business channel (never Connor's own)."""
    from studio import ytauth
    me = ytauth.whoami()
    guard.verify_destination(me["id"])
    return me["id"]


def reconcile(video_id):
    """Finds this video on the channel if an earlier upload went through but its answer was lost."""
    ch = _get("channels", {"part": "contentDetails", "mine": "true"})
    items = ch.get("items") or []
    if not items:
        return None
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = _get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": 25})
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items") or []]
    if not ids:
        return None
    vs = _get("videos", {"part": "snippet", "id": ",".join(ids)})
    want = tag(video_id)
    for v in vs.get("items") or []:
        if want in (v.get("snippet", {}).get("tags") or []):
            return v["id"]
    return None


def upload(video_id, privacy="private", publish_at=None, progress=lambda f: None, stop=lambda: False):
    """Uploads the video once. Returns the YouTube video id. Safe to call again after any interruption."""
    from clipper import youtube
    guard.allow("youtube.upload", f"video {video_id} as {privacy}")
    v = db.video(video_id)
    if v["state"] in ("published", "rejected", "failed"):
        raise jobs.Permanent(f"video {video_id} is {v['state']}")
    path = v["path"]
    if not path or not os.path.isfile(path):
        raise jobs.Permanent("the video file is missing")
    fails = db.one("SELECT COUNT(*) n FROM qa_checks WHERE video_id=? AND passed=0", video_id)["n"]
    passes = db.one("SELECT COUNT(*) n FROM qa_checks WHERE video_id=? AND passed=1", video_id)["n"]
    if fails or not passes:
        raise jobs.Permanent("the video hasn't passed every quality check")
    channel = _destination()
    j = job(video_id)
    if j and j["youtube_id"]:
        set_cover(video_id, j["youtube_id"])                # a cover that didn't go up last time
        return j["youtube_id"]                              # already uploaded: never twice
    if not j:
        db.insert("INSERT INTO publish_jobs(video_id, channel_id, status, attempts, privacy, publish_at, created, updated) "
                  "VALUES(?,?,?,?,?,?,?,?)", video_id, channel, "queued", 0, privacy,
                  str(publish_at) if publish_at else None, time.time(), time.time())
        j = job(video_id)
    if j["channel_id"] != channel:
        raise guard.WrongChannel("the upload was started for a different channel; refusing")
    _set(video_id, attempts=j["attempts"] + 1, status="uploading")
    size = os.path.getsize(path)
    session = j["session_url"]
    if session:
        pos, done = _progress(session, size)
        if done:
            return _finish(video_id, done, privacy)
        if pos is None:                                     # the session expired: did YouTube keep it anyway?
            found = reconcile(video_id)
            if found:
                return _finish(video_id, {"id": found}, privacy, reconciled=True)
            session = None
    elif j["attempts"] >= 1:                                # a try with no session recorded: check before retrying
        found = reconcile(video_id)
        if found:
            return _finish(video_id, {"id": found}, privacy, reconciled=True)
    if not session:
        youtube._spend("insert")
        h = dict(_auth(), **{"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
                             "X-Upload-Content-Length": str(size)})
        status, headers, resp = _http("POST", UPLOAD + "?uploadType=resumable&part=snippet,status", h,
                                      json.dumps(body(video_id, privacy, publish_at)).encode())
        if status not in (200, 201):
            _check(status, resp, "starting the upload")
        session = {k.lower(): val for k, val in headers.items()}.get("location")
        if not session:
            raise UploadError("YouTube didn't open an upload session")
        _set(video_id, session_url=session, status="uploading")
        guard.audit("youtube.upload", True, {"video": video_id, "channel": channel, "privacy": privacy, "step": "session"})
    try:
        done = _send(session, path, size, progress, stop)
    except SessionExpired:
        found = reconcile(video_id)                         # it may have finished just as the line dropped
        if found:
            return _finish(video_id, {"id": found}, privacy, reconciled=True)
        _set(video_id, session_url=None)
        raise
    if done is None:
        raise jobs.Interrupted("upload paused; it resumes from the same place")
    return _finish(video_id, done, privacy)


def _progress(session, size):
    status, headers, body = _http("PUT", session, dict(_auth(), **{"Content-Range": f"bytes */{size}", "Content-Length": "0"}), b"")
    if status in (200, 201):
        return size, _json(body)
    if status == 308:
        rng = {k.lower(): v for k, v in headers.items()}.get("range")
        return (int(rng.split("-")[1]) + 1 if rng else 0), None
    if status in (404, 410):
        return None, None
    raise UploadError(f"checking the upload failed ({status})")


def _send(session, path, size, progress, stop):
    pos, done = _progress(session, size)
    if done:
        return done
    if pos is None:
        raise SessionExpired("the upload session expired")
    tries = 0
    with open(path, "rb") as f:
        while pos < size:
            if stop():
                return None
            f.seek(pos)
            chunk = f.read(CHUNK)
            end = pos + len(chunk) - 1
            try:
                status, headers, body = _http("PUT", session, dict(_auth(), **{
                    "Content-Length": str(len(chunk)), "Content-Range": f"bytes {pos}-{end}/{size}",
                    "Content-Type": "video/mp4"}), chunk)
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                status, headers, body = 0, {}, b""
            if status in (200, 201):
                return _json(body)
            if status == 308:
                rng = {k.lower(): v for k, v in headers.items()}.get("range")
                pos = int(rng.split("-")[1]) + 1 if rng else 0
                tries = 0
                progress(pos / size)
                continue
            if status in (0, 500, 502, 503, 504) and tries < 5:
                tries += 1
                time.sleep(0 if HTTP else min(32, 2 ** tries))
                pos, done = _progress(session, size)
                if done:
                    return done
                if pos is None:
                    raise SessionExpired("the upload session expired")
                continue
            _check(status, body, "uploading")
    raise UploadError("the upload ended without YouTube confirming it")


def _finish(video_id, resource, privacy, reconciled=False):
    yid = resource.get("id")
    if not yid:
        raise UploadError("YouTube didn't return a video id")
    j = job(video_id)
    _set(video_id, youtube_id=yid, status="uploaded", response=json.dumps(resource)[:4000], error=None)
    db.set_video(video_id, meta=json.dumps(dict(json.loads(db.video(video_id)["meta"] or "{}"), youtube_id=yid)))
    guard.audit("youtube.upload", True, {"video": video_id, "youtube_id": yid, "channel": j["channel_id"],
                                          "privacy": privacy, "reconciled": reconciled})
    set_cover(video_id, yid)
    return yid


THUMB = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"


def set_cover(video_id, youtube_id):
    """Sends the Short's cover.jpg as its YouTube thumbnail, once. Never fails the upload: a problem is
    saved in videos.meta["cover"]["youtube"] (for example YouTube only accepts custom thumbnails from
    phone-verified channels). Returns True when YouTube took it."""
    from clipper import youtube
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    c = meta.get("cover") or {}
    path = c.get("path")
    prev = c.get("youtube") or {}
    if not path or not os.path.isfile(path) or prev.get("ok") or prev.get("tries", 0) >= 3:
        return bool(prev.get("ok"))
    res = {"t": time.time(), "ok": False, "tries": prev.get("tries", 0) + 1}
    try:
        guard.allow("youtube.set_thumbnail", f"video {video_id} -> {youtube_id}")
        if _destination() != (job(video_id) or {}).get("channel_id"):
            raise guard.WrongChannel("the cover is for a different channel; refusing")
        youtube._spend("thumbnail")
        with open(path, "rb") as f:
            data = f.read()
        status, _, body = _http("POST", f"{THUMB}?videoId={urllib.parse.quote(youtube_id)}&uploadType=media",
                                dict(_auth(), **{"Content-Type": "image/jpeg", "Content-Length": str(len(data))}), data)
        if status in (200, 201):
            res["ok"] = True
        else:
            reason = _reason(body)
            res["error"] = f"{status} {reason}".strip()
            if status == 403 and reason in ("forbidden", ""):
                res["hint"] = ("YouTube only lets phone-verified channels use custom thumbnails: "
                               "youtube.com/verify on the business account turns it on.")
    except Exception as e:                                  # the video is up; the cover is a nice-to-have
        res["error"] = guard.redact(str(e))[:200]
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    meta.setdefault("cover", {})["youtube"] = res
    db.set_video(video_id, meta=json.dumps(meta))
    guard.audit("youtube.set_thumbnail", res["ok"], {"video": video_id, "youtube_id": youtube_id, **res})
    return res["ok"]


# ---------- verification ----------

def verify(video_id):
    """{"state": processing|failed|scheduled|published|private, ...} from YouTube itself."""
    guard.allow("youtube.read_video", f"video {video_id}", record=False)
    j = job(video_id)
    if not j or not j["youtube_id"]:
        raise jobs.Permanent("nothing was uploaded for this video")
    r = _get("videos", {"part": "status,processingDetails,snippet", "id": j["youtube_id"]})
    items = r.get("items") or []
    if not items:
        return {"state": "missing", "detail": "YouTube has no video with that id (it may still be appearing)"}
    it = items[0]
    if it["snippet"]["channelId"] != j["channel_id"]:
        raise guard.WrongChannel("the uploaded video is on a different channel than expected")
    st, proc = it.get("status", {}), (it.get("processingDetails") or {}).get("processingStatus")
    out = {"youtube_id": j["youtube_id"], "privacy": st.get("privacyStatus"), "upload": st.get("uploadStatus"),
           "publish_at": st.get("publishAt"), "processing": proc,
           "problem": st.get("rejectionReason") or st.get("failureReason")}
    if st.get("uploadStatus") in ("failed", "rejected", "deleted") or proc == "failed":
        out["state"] = "failed"
    elif st.get("uploadStatus") == "uploaded" or proc == "processing":
        out["state"] = "processing"
    elif st.get("privacyStatus") == "public":
        out["state"] = "published"
    elif st.get("publishAt"):
        out["state"] = "scheduled"
    else:
        out["state"] = "private"
    _set(video_id, status=out["state"], response=json.dumps(it)[:4000])
    if out["state"] in ("published", "private"):
        db.run("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy, published_at, verified) "
               "VALUES(?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET privacy=excluded.privacy, "
               "published_at=excluded.published_at, verified=excluded.verified", video_id, j["youtube_id"],
               j["channel_id"], it["snippet"]["title"], out["privacy"], it["snippet"].get("publishedAt"), time.time())
    return out


# ---------- pace ----------

def next_slot(now=None):
    """The time the next Short should go public: at the configured hour (Central time), at least
    min_hours_between after the last one, and no more than posts_per_week in any 7 days."""
    s = settings()
    now = now or time.time()
    taken = sorted(float(r["publish_at"]) for r in db.q("SELECT publish_at FROM publish_jobs WHERE publish_at IS NOT NULL")
                   if r["publish_at"])
    last = taken[-1] if taken else 0
    tz = datetime.timezone(datetime.timedelta(hours=-6))
    t = max(now + 3 * 3600, last + s["min_hours_between"] * 3600)
    for _ in range(60):
        d = datetime.datetime.fromtimestamp(t, tz)
        cand = d.replace(hour=int(s["publish_hour"]), minute=0, second=0, microsecond=0)
        if cand.timestamp() < t:
            cand += datetime.timedelta(days=1)
        c = cand.timestamp()
        week = [x for x in taken if c - 7 * 86400 < x <= c]
        if len(week) < int(s["posts_per_week"]):
            return c
        t = c + 3600
    return t
