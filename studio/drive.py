"""Google Drive backup: finished Shorts go to Connor's own Google Drive, and the Mac's copy is only
removed once Drive's copy is checked byte for byte.

Sign-in (once): "Connect Google Drive" opens Google's own page; Connor picks the Google account whose
Drive should hold the backups and presses Allow. Jarvis asks for ONE permission, drive.file: it can
only see and change files it created itself. It can't read, list or delete anything else in that
Drive. Token: studio_data/secrets/drive_backup.json (chmod 600, never logged). Uses the same Google
OAuth client as YouTube (free; the Google Drive API must be switched on in that project once).

What is copied, into "Jarvis YouTube Backups" in that Drive:
    Shorts/<id> <title>/   short.mp4, voice.wav, thumb.jpg, script.md, upload.json
    Reports/               the weekly reports
Every finished Short is copied (waiting for upload, uploaded, or published). Rejected and failed
Shorts aren't.

Verified: after each upload Jarvis reads the file back from Drive and compares its size and MD5
checksum with the file on the Mac. Only verified copies count.

Freeing space on the Mac (only all of these):
  - the Short is on YouTube and YouTube confirmed it (the "published" table, written by verify());
  - every big file of that Short (over 1 MB) has a verified Drive copy, re-checked against Drive
    right before deleting (still there, not in the bin, same size and checksum);
  - the Short went up at least drive_keep_local_days ago (default 7), unless disk space is low.
Small files (script, metadata, thumbnail) always stay on the Mac. Shorts still waiting for upload
are never removed. `python -m studio drive restore <id>` brings a Short's files back.

Drive's free 15 GB is shared with Gmail and Photos: Jarvis checks the space left before each upload
and stops (and tells Connor) instead of filling it up. Costs nothing: the Drive API is free."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from studio import config, data_dir, db, guard, health, jobs

SCOPE = "https://www.googleapis.com/auth/drive.file"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
ROOT_NAME = "Jarvis YouTube Backups"
BACKUP_FILES = ("short.mp4", "voice.wav", "thumb.jpg", "script.md", "upload.json")
BIG = 1 << 20                        # files over 1 MB may be removed from the Mac once backed up
FINISHED = ("ready", "uploading", "processing", "scheduled", "published")
DEFAULTS = {"drive_backup": True, "drive_keep_local_days": 7, "drive_free_local": True,
            "drive_reserve_gb": 1.0, "drive_every_minutes": 30, "drive_max_files_per_run": 10}
TYPES = {".mp4": "video/mp4", ".wav": "audio/wav", ".jpg": "image/jpeg", ".md": "text/markdown",
         ".json": "application/json", ".txt": "text/plain"}

HTTP = None       # tests swap this: (method, url, headers, data) -> (status, headers_dict, body_bytes)


class DriveError(jobs.Transient):
    pass


class DriveOff(Exception):
    """The Drive API isn't switched on in the Google project yet."""

    def __init__(self, msg, link):
        super().__init__(msg)
        self.link = link


class DriveFull(Exception):
    pass


def settings():
    g = config.general()
    return {k: g.get(k, v) for k, v in DEFAULTS.items()}


# ---------- sign-in ----------

def _path():
    d = data_dir("secrets")
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d / "drive_backup.json"


def connected():
    return _path().exists()


def _save(tok):
    from clipper import youtube
    youtube._write_secret(_path(), tok)


def access_token():
    from clipper import youtube
    if not connected():
        raise jobs.AuthProblem("Google Drive isn't connected (double-click Connect Google Drive)")
    tok = json.loads(_path().read_text())
    if time.time() < float(tok.get("expires_at", 0)):
        return tok["access_token"]
    try:
        youtube.refresh(tok)
    except youtube.NotConnected as e:
        guard.audit("drive.token", False, str(e))
        raise jobs.AuthProblem("Google signed Jarvis out of Google Drive. Double-click Connect Google Drive again.")
    _save(tok)
    return tok["access_token"]


def enable_link():
    """The Google Cloud page that switches the Drive API on for Jarvis's own Google project."""
    from clipper import youtube
    cl = youtube.client() or {}
    project = (cl.get("client_id") or "").split("-")[0]
    q = f"?project={project}" if project.isdigit() else ""
    return f"https://console.cloud.google.com/apis/library/drive.googleapis.com{q}"


def connect(open_browser=True, timeout=300, wait_for_enable=240, say=print):
    """Signs in to Google Drive (drive.file only), then checks Drive answers. Returns about()."""
    from clipper import youtube
    guard.allow("drive.backup", "connect")
    tok = youtube.exchange(youtube.browser_code((SCOPE,), open_browser, timeout,
                                                done_text="Jarvis is connected to Google Drive."))
    _save(tok)
    end = time.time() + wait_for_enable
    told = False
    while True:
        try:
            me = about()
            break
        except DriveOff as e:
            if time.time() > end:
                raise
            if not told:
                say("\nOne more click: Google Drive isn't switched on for Jarvis's Google project yet.\n"
                    f"I'm opening {e.link}\nPress the blue Enable button there. I'll wait here and carry on by myself.")
                if open_browser:
                    import webbrowser
                    webbrowser.open(e.link)
                told = True
            time.sleep(10)
    db.put("drive_account", me.get("email") or "")
    guard.audit("drive.connect", True, {"account": me.get("email")})
    return me


def disconnect():
    if connected():
        _path().unlink()
    guard.audit("drive.disconnect", True, "")


# ---------- HTTP ----------

def _http(method, url, headers, data=None, timeout=300):
    if HTTP:
        return HTTP(method, url, headers, data)
    from clipper import youtube
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=youtube._ctx()) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, OSError) as e:
        raise DriveError(f"Google Drive couldn't be reached ({e})")


def _call(method, url, params=None, body=None, data=None, headers=None, raw=False):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"Authorization": f"Bearer {access_token()}"}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json; charset=UTF-8"
    h.update(headers or {})
    for attempt in range(4):
        status, rh, out = _http(method, url, h, data)
        if status in (200, 201, 204):
            if raw:
                return rh, out
            return json.loads(out or b"{}")
        text = (out or b"").decode("utf-8", "replace")
        if status == 403 and ("accessNotConfigured" in text or "SERVICE_DISABLED" in text or "has not been used" in text):
            raise DriveOff("the Google Drive API is switched off in Jarvis's Google project", enable_link())
        if status == 403 and "storageQuotaExceeded" in text:
            raise DriveFull("your Google Drive is full")
        if status == 401 and attempt == 0:
            tok = json.loads(_path().read_text())
            tok["expires_at"] = 0
            _save(tok)
            h["Authorization"] = f"Bearer {access_token()}"
            continue
        if status == 401:
            raise jobs.AuthProblem("Google refused Jarvis's Drive sign-in. Double-click Connect Google Drive again.")
        if status == 404:
            return None
        if status in (429, 500, 502, 503, 504) or (status == 403 and "rateLimitExceeded" in text):
            time.sleep(0 if HTTP else min(30, 2 ** attempt))
            continue
        raise DriveError(guard.redact(f"Google Drive said {status}: {text[:300]}"))
    raise DriveError("Google Drive kept answering with errors; I'll try again later")


# ---------- Drive pieces ----------

def about():
    """{"email", "name", "limit", "usage"} (bytes; limit None means unlimited)."""
    r = _call("GET", f"{API}/about", {"fields": "user(emailAddress,displayName),storageQuota(limit,usage)"}) or {}
    q, u = r.get("storageQuota") or {}, r.get("user") or {}
    return {"email": u.get("emailAddress", ""), "name": u.get("displayName", ""),
            "limit": int(q["limit"]) if q.get("limit") else None, "usage": int(q.get("usage") or 0)}


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _find(key):
    """A live Drive file Jarvis made earlier with this key, or None (finds uploads whose answer was lost)."""
    r = _call("GET", f"{API}/files", {"q": f"appProperties has {{ key='jarvis' and value='{_q(key)}' }} and trashed=false",
                                       "fields": "files(id,name,size,md5Checksum,trashed)", "spaces": "drive"}) or {}
    files = r.get("files") or []
    return files[0] if files else None


def meta(file_id):
    return _call("GET", f"{API}/files/{file_id}", {"fields": "id,name,size,md5Checksum,trashed,parents"})


def folder(key, name, parent=None):
    """The id of Jarvis's folder for this key, made if missing (or if Connor deleted it)."""
    cached = db.get(f"drive_folder_{key}")
    if cached:
        m = meta(cached)
        if m and not m.get("trashed"):
            return cached
    found = _find(f"folder:{key}")
    if found:
        fid = found["id"]
    else:
        body = {"name": name, "mimeType": FOLDER, "appProperties": {"jarvis": f"folder:{key}"}}
        if parent:
            body["parents"] = [parent]
        fid = _call("POST", f"{API}/files", {"fields": "id"}, body=body)["id"]
    db.put(f"drive_folder_{key}", fid)
    return fid


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def _matches(m, size, md5):
    return bool(m) and not m.get("trashed") and int(m.get("size") or -1) == size and m.get("md5Checksum") == md5


def upload(path, parent, key):
    """Uploads one file (resumable session, then the bytes) and returns Drive's metadata for it."""
    size = os.path.getsize(path)
    ctype = TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    rh, _ = _call("POST", UPLOAD, {"uploadType": "resumable", "fields": "id"},
                  body={"name": os.path.basename(path), "parents": [parent], "appProperties": {"jarvis": key}},
                  headers={"X-Upload-Content-Type": ctype, "X-Upload-Content-Length": str(size)}, raw=True)
    session = rh.get("Location") or rh.get("location")
    if not session:
        raise DriveError("Google Drive didn't open an upload")
    with open(path, "rb") as f:
        data = f.read()
    status, _, out = _http("PUT", session, {"Content-Type": ctype, "Content-Length": str(size)}, data)
    if status not in (200, 201):
        raise DriveError(f"the upload of {os.path.basename(path)} stopped (Drive said {status}); I'll try again later")
    return meta(json.loads(out or b"{}")["id"])


# ---------- the backup ledger ----------

def _ensure_table():
    db.run("CREATE TABLE IF NOT EXISTS drive_backups (key TEXT PRIMARY KEY, video_id INTEGER, name TEXT, path TEXT, "
           "drive_id TEXT, size INTEGER, md5 TEXT, uploaded REAL, verified REAL, freed REAL, error TEXT)")


def _row(key):
    _ensure_table()
    return db.one("SELECT * FROM drive_backups WHERE key=?", key)


def _record(key, **f):
    _ensure_table()
    cols = ["key"] + list(f)
    db.run(f"INSERT INTO drive_backups({','.join(cols)}) VALUES({','.join('?' * len(cols))}) ON CONFLICT(key) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in f), key, *f.values())


def _room(size, info):
    if info["limit"] is None:
        return True
    reserve = int(float(settings()["drive_reserve_gb"]) * (1 << 30))
    return info["usage"] + size + reserve <= info["limit"]


def _video_folder(v, shorts):
    title = (v.get("title") or "Short").replace("/", "-")[:80]
    return folder(f"video:{v['id']}", f"{v['id']} {title}", shorts)


def pending():
    """[(key, video_id_or_None, local path)] of files that don't have a verified Drive copy yet."""
    _ensure_table()
    files = []
    for v in db.q(f"SELECT id FROM videos WHERE state IN ({','.join('?' * len(FINISHED))}) ORDER BY id", *FINISHED):
        d = data_dir("videos", str(v["id"]))
        files += [(f"{v['id']}/{name}", v["id"], d / name) for name in BACKUP_FILES]
    files += [(f"reports/{p.name}", None, p) for p in sorted(data_dir("reports").glob("*.md"))]
    out = []
    for key, vid, p in files:
        if not p.exists():
            continue
        r = _row(key)
        if r and r["verified"] and r["size"] == p.stat().st_size:
            continue
        out.append((key, vid, p))
    return out


def back_up(limit=None, say=lambda m: None):
    """Copies files that aren't safely on Drive yet. Returns {"copied", "bytes", "left", "problem"}."""
    guard.allow("drive.backup", "back up", record=False)
    s = settings()
    todo = pending()
    res = {"copied": 0, "bytes": 0, "left": len(todo), "problem": None}
    if not todo:
        return res
    info = about()
    root = folder("root", ROOT_NAME)
    shorts = reports = None
    for key, vid, p in todo[: limit or s["drive_max_files_per_run"]]:
        size, md5 = p.stat().st_size, _md5(p)
        if not _room(size, info):
            res["problem"] = "full"
            health.notify("drive_full", "Your Google Drive is almost full, so I've stopped backing up YouTube Shorts "
                                        "there. Nothing on your Mac was removed.", "warn")
            break
        if vid is None:
            reports = reports or folder("reports", "Reports", root)
            parent = reports
        else:
            shorts = shorts or folder("shorts", "Shorts", root)
            parent = _video_folder(db.video(vid), shorts)
        old = _find(key)
        m = old
        if not _matches(m, size, md5):
            m = upload(str(p), parent, key)
            info["usage"] += size
            if old and _matches(m, size, md5):         # an older version of the same file (it changed since)
                _call("PATCH", f"{API}/files/{old['id']}", body={"trashed": True})
        if not _matches(m, size, md5):
            _record(key, video_id=vid, name=p.name, path=str(p), drive_id=(m or {}).get("id"), size=size, md5=md5,
                    uploaded=time.time(), verified=None, freed=None, error="Drive's copy didn't match")
            raise DriveError(f"Drive's copy of {p.name} didn't match the file on this Mac; I'll upload it again later")
        _record(key, video_id=vid, name=p.name, path=str(p), drive_id=m["id"], size=size, md5=md5,
                uploaded=time.time(), verified=time.time(), freed=None, error=None)
        res["copied"] += 1
        res["bytes"] += size
        res["left"] -= 1
        say(f"  backed up {key} ({size / (1 << 20):.1f} MB)")
    if res["copied"]:
        guard.audit("drive.backup", True, {"files": res["copied"], "bytes": res["bytes"]})
    return res


def freeable(urgent=False, now=None):
    """[row] of verified backups whose local file may go: big, on YouTube for long enough."""
    _ensure_table()
    now = now or time.time()
    keep = 0 if urgent else float(settings()["drive_keep_local_days"]) * 86400
    out = []
    for r in db.q("SELECT b.*, p.verified AS on_youtube FROM drive_backups b JOIN published p ON p.video_id=b.video_id "
                  "WHERE b.verified IS NOT NULL AND b.freed IS NULL AND p.verified IS NOT NULL AND p.youtube_id IS NOT NULL"):
        p = r["path"]
        if not os.path.exists(p) or os.path.getsize(p) != r["size"] or r["size"] < BIG:
            continue
        if now - float(r["on_youtube"]) < keep:
            continue
        v = db.video(r["video_id"])
        if not v or v["state"] not in ("published", "scheduled", "processing"):
            continue                                   # only Shorts YouTube already has
        out.append(r)
    return out


def free_local(urgent=False, now=None, say=lambda m: None):
    """Removes big local files that are on YouTube and verified on Drive (checked again first). Returns bytes freed."""
    if not settings()["drive_free_local"]:
        return 0
    freed = 0
    for r in freeable(urgent, now):
        m = meta(r["drive_id"])
        if not _matches(m, r["size"], r["md5"]) or _md5(r["path"]) != r["md5"]:
            db.run("UPDATE drive_backups SET verified=NULL, error=? WHERE key=?", "re-check failed; will copy again", r["key"])
            continue
        os.remove(r["path"])
        db.run("UPDATE drive_backups SET freed=? WHERE key=?", time.time(), r["key"])
        freed += r["size"]
        say(f"  removed the Mac copy of {r['key']} (safe on Drive)")
    if freed:
        guard.audit("drive.free_local", True, {"bytes": freed, "urgent": urgent})
        health.recovery("disk", f"freed {freed / (1 << 20):.0f} MB of Shorts that are safe on Google Drive", "freed")
    return freed


def restore(video_id, say=print):
    """Downloads a Short's removed files back from Drive (checked), so it can be re-edited."""
    guard.allow("drive.backup", f"restore {video_id}", record=False)
    _ensure_table()
    n = 0
    for r in db.q("SELECT * FROM drive_backups WHERE video_id=? AND freed IS NOT NULL", int(video_id)):
        _, data = _call("GET", f"{API}/files/{r['drive_id']}", {"alt": "media"}, raw=True)
        if len(data) != r["size"] or hashlib.md5(data).hexdigest() != r["md5"]:
            raise DriveError(f"the Drive copy of {r['name']} doesn't match what was backed up")
        tmp = r["path"] + ".restore"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, r["path"])
        db.run("UPDATE drive_backups SET freed=NULL WHERE key=?", r["key"])
        say(f"  restored {r['name']}")
        n += 1
    return n


# ---------- the background chore ----------

def maybe_run(now=None, urgent=False):
    """Every drive_every_minutes (sooner when disk space is low): back up, then free what's safe."""
    now = now or time.time()
    s = settings()
    if not config.enabled() or not s["drive_backup"] or not connected():
        return None
    gap = 5 if urgent else s["drive_every_minutes"]
    if now - float(db.get("drive_last_run", 0) or 0) < gap * 60:
        return None
    db.put("drive_last_run", now)
    try:
        res = back_up()
        res["freed"] = free_local(urgent=urgent, now=now)
    except DriveOff as e:
        health.notify("drive_off", "I can't back up Shorts to Google Drive yet: the Drive API needs switching on. "
                                   "Double-click Connect Google Drive and it'll walk you through it.", "warn")
        raise jobs.Permanent(str(e))
    except jobs.AuthProblem as e:
        health.notify("drive_auth", f"{e}", "warn")
        raise
    db.put("drive_last_result", dict(res, t=now))
    return res


def summary():
    _ensure_table()
    r = db.one("SELECT COUNT(*) n, COALESCE(SUM(size),0) b, COALESCE(SUM(CASE WHEN freed IS NOT NULL THEN size END),0) f, "
               "COUNT(DISTINCT video_id) v FROM drive_backups WHERE verified IS NOT NULL")
    return {"connected": connected(), "account": db.get("drive_account", ""), "files": r["n"], "shorts": r["v"],
            "bytes": r["b"], "freed_bytes": r["f"], "waiting": len(pending()) if connected() else None,
            "last": db.get("drive_last_result")}


def say():
    s = summary()
    if not s["connected"]:
        return "Google Drive backup isn't set up yet. Double-click Connect Google Drive in the Jarvis folder."
    acct = f" ({s['account']})" if s["account"] else ""
    out = (f"{s['shorts']} Short{'s' if s['shorts'] != 1 else ''} backed up to Google Drive{acct}, "
           f"{s['bytes'] / (1 << 20):.0f} MB in all; {s['freed_bytes'] / (1 << 20):.0f} MB cleared from the Mac.")
    if s["waiting"]:
        out += f" {s['waiting']} file{'s' if s['waiting'] != 1 else ''} still to copy."
    return out
