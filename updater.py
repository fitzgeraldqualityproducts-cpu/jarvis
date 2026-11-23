"""Auto-updates: Jarvis installs his own new features.

New versions of Jarvis are published to a GitHub repository (set "update_repo" in config.json;
"auto_update": false turns this off). A few minutes after he starts, and then every few hours,
Jarvis checks it with git. When there's something new he:
  1. saves a snapshot of his folder (so your voice edits are kept),
  2. merges the new version in with your own changes (git merge; where they clash, the new
     version wins),
  3. installs any new Python parts it needs,
  4. runs a self-check (selftest.py: code, modules, voice, brain, microphone, speaker) before
     and after, and if anything that worked before is now broken, tries to fix it
     (reinstalling packages) and otherwise puts everything back,
  5. waits until you're not talking to him, then restarts and tells you what's new,
  6. checks himself again once he's started on the new version, and undoes the update
     (and restarts) if something broke that the first check missed.
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


DATA_PATTERNS = ("clipper_data/", "studio_data/", "db-rescue")
DATA_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".db.bak")


def _is_data(path):
    return path.startswith(DATA_PATTERNS) or path.endswith(DATA_SUFFIXES)


def _untrack_data():
    """Your databases and clips must never be replaced by a file from an update (an update once
    carried an empty clipper.db, which broke the real one). Anything like that which git is tracking
    in this folder is untracked (the files themselves stay exactly where they are), so a merge can't
    touch them."""
    tracked = [p for p in _out("ls-files").splitlines() if _is_data(p)]
    if not tracked:
        return []
    _git("rm", "-q", "--cached", "--", *tracked)
    _git("commit", "-q", "-m", "Keep databases out of updates")
    print(f"(update: untracked {len(tracked)} data file(s) so updates never replace them)")
    return tracked


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
    _untrack_data()                              # databases are never part of an update
    old = _out("rev-parse", "HEAD")
    before = _selftest(None)                     # how well everything works now, to compare with
    news = _summary(old, new)
    print(f"(update: installing {new[:8]}: {'; '.join(news)})")
    msg = "Update: " + ("; ".join(news) or new[:8])
    r = _git("merge", "--no-edit", "-m", msg, REMOTE_REF)
    clashed = False
    if r.returncode != 0:
        # A voice edit touched the same lines as the update. Keep the rest of the voice edits and
        # take the published version of the lines that clash (the old version stays in git, and a
        # failed self-check below puts it back).
        _git("merge", "--abort")
        _git("reset", "-q", "--hard", old)
        print(f"(update clashed with local changes, taking the new version of those lines: "
              f"{(r.stdout + r.stderr).strip()[-300:]})")
        r = _git("merge", "--no-edit", "-X", "theirs", "-m", msg, REMOTE_REF)
        clashed = r.returncode == 0
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
    after = None
    if not problem and before is not None:
        after, problem = _selftest(before, fix=True)
    if problem:
        print(f"(update broke something, undoing it: {problem})")
        _git("reset", "-q", "--hard", old)
        state.setdefault("skip", []).append(new)
        if problem.startswith("self-check") and not manual:
            state["note"] = ("I found an update, but it broke " + _broken_names(problem) +
                             " on this computer, so I left myself as I was.")
        _save(state)
        return ("There's an update, but it didn't work on this computer, so I've put myself back." if manual else None), False
    state.update(pre=old, applied=new, applied_at=time.time(), verified=False,
                 baseline=before or state.get("baseline"), selftest=after,
                 note=(("I've updated myself. What's new: " + ". ".join(news) + ".") if news else "I've updated myself.") +
                      (" It overlapped a change you'd asked me to make, so I used the new version of those parts."
                       if clashed else ""))
    state["skip"] = [s for s in state.get("skip", []) if s != new][-20:]
    _save(state)
    _ready.set()
    print("(update installed; restarting when Jarvis is free)")
    return "I've installed an update. " + (("What's new: " + ". ".join(news) + ". ") if news else ""), True


def _selftest(before, fix=False):
    """With before=None: runs the self-check and returns its results (None if it can't run).
    Otherwise runs it again and returns (results, problem): problem names anything that
    worked before but doesn't now, after trying to fix it by reinstalling packages."""
    if not _cfg().get("self_test", True):
        return None if before is None else (None, None)
    try:
        import importlib
        import selftest
        importlib.reload(selftest)                # the version that just arrived
        if before is None:
            return selftest.run()
        after = selftest.run()
        broken = selftest.regressions(before, after)
        for _ in range(2):                        # a busy computer can make a check time out: try those again
            slow = selftest.timed_out(after, broken)
            if not slow:
                break
            print(f"(self-check: {', '.join(slow)} took too long, trying again in a moment)")
            time.sleep(20)
            after.update(selftest.run(only=slow))
            broken = selftest.regressions(before, after)
        if broken and fix and selftest.missing_packages(after, broken):
            print("(self-check: something's missing, reinstalling packages)")
            if not _pip_install():
                after = selftest.run()
                broken = selftest.regressions(before, after)
        if broken:
            return after, "self-check: " + "; ".join(f"{n} ({after[n]['detail'][:100]})" for n in broken)
        return after, None
    except Exception as e:
        print(f"(self-check couldn't run: {e})")
        return None if before is None else (None, None)


def verify_on_start(restart, is_busy=lambda: False, delay=25):
    """Runs on startup: if an update was just installed, check everything again on the new
    version. If something broke, undo the update and restart on the old one."""
    state = _state()
    if state.get("verified", True) or not state.get("pre") or not state.get("applied_at"):
        return

    def go():
        time.sleep(delay)                         # let him finish starting up
        while is_busy():
            time.sleep(5)
        st = _state()
        baseline = st.get("baseline")
        if not baseline:
            import selftest
            st.update(verified=True, baseline=selftest.run())
            _save(st)
            return
        after, problem = _selftest(baseline, fix=True)
        st = _state()
        st["verified"] = True
        if after:
            st["selftest"] = after
        _save(st)
        if problem:
            print(f"(the new version failed its self-check, undoing the update: {problem})")
            if rollback_recent(window=7 * 24 * 3600,
                               note="I tried an update, but it broke " + _broken_names(problem) +
                                    ", so I undid it and went back to how I was."):
                while is_busy():
                    time.sleep(5)
                restart()
        else:
            print("(self-check after the update: all good)")
    threading.Thread(target=go, daemon=True, name="selfcheck").start()


def _broken_names(problem):
    import selftest
    names = [v for k, v in selftest.NAMES.items() if f" {k} (" in " " + problem.split(":", 1)[-1]]
    return " and ".join(names) or "something"


def self_check():
    """For "run a self check": returns what to say."""
    import importlib
    import selftest
    importlib.reload(selftest)
    results = selftest.run()
    st = _state()
    st["last_selftest"] = results
    _save(st)
    return selftest.describe(results)


def rollback_recent(window=1800, note=None):
    """Undo an automatic update installed in the last half hour. Used when Jarvis keeps crashing."""
    state = _state()
    pre, applied = state.get("pre"), state.get("applied")
    if not pre or time.time() - state.get("applied_at", 0) > window:
        return False
    r = _git("reset", "-q", "--hard", pre)
    if r.returncode != 0:
        return False
    state.setdefault("skip", []).append(applied)
    state.update(pre=None, applied_at=0, verified=True,
                 note=note or "An update made me crash, so I undid it and went back to how I was.")
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
