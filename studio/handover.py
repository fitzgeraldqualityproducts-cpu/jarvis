"""Moving the YouTube business from one computer to another (Mac -> Windows PC), so only ONE computer ever
makes and uploads Shorts.

  export (on the computer that runs it now, the Mac)
      1. switches the studio off here first (enabled, autopilot off, "handed_over" set). From then on this
         computer's studio stays off, whatever "enabled" says, until `handover undo`.
      2. waits for a running stage to stop, then copies the database (a consistent snapshot), the brand
         kit, lessons, settings and the Shorts that aren't on YouTube yet into one zip on the Desktop.
      The YouTube sign-in (the token that can upload) is NOT in the zip: the new computer signs in again
      with "Connect YouTube Business", and the channel lock makes sure it's the same channel.
  import (on the new computer, the PC)
      unpacks the zip into studio_data/, points every saved file path at this computer's folders, keeps
      Connor's protected channels, and turns the studio on with the autopilot OFF. The autopilot starts
      only after the channel is connected and verified (`autopilot on`).
  undo (on the old computer)
      gives it the studio back (only after the other computer's studio has been switched off).
"""
import json
import os
import shutil
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

from studio import ROOT, config, home

FORMAT = 1
NAME = "Jarvis Studio Handover.zip"
DONE_STATES = ("published", "rejected", "failed")           # their videos are on YouTube (or not wanted)
MEDIA = (".mp4", ".mov", ".wav", ".m4a", ".mp3", ".webm", ".mkv")
SKIP_FILES = {"heavy.lock", "studio.db", "studio.db-wal", "studio.db-shm", "youtube_business.json"}
SKIP_DIRS = {"sample", "backups", "tmp", "__pycache__"}
NOT_COPIED = ("enabled", "allow_windows", "handed_over", "autopilot")


class HandoverError(Exception):
    pass


def handed_over():
    """The handover record on a computer that gave the studio away, else None."""
    return config.general().get("handed_over") or None


def _desktop():
    d = Path.home() / "Desktop"
    return d if d.is_dir() else Path.home()


def _running_stage(stall=180):
    from studio import db
    r = db.one("SELECT id, state FROM videos WHERE owner IS NOT NULL AND heartbeat>?", time.time() - stall)
    return r


def _protected_ids():
    from studio import guard
    return sorted(guard.protected_channels())


def export(dest=None, wait=900, log=print):
    from studio import db, guard
    locked = guard.locked_channel()
    if not db.path().exists():
        raise HandoverError("there's no studio on this computer to hand over")
    protected = _protected_ids()
    before = {k: config.general().get(k) for k in ("enabled", "autopilot")}
    config.set_values(enabled=False, autopilot=False,
                      handed_over={"at": time.time(), "to": "the PC", "was": before})
    guard.audit("handover.export", True, "studio switched off here before the handover")
    log("The studio is switched off on this computer (it won't make or upload anything here now).")
    t0 = time.time()
    while (r := _running_stage()):
        if time.time() - t0 > wait:
            raise HandoverError(f"Short {r['id']} is still {r['state']}; try again in a few minutes "
                                "(the studio here stays off)")
        log(f"Waiting for Short {r['id']} to finish its current step ({r['state']})...")
        time.sleep(10)
    out = Path(dest) if dest else _desktop() / NAME
    tmp = out.with_suffix(".tmp")
    snap = home() / "handover-snapshot.db"
    if snap.exists():
        snap.unlink()
    src = sqlite3.connect(str(db.path()))
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    src.close()
    dst.close()
    keep_videos = {str(r["id"]) for r in db.q("SELECT id, state FROM videos") if r["state"] not in DONE_STATES}
    settings = {k: v for k, v in (json.loads(config.CONFIG.read_text()).get("studio") or {}).items()
                if k not in NOT_COPIED} if config.CONFIG.exists() else {}
    settings["protected_channels"] = sorted(set(settings.get("protected_channels") or []) | set(protected))
    manifest = {"format": FORMAT, "made_at": time.time(), "from_root": str(ROOT), "from_home": str(home()),
                "from_platform": sys.platform, "channel": dict(locked) if locked else None,
                "protected_channels": settings["protected_channels"], "settings": settings}
    n = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.write(snap, "studio_data/studio.db")
        base = home()
        for root, dirs, files in os.walk(base):
            rel = Path(root).relative_to(base)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            parts = rel.parts
            for f in files:
                if f in SKIP_FILES or f == snap.name or f.endswith((".lock", ".tmp", ".part")):
                    continue
                if parts[:1] == ("videos",) and len(parts) > 1 and parts[1] not in keep_videos \
                        and f.lower().endswith(MEDIA):
                    continue                         # already on YouTube (and on Drive): not needed on the PC
                z.write(Path(root) / f, (Path("studio_data") / rel / f).as_posix())
                n += 1
        client = ROOT / "clipper_data" / "secrets" / "google_client.json"
        if client.exists():
            z.write(client, "google_client.json")    # the Google sign-in APP (not a sign-in): saves redoing the Cloud setup
    snap.unlink()
    tmp.replace(out)
    guard.audit("handover.export", True, {"file": str(out), "files": n})
    return {"path": str(out), "files": n, "mb": round(out.stat().st_size / 2 ** 20, 1),
            "channel": manifest["channel"], "protected": len(settings["protected_channels"])}


def _data_members(z, target):
    """(member, destination) for every studio_data/ file; refuses the whole file if any path escapes."""
    target = target.resolve()
    out = []
    for m in z.infolist():
        if not m.filename.startswith("studio_data/") or m.is_dir():
            continue
        dest = (target / m.filename[len("studio_data/"):]).resolve()
        if target not in dest.parents:
            raise HandoverError(f"the handover file has a bad path in it ({m.filename}), so nothing was unpacked")
        out.append((m, dest))
    return out


def _two_step(pairs):
    """old -> marker -> new, so a new path that contains an old one is never rewritten twice."""
    marks = [f"\u241ejv-handover-{i}\u241e" for i in range(len(pairs))]
    return [(old, m) for (old, _), m in zip(pairs, marks)] + [(m, new) for (_, new), m in zip(pairs, marks)]


def _repoint(dbfile, pairs):
    """Rewrites saved file paths (old Jarvis folder -> this one) in every text column."""
    pairs = _two_step(pairs)
    c = sqlite3.connect(str(dbfile))
    try:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")') if (r[2] or "").upper() in ("TEXT", "")]
            for col in cols:
                for old, new in pairs:
                    c.execute(f'UPDATE "{t}" SET "{col}"=replace("{col}", ?, ?) WHERE typeof("{col}")=\'text\' '
                              f'AND instr("{col}", ?)>0', (old, new, old))
        c.execute("UPDATE videos SET owner=NULL, heartbeat=0")
        c.commit()
    finally:
        c.close()


def _repoint_files(folder, pairs):
    pairs = _two_step(pairs)
    for p in Path(folder).rglob("*.json"):
        try:
            s = p.read_text(encoding="utf-8")
        except Exception:
            continue
        new = s
        for old, rep in pairs:
            new = new.replace(old if "\u241e" in old else json.dumps(old)[1:-1],
                              rep if "\u241e" in rep else json.dumps(rep)[1:-1])
        if new != s:
            p.write_text(new, encoding="utf-8")


def import_(zpath, replace=False, log=print):
    from studio import db
    zpath = Path(str(zpath).strip().strip('"\''))
    if not zpath.exists():
        raise HandoverError(f"couldn't find {zpath}")
    target = home()
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        if "manifest.json" not in names or "studio_data/studio.db" not in names:
            raise HandoverError("that isn't a Jarvis studio handover file")
        man = json.loads(z.read("manifest.json"))
        if man.get("format") != FORMAT:
            raise HandoverError("that handover file is from a different Jarvis version; update both computers first")
        if Path(man["from_home"]) == target and Path(man["from_root"]) == ROOT:
            raise HandoverError("that handover file was made on this computer")
        if (target / "studio.db").exists():
            db.reset_connections()
            c = sqlite3.connect(str(target / "studio.db"))
            try:
                n = c.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            except sqlite3.Error:
                n = 0
            c.close()
            if n and not replace:
                raise HandoverError(f"this computer already has a studio with {n} Shorts; "
                                    "add --replace to put it aside and use the handover instead")
            aside = target.with_name(f"studio_data-before-handover-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.move(str(target), str(aside))
            log(f"Put the old studio aside in {aside}")
        target.mkdir(parents=True, exist_ok=True)
        for m, dest in _data_members(z, target):
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as fsrc, open(dest, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
        client = z.read("google_client.json") if "google_client.json" in names else None
    # forward slashes work on Windows too, and need no escaping inside the JSON the database keeps
    pairs = [(man["from_home"], target.as_posix()), (man["from_root"], ROOT.as_posix())]
    db.reset_connections()
    _repoint(target / "studio.db", pairs)
    _repoint_files(target, pairs)
    if client:
        from clipper import youtube
        if not youtube.client():
            youtube.save_client(client.decode("utf-8"))
            log("Saved the Google sign-in app from the Mac (so no Google Cloud setup is needed here).")
    settings = dict(man.get("settings") or {})
    settings.update(enabled=True, allow_windows=True, autopilot=False, handed_over=None)
    config.set_values(**settings)
    from studio import guard
    guard.audit("handover.import", True, {"from": man["from_platform"], "channel": man.get("channel")})
    return {"channel": man.get("channel"), "protected": man.get("protected_channels") or [],
            "made_at": man["made_at"]}


def undo():
    """Gives this (old) computer the studio back."""
    rec = handed_over()
    if not rec:
        raise HandoverError("this computer's studio wasn't handed over")
    was = rec.get("was") or {}
    config.set_values(handed_over=None, enabled=bool(was.get("enabled", True)), autopilot=False)
    from studio import guard
    guard.audit("handover.undo", True, "studio given back to this computer (autopilot off)")
    return True


def main(argv, log=print):
    sub = argv[1] if len(argv) > 1 else ""
    try:
        if sub == "export":
            print("Handing the YouTube business over to the PC.\n")
            r = export(log=log)
            ch = r["channel"]
            print(f"\nSaved {r['path']} ({r['mb']} MB, {r['files']} files).")
            print(f"Channel: {ch['title'] if ch else 'not connected'}; protected channels kept: {r['protected']}.")
            print("\nNext:\n 1. Copy that file to the PC (a USB stick, or your own Google Drive / email to yourself).\n"
                  "    It has no YouTube sign-in in it, so it can't upload anything by itself.\n"
                  " 2. On the PC, double-click \"Take Over YouTube Studio\" and pick the file.\n"
                  "The studio on this Mac stays off. (To give it back to the Mac later: python -m studio handover undo)")
            return 0
        if sub == "import":
            if len(argv) > 2:
                path = argv[2]
            else:
                found = [p for p in (_desktop() / NAME, Path.home() / "Downloads" / NAME) if p.exists()]
                path = str(found[0]) if found else input("Drag the handover .zip here and press Enter: ")
            r = import_(path, replace="--replace" in argv, log=log)
            ch = r["channel"]
            print(f"\nThe studio is on this computer now: {ch['title'] if ch else 'no channel yet'}, "
                  f"{len(r['protected'])} protected channel(s) kept. The autopilot is OFF.")
            print("\nNext:\n 1. Double-click \"Connect YouTube Business\" and sign in to the business account.\n"
                  " 2. Double-click \"Install Studio Tools\" (voice, timing, animation; free, one time).\n"
                  " 3. Double-click \"Start YouTube Autopilot\".")
            return 0
        if sub == "undo":
            undo()
            print("The studio is back on this computer, with the autopilot off. Make sure the PC's studio is off first.")
            return 0
        rec = handed_over()
        print(json.dumps({"handed_over": rec, "studio_on_here": config.enabled()}, indent=2, default=str))
        return 0
    except HandoverError as e:
        print(f"\nStopped: {e}")
        return 1
