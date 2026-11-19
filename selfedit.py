"""Lets Jarvis change his own code when you ask by voice.

He hands your request to Claude Code (the same login as his brain), which edits the
files in this folder. Before every change he saves a snapshot with git, so
"undo that" puts everything back. If an edit breaks his code, he undoes it himself."""
import json
import subprocess
import sys
from pathlib import Path

import brain

HERE = Path(__file__).resolve().parent
GITIGNORE = """.venv*/
models/
__pycache__/
*.pyc
jarvis.log
memory.json
.spotify_token
.update-*
*.part
oura_tokens.json
sounds/
calendar.json
jarvis.ico
updates/
*.tgz
lessons.json
lessons.tmp
habits.json
habits.tmp
team.json
team.tmp
team/
storeforge_actions.json
storeforge_actions.tmp
storeforge.log
.night/
nightschool.json
team_lessons.json
"""
PROMPT = """You are editing the source code of Jarvis, a Python voice assistant that lives in this folder.
It runs on macOS and on Windows (Windows-only parts are in win.py and behind sys.platform == "win32"
checks); keep both working. Read README.md and jarvis.py first to see how it fits together: voice commands are
routed in jarvis.py (handle -> organize / computer / music), settings live in config.json, the
display is display.html, speech is voice.py, listening is ears.py.

The owner asked, out loud: "{request}"

Rules:
- Make the smallest change that does this well, in the existing style. Don't remove features.
- Code must work on Python 3.9 and later and must not need new packages unless unavoidable
  (if a new package is needed, add it to requirements.txt and say so).
- Never touch .venv, models/, memory.json, .spotify_token or .git.
- If the request is unclear, unsafe, or impossible, change nothing and explain why.
- You can't run commands; Jarvis checks the code himself afterwards, so don't mention testing.
- Finish with ONE short plain sentence, to be spoken aloud, saying what you changed
  (or why you didn't). No markdown."""


def _git(*args):
    return subprocess.run(["git", "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost", *args],
                          cwd=HERE, capture_output=True, text=True)


def ensure_repo():
    if not (HERE / ".git").exists():
        _git("init", "-q")
    ignore = HERE / ".gitignore"
    have = ignore.read_text() if ignore.exists() else ""
    missing = [l for l in GITIGNORE.splitlines() if l and l not in have.splitlines()]
    if missing:
        ignore.write_text(have + ("" if not have or have.endswith("\n") else "\n") + "\n".join(missing) + "\n")
    _git("add", "-A")
    if _git("status", "--porcelain").stdout.strip():
        _git("commit", "-q", "-m", "Snapshot before a voice edit")


def _changed_files():
    out = _git("status", "--porcelain").stdout.splitlines()
    return [line[3:].strip().strip('"') for line in out if line.strip()]


def _check(folder=None):
    """Returns None if the code still looks healthy, otherwise what's wrong. folder: a copy of
    Jarvis to check instead of this one (night school builds its fixes in a copy)."""
    folder = Path(folder or HERE)
    for f in folder.glob("*.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(f)], capture_output=True, text=True)
        if r.returncode != 0:
            return f"{f.name}: {r.stderr.strip()[-300:]}"
    try:
        json.loads((folder / "config.json").read_text()) if (folder / "config.json").exists() else None
    except Exception as e:
        return f"config.json: {e}"
    import os
    r = subprocess.run([sys.executable, "-c", "import jarvis"], cwd=folder, capture_output=True, text=True,
                       env=dict(os.environ, JARVIS_CHECK="1"))
    if r.returncode != 0:
        return r.stderr.strip()[-300:]
    return None


def _rollback():
    _git("reset", "-q", "--hard", "HEAD")
    _git("clean", "-q", "-fd")


def apply(request):
    """Returns (spoken reply, needs_restart)."""
    claude = brain.find_claude()
    if not claude:
        return "I can't find Claude Code, so I can't edit myself.", False
    ensure_repo()
    print(f"(self-edit: {request})")
    try:
        out = brain.run_claude(
            [claude, "-p", PROMPT.format(request=request.replace('"', "'")),
             "--permission-mode", "acceptEdits",
             "--allowedTools", "Read,Edit,Write,Glob,Grep"],
            timeout=900, cwd=HERE)
    except subprocess.TimeoutExpired:
        _rollback()
        return "That took too long, so I've left everything as it was.", False
    summary = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    print(f"(self-edit result: {out.stdout.strip()[-600:]} {out.stderr.strip()[-300:]})")
    changed = _changed_files()
    if not changed:
        return summary or "I didn't end up changing anything.", False
    problem = _check()
    if problem:
        print(f"(self-edit broke something, rolling back: {problem})")
        _rollback()
        return "I tried, but the change broke part of me, so I've put everything back the way it was.", False
    _git("add", "-A")
    _git("commit", "-q", "-m", f"Voice edit: {request}")
    print(f"(self-edit changed: {', '.join(changed)})")
    return summary or "Done.", True


def undo():
    """Reverses the most recent voice edit, keeping any other changes made since.
    Returns (spoken reply, needs_restart)."""
    if not (HERE / ".git").exists():
        return "I haven't changed myself yet, so there's nothing to undo.", False
    ensure_repo()
    log = _git("log", "-50", "--format=%H %s").stdout.splitlines()
    undone = {l.split(" ", 1)[1].removeprefix("Undo: ") for l in log if " Undo: " in l}
    for line in log:
        sha, subject = line.split(" ", 1)
        if subject.startswith("Voice edit:") and subject not in undone:
            r = _git("revert", "--no-edit", sha)
            if r.returncode != 0:
                _git("revert", "--abort")
                return "I couldn't cleanly undo that one, because later changes depend on it.", False
            _git("commit", "--amend", "-q", "-m", f"Undo: {subject}")
            return f"Undone. I've reversed: {subject.removeprefix('Voice edit: ')}.", True
    return "There's no voice edit left to undo.", False


def last_change():
    if not (HERE / ".git").exists():
        return "I haven't changed myself yet."
    log = _git("log", "-5", "--format=%s").stdout.splitlines()
    edits = [l.removeprefix("Voice edit: ") for l in log if l.startswith("Voice edit:")]
    if not edits:
        return "I haven't changed myself yet."
    return "My last change was: " + edits[0] + "." + (f" Before that: {edits[1]}." if len(edits) > 1 else "")


def restart():
    """Reload Jarvis with his new code."""
    import os
    args = [a for a in sys.argv[1:] if a != "--no-display"] + ["--no-display"]
    print("(restarting to load the new code)")
    sys.stdout.flush()
    if os.environ.get("JARVIS_CHILD") or sys.platform == "win32":
        sys.stdout.flush()
        os._exit(3)          # the watchdog (keeper.py) sees code 3 and starts him again
    os.execv(sys.executable, [sys.executable, str(HERE / "jarvis.py")] + args)
