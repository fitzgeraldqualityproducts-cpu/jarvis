"""Auto-updates: Jarvis installs his own new features.

New versions of Jarvis are published to a GitHub repository (set "update_repo" in config.json;
"auto_update": false turns this off). A few minutes after he starts, and then every few hours,
Jarvis checks it with git. When there's something new he:
  1. saves a snapshot of his folder (so your voice edits are kept),
  2. merges the new version in with your own changes (git merge),
  3. installs any new Python parts it needs,
  4. checks that his code still loads, and puts everything back if it doesn't,
  5. waits until you're not talking to him, then restarts and tells you what's new.
If he keeps crashing right after an update, the watchdog (keeper.py) undoes it.
Your settings, memory, tokens, calendar and notes are never part of an update."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".update-state.json"
DEFAULT_REPO = "https://github.com/fitzgeraldqualityproducts-cpu/jarvis.git"
BRANCH = "main"
REMOTE_REF = "refs/remotes/updates/main"
FIRST_CHECK = 3 * 60            # seconds after start
EVERY = 3 * 3600                # then every 3 hours
_lock = threading.Lock()
_ready = threading.Event()      # an update is installed and waiting for a restart


def _cfg():
    try:
        return json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"(update: couldn't save state: {e})")


def _git_exe():
    found = shutil.which("git")
    if found:
        return found
    for p in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe",
              "/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git"):
        if os.path.exists(p):
            return p
    return None


def _git(*args, timeout=120):
    git = _git_exe()
    if not git:
        raise RuntimeError("git isn't installed")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="",
               GIT_MERGE_AUTOEDIT="no")
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000          # no flashing console window
    return subprocess.run([git, "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost",
                           "-c", "credential.helper=", "-c", "core.autocrlf=false", *args],
                          cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL, **kw)


def _out(*args):
    r = _git(*args)
    return r.stdout.strip() if r.returncode == 0 else ""


def enabled():
    return _cfg().get("auto_update", True) and bool(_repo())


def _repo():
    return (_cfg().get("update_repo") or DEFAULT_REPO).strip()


def _fetch():
    r = _git("fetch", "--quiet", "--no-tags", _repo(), f"+refs/heads/{BRANCH}:{REMOTE_REF}", timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "fetch failed")
    return _out("rev-parse", REMOTE_REF)


def _connect_history():
    """First time only: tell git that this folder already has the first published version,
    so later updates only bring the changes since then (and keep your voice edits)."""
    if _out("merge-base", "HEAD", REMOTE_REF):
        return
    roots = _out("rev-list", "--max-parents=0", REMOTE_REF).splitlines()
    if not roots:
        raise RuntimeError("the update repository is empty")
    r = _git("merge", "-s", "ours", "--allow-unrelated-histories", "--no-edit",
             "-m", "Connect to Jarvis updates", roots[-1])
    if r.returncode != 0:
        raise RuntimeError(f"couldn't connect to updates: {r.stderr.strip()[-200:]}")
    print("(update: connected this folder to Jarvis updates)")


def _pip_install():
    req = str(HERE / "requirements.txt")
    home = Path.home()
    uv = shutil.which("uv") or next((str(p) for p in (home / ".local/bin/uv", home / ".local/bin/uv.exe",
                                                       home / ".cargo/bin/uv") if p.exists()), None)
    cmd = [uv, "pip", "install", "--python", sys.executable, "-r", req] if uv else \
        [sys.executable, "-m", "pip", "install", "-q", "-r", req]
    print("(update: installing new parts)")
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=1500)
    if r.returncode != 0:
        return "installing new parts failed: " + (r.stderr or r.stdout).strip()[-300:]
    return None


def _summary(old, new):
    subjects = _out("log", "--no-merges", "--format=%s", f"{old}..{new}").splitlines()
    subjects = [s.strip().rstrip(".") for s in subjects
                if s.strip() and not s.startswith(("Snapshot", "Voice edit", "Undo:", "Connect to", "Merge"))]
    return subjects[:3]


def check(manual=False):
    """Looks for an update and installs it. Returns (what to say or None, restart_needed)."""
    if not _lock.acquire(blocking=False):
        return ("I'm already checking for updates.", False) if manual else (None, False)
    try:
        return _check(manual)
    except Exception as e:
        print(f"(update check failed: {e})")
        return ("I couldn't check for updates just now. I'll try again later.", False) if manual else (None, False)
    finally:
        _lock.release()


def _check(manual):
    if _ready.is_set():
        return "I've already installed an update. I'll restart to load it as soon as we're done talking.", True
    import selfedit
    selfedit.ensure_repo()                       # makes sure there's a git snapshot of the folder
    state = _state()
    state["last_check"] = time.time()
    _save(state)
    new = _fetch()
    _connect_history()
    if _git("merge-base", "--is-ancestor", REMOTE_REF, "HEAD").returncode == 0:
        return ("I'm already up to date." if manual else None), False
    if new in state.get("skip", []) and not manual:
        return None, False
    selfedit.ensure_repo()                       # snapshot anything changed since
    old = _out("rev-parse", "HEAD")
    news = _summary(old, new)
    print(f"(update: installing {new[:8]}: {'; '.join(news)})")
    r = _git("merge", "--no-edit", "-m", "Update: " + ("; ".join(news) or new[:8]), REMOTE_REF)
    if r.returncode != 0:
        _git("merge", "--abort")
        _git("reset", "-q", "--hard", old)
        print(f"(update clashed with local changes: {(r.stdout + r.stderr).strip()[-400:]})")
        state.setdefault("skip", []).append(new)
        _save(state)
        return ("There's an update, but it clashes with changes you've made to me, so I've left myself as I am."
                if manual else None), False
    problem = None
    if _git("diff", "--quiet", old, "HEAD", "--", "requirements.txt").returncode != 0:
        problem = _pip_install()
    problem = problem or selfedit._check()
    if problem:
        print(f"(update broke something, undoing it: {problem})")
        _git("reset", "-q", "--hard", old)
        state.setdefault("skip", []).append(new)
        _save(state)
        return ("There's an update, but it didn't work on this computer, so I've put myself back." if manual else None), False
    state.update(pre=old, applied=new, applied_at=time.time(),
                 note=("I've updated myself. What's new: " + ". ".join(news) + ".") if news else "I've updated myself.")
    state["skip"] = [s for s in state.get("skip", []) if s != new][-20:]
    _save(state)
    _ready.set()
    print("(update installed; restarting when Jarvis is free)")
    return "I've installed an update. " + (("What's new: " + ". ".join(news) + ". ") if news else ""), True


def rollback_recent(window=1800):
    """Undo an automatic update installed in the last half hour. Used when Jarvis keeps crashing."""
    state = _state()
    pre, applied = state.get("pre"), state.get("applied")
    if not pre or time.time() - state.get("applied_at", 0) > window:
        return False
    r = _git("reset", "-q", "--hard", pre)
    if r.returncode != 0:
        return False
    state.setdefault("skip", []).append(applied)
    state.update(pre=None, applied_at=0, note="An update made me crash, so I undid it and went back to how I was.")
    _save(state)
    return True


def pop_note():
    """Something to say on the first wake after an update, or None."""
    state = _state()
    note = state.pop("note", None)
    if note:
        state["last_note"] = note
        _save(state)
    return note


def last_note():
    return _state().get("last_note") or "I haven't updated myself recently."


def start(is_busy, restart):
    """Background thread: checks now and then, restarts when Jarvis is free after an update."""
    def loop():
        time.sleep(FIRST_CHECK)
        while True:
            if enabled():
                _, needs_restart = check()
                if needs_restart:
                    while is_busy():
                        time.sleep(5)
                    time.sleep(2)
                    if not is_busy():
                        print("(restarting to load the update)")
                        restart()
            time.sleep(EVERY)
    threading.Thread(target=loop, daemon=True, name="updater").start()
