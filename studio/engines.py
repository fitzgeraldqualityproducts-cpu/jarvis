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
  images   "classic" (default) | "sdxl"        SDXL-Turbo (Stability AI Community License: free, also for
                                               commercial use, under $1M a year in revenue): an AI
                                               illustration behind each scene's numbers. NVIDIA card only.
  music    "none" (default)    | "acestep"     ACE-Step 1.5 (MIT; its music may be used commercially): quiet
                                               instrumental music under the voice, matched to the Short.
                                               NVIDIA card only. (MusicGen was ruled out: CC-BY-NC weights.)
  look     "diagram" (default) | "hud"         "hud" is the cinematic look from Connor's reference video: a
                                               full-screen AI picture per scene, a counter in the top left and
                                               one serif sentence at a time. Needs the AI pictures (images).
  sharpen  "none" (default)    | "esrgan"      Real-ESRGAN x4plus (BSD-3): each AI picture made 4x sharper
                                               (runs in the sdxl environment, weights 64 MB on D:).
  motion   "zoom" (default)    | "depth"       Depth Anything V2 Small (Apache-2.0) finds how far away every
                                               pixel is and the camera moves through the picture (near things
                                               move more than far ones) instead of a flat zoom.
  edit     "calm" (default)    | "lively"      animated diagrams over the picture (bars, calculations, steps),
                                               a counter that pops when it lands, a flash on the re-hook, a
                                               punch-in and shake on the payoff, a jump zoom in long scenes.
  sfx      "none" (default)    | "synth"       sound effects made by Jarvis itself (whooshes, ticks, a hit on
                                               the payoff), under the voice. Nothing downloaded.
  footage  "none" (default)    | "pexels"      real stock clips from Pexels for some scenes (free key, saved
                                               with Connect Stock Footage); the AI picture stays the fallback.

Config: "studio": {"engines": {"voice": ..., "align": ..., "visuals": ..., "compose": ...}}.
`python -m studio engines install` sets the tools up (free downloads, a few GB), `... status` shows
them. Only one heavy job runs at a time (a lock shared with renders), and a tool that needs more free
memory than the computer has right now is skipped for that job.

Windows PC with an NVIDIA card: Chatterbox and whisperX get the CUDA build of PyTorch (so they run on
the graphics card), Manim comes from PyPI (its drawing libraries have Windows wheels) and Remotion
uses Node.js (its official portable copy in tools/node if the PC has none). SDXL-Turbo and ACE-Step keep
their model downloads and raw outputs on D:\\Jarvis when the PC has a D: drive (code and environments stay
in tools/ on C:); config "studio.models_dir" / "studio.outputs_dir" can point elsewhere."""
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from studio import ROOT, config, data_dir

TOOLS = ROOT / "tools"
WORKERS = Path(__file__).parent / "workers"
REMOTION = Path(__file__).parent / "remotion"
DEFAULTS = {"voice": "kokoro", "align": "whisper", "visuals": "classic", "compose": "classic", "images": "classic",
            "music": "none", "look": "diagram", "sharpen": "none", "motion": "zoom", "edit": "calm", "sfx": "none",
            "footage": "none"}
UPGRADED = {"voice": "chatterbox", "align": "whisperx", "visuals": "manim", "compose": "remotion", "images": "sdxl",
            "music": "acestep", "look": "hud", "sharpen": "esrgan", "motion": "depth", "edit": "lively",
            "sfx": "synth", "footage": "pexels"}
# ACE-Step 1.5 at a fixed commit (its own pyproject pins the CUDA PyTorch for Windows), as a zip: no git needed
ACESTEP_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"
ACESTEP_ZIP = f"https://github.com/ACE-Step/ACE-Step-1.5/archive/{ACESTEP_COMMIT}.zip"
# what each tool needs: its environment's python, the packages, free memory (GB) and a time limit (s)
SPECS = {
    "chatterbox": {"python": "3.11", "pip": ["chatterbox-tts==0.1.7", "setuptools<81"], "mem_gb": 3.0, "timeout": 1500},
    "whisperx": {"python": "3.12", "pip": ["whisperx==3.8.6", "num2words==0.5.14"], "mem_gb": 1.5, "timeout": 600},
    "manim": {"conda": ["python=3.12", "manim"], "mem_gb": 1.0, "timeout": 1200},
    "remotion": {"node": True, "mem_gb": 1.5, "timeout": 1800},
    "sdxl": {"python": "3.12", "pip": ["diffusers==0.32.2", "transformers==4.48.3", "accelerate==1.3.0",
                                       "safetensors==0.5.2", "pillow==11.1.0"],
             "mem_gb": 3.0, "timeout": 1200, "gpu_only": True},
    "acestep": {"python": "3.12", "project": ACESTEP_ZIP, "mem_gb": 4.0, "timeout": 1800, "gpu_only": True,
                "commit_gb": 8.0},   # Windows: loads the full 4.6 GB model into memory before shrinking it
}
GPU_TOOLS = ("chatterbox", "whisperx", "sdxl")  # these run on an NVIDIA card when there is one
MODELS_ON_D = ("sdxl", "acestep")              # their model downloads go to models_dir()
CUDA_BACKEND = "cu126"
TOOL_FOR = {"voice": "chatterbox", "align": "whisperx", "visuals": "manim", "compose": "remotion", "images": "sdxl",
            "music": "acestep"}
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


def _big_drive():
    """D:\\Jarvis on a Windows PC that has a D: drive (the roomy one), else None."""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/Jarvis")
    return None


def models_dir():
    """Where SDXL-Turbo and ACE-Step download their models (None: each library's usual cache)."""
    p = config.general().get("models_dir") or (_big_drive() and _big_drive() / "models")
    return Path(p) if p else None


def outputs_dir(*parts):
    """Where raw AI pictures and music are kept (D:\\Jarvis\\studio\\... on the PC, else the studio folder)."""
    base = config.general().get("outputs_dir") or (_big_drive() and _big_drive() / "studio")
    d = Path(base).joinpath(*parts) if base else data_dir(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def temp_dir():
    """Scratch space for the tools (D:\\Jarvis\\temp on the PC, so big temporary files never fill C:)."""
    big = config.general().get("temp_dir") or (_big_drive() and _big_drive() / "temp")
    if not big:
        return None
    d = Path(big)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def _temp_env(env):
    t = temp_dir()
    if t:
        env.update(TEMP=str(t), TMP=str(t), TMPDIR=str(t))
    return env


def installed(tool):
    if tool == "remotion":
        return (REMOTION / "node_modules" / "@remotion" / "cli").exists() and bool(_node_cmd("npx"))
    return python(tool).exists()


NODE_DIRS = ("/usr/local/bin", "/opt/homebrew/bin", r"C:\Program Files\nodejs")


def _node_tool(name):
    """npm / npx: on PATH, or where the Node.js installer puts them (Windows: npm.cmd, npx.cmd)."""
    found = shutil.which(name)
    if found:
        return found
    for d in NODE_DIRS:
        for n in ((name + ".cmd", name) if os.name == "nt" else (name,)):
            if Path(d, n).exists():
                return str(Path(d, n))
    return None


def _npx():
    return _node_tool("npx")


NODE_VERSION = "v24.22.3"                       # Node.js LTS, same major as the Mac's
NODE_LOCAL = TOOLS / "node"                     # Windows: Jarvis's own copy (no installer, no admin)


def _node_exe():
    """Windows: node.exe from Jarvis's own copy, the Node.js installer's folder, or PATH."""
    for d in (NODE_LOCAL, Path(r"C:\Program Files\nodejs")):
        if (d / "node.exe").exists() and (d / "node_modules" / "npm" / "bin" / "npm-cli.js").exists():
            return d / "node.exe"
    w = shutil.which("node")
    if w and (Path(w).parent / "node_modules" / "npm" / "bin" / "npm-cli.js").exists():
        return Path(w)
    return None


def _node_cmd(name):
    """The command list that runs npm / npx. On Windows it's node.exe + npm's own script, never npm.cmd
    (a .cmd file under "Program Files" breaks on the spaces when Python starts it)."""
    if os.name == "nt":
        node = _node_exe()
        if not node:
            return None
        return [str(node), str(node.parent / "node_modules" / "npm" / "bin" / f"{name}-cli.js")]
    t = _node_tool(name)
    return [t] if t else None


def _node_env(**more):
    env = dict(os.environ, **more)
    if os.name == "nt" and _node_exe():               # packages run "node" themselves
        env["PATH"] = str(_node_exe().parent) + os.pathsep + env.get("PATH", "")
    return env


def _download_node(log=print):
    """Windows: Node.js's official portable zip into tools/node, checked against nodejs.org's SHA-256 list."""
    import hashlib
    import io
    import urllib.request
    import zipfile
    name = f"node-{NODE_VERSION}-win-x64"
    base = f"https://nodejs.org/dist/{NODE_VERSION}/"
    log(f"Downloading Node.js {NODE_VERSION} (free, about 35 MB) for Remotion...")
    data = urllib.request.urlopen(base + name + ".zip", timeout=600).read()
    sums = urllib.request.urlopen(base + "SHASUMS256.txt", timeout=60).read().decode()
    want = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(name + ".zip")), None)
    if not want or hashlib.sha256(data).hexdigest() != want:
        raise EngineError("the Node.js download didn't match its checksum, so it wasn't used")
    tmp = TOOLS / "node-unpacking"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    shutil.rmtree(NODE_LOCAL, ignore_errors=True)
    (tmp / name).rename(NODE_LOCAL)
    shutil.rmtree(tmp, ignore_errors=True)
    return _node_exe()


_GPU = []


def nvidia():
    """{"name", "vram_gb"} for an NVIDIA graphics card with a working driver, else None (always None on a Mac)."""
    if _GPU:
        return _GPU[0]
    gpu = None
    if sys.platform != "darwin":
        smi = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if os.name == "nt" else None)
        if smi and Path(smi).exists():
            try:
                out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout.strip().splitlines()
                if out:
                    name, mib = [x.strip() for x in out[0].split(",")[:2]]
                    gpu = {"name": name, "vram_gb": round(float(mib) / 1024, 1)}
            except Exception:
                gpu = None
    _GPU.append(gpu)
    return gpu


# ---------- memory and the one-heavy-job lock ----------

def _pressure_normal():
    """macOS: True while the system says memory pressure is normal (it compresses and frees memory on
    its own, so "available" pages under-count what a short job can really get)."""
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
        return out == "1"
    except Exception:
        return False


def free_gb():
    """Memory the Mac can hand out right now (free + inactive + purgeable), or None if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / 2 ** 30
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5).stdout
            page = 16384 if "16384" in out.splitlines()[0] else 4096
            n = 0
            for line in out.splitlines():
                if line.startswith(("Pages free", "Pages inactive", "Pages purgeable", "Pages speculative")):
                    n += int(line.split(":")[1].strip().rstrip("."))
            return n * page / 2 ** 30
        except Exception:
            return None
    return None


def commit_free_gb():
    """Windows only: how much more memory programs may reserve right now (RAM + page file, the "commit
    limit"). Loading a big model fails with error 1450 when this runs out, even with RAM free. None elsewhere."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return m.ullAvailPageFile / 2 ** 30
    except Exception:
        return None


def enough_memory(tool):
    """(ok, free GB, needed GB). Under normal macOS memory pressure half the nominal need is enough,
    because macOS compresses idle memory to make room; under warning or critical pressure the full
    amount must be free."""
    need = SPECS[tool]["mem_gb"]
    free = free_gb()
    if free is None:
        return True, None, need
    if _pressure_normal():
        need = need * 0.5
    return free >= need, free, need


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
        while not _try_lock(f):
            if time.time() - t0 > wait:
                raise EngineError(f"another heavy job kept the computer busy, so {what} was skipped")
            time.sleep(2)
        from studio import timings
        waited = time.time() - t0
        if waited >= 2:
            timings.add(timings.current(), "waiting for the graphics card", waited, "wait")
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()} {what} {time.time():.0f}\n")
        f.flush()
        _free_local_brain()
        with timings.step(what, "heavy"):
            yield
    finally:
        _unlock(f)
        f.close()


def _try_lock(f):
    """Exclusive, non-blocking lock on the open lock file. macOS/Linux: flock. Windows: a lock on its
    first byte (msvcrt), which other processes and other open handles in this one respect too."""
    try:
        import fcntl
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    except ImportError:
        pass
    import msvcrt
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock(f):
    try:
        import fcntl  # noqa: F401  (flock is released when the file closes)
        return
    except ImportError:
        pass
    try:
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass


# ---------- running a worker ----------

def _tool_bin():
    """tools/bin with an "ffmpeg" that points at the ffmpeg Jarvis already has (imageio-ffmpeg's), for tools
    that call the ffmpeg command (whisperX loads audio that way). Nothing outside tools/ changes."""
    b = TOOLS / "bin"
    b.mkdir(parents=True, exist_ok=True)
    link = b / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not link.exists() and not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            try:
                link.symlink_to(exe)
            except OSError:
                shutil.copy(exe, link)
        except Exception:
            pass
    return b


def run(tool, worker, payload, timeout=None):
    """Runs studio/workers/<worker>.py inside the tool's environment with a JSON payload; returns its
    JSON answer. Raises EngineError on any problem (the caller falls back)."""
    spec = SPECS[tool]
    timeout = timeout or spec["timeout"]
    if RUNNER:
        return RUNNER(tool, worker, payload, timeout)
    if not installed(tool):
        raise EngineError(f"{tool} isn't installed (run: python -m studio engines install)")
    if spec.get("gpu_only") and not nvidia():
        raise EngineError(f"{tool} needs an NVIDIA graphics card")
    ok, free, need = enough_memory(tool)
    if not ok:
        raise EngineError(f"{tool} needs about {need:.1f} GB of free memory and only {free:.1f} GB is free")
    commit, want = commit_free_gb(), spec.get("commit_gb")
    if want and commit is not None and commit < want:
        raise EngineError(f"{tool} needs about {want:.0f} GB of Windows memory (RAM plus page file) and only "
                          f"{commit:.1f} GB is left; a bigger page file on D: would make room")
    with tempfile.TemporaryDirectory(prefix=f"jv-{tool}-", dir=temp_dir()) as tmp:
        pin, pout = Path(tmp) / "in.json", Path(tmp) / "out.json"
        pin.write_text(json.dumps(payload))
        env = dict(os.environ, PYTHONNOUSERSITE="1", TOKENIZERS_PARALLELISM="false",
                   HF_HUB_DISABLE_TELEMETRY="1", JV_WORKERS=str(WORKERS))
        env.pop("PYTHONPATH", None)
        _temp_env(env)
        env["PATH"] = str(_tool_bin()) + os.pathsep + env.get("PATH", "")
        md = models_dir() if tool in MODELS_ON_D else None
        if md:
            env["HF_HOME"] = str(md / "huggingface")
            env["ACESTEP_CHECKPOINTS_DIR"] = str(md / "acestep")
        if tool == "acestep":
            env["JV_ACESTEP_SRC"] = str(TOOLS / "acestep-src")
        cmd = [str(python(tool)), "-I", str(WORKERS / f"{worker}.py"), str(pin), str(pout)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env, cwd=tmp)
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
    ok, free, need = enough_memory("remotion")
    if not ok:
        raise EngineError(f"Remotion needs about {need:.1f} GB of free memory and only {free:.1f} GB is free")
    pf = Path(public_dir) / "remotion_props.json"
    pf.write_text(json.dumps(props))
    cmd = [*_node_cmd("npx"), "remotion", "render", "src/index.ts", "Short", str(out), f"--props={pf}",
           f"--public-dir={public_dir}", "--concurrency=2", "--codec=h264", "--audio-codec=aac",
           "--log=error", "--overwrite"]
    if os.environ.get("JARVIS_REMOTION_BROWSER"):                 # a browser already on this computer
        cmd.append(f"--browser-executable={os.environ['JARVIS_REMOTION_BROWSER']}")
    env = _temp_env(_node_env(REMOTION_DISABLE_TELEMETRY="1"))
    with _REMOTION_LOCK:                       # one render at a time: the prepared copy has one picture folder
        bundle = remotion_bundle(env) if config.general().get("remotion_prepared", True) else None
        prepared = bool(bundle) and _point_public(bundle, public_dir)
        try:
            p, gpu = _remotion_both(cmd, str(bundle) if prepared else None, out, env, timeout)
            if prepared and (p.returncode != 0 or not Path(out).exists()):
                _BUNDLE_BAD.add(bundle.name)   # the prepared copy didn't work: build it the old way from now on
                prepared = False
                p, gpu = _remotion_both(cmd, None, out, env, timeout)
        finally:
            if bundle:
                _unpoint_public(bundle)
    if p.returncode != 0 or not Path(out).exists():
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
        raise EngineError(f"Remotion failed: {' | '.join(tail)[:300]}")
    return {"path": str(out), "encoder": "nvenc" if gpu else "libx264", "prepared": prepared}


def _remotion_both(cmd, entry, out, env, timeout):
    """(result, gpu used): the render from entry (the prepared copy, or the source when None), on the card's
    encoder first when there is one, then on the processor."""
    c = list(cmd)
    if entry:
        c[c.index("src/index.ts")] = entry
    gpu = remotion_gpu()
    p = _remotion_run(c + (REMOTION_GPU_ARGS if gpu else []), env, timeout)
    if gpu and (p.returncode != 0 or not Path(out).exists()):     # the card's encoder failed: encode on the processor
        from clipper import media
        media.nvenc_failed()
        p = _remotion_run(c, env, timeout)
        gpu = False
    return p, gpu


# ---------- Remotion prepared once per update ----------
# "remotion render src/index.ts" builds the whole project (webpack) and copies the Short's folder into a new temporary
# copy before every render. The build only changes when the composition's code does, so it is made once per update
# (tools/remotion-bundle/<hash of the code>) and each render points the copy's picture folder at the Short's own
# folder (a junction on Windows, a symlink elsewhere) instead of copying it. Same code, same frames. Any problem
# falls back to the old way for that render.

_REMOTION_LOCK = threading.Lock()
_BUNDLE_BAD = set()


def _remotion_code_hash():
    import hashlib
    h = hashlib.sha256()
    files = sorted((REMOTION / "src").rglob("*")) + [REMOTION / n for n in
                                                    ("remotion.config.ts", "package.json", "package-lock.json")]
    for f in files:
        if f.is_file():
            h.update(str(f.relative_to(REMOTION)).replace("\\", "/").encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def remotion_bundle(env=None):
    """The prepared Remotion build for the current code (made now if it's missing), or None."""
    try:
        name = _remotion_code_hash()
    except OSError:
        return None
    if name in _BUNDLE_BAD:
        return None
    base = TOOLS / "remotion-bundle"
    d = base / name
    if (d / "index.html").exists():
        return d
    try:
        base.mkdir(parents=True, exist_ok=True)
        tmp, empty = base / f"{name}.building", base / "empty-public"
        empty.mkdir(exist_ok=True)
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        p = subprocess.run([*_node_cmd("npx"), "remotion", "bundle", "src/index.ts", f"--out-dir={tmp}",
                            f"--public-dir={empty}", "--log=error"], cwd=REMOTION, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600,
                           env=env or _temp_env(_node_env(REMOTION_DISABLE_TELEMETRY="1")))
        if p.returncode != 0 or not (tmp / "index.html").exists():
            raise EngineError((p.stderr or p.stdout or "").strip()[-200:])
        os.replace(tmp, d)
        for old in base.iterdir():                                  # builds for older code
            if old.is_dir() and old.name not in (name, "empty-public") and not old.name.endswith(".building"):
                _unpoint_public(old)
                shutil.rmtree(old, ignore_errors=True)
        return d
    except Exception:
        _BUNDLE_BAD.add(name)
        return None


def _is_link(p):
    p = Path(p)
    try:
        return p.is_symlink() or bool(getattr(os.lstat(p), "st_file_attributes", 0) & 0x400)  # 0x400: a junction
    except OSError:
        return False


def _unpoint_public(bundle):
    """Takes away the link (never the files it points at). Leaves a real folder alone unless it's empty."""
    pub = Path(bundle) / "public"
    try:
        if _is_link(pub):
            if os.name == "nt":
                os.rmdir(pub)                  # removes a junction itself; its target is untouched
            else:
                os.unlink(pub)
        elif pub.is_dir():
            os.rmdir(pub)                      # only succeeds when empty
    except OSError:
        pass


def _point_public(bundle, public_dir):
    """Points the prepared copy's picture folder at this Short's folder. False if it couldn't."""
    pub = Path(bundle) / "public"
    _unpoint_public(bundle)
    if pub.exists():
        return False
    try:
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(pub), str(Path(public_dir).resolve())],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0 and pub.exists()
        os.symlink(str(Path(public_dir).resolve()), str(pub), target_is_directory=True)
        return True
    except OSError:
        return False


# Remotion 4.0.228+ can encode with NVENC on Windows and Linux ("if-possible" probes its own ffmpeg and falls back to
# libx264). It refuses crf with hardware encoding and NVENC's own default bitrate is low, so a fixed 12 Mbit/s is
# given: about what YouTube recommends for 1080p uploads, and plenty for a 1080x1920 Short.
REMOTION_GPU_ARGS = ["--hardware-acceleration=if-possible", "--video-bitrate=12M"]


def remotion_gpu():
    """True when the Remotion render should try the graphics card's encoder (an NVIDIA card that ffmpeg can use)."""
    from clipper import media
    return nvidia() is not None and media.nvenc_ok()


def _remotion_run(cmd, env, timeout):
    try:
        return subprocess.run(cmd, cwd=REMOTION, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout or SPECS["remotion"]["timeout"], env=env)
    except subprocess.TimeoutExpired:
        raise EngineError("Remotion took too long")


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
        if os.name == "nt" and not _node_exe():
            _download_node(log)
        npm, npx = _node_cmd("npm"), _node_cmd("npx")
        if not npm:
            raise EngineError("Node.js isn't installed, so Remotion can't be set up")
        log("Installing Remotion (Node packages, about 300 MB)...")
        subprocess.run([*npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=REMOTION, check=True,
                       env=_node_env())
        log("Downloading Remotion's headless browser...")
        subprocess.run([*npx, "remotion", "browser", "ensure"], cwd=REMOTION, check=False, env=_node_env())
        return
    if spec.get("gpu_only") and not nvidia():
        raise EngineError(f"{tool} needs an NVIDIA graphics card, so it isn't set up on this computer")
    if "project" in spec:
        _install_project(tool, spec, log)
        return
    if "conda" in spec and sys.platform != "darwin":
        spec = {"python": "3.12", "pip": ["manim==0.22.1"]}       # PyPI has Windows/Linux wheels for its drawing libraries
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
    gpu = nvidia() if tool in GPU_TOOLS else None
    log(f"Installing {tool} (Python {spec['python']}, " +
        (f"for the {gpu['name']} graphics card, about 3 GB)..." if gpu else "a few hundred MB)..."))
    subprocess.run([uv, "venv", "-q", "--clear", "--python", spec["python"], str(env_dir(tool))], check=True)
    cmd = [uv, "pip", "install", "-q", "--python", str(python(tool)), *spec["pip"]]
    if gpu:
        # the CUDA build of PyTorch from pytorch.org (PyPI's Windows build is CPU-only). cu126 has both
        # torch 2.6 (Chatterbox) and 2.8 (whisperX) and runs on every NVIDIA card from the GTX 900s up.
        if subprocess.run(cmd + ["--torch-backend", CUDA_BACKEND]).returncode == 0:
            return
        log("That didn't work; updating uv and trying once more...")
        subprocess.run([uv, "self", "update"], capture_output=True)
        if subprocess.run(cmd + ["--torch-backend", CUDA_BACKEND]).returncode == 0:
            return
        log(f"Couldn't get the graphics-card version, so installing the slower processor-only {tool}.")
    subprocess.run(cmd, check=True)


def _install_project(tool, spec, log=print):
    """A tool that is a whole project with its own locked dependencies (ACE-Step): its source zip at a fixed
    commit into tools/<tool>-src, then `uv sync` from its lock file into tools/<tool>."""
    import io
    import urllib.request
    import zipfile
    uv = _uv()
    if not uv:
        raise EngineError("uv isn't installed (it's what Jarvis uses to make Python environments)")
    src = TOOLS / f"{tool}-src"
    log(f"Downloading {tool} (source at a fixed version)...")
    from clipper import youtube
    with urllib.request.urlopen(spec["project"], timeout=120, context=youtube._ctx()) as r:
        data = r.read()
    tmp = TOOLS / f"{tool}-src.new"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    inner = next(p for p in tmp.iterdir() if p.is_dir())
    shutil.rmtree(src, ignore_errors=True)
    inner.rename(src)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"Installing {tool} (Python {spec['python']}, for the graphics card, about 4 GB)...")
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(env_dir(tool)), UV_PYTHON=spec["python"])
    if subprocess.run([uv, "sync", "--frozen", "--no-dev"], cwd=src, env=env).returncode != 0:
        log("The locked install didn't work; trying once more without the lock...")
        subprocess.run([uv, "sync", "--no-dev"], cwd=src, env=env, check=True)


def check(tool):
    """(ok, detail) from a tiny real job in the tool's environment (loads the library, not the models)."""
    if not installed(tool):
        return False, "not installed"
    if tool == "remotion":
        try:
            p = subprocess.run([*_node_cmd("npx"), "remotion", "versions"], cwd=REMOTION,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=_node_env())
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
    out = {"settings": s, "free_memory_gb": None if free_gb() is None else round(free_gb(), 1),
           "gpu": nvidia(), "tools": {}}
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
