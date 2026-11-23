"""The studio's database: studio_data/studio.db (SQLite, WAL).

Migrations are numbered and run once each, in order, inside a transaction; the version is kept in
PRAGMA user_version. Never edit an old migration: add a new one. Historical rows (analytics
snapshots, eligibility snapshots, audit log) are only ever inserted, never overwritten.

Production job states (a job is one Short, from idea to YouTube):
    researching -> scripting -> fact_checking -> generating_audio -> rendering -> validating
    -> ready -> uploading -> processing -> scheduled -> published ; failed ; rejected
"""
import json
import sqlite3
import threading
import time

from studio import data_dir

STATES = ["researching", "scripting", "fact_checking", "generating_audio", "rendering", "validating",
          "ready", "uploading", "processing", "scheduled", "published", "failed", "rejected"]
FINAL = ("published", "failed", "rejected")
WORK_STATES = STATES[:10]                    # stages the worker runs (researching ... scheduled)

MIGRATIONS = [
    # 1: the full record set from the business spec (Part 16)
    """
    CREATE TABLE channel (
        id INTEGER PRIMARY KEY CHECK (id = 1), youtube_channel_id TEXT, title TEXT, handle TEXT,
        niche TEXT, positioning TEXT, voice TEXT, locked_at REAL, settings TEXT DEFAULT '{}', updated REAL);
    CREATE TABLE niche_research (
        id INTEGER PRIMARY KEY, run_id TEXT, niche TEXT, scores TEXT DEFAULT '{}', overall REAL,
        evidence TEXT DEFAULT '{}', confidence TEXT, chosen INTEGER DEFAULT 0, created REAL);
    CREATE TABLE competitors (
        id INTEGER PRIMARY KEY, run_id TEXT, niche TEXT, youtube_channel_id TEXT, title TEXT,
        stats TEXT DEFAULT '{}', fetched REAL, UNIQUE(run_id, youtube_channel_id));
    CREATE TABLE pillars (
        id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT, active INTEGER DEFAULT 1, created REAL);
    CREATE TABLE topics (
        id INTEGER PRIMARY KEY, title TEXT, fingerprint TEXT UNIQUE, pillar TEXT, kind TEXT,
        sources TEXT DEFAULT '[]', score REAL, scores TEXT DEFAULT '{}', status TEXT DEFAULT 'idea',
        video_id INTEGER, discovered REAL, updated REAL);
    CREATE TABLE scripts (
        id INTEGER PRIMARY KEY, video_id INTEGER, version INTEGER, hooks TEXT DEFAULT '[]', hook TEXT,
        body TEXT, structure TEXT, checks TEXT DEFAULT '{}', status TEXT, created REAL);
    CREATE TABLE sources (
        id INTEGER PRIMARY KEY, video_id INTEGER, topic_id INTEGER, claim TEXT, url TEXT, title TEXT,
        retrieved REAL, verdict TEXT, note TEXT);
    CREATE TABLE voiceovers (
        id INTEGER PRIMARY KEY, video_id INTEGER, path TEXT, engine TEXT, voice TEXT, model_version TEXT,
        seconds REAL, loudness REAL, checks TEXT DEFAULT '{}', created REAL);
    CREATE TABLE assets (
        id INTEGER PRIMARY KEY, video_id INTEGER, kind TEXT, path TEXT, origin TEXT, source_url TEXT,
        license TEXT, attribution TEXT, sha256 TEXT, created REAL);
    CREATE TABLE videos (
        id INTEGER PRIMARY KEY, topic_id INTEGER, idem_key TEXT UNIQUE, state TEXT, stage_done TEXT,
        attempts INTEGER DEFAULT 0, not_before REAL DEFAULT 0, heartbeat REAL DEFAULT 0, owner TEXT,
        error TEXT, error_kind TEXT, title TEXT, path TEXT, duration REAL, meta TEXT DEFAULT '{}',
        created REAL, updated REAL, finished REAL);
    CREATE TABLE qa_checks (
        id INTEGER PRIMARY KEY, video_id INTEGER, check_name TEXT, passed INTEGER, detail TEXT, t REAL);
    CREATE TABLE publish_jobs (
        id INTEGER PRIMARY KEY, video_id INTEGER UNIQUE, channel_id TEXT, status TEXT, attempts INTEGER DEFAULT 0,
        session_url TEXT, youtube_id TEXT, privacy TEXT, publish_at TEXT, response TEXT, error TEXT,
        not_before REAL DEFAULT 0, created REAL, updated REAL);
    CREATE TABLE published (
        id INTEGER PRIMARY KEY, video_id INTEGER UNIQUE, youtube_id TEXT UNIQUE, channel_id TEXT,
        title TEXT, privacy TEXT, published_at TEXT, verified REAL);
    CREATE TABLE analytics (
        id INTEGER PRIMARY KEY, youtube_id TEXT, video_id INTEGER, fetched REAL, period TEXT, source TEXT,
        metrics TEXT DEFAULT '{}', unavailable TEXT DEFAULT '[]');
    CREATE TABLE eligibility (
        id INTEGER PRIMARY KEY, fetched REAL, rules_checked TEXT, values_json TEXT DEFAULT '{}',
        unavailable TEXT DEFAULT '[]', source TEXT);
    CREATE TABLE tools (
        name TEXT PRIMARY KEY, purpose TEXT, license TEXT, local INTEGER, needs_key INTEGER,
        cost_usd REAL DEFAULT 0, limits TEXT, verified REAL, status TEXT, fails INTEGER DEFAULT 0,
        open_until REAL DEFAULT 0, last_error TEXT);
    CREATE TABLE errors (
        id INTEGER PRIMARY KEY, t REAL, kind TEXT, video_id INTEGER, stage TEXT, detail TEXT,
        recovery TEXT, result TEXT, remaining TEXT);
    CREATE TABLE recovery_events (
        id INTEGER PRIMARY KEY, t REAL, what TEXT, video_id INTEGER, detail TEXT, result TEXT);
    CREATE TABLE experiments (
        id INTEGER PRIMARY KEY, name TEXT, variable TEXT, arms TEXT DEFAULT '[]', status TEXT,
        result TEXT DEFAULT '{}', started REAL, ended REAL);
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY, t REAL, area TEXT, decision TEXT, evidence TEXT DEFAULT '{}');
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY, t REAL, action TEXT, allowed INTEGER, detail TEXT);
    CREATE TABLE notices (
        id INTEGER PRIMARY KEY, t REAL, key TEXT, level TEXT, msg TEXT, told INTEGER DEFAULT 0);
    CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
    CREATE INDEX videos_state ON videos(state);
    CREATE INDEX analytics_vid ON analytics(youtube_id, fetched);
    CREATE INDEX errors_t ON errors(t);
    CREATE INDEX audit_t ON audit(t);
    """,
    # 2: content intelligence (Phase 5): the idea database, source checks, discovery runs
    """
    ALTER TABLE topics ADD COLUMN angle TEXT;
    ALTER TABLE topics ADD COLUMN hook TEXT;
    ALTER TABLE topics ADD COLUMN why TEXT;
    ALTER TABLE topics ADD COLUMN keywords TEXT DEFAULT '[]';
    ALTER TABLE topics ADD COLUMN origin TEXT;
    ALTER TABLE topics ADD COLUMN fresh_until REAL;
    ALTER TABLE topics ADD COLUMN similar_to INTEGER;
    ALTER TABLE topics ADD COLUMN reject_reason TEXT;
    ALTER TABLE topics ADD COLUMN script_status TEXT DEFAULT 'none';
    ALTER TABLE topics ADD COLUMN publish_status TEXT DEFAULT 'none';
    ALTER TABLE topics ADD COLUMN performance TEXT DEFAULT '{}';
    ALTER TABLE topics ADD COLUMN selected REAL;
    ALTER TABLE sources ADD COLUMN domain TEXT;
    ALTER TABLE sources ADD COLUMN tier INTEGER;
    ALTER TABLE sources ADD COLUMN http_status INTEGER;
    CREATE TABLE discovery_runs (
        id INTEGER PRIMARY KEY, t REAL, pillar TEXT, found INTEGER, added INTEGER, duplicates INTEGER,
        rejected INTEGER, claude_calls INTEGER, youtube_searches INTEGER, detail TEXT DEFAULT '{}');
    CREATE INDEX topics_status ON topics(status);
    CREATE INDEX sources_topic ON sources(topic_id)
    """,
    # 3: when each job entered its current stage (the dashboard's "for 4 min")
    """
    ALTER TABLE videos ADD COLUMN state_since REAL
    """,
]

_conns = {}
_lock = threading.RLock()


def path():
    return data_dir() / "studio.db"


def version(c=None):
    return (c or conn()).execute("PRAGMA user_version").fetchone()[0]


def migrate(c):
    """Runs every migration newer than the database, each in its own transaction."""
    current = version(c)
    for n, sql in enumerate(MIGRATIONS, start=1):
        if n <= current:
            continue
        c.execute("BEGIN IMMEDIATE")
        try:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                c.execute(stmt)
            c.execute(f"PRAGMA user_version={n}")
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return version(c)


def conn():
    key = (threading.get_ident(), str(path()))
    c = _conns.get(key)
    if c is None:
        c = sqlite3.connect(str(path()), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        with _lock:
            migrate(c)
        _conns[key] = c
    return c


def reset_connections():
    for c in list(_conns.values()):
        try:
            c.close()
        except Exception:
            pass
    _conns.clear()


class transaction:
    """with db.transaction(): ...  (BEGIN IMMEDIATE, so two workers can't claim the same job)"""
    def __enter__(self):
        _lock.acquire()
        conn().execute("BEGIN IMMEDIATE")
        return conn()

    def __exit__(self, kind, value, tb):
        try:
            conn().execute("COMMIT" if kind is None else "ROLLBACK")
        finally:
            _lock.release()
        return False


def q(sql, *args):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, *args):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def run(sql, *args):
    return conn().execute(sql, args).rowcount


def insert(sql, *args):
    return conn().execute(sql, args).lastrowid


def get(key, default=None):
    r = conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def put(key, value):
    conn().execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))


def video(vid):
    return one("SELECT * FROM videos WHERE id=?", vid)


def set_video(vid, **changes):
    changes["updated"] = time.time()
    if "state" in changes:
        run("UPDATE videos SET state_since=? WHERE id=? AND (state IS NOT ? OR state_since IS NULL)",
            changes["updated"], vid, changes["state"])
    cols = ", ".join(f'"{k}"=?' for k in changes)
    run(f"UPDATE videos SET {cols} WHERE id=?", *changes.values(), vid)
