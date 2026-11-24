"""The live YouTube business dashboard (studio.html at /studio on Jarvis's display server).

An "assembly line" of nine stations from Idea to Live. Every Short sits at the station it's on right
now, read straight from studio.db; nothing here is made up or changes anything. The page asks for
state() every few seconds.

When Jarvis isn't running, `python -m studio dashboard` serves the same page by itself."""
import json
import time
import urllib.parse
from pathlib import Path

from studio import config, db, guard

PAGE = Path(__file__).resolve().parent.parent / "studio.html"

# (key, label, icon, the job states that sit at this station)
STATIONS = [
    ("idea", "Idea", "💡", ("researching",)),
    ("script", "Script", "✍️", ("scripting",)),
    ("factcheck", "Fact-check", "🔎", ("fact_checking",)),
    ("voice", "Voice", "🎙️", ("generating_audio",)),
    ("video", "Video", "🎬", ("rendering",)),
    ("qa", "Quality check", "✅", ("validating",)),
    ("ready", "Ready", "📦", ("ready",)),
    ("upload", "Uploading", "⬆️", ("uploading", "processing", "scheduled")),
    ("live", "Live", "📺", ("published",)),
]
STATION_OF = {s: i for i, (_, _, _, states) in enumerate(STATIONS) for s in states}

# What the card says while a Short is at each state
DOING = {
    "researching": "Researching the idea", "scripting": "Writing the script", "fact_checking": "Checking every fact",
    "generating_audio": "Recording the narration", "rendering": "Drawing and rendering the video",
    "validating": "Running the quality checks", "ready": "Finished, waiting to go out",
    "uploading": "Uploading to YouTube", "processing": "YouTube is processing it",
    "scheduled": "Uploaded, goes public soon", "published": "Live on YouTube",
    "failed": "Stopped here", "rejected": "Turned down by the safety checks",
}
RECENT_FAIL = 24 * 3600          # failed / rejected Shorts stay on the line this long


def _j(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default


def _views(youtube_id):
    from studio import analytics
    a = (analytics.latest(youtube_id, "analytics") or {}).get("metrics") or {}
    d = (analytics.latest(youtube_id, "data_api") or {}).get("metrics") or {}
    v = a.get("views", d.get("viewCount"))
    return int(v) if v is not None else None


def _thumb(vid):
    folder = _folder(vid)
    for name in ("thumbnail.jpg", "thumbnail.png", "scene_01.png"):
        if (folder / name).is_file():
            return folder / name
    return None


def _folder(vid):
    from studio import home
    return home() / "videos" / str(int(vid))


def thumb_file(vid):
    """The picture for a Short's card (its thumbnail or first scene), or None."""
    return _thumb(vid)


def _card(v, now, publish_jobs, last_error):
    state = v["state"]
    failed = state in ("failed", "rejected")
    where = v["stage_done"] if failed else state
    if failed:
        err = last_error.get(v["id"])
        if err and err["stage"] in STATION_OF:
            where = err["stage"]
        elif v["stage_done"] in STATION_OF:            # failed in the stage after the last finished one
            from studio.db import STATES
            i = STATES.index(v["stage_done"]) if v["stage_done"] in STATES else 0
            where = STATES[min(i + 1, 9)]
        else:
            where = "researching"
    meta = _j(v["meta"], {})
    pj = publish_jobs.get(v["id"]) or {}
    waiting = None
    if not failed and (v["not_before"] or 0) > now:
        waiting = {"until": v["not_before"], "why": meta.get("waiting") or (v["error"] or "")[:160]}
    title = v["title"] or ""
    if not title and v["topic_id"]:
        t = db.one("SELECT title FROM topics WHERE id=?", v["topic_id"])
        title = t["title"] if t else ""
    return {
        "id": v["id"], "title": title or f"Short #{v['id']}", "state": state,
        "station": STATION_OF.get(where, 0), "doing": DOING.get(state, state.replace("_", " ")),
        "since": v.get("state_since") or v["created"] or v["updated"], "created": v["created"],
        "working": bool(v["owner"]) and now - (v["heartbeat"] or 0) < 300,
        "attempts": v["attempts"] or 0, "waiting": waiting,
        "error": guard.redact(v["error"] or "")[:220] if failed or v["error"] else "",
        "failed": failed, "duration": v["duration"],
        "thumb": bool(_thumb(v["id"])), "publish_at": pj.get("publish_at"), "privacy": pj.get("privacy"),
        "youtube_id": pj.get("youtube_id"),
    }


def _activity(now, limit=14):
    """What the studio did lately, newest first, in plain words."""
    out = []
    day = now - 3 * 86400
    for r in db.q("SELECT t, what, detail, result, video_id FROM recovery_events WHERE t>? ORDER BY t DESC LIMIT 20", day):
        out.append({"t": r["t"], "kind": "info", "text": " ".join(x for x in (r["what"], r["detail"]) if x).strip()})
    for r in db.q("SELECT t, stage, detail, recovery, video_id FROM errors WHERE t>? ORDER BY t DESC LIMIT 20", day):
        stage = (r["stage"] or "").replace("_", " ")
        out.append({"t": r["t"], "kind": "bad", "text": f"Problem at {stage}: {guard.redact(r['detail'] or '')[:120]}"
                    + (f" ({r['recovery']})" if r["recovery"] else "")})
    for r in db.q("SELECT t, area, decision FROM decisions WHERE t>? ORDER BY t DESC LIMIT 20", day):
        out.append({"t": r["t"], "kind": "info", "text": f"{(r['area'] or '').capitalize()}: {r['decision']}"})
    for r in db.q("SELECT t, level, msg FROM notices WHERE t>? ORDER BY t DESC LIMIT 20", day):
        out.append({"t": r["t"], "kind": "warn" if r["level"] in ("warn", "error") else "info", "text": r["msg"]})
    privacy = {r["video_id"]: r["privacy"] for r in db.q("SELECT video_id, privacy FROM published")}
    for r in db.q("SELECT id, title, state, updated FROM videos WHERE updated>? ORDER BY updated DESC LIMIT 20", day):
        st = r["state"]
        went = "went live" if privacy.get(r["id"]) == "public" else f"was uploaded ({privacy.get(r['id']) or 'not public yet'})"
        word = {"ready": "finished and passed every check", "published": went, "failed": "failed",
                "rejected": "was turned down by the safety checks", "scheduled": "was uploaded and scheduled"}.get(st)
        if word:
            out.append({"t": r["updated"], "kind": "good" if st in ("ready", "published", "scheduled") else "bad",
                        "text": f"\"{r['title'] or 'Short #' + str(r['id'])}\" {word}"})
        else:
            out.append({"t": r["updated"], "kind": "info",
                        "text": f"\"{r['title'] or 'Short #' + str(r['id'])}\": {DOING.get(st, st).lower()}"})
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: -(e["t"] or 0)):
        k = (int(e["t"] or 0) // 60, e["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq[:limit]


def _stats():
    from studio import analytics, eligibility
    ch = guard.locked_channel()
    out = {"subscribers": None, "views": None, "videos": None, "fetched": None,
           "ypp": None}
    if ch:
        m = analytics.latest(ch["youtube_channel_id"], "channel")
        if m:
            met = m["metrics"]
            out.update(subscribers=met.get("subscriberCount"), views=met.get("viewCount"),
                       videos=met.get("videoCount"), fetched=m["fetched"])
    rep = eligibility.latest()
    if rep:
        tier = (rep.get("tiers") or {}).get("full") or next(iter((rep.get("tiers") or {}).values()), None)
        if tier:
            rules = tier["rules"]
            shares = [r["share"] for r in rules.values() if r["share"] is not None]
            share = tier.get("share", min(shares) if shares else None)     # with routes: the best route counts
            out["ypp"] = {"share": share, "met": tier["met"], "checked": rep["checked"],
                          "rules": {k: {"have": r["have"], "need": r["need"], "met": r["met"]} for k, r in rules.items()},
                          "estimate": any("ESTIMATE" in b for b in (rep.get("basis") or {}).values())}
    return out


def state(now=None):
    """Everything the dashboard shows, from the studio's own records."""
    from studio import autopilot, jobs, publish
    now = now or time.time()
    g = config.general()
    on = config.enabled()
    ch = guard.locked_channel()
    ap = autopilot.status(now)
    paused = jobs.paused()
    if not on:
        run = {"level": "off", "text": "Switched off"}
    elif ap["halted"]:
        run = {"level": "bad", "text": "Stopped: " + str(ap["halted"].get("reason") or "too many failures")}
    elif paused:
        run = {"level": "warn", "text": "Paused"}
    elif not ap["on"]:
        run = {"level": "warn", "text": "Autopilot off (makes Shorts only when asked)"}
    else:
        run = {"level": "good", "text": "Running on its own"}

    pjs = {r["video_id"]: dict(r) for r in db.q("SELECT video_id, publish_at, privacy, youtube_id, status FROM publish_jobs")}
    last_err = {}
    for r in db.q("SELECT video_id, stage FROM errors ORDER BY id"):
        last_err[r["video_id"]] = dict(r)
    rows = db.q("SELECT * FROM videos WHERE state NOT IN ('published','failed','rejected') "
                "OR (state IN ('failed','rejected') AND COALESCE(finished, updated, 0)>?) ORDER BY id", now - RECENT_FAIL)
    cards = [_card(dict(v), now, pjs, last_err) for v in rows]

    pub = []
    for r in db.q("SELECT p.video_id, p.youtube_id, p.title, p.privacy, p.published_at, v.updated FROM published p "
                  "LEFT JOIN videos v ON v.id=p.video_id ORDER BY p.id DESC LIMIT 8"):
        pub.append({"id": r["video_id"], "youtube_id": r["youtube_id"], "title": r["title"], "privacy": r["privacy"],
                    "published_at": r["published_at"], "views": _views(r["youtube_id"]),
                    "thumb": bool(r["video_id"] and _thumb(r["video_id"]))})
    # "Live" means public on YouTube; an upload that's still private is counted (and shown) as private
    live_count = db.one("SELECT COUNT(*) n FROM videos v JOIN published p ON p.video_id=v.id "
                        "WHERE v.state='published' AND p.privacy='public'")["n"]
    private_count = db.one("SELECT COUNT(*) n FROM videos v LEFT JOIN published p ON p.video_id=v.id "
                           "WHERE v.state='published' AND COALESCE(p.privacy, '')<>'public'")["n"]

    mode = publish.mode()
    sched = [float(r["publish_at"]) for r in db.q("SELECT publish_at FROM publish_jobs WHERE publish_at IS NOT NULL")
             if r["publish_at"] and float(r["publish_at"]) > now]
    if sched:
        nxt = {"t": min(sched), "text": None}
    elif mode == "manual":
        nxt = {"t": None, "text": "You upload them (manual mode)"}
    else:
        try:
            nxt = {"t": publish.next_slot(now), "text": None}
        except Exception:
            nxt = {"t": None, "text": "Not scheduled yet"}

    counts = {k: 0 for k, _, _, _ in STATIONS}
    for c in cards:
        counts[STATIONS[c["station"]][0]] += 1
    counts["live"] = live_count
    ideas = db.one("SELECT COUNT(*) n FROM topics WHERE status='idea'")["n"]

    return {
        "now": now, "page": PAGE.stat().st_mtime if PAGE.exists() else 0,
        "channel": {"title": (ch or {}).get("title") or "Your channel", "handle": _handle(),
                    "connected": bool(ch)},
        "run": run, "mode": mode, "autopilot": ap, "stop_after": g["stop_after_failures"],
        "stations": [{"key": k, "label": lab, "icon": ic, "count": counts[k]} for k, lab, ic, _ in STATIONS],
        "cards": cards, "ideas": ideas, "published": pub, "live_count": live_count, "private_count": private_count,
        "next_upload": nxt, "stats": _stats(), "activity": _activity(now),
        "cost_usd": sum(t["cost_usd"] or 0 for t in db.q("SELECT cost_usd FROM tools")),
    }


def _handle():
    r = db.one("SELECT handle FROM channel WHERE id=1")
    return (r["handle"] if r else "") or ""


# ---------- HTTP (shared by display.py and the stand-alone server) ----------

def handle(path):
    """(status, body bytes, content type) for a /studio path, or None if it isn't one."""
    path = urllib.parse.urlparse(path).path
    if not path.startswith("/studio"):
        return None
    if path in ("/studio", "/studio/"):
        return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
    try:
        if path == "/studio/state":
            return 200, json.dumps(state(), default=str).encode(), "application/json"
        if path.startswith("/studio/thumb/") and path.endswith(".img") and path[14:-4].isdigit():
            f = thumb_file(int(path[14:-4]))
            if f:
                ctype = "image/jpeg" if f.suffix == ".jpg" else "image/png"
                return 200, f.read_bytes(), ctype
    except Exception as e:
        return 500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json"
    return 404, b"not found", "text/plain"


def serve(port=8766, open_browser=True):
    """Serves the dashboard by itself (when Jarvis isn't running)."""
    import subprocess
    import sys
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path if self.path.startswith("/studio") else "/studio"
            code, body, ctype = handle(p)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=300" if "/thumb/" in p else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://localhost:{port}/studio"
    print(f"YouTube dashboard: {url}  (Ctrl+C or close this window to stop)")
    if open_browser:
        if sys.platform == "darwin":
            subprocess.run(["open", url])
        else:
            webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
