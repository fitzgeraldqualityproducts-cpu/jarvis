"""Night school: Jarvis teaches himself overnight.

Each night, while you're asleep and he's idle, he looks back at the day: the things he couldn't do
or got wrong (lessons.json), his own error log (jarvis.log) and any upgrade idea he's worked out but
you haven't heard yet. He picks the one a code change would fix best and builds that fix in a
separate copy of himself (a git worktree in .night/), with the same Claude Code login as his brain.
Then he checks the copy still works.

Nothing he learns overnight is used until you say so. The next time you talk to him he says what
he learned ("Overnight I taught myself to control OBS. Want to keep it?"):
  yes   he adds it to himself, checks himself again and restarts. "Undo that" still takes it out.
  no    he throws it away.
If it broke anything, or didn't work out, he throws it away himself and doesn't mention it.

  "what did you learn last night"   hear it again
  "night school off" / "night school on"
Config: "night_school" (true), "night_school_hours" ([1, 5]: when he may work, by the clock)."""
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "nightschool.json"
CONFIG = HERE / "config.json"
LOG = HERE / "jarvis.log"
WORK = HERE / ".night"
_lock = threading.RLock()
_working = threading.Event()

PICK_PROMPT = """You are helping Jarvis, a Python voice assistant (his code is in this folder), improve himself
overnight. Read README.md and jarvis.py to see how he works. Here is what went wrong for him recently:

Things he couldn't do, or got wrong (what the owner said, what Jarvis said):
{misses}

Errors from his own log:
{errors}

Upgrade ideas he already had:
{ideas}

Fixes the owner turned down before (don't suggest these again): {declined}

Pick the ONE thing that a code change to Jarvis would fix best: something the owner clearly wanted,
that would come up again, and that can be done well in his code with no new paid service, API key or
account. Never anything that sends messages, spends money, deletes files, or weakens a safety check.
If nothing is worth it, say so.

Reply with ONLY one line of JSON, no markdown:
{{"worth_it": true or false,
  "about": "what went wrong, in a few words",
  "request": "precise instructions for the change, written as the owner asking Jarvis to change himself",
  "learned": "what Jarvis can do now, finishing the sentence 'Overnight I taught myself to ...' (no full stop)",
  "try_it": "a short thing the owner could say to try it"}}"""


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def enabled():
    return _cfg().get("night_school", True)


def set_enabled(on):
    cfg = _cfg()
    cfg["night_school"] = bool(on)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def _load():
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {"pending": None, "history": []}


def _save(data):
    data["history"] = (data.get("history") or [])[-40:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(FILE)


def _git(*args, cwd=None, timeout=120):
    return subprocess.run(["git", "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost", *args],
                          cwd=str(cwd or HERE), capture_output=True, text=True, timeout=timeout)


def busy():
    return _working.is_set()


# ---------- what went wrong today ----------

ERROR_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \((.*(?:error|failed|broke|couldn'?t|can'?t|traceback|exception).*)\)$", re.I)
NOT_MINE = re.compile(r"no speech|heard nothing|transcrib|network|timed out|offline|wifi|wi-fi|internet|"
                      r"name resolution|connection (?:refused|reset)|update clashed|rolling back|undoing it", re.I)


def _log_errors(limit=20):
    """Jarvis's own error lines from the last day of his log (things that broke, not network hiccups)."""
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 600_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-6000:]:
        m = ERROR_LINE.match(line.strip())
        if m and not NOT_MINE.search(m.group(2)):
            text = re.sub(r"\s+", " ", m.group(2))[:240]
            if text not in out:
                out.append(text)
    return out[-limit:]


def _gather():
    import learn
    data = learn._load()
    since = time.time() - 36 * 3600
    misses = [m for m in data.get("misses", []) if m.get("t", 0) > since][-15:]
    ideas = [i for i in data.get("ideas", []) if i.get("type", "code") == "code" and i.get("status") in ("new", "offered")]
    night = _load()
    declined = [h.get("about", "") for h in night.get("history", []) if h.get("result") == "declined"]
    declined += [i.get("about", "") for i in data.get("ideas", []) if i.get("status") == "declined"]
    return misses, _log_errors(), ideas[-5:], declined[-12:]


def _pick(misses, errors, ideas, declined):
    import brain
    claude = brain.find_claude()
    if not claude:
        return None
    prompt = PICK_PROMPT.format(
        misses="\n".join(f"- {m['asked']!r} -> {m.get('reply', '')[:200]!r}"
                         + (f" ({m['detail'][:150]})" if m.get("detail") else "") for m in misses) or "- none",
        errors="\n".join(f"- {e}" for e in errors) or "- none",
        ideas="\n".join(f"- {i.get('about')}: {i.get('request', '')[:300]}" for i in ideas) or "- none",
        declined="; ".join(d for d in declined if d) or "none")
    out = brain.run_background([claude, "-p", prompt, "--allowedTools", "Read,Glob,Grep",
                                "--disallowedTools", "Edit Write NotebookEdit Bash WebSearch WebFetch Task"],
                               timeout=420, cwd=str(HERE), who="night school")
    m = re.search(r"\{.*\}", out.stdout or "", re.S)
    if not m:
        return None
    try:
        pick = json.loads(m.group(0))
    except ValueError:
        return None
    if not pick.get("worth_it") or not pick.get("request") or not pick.get("learned"):
        return None
    return pick


# ---------- building the fix in a copy of himself ----------

def _clean_work():
    _git("worktree", "prune")
    if WORK.exists():
        for d in WORK.iterdir():
            _git("worktree", "remove", "--force", str(d))
        shutil.rmtree(WORK, ignore_errors=True)


def _build(pick):
    """Makes the change in a git worktree and checks it. Returns (commit sha, branch) or (None, why)."""
    import brain
    import selfedit
    claude = brain.find_claude()
    if not claude:
        return None, "no Claude Code"
    selfedit.ensure_repo()                        # snapshot the live folder (and make sure .night/ is ignored)
    _clean_work()
    stamp = time.strftime("%Y%m%d-%H%M")
    branch, wt = f"night/{stamp}", WORK / stamp
    WORK.mkdir(exist_ok=True)
    r = _git("worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    if r.returncode != 0:
        return None, f"couldn't make a copy: {r.stderr.strip()[-200:]}"
    copied = []
    for name in ("config.json",):                 # settings the copy needs to start, if git doesn't have them
        if (HERE / name).exists() and not (wt / name).exists():
            shutil.copy2(HERE / name, wt / name)
            copied.append(name)
    try:
        out = brain.run_background(
            [claude, "-p", selfedit.PROMPT.format(request=pick["request"].replace('"', "'")),
             "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write,Glob,Grep"],
            timeout=1200, cwd=str(wt), who="night school")
        print(f"(night school: {out.stdout.strip()[-300:]})")
        if not [l for l in _git("status", "--porcelain", cwd=wt).stdout.splitlines()
                if l[3:].strip() not in copied]:
            return None, "nothing changed"
        problem = selfedit._check(wt) or _modules_load(wt)
        if problem:
            return None, f"it broke something: {problem[:200]}"
        _git("add", "-A", cwd=wt)
        for name in copied:
            _git("reset", "-q", "--", name, cwd=wt)
        if not _git("diff", "--cached", "--quiet", cwd=wt).returncode:
            return None, "nothing changed"
        _git("commit", "-q", "-m", f"Voice edit: (learned overnight) {pick['request'][:300]}", cwd=wt)
        sha = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        return sha, branch
    except subprocess.TimeoutExpired:
        return None, "it took too long"
    finally:
        _git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)
        _git("worktree", "prune")


def _modules_load(folder):
    """Every one of his modules still imports in the copy."""
    import os
    import sys
    import selftest
    names = selftest.modules()
    code = ("import importlib\nbad = []\n"
            f"for m in {names!r}:\n"
            "    try:\n        importlib.import_module(m)\n"
            "    except BaseException as e:\n        bad.append(f'{m}: {type(e).__name__}: {e}'[:200])\n"
            "print('@@' + '; '.join(bad))")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(folder), capture_output=True, text=True,
                       timeout=180, env=dict(os.environ, JARVIS_CHECK="1", PYTHONUTF8="1"))
    for line in reversed(r.stdout.splitlines()):
        if line.startswith("@@"):
            return line[2:] or None
    return (r.stderr or "modules didn't load").strip()[-200:]


def _night_key():
    """The night it is: from noon to noon counts as one night."""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - 12 * 3600))


def study():
    """One night's work: pick what to learn, build it, check it. Returns the pending lesson or None."""
    if _working.is_set():
        return None
    _working.set()
    try:
        with _lock:
            data = _load()
            data["last_night"] = _night_key()
            _save(data)
        misses, errors, ideas, declined = _gather()
        if not (misses or errors or ideas):
            print("(night school: nothing went wrong today, nothing to learn)")
            return None
        print(f"(night school: looking at {len(misses)} misses, {len(errors)} errors, {len(ideas)} ideas)")
        pick = _pick(misses, errors, ideas, declined)
        if not pick:
            print("(night school: nothing worth learning tonight)")
            return None
        print(f"(night school: learning to {pick['learned']})")
        sha, branch = _build(pick)
        entry = {"about": pick.get("about", ""), "learned": pick["learned"].rstrip(". "),
                 "request": pick["request"], "try_it": pick.get("try_it", ""), "t": time.time()}
        with _lock:
            data = _load()
            if not sha:
                print(f"(night school: didn't work out: {branch})")
                data.setdefault("history", []).append(dict(entry, result="failed", why=branch))
                _save(data)
                return None
            entry.update(sha=sha, branch=branch, id=int(time.time()), offered=0)
            data["pending"] = entry
            _save(data)
        try:                                       # the daytime idea machine shouldn't offer it separately
            import learn
            with learn._lock:
                ld = learn._load()
                import difflib
                for i in ld.get("ideas", []):
                    if i.get("status") in ("new", "offered") and difflib.SequenceMatcher(
                            None, (i.get("request") or "").lower(), pick["request"].lower()).ratio() > 0.45:
                        i["status"] = "night"
                stamps = {m["t"] for m in misses}
                for m in ld.get("misses", []):
                    if m["t"] in stamps:
                        m["looked"] = True
                learn._save(ld)
        except Exception:
            pass
        print(f"(night school: ready to show Connor: {entry['learned']})")
        return entry
    except Exception as e:
        print(f"(night school failed: {e})")
        return None
    finally:
        _working.clear()


# ---------- the morning ask ----------

def pending():
    p = _load().get("pending")
    if p and time.time() - p.get("t", 0) > 4 * 86400:      # never answered: let it go
        discard(p["id"], "expired")
        return None
    return p


def morning_offer():
    """What he learned overnight, to ask about now (once he's up), or None."""
    p = pending()
    if not p:
        return None
    hour = time.localtime().tm_hour
    start, end = _cfg().get("night_school_hours", [1, 5])
    if start <= hour < max(end, 6) or (p.get("offered") and time.time() - p.get("last_offer", 0) < 3 * 3600):
        return None                                   # still night, or he was asked in the last 3 hours
    return p


def offer_line(p):
    with _lock:
        data = _load()
        if data.get("pending") and data["pending"].get("id") == p["id"]:
            data["pending"].update(offered=p.get("offered", 0) + 1, last_offer=time.time())
            _save(data)
    return f"Overnight I taught myself to {p['learned']}. Want to keep it?"


def keep(pid):
    """Connor said yes: add the overnight fix to the live Jarvis. Returns (spoken reply, needs_restart)."""
    import learn
    import selfedit
    p = pending()
    if not p or p.get("id") != pid:
        return "That lesson's gone, sorry. I'll keep learning.", False
    selfedit.ensure_repo()
    old = _git("rev-parse", "HEAD").stdout.strip()
    r = _git("cherry-pick", p["sha"])
    if r.returncode != 0:
        _git("cherry-pick", "--abort")
        _git("reset", "-q", "--hard", old)
        _finish(p, "clashed")
        return "It doesn't fit with changes I've had since, so I've thrown it out. I'll learn it again tonight.", False
    problem = selfedit._check()
    if problem:
        _git("reset", "-q", "--hard", old)
        _finish(p, "failed", problem)
        learn.record_result("upgrade", p["request"][:150], False, detail=problem)
        return "It broke something when I added it, so I've put myself back how I was.", False
    _finish(p, "kept")
    learn.record_result("upgrade", p["request"][:150], True, detail="learned overnight")
    tip = f" Try saying: {p['try_it'].rstrip('.')}." if p.get("try_it") else ""
    return f"Kept. I can {p['learned']} now.{tip}", True


def discard(pid, result="declined"):
    p = _load().get("pending")
    if p and p.get("id") == pid:
        _finish(p, result)


def _finish(p, result, why=""):
    with _lock:
        data = _load()
        if data.get("pending") and data["pending"].get("id") == p.get("id"):
            data["pending"] = None
        data.setdefault("history", []).append(dict({k: v for k, v in p.items() if k != "sha"}, result=result,
                                                   why=why[:200], done=time.time()))
        _save(data)
    if p.get("branch"):
        _git("branch", "-D", p["branch"])


def last_night():
    """What he learned last night, to say out loud."""
    p = pending()
    if p:
        return offer_line(p)
    h = [x for x in _load().get("history", []) if x.get("t", 0) > time.time() - 2 * 86400]
    if not h:
        return "I didn't learn anything new last night. Nothing tripped me up enough to need it."
    x = h[-1]
    return {"kept": f"Last night I learned to {x['learned']}, and you kept it.",
            "declined": f"Last night I learned to {x['learned']}, but you passed on it.",
            "failed": f"Last night I tried to learn to {x['learned']}, but it didn't work out, so I dropped it.",
            "clashed": f"Last night I learned to {x['learned']}, but it didn't fit with later changes."
            }.get(x.get("result"), "I didn't learn anything new last night.")


# ---------- the night shift ----------

def _night_now():
    start, end = _cfg().get("night_school_hours", [1, 5])
    h = time.localtime().tm_hour
    return start <= h < end if start < end else (h >= start or h < end)


def start(is_busy):
    """Background: once a night, when it's night school hours and he's idle, he studies."""
    def loop():
        time.sleep(300)
        while True:
            try:
                if enabled() and _night_now() and not is_busy() and not pending() and \
                        _load().get("last_night") != _night_key():
                    study()
            except Exception as e:
                print(f"(night school check failed: {e})")
            time.sleep(300)
    threading.Thread(target=loop, daemon=True, name="night-school").start()
