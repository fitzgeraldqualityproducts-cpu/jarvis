"""Keeps Jarvis's SQLite databases (clipper_data/clipper.db, memory.db) healthy.

check(path) runs SQLite's own quick check. If a database is damaged ("database disk image is
malformed"), repair(path):
  1. copies the damaged file (with its -wal/-shm journal) into db-rescue/<time>/ next to it, untouched,
     so nothing is ever lost by trying;
  2. rebuilds a fresh database from everything that can still be read: first with the sqlite3
     tool's ".recover" (the official salvage command) when the computer has it, otherwise table by
     table and row by row, skipping only the rows that are really damaged;
  3. if that recovers less than the last good backup has, uses the backup instead;
  4. swaps the rebuilt file in.
backup(path) keeps a daily copy (<name>.bak, made with SQLite's backup API, safe while in use), so a
repair always has a good copy to fall back on. Nothing here ever deletes the rescue copies."""
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

BACKUP_EVERY = 20 * 3600


def _journal(path):
    return [Path(str(path) + s) for s in ("-wal", "-shm", "-journal")]


def check(path):
    """True when the database is fine (or doesn't exist yet)."""
    path = Path(path)
    if not path.exists():
        return True
    try:
        c = sqlite3.connect(str(path), timeout=30)
        try:
            row = c.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            c.close()
    except sqlite3.DatabaseError:
        return False


def is_corrupt_error(err):
    s = str(err).lower()
    return isinstance(err, sqlite3.DatabaseError) and ("malformed" in s or "not a database" in s or "corrupt" in s)


def backup(path, force=False):
    """A daily copy of a healthy database next to it (<name>.bak). Returns True if one was made."""
    path = Path(path)
    bak = Path(str(path) + ".bak")
    try:
        if not path.exists() or (not force and bak.exists() and time.time() - bak.stat().st_mtime < BACKUP_EVERY):
            return False
        if not check(path):
            return False                       # never overwrite a good backup with a damaged copy
        tmp = Path(str(bak) + ".tmp")
        src = sqlite3.connect(str(path), timeout=30)
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp, bak)
        return True
    except (OSError, sqlite3.Error) as e:
        print(f"(database backup of {path.name} skipped: {e})")
        return False


def _rows(path):
    """{table: row count} for a database, or {} if it can't be read."""
    out = {}
    try:
        c = sqlite3.connect(str(path))
        try:
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                     "AND name NOT LIKE '%_fts%' AND name != 'lost_and_found'"):
                try:
                    out[name] = c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except sqlite3.Error:
                    out[name] = 0
        finally:
            c.close()
    except sqlite3.Error:
        pass
    return out


def _recover_cli(broken, fresh):
    exe = shutil.which("sqlite3")
    if not exe:
        return False
    try:
        dump = subprocess.run([exe, str(broken), ".recover"], capture_output=True, timeout=600)
        if dump.returncode != 0 or not dump.stdout:
            return False
        load = subprocess.run([exe, str(fresh)], input=dump.stdout, capture_output=True, timeout=600)
        return load.returncode == 0 and check(fresh)
    except (OSError, subprocess.SubprocessError):
        return False


def _recover_python(broken, fresh):
    """Copies every table's readable rows into a fresh database (damaged rows are skipped)."""
    try:
        src = sqlite3.connect(str(broken))
        schema = src.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    except sqlite3.Error:
        return False
    dst = sqlite3.connect(str(fresh))
    try:
        tables = [(n, s) for t, n, s in schema if t == "table" and not n.startswith("sqlite_")]
        for name, sql in tables:
            try:
                dst.execute(sql)
            except sqlite3.Error:
                pass
        for name, _ in tables:
            if "_fts" in name:
                continue                           # full-text indexes are rebuilt from their table
            try:
                cols = [r[1] for r in src.execute(f'PRAGMA table_info("{name}")')]
            except sqlite3.Error:
                continue
            if not cols:
                continue
            ins = f'INSERT OR IGNORE INTO "{name}"({",".join(chr(34) + c + chr(34) for c in cols)}) ' \
                  f'VALUES({",".join("?" * len(cols))})'
            sel = f'SELECT {",".join(chr(34) + c + chr(34) for c in cols)} FROM "{name}"'
            try:
                dst.executemany(ins, src.execute(sel).fetchall())
                continue
            except sqlite3.Error:
                pass
            try:                                   # damaged somewhere: go row by row
                top = src.execute(f'SELECT MAX(rowid) FROM "{name}"').fetchone()[0] or 0
            except sqlite3.Error:
                top = 0
            for rid in range(1, int(top) + 1):
                try:
                    row = src.execute(sel + " WHERE rowid=?", (rid,)).fetchone()
                    if row:
                        dst.execute(ins, row)
                except sqlite3.Error:
                    continue
        for t, name, sql in schema:                # indexes and triggers after the data
            if t in ("index", "trigger"):
                try:
                    dst.execute(sql)
                except sqlite3.Error:
                    pass
        for name, _ in tables:                     # rebuild full-text indexes from their content
            if name.endswith("_fts"):
                try:
                    dst.execute(f"INSERT INTO \"{name}\"(\"{name}\") VALUES('rebuild')")
                except sqlite3.Error:
                    pass
        dst.commit()
        return check(fresh)
    finally:
        dst.close()
        src.close()


def repair(path, log=print):
    """Rebuilds a damaged database (see the top of this file). Returns a short plain-words summary,
    or None if it was fine. Call it only when nothing else has the database open."""
    path = Path(path)
    if check(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rescue = path.parent / "db-rescue" / f"{path.stem}-{stamp}"
    rescue.mkdir(parents=True, exist_ok=True)
    for f in [path] + _journal(path):
        if f.exists():
            shutil.copy2(f, rescue / f.name)
    work = rescue / "work.db"                      # salvage from a copy, never the original
    shutil.copy2(path, work)
    for f in _journal(path):
        if f.exists():
            shutil.copy2(f, Path(str(work) + f.name[len(path.name):]))
    fresh = rescue / "rebuilt.db"
    how = "the sqlite3 recover tool"
    ok = _recover_cli(work, fresh)
    if not ok:
        fresh.unlink(missing_ok=True)
        how = "reading it table by table"
        ok = _recover_python(work, fresh)
    got = _rows(fresh) if ok else {}
    bak = Path(str(path) + ".bak")
    had = _rows(bak) if bak.exists() and check(bak) else {}
    if had and sum(had.values()) > sum(got.values()):
        fresh.unlink(missing_ok=True)
        shutil.copy2(bak, fresh)
        how, got, ok = "the last daily backup (it had more in it)", had, True
    if not ok:
        log(f"(database {path.name} is damaged and couldn't be rebuilt; the damaged copy is in {rescue})")
        return f"{path.name} was damaged and I couldn't rebuild it. A copy is safe in {rescue}."
    for f in _journal(path):
        f.unlink(missing_ok=True)
    os.replace(fresh, path)
    for f in rescue.glob("work.db*"):
        f.unlink(missing_ok=True)
    total = sum(got.values())
    msg = f"{path.name} was damaged; I rebuilt it from {how} ({total} rows kept). The damaged copy is saved in {rescue}."
    log(f"({msg})")
    return msg
