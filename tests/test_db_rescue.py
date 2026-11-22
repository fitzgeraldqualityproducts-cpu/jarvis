"""Damaged databases are repaired without losing anything, lost clips come back, and updates can
never replace a database again."""
import sqlite3
import subprocess
from pathlib import Path

import pytest

import dbrescue
from conftest import ROOT


def _make(path, n=50):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    c.executemany("INSERT INTO notes(body) VALUES(?)", [(f"note {i} " + "x" * 300,) for i in range(n)])
    c.commit()
    c.close()


def _damage(path):
    """Scribbles over the middle of the file (a few table pages), like a bad write would."""
    data = bytearray(Path(path).read_bytes())
    page = int.from_bytes(data[16:18], "big") or 65536
    pages = len(data) // page
    for k in range(pages // 2, pages // 2 + 2):
        data[k * page: k * page + 120] = b"\xff" * 120
    Path(path).write_bytes(bytes(data))


def test_healthy_database_is_left_alone(tmp_path):
    db = tmp_path / "a.db"
    _make(db)
    assert dbrescue.check(db)
    assert dbrescue.repair(db) is None
    assert not (tmp_path / "db-rescue").exists()
    assert dbrescue.check(tmp_path / "missing.db")


def test_damaged_database_is_rebuilt_and_the_damaged_copy_kept(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 200)
    _damage(db)
    assert not dbrescue.check(db)
    msg = dbrescue.repair(db, log=lambda m: None)
    assert msg and "rebuilt" in msg
    assert dbrescue.check(db)
    left = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert 100 <= left <= 200                           # most rows kept, only the damaged page lost
    saved = list((tmp_path / "db-rescue").glob("a-*/a.db"))
    assert saved and not dbrescue.check(saved[0])       # the damaged original is kept, untouched
    assert not list((tmp_path / "db-rescue").glob("*/work.db*"))


def test_python_salvage_works_without_the_sqlite3_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(dbrescue.shutil, "which", lambda name: None)
    db = tmp_path / "a.db"
    _make(db, 200)
    _damage(db)
    msg = dbrescue.repair(db, log=lambda m: None)
    assert "table by table" in msg
    assert dbrescue.check(db)
    assert sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM notes").fetchone()[0] >= 100


def test_backup_is_used_when_it_has_more(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 200)
    assert dbrescue.backup(db)
    assert not dbrescue.backup(db)                      # once a day
    assert (tmp_path / "a.db.bak").exists()
    Path(db).write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)   # wrecked beyond salvage
    msg = dbrescue.repair(db, log=lambda m: None)
    assert "backup" in msg
    assert sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 200


def test_damaged_database_never_overwrites_a_good_backup(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 200)
    dbrescue.backup(db)
    _damage(db)
    assert not dbrescue.backup(db, force=True)
    assert dbrescue.check(tmp_path / "a.db.bak")


def _clip_files(jid, cid, title, seconds=12):
    from clipper import home, media
    from conftest import make_video
    lib = home() / "library" / "main"
    lib.mkdir(parents=True, exist_ok=True)
    f = make_video(lib / f"2026-11-21_job{jid}_clip{cid}.mp4", seconds=seconds, size="320x568")
    f.with_suffix(".jpg").write_bytes(b"jpg")
    folder = home() / "jobs" / str(jid)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_lost_clips_are_relinked_from_the_library(tmp_path):
    from clipper import db, health, media
    db.q("SELECT 1")
    folder = _clip_files(2, 3, "The forty dollar habit")
    _clip_files(2, 4, "Untitled one", seconds=8)
    media.dump({"candidates": [
        {"title": "The forty dollar habit", "start": 30.0, "end": 43.0, "duration": 13.0, "score": 88,
         "hook": "I was broke", "category": "story", "titles": ["A", "B"]},
        {"title": "Something else", "start": 90.0, "end": 98.5, "duration": 8.5, "score": 70}]}, folder / "candidates.json")
    (folder / "job.log").write_text('12:00 Clip 3 ready: "The forty dollar habit" (88/100)\n')
    assert health.relink_clips() == 2
    c3, c4 = db.clip(3), db.clip(4)
    assert c3["title"] == "The forty dollar habit" and c3["score"] == 88 and c3["start"] == 30.0
    assert c3["state"] == "awaiting_approval" and "re-linked" in c3["note"]
    assert c4["title"] == "Something else"              # matched by length
    assert health.relink_clips() == 0                   # only ever once


def test_startup_repairs_the_clipper_database(tmp_path):
    from clipper import db, health
    db.add_clip(id=1, title="keep me", state="awaiting_approval")
    for i in range(300):
        db.log(f"event {i} " + "y" * 200)
    db.reset_connections()
    p = db.path()
    c = sqlite3.connect(str(p))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    _damage(p)
    assert not dbrescue.check(p)
    health.startup(log=lambda m: None)
    assert dbrescue.check(p)
    assert db.clip(1)["title"] == "keep me"


def _git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


def test_updates_can_never_replace_a_database(tmp_path, monkeypatch):
    import updater
    up, me = tmp_path / "upstream", tmp_path / "jarvis"
    (up / "clipper_data").mkdir(parents=True)
    _git(up, "init", "-q", "-b", "main")
    (up / "app.py").write_text("v1\n")
    (up / "clipper_data" / "clipper.db").write_bytes(b"empty from an update")   # the mistake
    _git(up, "add", "-A")
    _git(up, "commit", "-q", "-m", "upstream")
    _git(tmp_path, "clone", "-q", str(up), str(me))
    (me / "clipper_data" / "clipper.db").write_bytes(b"Connor's real clips")
    (me / "memory.db").write_bytes(b"memories")
    _git(me, "add", "-f", "-A")                          # what the old snapshot did
    _git(me, "commit", "-q", "-m", "Snapshot")
    monkeypatch.setattr(updater, "HERE", me)
    assert updater._is_data("clipper_data/clipper.db") and updater._is_data("memory.db")
    assert not updater._is_data("clipper/db.py")
    assert sorted(updater._untrack_data()) == ["clipper_data/clipper.db", "memory.db"]
    assert updater._untrack_data() == []
    # upstream changes code, then a later update stops tracking its stray database
    (up / "app.py").write_text("v2\n")
    _git(up, "commit", "-q", "-am", "code")
    _git(me, "fetch", "-q", "origin")
    _git(me, "merge", "-q", "--no-edit", "origin/main")
    _git(up, "rm", "-q", "clipper_data/clipper.db")
    _git(up, "commit", "-q", "-m", "stop tracking")
    _git(me, "fetch", "-q", "origin")
    _git(me, "merge", "-q", "--no-edit", "origin/main")
    assert (me / "app.py").read_text() == "v2\n"
    assert (me / "clipper_data" / "clipper.db").read_bytes() == b"Connor's real clips"
    assert (me / "memory.db").read_bytes() == b"memories"


def test_tests_never_write_into_the_real_clipper_folder():
    tracked = (ROOT / "clipper_data" / "clipper.db")
    # the only file allowed there is the old one git still tracks (removed in a later update)
    extras = [p.name for p in (ROOT / "clipper_data").glob("*") if p.name != "clipper.db"] \
        if (ROOT / "clipper_data").exists() else []
    assert extras == []
    if tracked.exists():
        assert not Path(str(tracked) + "-wal").exists()
