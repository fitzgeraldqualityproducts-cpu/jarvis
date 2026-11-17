"""Keeper: keeps Jarvis running, and makes sure only one copy runs.

When you start Jarvis, this small watchdog starts first. It runs the real Jarvis as a
child process and:
  - restarts him if he crashes (waiting a little longer each time, and giving up after
    5 crashes in 5 minutes so a broken setup doesn't loop forever),
  - restarts him straight away when he asks (exit code 3: after a self-edit or an update),
  - rolls back a fresh automatic update if he keeps crashing right after it,
  - stops for good when you quit him (menu bar / tray "Quit Jarvis", or Ctrl+C).

Only one copy can run: a second start just opens the display of the one already running.
Exit codes: 0 quit, 3 restart me, 4 another copy is already running, anything else = crash."""
import datetime
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "jarvis.log"
KEEPER_PORT = 47621          # held by the watchdog while it runs
JARVIS_PORT = 47622          # held by Jarvis himself while he runs
DISPLAY_URL = "http://localhost:8765"
RESTART, ALREADY_RUNNING = 3, 4
STOP_SIGNALS = {-getattr(signal, n) for n in ("SIGTERM", "SIGKILL", "SIGINT", "SIGHUP") if hasattr(signal, n)}
_held = []


def _log(msg):
    print(msg)
    try:
        with open(LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    except OSError:
        pass


def take_lock(port):
    """Claims a local port as a 'Jarvis is running' flag. False if someone already has it.
    The OS frees it automatically when the process ends, even after a crash."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _held.append(s)
    return True


def already_running(open_display=True):
    where = "the icon by the clock" if sys.platform == "win32" else "his icon in the menu bar"
    _log(f"Jarvis is already running (look for {where}), so I won't start a second copy.")
    if open_display:
        try:
            webbrowser.open(DISPLAY_URL)
        except Exception:
            pass


def claim_jarvis_lock():
    """Called by Jarvis himself as he starts. Exits if another Jarvis is already running."""
    if not take_lock(JARVIS_PORT):
        already_running("--no-display" not in sys.argv)
        sys.stdout.flush()
        os._exit(ALREADY_RUNNING)


def supervised():
    return bool(os.environ.get("JARVIS_CHILD"))


def restart_now():
    """Ask the watchdog to start a fresh Jarvis (loads new code)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(RESTART)


def _rollback_recent_update():
    try:
        import updater
        return updater.rollback_recent()
    except Exception as e:
        _log(f"(couldn't check for a recent update to roll back: {e})")
        return False


def supervise(script):
    """Runs Jarvis and keeps him running. Returns the exit code for the launcher."""
    if not take_lock(KEEPER_PORT):
        already_running("--no-display" not in sys.argv)
        return ALREADY_RUNNING
    args = sys.argv[1:]
    crashes = []
    env = dict(os.environ, JARVIS_CHILD="1", PYTHONUTF8="1")
    while True:
        proc = None
        try:
            proc = subprocess.Popen([sys.executable, str(script)] + args, env=env, cwd=str(HERE))
            code = proc.wait()
        except KeyboardInterrupt:
            if proc:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            return 0
        if code == 0 or code in STOP_SIGNALS:
            return 0
        if code == ALREADY_RUNNING:
            return ALREADY_RUNNING
        if "--no-display" not in args:      # the display tab is already open; it reconnects
            args = args + ["--no-display"]
        if code == RESTART:
            continue
        now = time.time()
        crashes = [t for t in crashes if now - t < 300] + [now]
        if len(crashes) >= 3 and _rollback_recent_update():
            _log("(Jarvis kept crashing after an automatic update, so I've undone that update.)")
            crashes = []
            continue
        if len(crashes) >= 5:
            _log("Jarvis crashed 5 times in 5 minutes, so I've stopped trying. "
                 "Send the jarvis.log file in his folder to Claude.")
            return code
        wait = min(5 * len(crashes), 30)
        _log(f"(Jarvis stopped unexpectedly, code {code}. Restarting him in {wait} seconds.)")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0
