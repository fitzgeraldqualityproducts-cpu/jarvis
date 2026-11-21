"""The clipper's database (clipper_data/clipper.db, SQLite).

Everything the clipper knows lives here, so it survives restarts and updates: channels, approved
sources, every video it has seen (so nothing is processed twice), jobs and their stage, clips,
uploads, performance numbers, a log of what it did, and a few switches (pause, emergency stop).

Job states, in the order a job normally moves through them:
    discovered -> awaiting_authorization -> queued -> acquiring -> transcribing -> analyzing
    -> selecting -> editing -> rendering -> quality_check -> awaiting_approval -> uploading
    -> published (or done when clips are saved but not uploaded) ; failed ; cancelled
"""
import json
import sqlite3
import threading
import time

from clipper import data_dir, home

JOB_STATES = ["discovered", "awaiting_authorization", "queued", "acquiring", "transcribing", "analyzing",
              "selecting", "editing", "rendering", "quality_check", "awaiting_approval", "uploading",
              "published", "done", "failed", "cancelled"]
ACTIVE_STATES = ["acquiring", "transcribing", "analyzing", "selecting", "editing", "rendering", "quality_check"]
FINAL_STATES = ["published", "done", "failed", "cancelled"]
CLIP_STATES = ["rendered", "qa_failed", "awaiting_approval", "approved", "rejected", "ready", "uploading",
               "uploaded", "scheduled", "published", "upload_failed", "regenerate", "deleted"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY, name TEXT, youtube_channel_id TEXT, youtube_title TEXT,
    settings TEXT DEFAULT '{}', created REAL);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY, channel TEXT, kind TEXT, value TEXT, label TEXT,
    authorized INTEGER DEFAULT 0, auth_basis TEXT, enabled INTEGER DEFAULT 1,
    last_checked REAL DEFAULT 0, created REAL, UNIQUE(channel, kind, value));
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY, channel TEXT, platform TEXT, external_id TEXT, url TEXT, path TEXT,
    title TEXT, creator TEXT, creator_id TEXT, duration REAL, published_at TEXT, license TEXT,
    auth_status TEXT, auth_basis TEXT, discovery_score REAL, source_id INTEGER,
    info TEXT DEFAULT '{}', discovered REAL, UNIQUE(channel, platform, external_id));
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, video_id INTEGER, channel TEXT, state TEXT, priority INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0, max_clips INTEGER, options TEXT DEFAULT '{}', error TEXT,
    detail TEXT, progress REAL DEFAULT 0, not_before REAL DEFAULT 0, held INTEGER DEFAULT 0,
    created REAL, updated REAL, started REAL, finished REAL, pid INTEGER, heartbeat REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY, job_id INTEGER, video_id INTEGER, channel TEXT, rank INTEGER,
    start REAL, "end" REAL, duration REAL, score REAL, scores TEXT DEFAULT '{}', hook TEXT, hook_type TEXT,
    reason TEXT, transcript TEXT, topic TEXT, title TEXT, titles TEXT DEFAULT '[]', description TEXT,
    hashtags TEXT DEFAULT '[]', tags TEXT DEFAULT '[]', category_id TEXT, audience TEXT, pinned_comment TEXT,
    caption_style TEXT, layout TEXT, path TEXT, thumb TEXT, qa TEXT DEFAULT '{}', state TEXT,
    fingerprint TEXT, youtube_id TEXT, privacy TEXT, publish_at TEXT, approved INTEGER DEFAULT 0,
    note TEXT, created REAL, updated REAL, extra TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY, clip_id INTEGER UNIQUE, channel TEXT, status TEXT, privacy TEXT,
    publish_at TEXT, session_url TEXT, youtube_id TEXT, attempts INTEGER DEFAULT 0, error TEXT,
    not_before REAL DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY, clip_id INTEGER, youtube_id TEXT, fetched REAL, views INTEGER,
    minutes REAL, avg_view_duration REAL, avg_view_pct REAL, likes INTEGER, comments INTEGER,
    shares INTEGER, subs_gained INTEGER, extra TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, t REAL, level TEXT, job_id INTEGER, clip_id INTEGER, msg TEXT,
    told INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY, t REAL, channel TEXT, clip_id INTEGER, job_id INTEGER, kind TEXT,
    detail TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS feedback_kind ON feedback(channel, kind);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS clips_state ON clips(state);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
"""

_lock = threading.RLock()
_conns = {}


def path():
    return data_dir() / "clipper.db"


def conn():
    """One connection per thread (SQLite's rule), all to the same file. WAL lets the dashboard read
    while a job writes."""
    key = (threading.get_ident(), str(path()))
    c = _conns.get(key)
    if c is None:
        c = sqlite3.connect(str(path()), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.executescript(SCHEMA)
        _migrate(c)
        _conns[key] = c
    return c


def _migrate(c):
    """Brings an older clipper.db up to date without losing anything."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(clips)").fetchall()}
    if "extra" not in cols:
        c.execute("ALTER TABLE clips ADD COLUMN extra TEXT DEFAULT '{}'")
    done = c.execute("SELECT value FROM kv WHERE key='score_scale'").fetchone()
    if not done:                                   # scores used to be out of 10, now out of 100
        c.execute("UPDATE clips SET score=score*10 WHERE score IS NOT NULL AND score<=10")
        c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('score_scale', '100')")


def reset_connections():
    """Drops cached connections (tests point the clipper at a fresh folder)."""
    for c in list(_conns.values()):
        try:
            c.close()
        except Exception:
            pass
    _conns.clear()


def q(sql, *args):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, *args):
    rows = q(sql, *args)
    return rows[0] if rows else None


def run(sql, *args):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


class transaction:
    """with db.transaction(): ... several statements, all or nothing."""
    def __enter__(self):
        _lock.acquire()
        conn().execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, kind, *_):
        try:
            conn().execute("ROLLBACK" if kind else "COMMIT")
        finally:
            _lock.release()
        return False


# ---------- small helpers ----------

def loads(text, default=None):
    try:
        return json.loads(text) if text else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def get(key, default=None):
    row = one("SELECT value FROM kv WHERE key=?", key)
    return loads(row["value"], default) if row else default


def put(key, value):
    run("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key, json.dumps(value))


def log(msg, level="info", job_id=None, clip_id=None, tell=False):
    """The clipper's operational log (shown on the dashboard, also printed to jarvis.log).
    tell=True queues it for Jarvis to say out loud."""
    print(f"(clipper{' job ' + str(job_id) if job_id else ''}: {msg})")
    try:
        run("INSERT INTO events(t, level, job_id, clip_id, msg, told) VALUES(?,?,?,?,?,?)",
            time.time(), level, job_id, clip_id, str(msg)[:2000], 0 if tell else 1)
    except Exception as e:
        print(f"(clipper log failed: {e})")


def news():
    """Things Jarvis should say out loud that he hasn't yet."""
    return q("SELECT * FROM events WHERE told=0 ORDER BY id LIMIT 5")


def mark_told(ids):
    for i in ids:
        run("UPDATE events SET told=1 WHERE id=?", i)


# ---------- jobs ----------

def job(job_id):
    j = one("SELECT * FROM jobs WHERE id=?", job_id)
    if j:
        j["options"] = loads(j["options"])
    return j


def set_job(job_id, **changes):
    if "options" in changes and not isinstance(changes["options"], str):
        changes["options"] = json.dumps(changes["options"])
    if "state" in changes and changes["state"] not in JOB_STATES:
        raise ValueError(f"unknown job state {changes['state']}")
    changes["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in changes)
    run(f"UPDATE jobs SET {cols} WHERE id=?", *changes.values(), job_id)


def video(video_id):
    v = one("SELECT * FROM videos WHERE id=?", video_id)
    if v:
        v["info"] = loads(v["info"])
    return v


def clip(clip_id):
    c = one("SELECT * FROM clips WHERE id=?", clip_id)
    return _clip_row(c) if c else None


def _clip_row(c):
    for k in ("scores", "qa", "extra"):
        c[k] = loads(c.get(k))
    for k in ("titles", "hashtags", "tags"):
        c[k] = loads(c[k], [])
    return c


def clips(where="1=1", *args, limit=200):
    return [_clip_row(c) for c in q(f"SELECT * FROM clips WHERE {where} ORDER BY id DESC LIMIT {int(limit)}", *args)]


def set_clip(clip_id, **changes):
    for k in ("scores", "qa", "titles", "hashtags", "tags", "extra"):
        if k in changes and not isinstance(changes[k], str):
            changes[k] = json.dumps(changes[k])
    if "state" in changes and changes["state"] not in CLIP_STATES:
        raise ValueError(f"unknown clip state {changes['state']}")
    changes["updated"] = time.time()
    cols = ", ".join(f'"{k}"=?' for k in changes)
    run(f"UPDATE clips SET {cols} WHERE id=?", *changes.values(), clip_id)


def add_clip(**fields):
    for k in ("scores", "qa", "titles", "hashtags", "tags", "extra"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    fields.setdefault("created", time.time())
    fields["updated"] = time.time()
    cols = ", ".join(f'"{k}"' for k in fields)
    return run(f"INSERT INTO clips({cols}) VALUES({', '.join('?' for _ in fields)})", *fields.values())


def where_am_i():
    return str(home())


def merge_extra(clip_id, **fields):
    """Adds/changes keys in a clip's extra JSON (category, title options, edits...)."""
    c = clip(clip_id)
    if not c:
        return None
    extra = dict(c.get("extra") or {})
    extra.update(fields)
    set_clip(clip_id, extra=extra)
    return extra
