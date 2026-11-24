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
    """Share of memory in use (0-1), or None if unknown. macOS: vm_stat (no extra packages). Windows: psutil."""
    try:
        if sys.platform == "win32":
            import psutil
            return psutil.virtual_memory().percent / 100
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


def cpu_load():
    """Share of the CPU in use (0-1, about the last minute where the system keeps that), or None."""
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        pass
    try:
        import psutil                              # Windows has no load average
        return psutil.cpu_percent(interval=None) / 100
    except Exception:
        return None


GPU_RUN = None            # tests swap this: () -> nvidia-smi's csv line
_gpu_cache = {}


def gpu(max_age=60):
    """The NVIDIA card right now: {"name", "used_gb", "total_gb", "util", "temp_c"}, or None (no card / Mac).
    Read-only (nvidia-smi), cached for a minute."""
    hit = _gpu_cache.get("v")
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    out = None
    try:
        if GPU_RUN:
            line = GPU_RUN()
        else:
            if sys.platform == "darwin":
                raise RuntimeError("no NVIDIA card on a Mac")
            smi = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.name == "nt" else None)
            if not smi or not os.path.exists(smi):
                raise RuntimeError("no nvidia-smi")
            kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            line = subprocess.run([smi, "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                                   "--format=csv,noheader,nounits"], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=10, **kw).stdout.strip().splitlines()[0]
        name, used, total, util, temp = [x.strip() for x in line.split(",")[:5]]
        num = lambda v: float(v) if v.replace(".", "", 1).isdigit() else None
        out = {"name": name, "used_gb": round((num(used) or 0) / 1024, 1), "total_gb": round((num(total) or 0) / 1024, 1),
               "util": None if num(util) is None else round(num(util) / 100, 2), "temp_c": num(temp)}
    except Exception:
        out = None
    _gpu_cache["v"] = (time.time(), out)
    return out


def _drives():
    """Free space on C: and D: (the PC), or the data folder's disk elsewhere: {label: free GB}."""
    if os.name == "nt":
        out = {}
        for d in ("C:/", "D:/"):
            if os.path.exists(d):
                out[d[0]] = round(_free_gb(d), 1)
        return out
    return {"data": round(_free_gb(data_dir()), 1)}


def resources():
    home = data_dir()
    load = cpu_load()
    commit = None
    try:
        from studio import engines
        commit = engines.commit_free_gb()
    except Exception:
        pass
    return {"free_gb": round(_free_gb(home), 1), "studio_gb": round(_folder_gb(home), 2),
            "memory": None if (m := memory_pressure()) is None else round(m, 2),
            "load": None if load is None else round(load, 2), "gpu": gpu(),
            "commit_free_gb": None if commit is None else round(commit, 1), "drives": _drives(), "t": time.time()}


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


def peek_news(limit=3):
    """[(id, msg)] of alerts not yet told, without marking them (Jarvis marks them once spoken)."""
    return [(r["id"], r["msg"]) for r in db.q("SELECT id, msg FROM notices WHERE told=0 ORDER BY id LIMIT ?", limit)]


def mark_told(ids):
    ids = [int(i) for i in ids]
    if ids:
        db.run(f"UPDATE notices SET told=1 WHERE id IN ({','.join(map(str, ids))})")


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
        from studio import registry
        registry.register_all()                  # every engine too, with its licence, hardware and fallback
    except Exception:
        pass
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
