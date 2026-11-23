"""Self-check: Jarvis makes sure he still works, especially after an automatic update.

It checks, each in a separate little Python run so a broken part can't take Jarvis down:
  code    every file still compiles and jarvis.py still loads
  parts   each of his modules imports (and their Python packages are installed)
  voice   he can still make speech (with the same voice as before, and how quickly)
  brain   Claude Code still answers
  mic     the microphone still opens
  speaker there's still a speaker to talk through
  display the HUD page and its 3D engine are there and the display's web server serves them
  clipper the Shorts clipper's modules and video tools (OpenCV, FFmpeg, yt-dlp) load

updater.py runs it before and after installing an update (before restarting) and again when
he starts up on the new version. Anything that worked before the update but not after is a
"regression": first he tries to fix it (reinstalling Python packages), and if that doesn't
help he undoes the update. Say "run a self check" to hear the results any time."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP = {"mac", "menubar", "inbox"} if sys.platform == "win32" else {"win", "menubar_win", "wincal"}
NAMES = {"code": "my code", "parts": "some of my parts", "voice": "my voice", "brain": "my brain",
         "mic": "the microphone", "speaker": "the speaker", "display": "my display",
         "clipper": "my Shorts clipper", "studio": "my YouTube business module"}


def _py(code, timeout):
    """Runs a bit of Python with this folder's code; returns its JSON answer or raises."""
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    r = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                       env=dict(os.environ, JARVIS_CHECK="1", PYTHONUTF8="1"), **kw)
    for line in reversed(r.stdout.strip().splitlines()):
        if line.startswith("@@"):
            return json.loads(line[2:])
    lines = (r.stderr or r.stdout).strip().splitlines()
    raise RuntimeError(lines[-1][-300:] if lines else f"exit code {r.returncode}")      # the error itself


def modules():
    """Jarvis's library modules (setup and helper scripts are left out)."""
    out = []
    for f in sorted(HERE.glob("*.py")):
        name = f.stem
        if name in SKIP or name.startswith(("setup_", "choose_", "test")) or name == "jarvis":
            continue
        out.append(name)
    return out


def _parts():
    names = modules()
    got = _py("import importlib, json\nres = {}\n"
              f"for m in {names!r}:\n"
              "    try:\n        importlib.import_module(m); res[m] = ''\n"
              "    except BaseException as e:\n        res[m] = f'{type(e).__name__}: {e}'[:200]\n"
              "print('@@' + json.dumps(res))", 120)
    bad = {m: e for m, e in got.items() if e}
    return not bad, ("; ".join(f"{m}: {e}" for m, e in bad.items()) or f"{len(got)} modules load"), sorted(bad)


def _code():
    import selfedit
    problem = selfedit._check()
    return problem is None, problem or "all files load", []


def _voice():
    got = _py("import json, time, types, sys\n"
              "sys.modules.setdefault('display', types.SimpleNamespace(log=lambda *a: None, update=lambda **k: None))\n"
              "import voice\nt = time.time()\n"
              "a = (getattr(voice, 'synthesize_audio', None) or voice.synthesize)('Systems check.')\n"
              "d = time.time() - t\nt = time.time()\n"
              "b = (getattr(voice, 'synthesize_audio', None) or voice.synthesize)('All systems are working nicely.')\n"
              "e = time.time() - t\nok = bool(getattr(a, 'seconds', lambda: 1)())\n"
              "voice._remove(a) if hasattr(voice, '_remove') else None\nvoice._remove(b) if hasattr(voice, '_remove') else None\n"
              "print('@@' + json.dumps([ok, getattr(voice, 'LAST_ENGINE', ''), round(d, 2), round(e, 2)]))", 150)
    ok, engine, load, speed = got
    return ok, f"{engine or 'voice'} works, a short line takes {speed:.1f}s to make", [engine]


def _brain():
    ok, detail = _py("import json, brain\nprint('@@' + json.dumps(list(brain.selfcheck())))", 150)
    return ok, detail, []


def _mic():
    ok, detail = _py("import json\n"
                     "try:\n    import sounddevice as sd, numpy as np\n    s = sd.InputStream(samplerate=16000, channels=1, blocksize=640, dtype='int16'); s.start()\n"
                     "    b = np.concatenate([s.read(640)[0] for _ in range(10)]); s.close()\n"
                     "    print('@@' + json.dumps([True, f'opens (level {int(np.abs(b).max())})']))\n"
                     "except Exception as e:\n    print('@@' + json.dumps([False, str(e)[:200]]))", 30)
    return ok, detail, []


def _speaker():
    ok, detail = _py("import json\n"
                     "try:\n    import sounddevice as sd\n    d = sd.query_devices(kind='output'); print('@@' + json.dumps([True, d['name']]))\n"
                     "except Exception as e:\n    print('@@' + json.dumps([False, str(e)[:200]]))", 30)
    return ok, detail, []


def _display():
    ok, detail = _py("import json, re, threading, urllib.request\n"
                     "from http.server import ThreadingHTTPServer\n"
                     "import display\n"
                     "srv = ThreadingHTTPServer(('127.0.0.1', 0), display._Handler)\n"
                     "threading.Thread(target=srv.serve_forever, daemon=True).start()\n"
                     "base = f'http://127.0.0.1:{srv.server_address[1]}'\n"
                     "page = urllib.request.urlopen(base + '/', timeout=10).read().decode('utf-8', 'replace')\n"
                     "state = json.load(urllib.request.urlopen(base + '/state', timeout=10))\n"
                     "libs = sorted(set(re.findall(r'/vendor/([\\w.-]+[.]js)', page)))\n"
                     "sizes = [len(urllib.request.urlopen(base + '/vendor/' + n, timeout=10).read()) for n in libs]\n"
                     "srv.shutdown()\n"
                     "good = '<canvas id=\"gl\"' in page and 'status' in state and all(n > 1000 for n in sizes)\n"
                     "print('@@' + json.dumps([good, f'page and {len(libs)} 3D engine file(s) served' if good else 'the page or its 3D engine is broken']))", 60)
    return ok, detail, []


def _clipper():
    ok, detail = _py("import json\n"
                     "try:\n"
                     "    import cv2, PIL, yt_dlp\n"
                     "    from clipper import media, pipeline, jobs, youtube, api, intents\n"
                     "    media.ffmpeg()\n"
                     "    from clipper import acquire, selftune\n"
                     "    acquire.ffmpeg_on_path()\n"
                     "    from yt_dlp.downloader.external import FFmpegFD\n"
                     "    assert FFmpegFD.available(), 'yt-dlp cannot find ffmpeg to cut clip sections'\n"
                     "    print('@@' + json.dumps([True, 'video tools and modules load, yt-dlp can cut sections']))\n"
                     "except Exception as e:\n"
                     "    print('@@' + json.dumps([False, f'{type(e).__name__}: {e}'[:200]]))", 90)
    return ok, detail, []


def _studio():
    ok, detail = _py("import json, os, tempfile\n"
                     "try:\n"
                     "    os.environ['JARVIS_STUDIO_DATA'] = tempfile.mkdtemp()\n"
                     "    from studio import config, db, guard, health, jobs, status, narration, toolchain, claude, ytpublic, niche, ytauth, branding, setup_channel, topics, script, pipeline, video, qa, publish, analytics, eligibility, autopilot\n"
                     "    db.migrate(db.conn())\n"
                     "    guard.register_built_ins()\n"
                     "    status.overview()\n"
                     "    print('@@' + json.dumps([True, 'YouTube business module loads, database and guard work']))\n"
                     "except Exception as e:\n"
                     "    print('@@' + json.dumps([False, f'{type(e).__name__}: {e}'[:200]]))", 60)
    return ok, detail, []


CHECKS = {"code": _code, "parts": _parts, "voice": _voice, "brain": _brain, "mic": _mic, "speaker": _speaker, "display": _display, "clipper": _clipper, "studio": _studio}


def run(skip=(), only=None):
    """Runs every check (or just `only`). Returns {name: {"ok": bool, "detail": str, "extra": [...]}}."""
    results, start = {}, time.time()
    for name, check in CHECKS.items():
        if name in skip or (only is not None and name not in only):
            continue
        try:
            ok, detail, extra = check()
        except subprocess.TimeoutExpired:
            ok, detail, extra = False, "took too long", []
        except Exception as e:
            ok, detail, extra = False, str(e)[-300:], []
        results[name] = {"ok": bool(ok), "detail": detail, "extra": extra}
        print(f"(self-check {name}: {'ok' if ok else 'PROBLEM'}, {detail})")
    print(f"(self-check took {time.time() - start:.0f}s)")
    return results


def regressions(before, after):
    """What worked before but doesn't now (including a module that stopped loading, or the
    voice falling back to a worse engine)."""
    out = []
    for name, now in after.items():
        was = (before or {}).get(name)
        if not was:
            continue
        if was["ok"] and not now["ok"]:
            out.append(name)
        elif name == "parts" and set(now["extra"]) - set(was["extra"]):
            out.append(name)
        elif name == "voice" and now["ok"] and was["extra"] and was["extra"][0] and now["extra"] != was["extra"]:
            out.append(name)
    return out


def timed_out(results, names):
    """The checks among `names` that failed only because they took too long (often the
    computer being busy, or another app holding the mic for a moment), not a real breakage."""
    return [n for n in names if results.get(n, {}).get("detail") == "took too long"]


def missing_packages(results, names):
    """True if one of these checks failed because a Python package isn't installed."""
    return any("No module named" in results[n]["detail"] or "ModuleNotFoundError" in results[n]["detail"]
               for n in names)


def describe(results):
    """A short spoken summary."""
    bad = [n for n, r in results.items() if not r["ok"]]
    if not bad:
        return "Everything checks out: my code, voice, brain, microphone, speaker and display are all working."
    parts = [f"{NAMES.get(n, n)} {'are' if n == 'parts' else 'is'}n't working "
             f"({', '.join(results[n]['extra']) if n == 'parts' else results[n]['detail'][:90]})" for n in bad]
    good = [NAMES.get(n, n) for n in results if n not in bad]
    return ("I found a problem: " + "; ".join(parts) + "."
            + (f" Everything else is fine: {', '.join(good)}." if good else ""))


if __name__ == "__main__":
    print(describe(run()))
