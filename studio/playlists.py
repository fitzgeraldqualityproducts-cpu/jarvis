"""Playlists by series: each pillar of the channel is a series playlist, and every public Short is
added to its series, so a viewer who likes one can watch the rest in a row.

Only the studio's own public Shorts on the locked channel, only playlists the studio made. It never
deletes or edits anything else. Needs the wider YouTube permission Connor approved on 2026-11-23
(Google's playlist permission also covers comments; guard.py still refuses any comment action).
Costs YouTube quota (50 units per playlist or item, out of 10,000 a day), and nothing else."""
import json
import time

from studio import config, db, guard, publish

PLAYLIST_SCOPE = "https://www.googleapis.com/auth/youtube"
EVERY_HOURS = 6


def can_manage():
    from studio import ytauth
    try:
        return PLAYLIST_SCOPE in ytauth.granted_scopes()
    except Exception:
        return False


def _post(path, params, body):
    from clipper import youtube
    youtube._spend("update")
    import urllib.parse
    status, _, raw = publish._http("POST", f"{publish.API}/{path}?" + urllib.parse.urlencode(params),
                                   dict(publish._auth(), **{"Content-Type": "application/json"}),
                                   json.dumps(body).encode())
    return publish._check(status, raw, path)


def _series(pillar):
    from studio import topics
    p = next((x for x in topics.pillars() if x.get("name") == pillar), None) or {"name": pillar}
    d = (topics.niche() or {}).get("name", "")
    desc = (p.get("description") or "") + ("\n\n" if p.get("description") else "") + \
        f"A {d} series by this channel. Narration is AI-generated; every number is sourced and checked."
    return {"title": f"{pillar}"[:150], "description": desc[:5000]}


def playlist_for(pillar, channel_id):
    """The series playlist id (made once, then remembered)."""
    key = f"playlist_{pillar}"
    pid = db.get(key)
    if pid:
        return pid
    guard.allow("youtube.playlist", f"create series playlist {pillar}")
    s = _series(pillar)
    r = _post("playlists", {"part": "snippet,status"},
              {"snippet": {"title": s["title"], "description": s["description"]}, "status": {"privacyStatus": "public"}})
    pid = r["id"]
    db.put(key, pid)
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", time.time(), "playlists",
              f"made series playlist {pillar}", json.dumps({"playlist": pid, "channel": channel_id}))
    return pid


def sync(now=None):
    """Adds every public studio Short that isn't in its series playlist yet. Returns how many were added."""
    from studio import analytics, topics
    if not can_manage():
        return 0
    todo = []
    for v in analytics.videos():
        if v.get("privacy") != "public" or not v.get("topic_id"):
            continue
        if db.get(f"in_playlist_{v['youtube_id']}"):
            continue
        t = topics.topic(v["topic_id"]) or {}
        if t.get("pillar"):
            todo.append((v, t["pillar"]))
    if not todo:
        return 0
    channel_id = publish._destination()               # signed in to the locked business channel, or stop
    added = 0
    for v, pillar in todo:
        pid = playlist_for(pillar, channel_id)
        guard.allow("youtube.playlist", f"add {v['youtube_id']} to {pillar}")
        _post("playlistItems", {"part": "snippet"},
              {"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": v["youtube_id"]}}})
        db.put(f"in_playlist_{v['youtube_id']}", pid)
        added += 1
    return added


def maybe_sync(now=None):
    now = now or time.time()
    if not config.enabled() or not guard.locked_channel() or not config.general().get("series_playlists", True):
        return None
    if now - float(db.get("playlists_last", 0) or 0) < EVERY_HOURS * 3600:
        return None
    db.put("playlists_last", now)
    return sync(now)
