"""Verified backups of everything the YouTube business can't rebuild: the three databases (studio.db,
clipper.db, memory.db), config.json and the studio's secrets (the channel sign-in and API keys).

  make()          one backup folder, <backups>/<YYYY-MM-DD_HHMMSS>/. Databases are copied with SQLite's own
                  backup API (a consistent copy even while Jarvis is writing), every copy is opened and
                  integrity-checked, and manifest.json records each file's SHA-256, size and table row
                  counts. The folder only gets its final name after a full re-check, so a half-made
                  backup never looks like a good one.
  verify(folder)  re-reads a backup: every file is there, every hash matches, every database is "ok".
  rotate()        keeps the newest 14 daily backups plus one per week for 8 weeks. Only folders this module
                  made (they carry its manifest) are ever removed, and never the newest verified one.
  plan(folder)    what a restore would change, file by file, with row counts now vs. in the backup.
  restore(folder) puts the databases back, only while Jarvis is closed, after first backing up what's
                  there now. config.json and secrets are restored only when asked for by name.
  maybe_run()     the daily automatic backup (a background chore of the studio worker).

Where: D:\\Jarvis\\backups on the PC (a different disk from the databases on C:), config
"studio.backups_dir" if set, else studio_data/backups (same disk; status() says so).
Backups never leave this computer and nothing in them is ever printed."""
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import time
from pathlib import Path

from studio import ROOT, config, data_dir, db

MADE_BY = "jarvis-studio-backup"
KEEP_DAILY = 14
KEEP_WEEKLY = 8
EVERY = 20 * 3600            # a new automatic backup once this long has passed since the last good one
JARVIS_PORT = 47622          # keeper.JARVIS_PORT: held while Jarvis runs


def root():
    """(folder, separate_disk): where backups go and whether that's a different disk from the data."""
    p = config.general().get("backups_dir")
    if p:
        return Path(p), True
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/Jarvis/backups"), True
    return data_dir("backups"), False


def sources():
    """{name in the backup: live path} for every file that gets backed up (only those that exist)."""
    from clipper import db as clip_db
    out = {"studio.db": db.path(), "clipper.db": clip_db.path(), "memory.db": ROOT / "memory.db",
           "config.json": config.CONFIG}
    sec = data_dir("secrets")
    for f in sorted(sec.iterdir()):
        if f.is_file():
            out[f"secrets/{f.name}"] = f
    return {k: Path(v) for k, v in out.items() if Path(v).is_file()}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _integrity(path):
    try:
        c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            return c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error as e:
        return f"error: {e}"


def _rows(path):
    try:
        c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                             "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'")]
            return {n: c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
        finally:
            c.close()
    except sqlite3.Error:
        return {}


def _copy_db(src, dst):
    s = sqlite3.connect(str(src), timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def make(reason="manual"):
    """Makes and verifies one backup. Returns {"ok", "folder", "files", "problems", "separate_disk"}."""
    base, separate = root()
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    final = base / stamp
    n = 1
    while final.exists():
        final = base / f"{stamp}-{n}"
        n += 1
    work = final.with_name(final.name + ".partial")
    work.mkdir(parents=True)
    files, problems = {}, []
    for name, src in sources().items():
        dst = work / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if name.endswith(".db"):
                _copy_db(src, dst)
                check = _integrity(dst)
                if check != "ok":
                    problems.append(f"{name}: integrity {check[:120]}")
                files[name] = {"sha256": _sha(dst), "bytes": dst.stat().st_size, "integrity": check,
                               "rows": _rows(dst)}
            else:
                shutil.copy2(src, dst)
                files[name] = {"sha256": _sha(dst), "bytes": dst.stat().st_size}
            if name.startswith("secrets/") or name == "config.json":
                try:
                    dst.chmod(0o600)
                except OSError:
                    pass
        except (OSError, sqlite3.Error) as e:
            problems.append(f"{name}: {type(e).__name__}")
    manifest = {"made_by": MADE_BY, "t": time.time(), "reason": reason, "files": files,
                "separate_disk": separate, "problems": problems}
    (work / "manifest.json").write_text(json.dumps(manifest, indent=1))
    ok = not problems and "studio.db" in files
    rep = verify(work)
    if not rep["ok"]:
        ok = False
        problems += rep["problems"]
    if ok:
        work.rename(final)
        folder = final
        _record(folder, files)
    else:
        folder = work.with_name(final.name + ".failed")
        work.rename(folder)
    return {"ok": ok, "folder": str(folder), "files": sorted(files), "problems": problems, "separate_disk": separate}


def _record(folder, files):
    try:
        db.put("backup_last", {"t": time.time(), "folder": str(folder), "files": len(files)})
    except Exception:
        pass


def verify(folder):
    """{"ok", "problems", "files"}: every file in the manifest is there, its hash matches, databases are "ok"."""
    folder = Path(folder)
    try:
        man = json.loads((folder / "manifest.json").read_text())
    except (OSError, ValueError):
        return {"ok": False, "problems": ["no readable manifest.json"], "files": 0}
    problems = []
    if man.get("made_by") != MADE_BY:
        problems.append("not a Jarvis backup")
    for name, info in (man.get("files") or {}).items():
        p = folder / name
        if not p.is_file():
            problems.append(f"{name}: missing")
            continue
        if _sha(p) != info.get("sha256"):
            problems.append(f"{name}: changed since the backup (hash differs)")
        if name.endswith(".db") and _integrity(p) != "ok":
            problems.append(f"{name}: integrity check failed")
    return {"ok": not problems, "problems": problems, "files": len(man.get("files") or {})}


def _ours():
    """[(time, folder)] of finished backups this module made, newest first."""
    base, _ = root()
    out = []
    if not base.exists():
        return out
    for d in base.iterdir():
        if not d.is_dir() or d.name.endswith((".partial", ".failed")):
            continue
        try:
            man = json.loads((d / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        if man.get("made_by") == MADE_BY:
            out.append((float(man.get("t") or 0), d))
    return sorted(out, key=lambda x: -x[0])


def rotate(now=None):
    """Removes old backups beyond 14 daily + 8 weekly. Returns the removed folder names."""
    now = now or time.time()
    items = _ours()
    keep, days, weeks = set(), set(), set()
    for t, d in items:
        day = time.strftime("%Y-%m-%d", time.localtime(t))
        week = time.strftime("%G-%V", time.localtime(t))
        if day not in days and len(days) < KEEP_DAILY:
            days.add(day)
            keep.add(d)
        if week not in weeks and len(weeks) < KEEP_WEEKLY:
            weeks.add(week)
            keep.add(d)
    if items:
        keep.add(items[0][1])                     # never the newest
    removed = []
    for t, d in items:
        if d not in keep:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    for d in root()[0].glob("*.failed"):          # failed attempts older than a week
        if now - d.stat().st_mtime > 7 * 86400:
            shutil.rmtree(d, ignore_errors=True)
    return removed


def latest():
    items = _ours()
    return items[0][1] if items else None


def status():
    base, separate = root()
    items = _ours()
    last = items[0] if items else None
    return {"folder": str(base), "separate_disk": separate, "count": len(items),
            "last": None if not last else {"folder": last[1].name, "t": last[0],
                                            "hours_ago": round((time.time() - last[0]) / 3600, 1)}}


def maybe_run(now=None):
    """The daily automatic backup. Returns the make() report, or None when the last one is recent."""
    now = now or time.time()
    items = _ours()
    if items and now - items[0][0] < EVERY:
        return None
    rep = make("daily")
    if rep["ok"]:
        rotate(now)
    else:
        from studio import health
        health.notify("backup", "Tonight's backup of the YouTube business didn't pass its checks: "
                      + "; ".join(rep["problems"][:3]), "warn")
    return rep


# ---------- restore ----------

def jarvis_running():
    """True while Jarvis holds his 'I'm running' port (keeper.py)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", JARVIS_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def plan(folder):
    """[{"name", "action", "now_rows", "backup_rows"}]: what restore(folder) would do. Changes nothing."""
    folder = Path(folder)
    man = json.loads((folder / "manifest.json").read_text())
    live = sources()
    out = []
    for name, info in sorted((man.get("files") or {}).items()):
        cur = live.get(name)
        row = {"name": name, "action": "replace" if cur else "create"}
        if name.endswith(".db"):
            then, now = info.get("rows") or {}, (_rows(cur) if cur else {})
            row["backup_rows"] = sum(then.values())
            row["now_rows"] = sum(now.values())
            row["changed_tables"] = {t: [now.get(t, 0), then.get(t, 0)] for t in sorted(set(then) | set(now))
                                     if now.get(t, 0) != then.get(t, 0)}
            row["auto"] = True
        else:
            row["same"] = bool(cur) and _sha(cur) == info.get("sha256")
            row["auto"] = False                   # config and secrets only when named
        out.append(row)
    return out


def _live_path(name):
    from clipper import db as clip_db
    return {"studio.db": db.path(), "clipper.db": clip_db.path(), "memory.db": ROOT / "memory.db",
            "config.json": config.CONFIG}.get(name) or (data_dir("secrets") / name.split("/", 1)[1]
                                                        if name.startswith("secrets/") else None)


def restore(folder, also=(), running=None):
    """Puts the backup's databases (and any names in also, e.g. "config.json") back. Refuses while Jarvis
    runs or if the backup doesn't verify. What's there now is backed up first. Returns a report."""
    folder = Path(folder)
    if (jarvis_running() if running is None else running):
        raise RuntimeError("Close Jarvis first (his icon by the clock, then Quit), so nothing is writing.")
    rep = verify(folder)
    if not rep["ok"]:
        raise RuntimeError("That backup didn't pass its checks: " + "; ".join(rep["problems"][:3]))
    before = make("before restore")
    man = json.loads((folder / "manifest.json").read_text())
    done = []
    db.reset_connections()
    try:
        from clipper import db as clip_db
        clip_db.reset_connections()
    except Exception:
        pass
    for name in sorted(man.get("files") or {}):
        if not (name.endswith(".db") or name in also):
            continue
        dst = _live_path(name)
        if not dst:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".db"):
            for suffix in ("-wal", "-shm"):
                w = dst.with_name(dst.name + suffix)
                if w.exists():
                    w.unlink()
            tmp = dst.with_name(dst.name + ".restore")
            shutil.copy2(folder / name, tmp)
            os.replace(tmp, dst)
            if _integrity(dst) != "ok":
                raise RuntimeError(f"{name} didn't check out after restoring; the copy from just before is in "
                                   f"{before['folder']}")
        else:
            shutil.copy2(folder / name, dst)
        done.append(name)
    return {"restored": done, "safety_copy": before["folder"], "safety_ok": before["ok"]}
