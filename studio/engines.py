"""Optional free tools that make the Shorts look and sound better. Each one runs in its own small
environment under tools/ (so their library versions never clash with Jarvis's own), is started as a
separate process for one job and then exits (so its memory is given back), and has a fallback: if a
tool is missing, too slow, short of memory or fails, the studio uses what it used before.

  voice    "kokoro" (default)  | "chatterbox"  Chatterbox (MIT, Resemble AI): more expressive narration,
                                               in the channel's own voice (it copies a reference clip
                                               Kokoro makes). Kokoro stays the fallback.
  align    "whisper" (default) | "whisperx"    whisperX (BSD-2): forced alignment of the KNOWN script to
                                               the audio, so every caption word lands on time.
  visuals  "classic" (default) | "manim"       Manim Community (MIT): animated numbers, equations, bars,
                                               circles and steps instead of still pictures.
  compose  "classic" (default) | "remotion"    Remotion (free for individuals and companies of up to 3
                                               people): animated captions, transitions, motion.

Config: "studio": {"engines": {"voice": ..., "align": ..., "visuals": ..., "compose": ...}}.
`python -m studio engines install` sets the tools up (free downloads, a few GB), `... status` shows
them. Only one heavy job runs at a time (a lock shared with renders), and a tool that needs more free
memory than the Mac has right now is skipped for that job."""
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from studio import ROOT, config, data_dir

TOOLS = ROOT / "tools"
WORKERS = Path(__file__).parent / "workers"
REMOTION = Path(__file__).parent / "remotion"
DEFAULTS = {"voice": "kokoro", "align": "whisper", "visuals": "classic", "compose": "classic"}
UPGRADED = {"voice": "chatterbox", "align": "whisperx", "visuals": "manim", "compose": "remotion"}
# what each tool needs: its environment's python, the packages, free memory (GB) and a time limit (s)
SPECS = {
    "chatterbox": {"python": "3.11", "pip": ["chatterbox-tts==0.1.7"], "mem_gb": 3.0, "timeout": 1500},
    "whisperx": {"python": "3.12", "pip": ["whisperx==3.8.6", "num2words==0.5.14"], "mem_gb": 1.5, "timeout": 600},
    "manim": {"conda": ["python=3.12", "manim"], "mem_gb": 1.0, "timeout": 1200},
    "remotion": {"node": True, "mem_gb": 1.5, "timeout": 1800},
}
TOOL_FOR = {"voice": "chatterbox", "align": "whisperx", "visuals": "manim", "compose": "remotion"}
RUNNER = None          # tests swap this: (tool, worker, payload, timeout) -> dict


class EngineError(Exception):
    pass


def settings(override=None):
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (config.general().get("engines") or {}).items() if k in DEFAULTS and v})
    s.update({k: v for k, v in (override or {}).items() if k in DEFAULTS and v})
    return s


def wanted(area, override=None):
    """True when this area is set to its upgraded tool."""
    return settings(override)[area] == UPGRADED[area]


def env_dir(tool):
    return TOOLS / tool


def python(tool):
    d = env_dir(tool)
    p = d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return p


def installed(tool):
    if tool == "remotion":
        return (REMOTION / "node_modules" / "@remotion" / "cli").exists() and bool(shutil.which("npx") or _npx())
    return python(tool).exists()


def _npx():
    for p in ("/usr/local/bin/npx", "/opt/homebrew/bin/npx"):
        if Path(p).exists():
            return p
    return None


# ---------- memory and the one-heavy-job lock ----------

def free_gb():
    """Memory the Mac can hand out right now (free + inactive + purgeable), or None if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / 2 ** 30
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = 16384 if "16384" in out.splitlines()[0] else 4096
            n = 0
            for line in out.splitlines():
                if line.startswith(("Pages free", "Pages inactive", "Pages purgeable", "Pages speculative")):
                    n += int(line.split(":")[1].strip().rstrip("."))
            return n * page / 2 ** 30
        except Exception:
            return None
    return None


def _free_local_brain():
    """Heavy jobs get the memory: the local brain's model (Ollama) is unloaded first; it reloads when next needed."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import local_brain
        local_brain.unload()
    except Exception:
        pass


@contextmanager
def heavy(what, wait=900):
    """One heavy job at a time (Chatterbox, whisperX, Manim, Remotion, a render): waits for the lock."""
    path = data_dir() / "heavy.lock"
    f = open(path, "a+")
    t0 = time.time()
    try:
        try:
            import fcntl
        except ImportError:                       # Windows: no lock, the studio runs one job at a time anyway
            fcntl = None
        if fcntl:
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() - t0 > wait:
                        raise EngineError(f"another heavy job kept the Mac busy, so {what} was skipped")
                    time.sleep(2)
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()} {what} {time.time():.0f}\n")
        f.flush()
        _free_local_brain()
        yield
    finally:
        f.close()


# ---------- running a worker ----------

def run(tool, worker, payload, timeout=None):
    """Runs studio/workers/<worker>.py inside the tool's environment with a JSON payload; returns its
    JSON answer. Raises EngineError on any problem (the caller falls back)."""
    spec = SPECS[tool]
    timeout = timeout or spec["timeout"]
    if RUNNER:
        return RUNNER(tool, worker, payload, timeout)
    if not installed(tool):
        raise EngineError(f"{tool} isn't installed (run: python -m studio engines install)")
    free = free_gb()
    if free is not None and free < spec["mem_gb"]:
        raise EngineError(f"{tool} needs about {spec['mem_gb']:.1f} GB of free memory and only {free:.1f} GB is free")
    with tempfile.TemporaryDirectory(prefix=f"jv-{tool}-") as tmp:
        pin, pout = Path(tmp) / "in.json", Path(tmp) / "out.json"
        pin.write_text(json.dumps(payload))
        env = dict(os.environ, PYTHONNOUSERSITE="1", TOKENIZERS_PARALLELISM="false",
                   HF_HUB_DISABLE_TELEMETRY="1", JV_WORKERS=str(WORKERS))
        env.pop("PYTHONPATH", None)
        cmd = [str(python(tool)), "-I", str(WORKERS / f"{worker}.py"), str(pin), str(pout)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=tmp)
        except subprocess.TimeoutExpired:
            raise EngineError(f"{tool} took longer than {timeout // 60} minutes")
        if p.returncode != 0 or not pout.exists():
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
            raise EngineError(f"{tool} failed: {' | '.join(tail)[:300]}")
        ans = json.loads(pout.read_text())
        if ans.get("error"):
            raise EngineError(f"{tool}: {ans['error'][:300]}")
        return ans


def remotion_render(props, public_dir, out, timeout=None):
    """Renders the Remotion composition "Short" with these props into out (an MP4)."""
    if RUNNER:
        return RUNNER("remotion", "render", {"props": props, "public": str(public_dir), "out": str(out)}, timeout)
    if not installed("remotion"):
        raise EngineError("Remotion isn't installed (run: python -m studio engines install)")
    free = free_gb()
    if free is not None and free < SPECS["remotion"]["mem_gb"]:
        raise EngineError(f"Remotion needs about {SPECS['remotion']['mem_gb']} GB of free memory and only {free:.1f} GB is free")
    pf = Path(public_dir) / "remotion_props.json"
    pf.write_text(json.dumps(props))
    npx = shutil.which("npx") or _npx()
    cmd = [npx, "remotion", "render", "src/index.ts", "Short", str(out), f"--props={pf}",
           f"--public-dir={public_dir}", "--concurrency=2", "--codec=h264", "--audio-codec=aac",
           "--log=error", "--overwrite"]
    if os.environ.get("JARVIS_REMOTION_BROWSER"):                 # a browser already on this computer
        cmd.append(f"--browser-executable={os.environ['JARVIS_REMOTION_BROWSER']}")
    env = dict(os.environ, REMOTION_DISABLE_TELEMETRY="1")
    try:
        p = subprocess.run(cmd, cwd=REMOTION, capture_output=True, text=True, timeout=timeout or SPECS["remotion"]["timeout"],
                           env=env)
    except subprocess.TimeoutExpired:
        raise EngineError("Remotion took too long")
    if p.returncode != 0 or not Path(out).exists():
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
        raise EngineError(f"Remotion failed: {' | '.join(tail)[:300]}")
    return {"path": str(out)}


# ---------- install and status ----------

def _uv():
    for p in (shutil.which("uv"), str(Path.home() / ".local/bin/uv"), str(Path.home() / ".cargo/bin/uv")):
        if p and Path(p).exists():
            return p
    return None


def _micromamba():
    """A local micromamba (single free binary from conda-forge's mamba project) for Manim, whose drawing
    libraries (cairo, pango) have no Mac wheels on PyPI."""
    mm = TOOLS / "bin" / "micromamba"
    if mm.exists():
        return mm
    if sys.platform != "darwin":
        return shutil.which("micromamba")
    arch = "osx-arm64" if platform.machine() == "arm64" else "osx-64"
    url = f"https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-{arch}"
    import hashlib
    import urllib.request
    mm.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(url, timeout=120).read()
    try:
        want = urllib.request.urlopen(url + ".sha256", timeout=60).read().decode().split()[0]
    except Exception:
        want = None
    if want and hashlib.sha256(data).hexdigest() != want:
        raise EngineError("the micromamba download didn't match its checksum, so it wasn't used")
    mm.write_bytes(data)
    mm.chmod(0o755)
    return mm


def install(tool, log=print):
    """Sets one tool up. Free downloads only; nothing global is changed (no Homebrew, no sudo)."""
    TOOLS.mkdir(exist_ok=True)
    spec = SPECS[tool]
    if tool == "remotion":
        npm = shutil.which("npm") or "/usr/local/bin/npm"
        if not Path(npm).exists():
            raise EngineError("Node.js isn't installed, so Remotion can't be set up")
        log("Installing Remotion (Node packages, about 300 MB)...")
        subprocess.run([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=REMOTION, check=True)
        log("Downloading Remotion's headless browser...")
        subprocess.run([shutil.which("npx") or _npx(), "remotion", "browser", "ensure"], cwd=REMOTION, check=False)
        return
    if "conda" in spec:
        mm = _micromamba()
        if not mm:
            raise EngineError("micromamba isn't available")
        log(f"Installing {tool} from conda-forge (about 700 MB)...")
        env = dict(os.environ, MAMBA_ROOT_PREFIX=str(TOOLS / "mamba"))
        subprocess.run([str(mm), "create", "-y", "-q", "-p", str(env_dir(tool)), "-c", "conda-forge", *spec["conda"]],
                       check=True, env=env)
        return
    uv = _uv()
    if not uv:
        raise EngineError("uv isn't installed (it's what Jarvis uses to make Python environments)")
    log(f"Installing {tool} (Python {spec['python']}, a few hundred MB)...")
    subprocess.run([uv, "venv", "-q", "--python", spec["python"], str(env_dir(tool))], check=True)
    subprocess.run([uv, "pip", "install", "-q", "--python", str(python(tool)), *spec["pip"]], check=True)


def check(tool):
    """(ok, detail) from a tiny real job in the tool's environment (loads the library, not the models)."""
    if not installed(tool):
        return False, "not installed"
    if tool == "remotion":
        try:
            p = subprocess.run([shutil.which("npx") or _npx(), "remotion", "versions"], cwd=REMOTION,
                               capture_output=True, text=True, timeout=120)
            return p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[-1:] and \
                (p.stdout or p.stderr).strip().splitlines()[-1][:120]
        except Exception as e:
            return False, str(e)[:120]
    try:
        ans = run(tool, "probe", {"tool": tool}, timeout=180)
        return True, ans.get("version", "ok")
    except EngineError as e:
        return False, str(e)


def status():
    s = settings()
    out = {"settings": s, "free_memory_gb": None if free_gb() is None else round(free_gb(), 1), "tools": {}}
    for area, tool in TOOL_FOR.items():
        out["tools"][tool] = {"for": area, "installed": installed(tool), "in_use": s[area] == UPGRADED[area]}
    return out


def use(on=True, areas=None):
    """Switches the live studio to the upgraded tools (or back)."""
    cur = dict(config.general().get("engines") or {})
    for area in areas or list(DEFAULTS):
        cur[area] = UPGRADED[area] if on else DEFAULTS[area]
    config.set_values(engines=cur)
    from studio import guard
    guard.audit("engines.use", True, cur)
    return cur
