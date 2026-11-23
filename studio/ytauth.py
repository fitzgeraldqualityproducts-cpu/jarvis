"""The business channel's Google sign-in (Phase 4). Separate from the clipper's sign-in.

Tokens: studio_data/secrets/youtube_business.json (chmod 600, never logged). Scopes: only the
three in guard.SCOPES (upload, read-only, analytics read-only). connect() signs in, reads which
channel the account belongs to, and locks the studio to it, refusing Connor's existing channel
(guard.lock_channel). Every later use re-checks the channel (whoami + guard.verify_destination).

Uses the same Google OAuth client file as the clipper (clipper_data/secrets/google_client.json)."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from studio import data_dir, guard
from studio.jobs import AuthProblem

API = "https://www.googleapis.com/youtube/v3"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"


def _path():
    d = data_dir("secrets")
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d / "youtube_business.json"


def connected():
    return _path().exists()


def _save(tok):
    from clipper import youtube
    youtube._write_secret(_path(), tok)


def _tokens():
    if not _path().exists():
        raise AuthProblem("the business channel isn't connected (double-click Connect YouTube Business)")
    return json.loads(_path().read_text())


def access_token():
    from clipper import youtube
    tok = _tokens()
    if time.time() < float(tok.get("expires_at", 0)):
        return tok["access_token"]
    try:
        youtube.refresh(tok)
    except youtube.NotConnected as e:
        guard.audit("token.refresh", False, str(e))
        raise AuthProblem("Google signed the business channel out. Double-click Connect YouTube Business again.")
    _save(tok)
    guard.audit("token.refresh", True, "business channel")
    return tok["access_token"]


OPEN = None          # tests swap this: (url, headers) -> dict


def _get(url, params, kind="list"):
    from clipper import youtube
    youtube._spend(kind)
    full = url + "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {access_token()}"}
    if OPEN:
        return OPEN(full, headers)
    try:
        with urllib.request.urlopen(urllib.request.Request(full, headers=headers), timeout=30, context=youtube._ctx()) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise AuthProblem("Google refused the business channel's sign-in")
        raise RuntimeError(guard.redact(f"YouTube API {e.code}: {body}"))


def whoami():
    """The channel the signed-in account belongs to right now: {id, title, handle}."""
    guard.allow("youtube.read_channel", "whoami", record=False)
    r = _get(f"{API}/channels", {"part": "snippet,status", "mine": "true"})
    items = r.get("items") or []
    if not items:
        raise guard.WrongChannel("that Google account has no YouTube channel yet; create the channel first")
    it = items[0]
    return {"id": it["id"], "title": it["snippet"]["title"], "handle": it["snippet"].get("customUrl", ""),
            "status": it.get("status", {})}


def granted_scopes():
    """The permissions Google actually granted (from Google's tokeninfo endpoint)."""
    url = TOKENINFO + "?" + urllib.parse.urlencode({"access_token": access_token()})
    if OPEN:
        r = OPEN(url, {})
    else:
        from clipper import youtube
        with urllib.request.urlopen(url, timeout=30, context=youtube._ctx()) as resp:
            r = json.loads(resp.read().decode())
    return (r.get("scope") or "").split()


def connect(open_browser=True, timeout=300):
    """Sign in, identify the channel, lock the studio to it. Returns the channel dict."""
    from clipper import youtube
    guard.check_scopes(guard.SCOPES)
    tok = youtube.exchange(youtube.browser_code(guard.SCOPES, open_browser, timeout,
                                                done_text="Jarvis is connected to the business channel."))
    _save(tok)
    try:
        me = whoami()
        guard.lock_channel(me["id"], me["title"])
    except Exception:
        _path().unlink()                       # never keep a sign-in for the wrong channel
        raise
    guard.audit("channel.connect", True, {"channel": me["id"], "title": me["title"]})
    return me


def verify():
    """Checks the connection end to end without changing anything. Returns a report dict."""
    rep = {"connected": connected(), "locked": guard.locked_channel()}
    if not rep["connected"]:
        return rep
    me = whoami()
    rep["signed_in"] = me
    try:
        guard.verify_destination(me["id"])
        rep["destination_ok"] = True
    except guard.WrongChannel as e:
        rep["destination_ok"], rep["problem"] = False, str(e)
    scopes = granted_scopes()
    rep["can_upload"] = guard.SCOPES[0] in scopes
    rep["can_read_analytics"] = guard.SCOPES[2] in scopes
    rep["extra_scopes"] = [s for s in scopes if s.startswith("https://www.googleapis.com/auth/") and s not in guard.SCOPES]
    rep["ok"] = rep["destination_ok"] and rep["can_upload"] and not rep["extra_scopes"]
    return rep


def protect_existing(handle_or_id):
    """Adds Connor's existing channel to the protected list (by @handle or UC... id)."""
    from studio import config, ytpublic
    v = handle_or_id.strip()
    if v.startswith("UC") and len(v) == 24:
        cid, title = v, v
    else:
        h = v if v.startswith("@") else "@" + v
        r = ytpublic.get("channels", {"part": "snippet", "forHandle": h})
        items = r.get("items") or []
        if not items:
            raise ValueError(f"YouTube has no channel with the handle {h}")
        cid, title = items[0]["id"], items[0]["snippet"]["title"]
    ids = list(config.general().get("protected_channels") or [])
    if cid not in ids:
        ids.append(cid)
        config.set_values(protected_channels=ids)
    guard.audit("channel.protect", True, {"channel": cid, "title": title})
    return {"id": cid, "title": title}
