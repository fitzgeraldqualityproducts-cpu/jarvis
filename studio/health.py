"""Start-up checks, resource limits and alerts.

startup()      repairs a damaged studio.db (dbrescue keeps a rescue copy), runs migrations, registers
               the $0 tools, and records what it found
resources()    free disk, studio folder size, memory pressure, load; ok_to_work() says whether heavy
               work may start (it also waits while the clipper is running a job, so the 8 GB Mac
               never does two heavy jobs at once)
notify()       an alert for Connor, spoken by Jarvis when he's free; the same alert is not repeated
               within notify_cooldown_minutes
"""
import os
import shutil
import subprocess
import sys
import time

from studio import ROOT, config, data_dir, db, guard


def _free_gb(path):
    try:
        return shutil.disk_usage(str(path)).free / (1 << 30)
    except OSError:
        return 999.0


def _folder_gb(path):
    total = 0
    for root, _, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1 << 30)


def memory_pressure():
    """Share of memory in use (0-1), or None if unknown. macOS: vm_stat (no extra packages)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = 16384 if "page size of 16384" in out else 4096
            vals = {}
            for line in out.splitlines()[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    try:
                        vals[k.strip()] = int(v.strip().rstrip("."))
                    except ValueError:
                        pass
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
            free = (vals.get("Pages free", 0) + vals.get("Pages inactive", 0) + vals.get("Pages speculative", 0)
                    + vals.get("Pages purgeable", 0)) * page
            return max(0.0, min(1.0, 1 - free / total))
        if os.path.exists("/proc/meminfo"):
            info = {}
            for line in open("/proc/meminfo"):
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
            return 1 - info.get("MemAvailable", info["MemFree"]) / info["MemTotal"]
    except Exception:
        pass
    return None


def resources():
    home = data_dir()
    try:
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        load = None
    return {"free_gb": round(_free_gb(home), 1), "studio_gb": round(_folder_gb(home), 2),
            "memory": None if (m := memory_pressure()) is None else round(m, 2),
            "load": None if load is None else round(load, 2), "t": time.time()}


def clipper_busy():
    try:
        from clipper import jobs as clip_jobs
        return clip_jobs.busy()
    except Exception:
        return False


def ok_to_work(heavy=True, r=None):
    """(ok, reason). Heavy work waits for disk, memory and the clipper."""
    g = config.general()
    r = r or resources()
    if r["free_gb"] < g["min_free_gb"]:
        return False, f"only {r['free_gb']} GB of disk is free (I need {g['min_free_gb']} GB)"
    if r["studio_gb"] > g["max_disk_gb"]:
        return False, f"the studio folder is over its {g['max_disk_gb']} GB limit"
    if heavy and r["memory"] is not None and r["memory"] > g["max_memory_pressure"]:
        return False, f"memory is {int(r['memory'] * 100)}% used"
    if heavy and clipper_busy():
        return False, "the clipper is making clips right now"
    return True, ""


def notify(key, msg, level="warn"):
    """Queues an alert for Connor (once per cooldown per key). Routine successes are never alerts."""
    g = config.general()
    last = db.one("SELECT t FROM notices WHERE key=? ORDER BY id DESC LIMIT 1", key)
    if last and time.time() - last["t"] < 60 * g["notify_cooldown_minutes"]:
        return False
    db.insert("INSERT INTO notices(t, key, level, msg) VALUES(?,?,?,?)", time.time(), key, level, guard.redact(msg))
    return True


def news():
    """Alerts not yet told to Connor, marked as told (Jarvis speaks them)."""
    rows = db.q("SELECT id, msg FROM notices WHERE told=0 ORDER BY id")
    if rows:
        db.run(f"UPDATE notices SET told=1 WHERE id IN ({','.join(str(r['id']) for r in rows)})")
    return [r["msg"] for r in rows]


def recovery(what, detail="", result="", video_id=None):
    db.insert("INSERT INTO recovery_events(t, what, video_id, detail, result) VALUES(?,?,?,?,?)",
              time.time(), what, video_id, guard.redact(detail)[:1000], result)


def startup():
    """Run before anything else touches the database. Returns a dict of what was checked."""
    report = {"db": "ok"}
    p = db.path()
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import dbrescue
        if p.exists() and not dbrescue.check(p):
            db.reset_connections()
            report["db"] = dbrescue.repair(p) or "repaired"
            if not dbrescue.check(p):
                # beyond repair: keep it aside (never deleted) and start a fresh database
                stamp = time.strftime("%Y%m%d-%H%M%S")
                for suffix in ("", "-wal", "-shm", "-journal"):
                    f = p.with_name(p.name + suffix)
                    if f.exists():
                        f.rename(f.with_name(f"{f.name}.broken-{stamp}"))
                report["db"] = f"the database couldn't be rebuilt, so I kept it as studio.db.broken-{stamp} and started a fresh one"
                notify("db", "The YouTube business database was damaged beyond repair. I kept the damaged copy and started a "
                             "fresh one. Uploads stay stopped until you reconnect the business channel.", "error")
    except Exception as e:
        report["db"] = f"check skipped: {e}"
    report["schema"] = db.version()
    guard.register_built_ins()
    try:
        import dbrescue
        report["backup"] = dbrescue.backup(p)
    except Exception:
        report["backup"] = False
    report["resources"] = resources()
    if report["db"] not in ("ok",):
        recovery("database", report["db"], "repaired")
    guard.audit("module.start", True, {"schema": report["schema"], "db": report["db"]})
    return report
