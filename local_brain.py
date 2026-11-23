"""Local brain: a small AI model that runs on this computer through Ollama, so easy things don't use
Connor's Claude plan at all.

What it does (and nothing else):
  - small talk, thanks, reactions and half-heard background speech: a quick reply from the local model
    instead of a whole Claude call (jarvis.py asks local_reply() first)
  - mechanical studio jobs: fixing broken JSON, and making a script fit the word count
    (studio/claude.py and studio/script.py), each checked before it's used
  - first drafts of Shorts scripts, as a measured trial: Claude still fact-checks every one, and the
    trial switches itself off if local drafts pass less often than Claude's (studio/script.py)

Claude still does everything that needs real knowledge, tools, web search, the fact-check and the final
word. If Ollama isn't installed, isn't running, is slow, or the Mac is short on memory (or a heavy studio
job is running), every one of these quietly goes to Claude exactly as before.

Setup: double-click "Install Local Brain" (or run `python local_brain.py install`). It installs Ollama
(free) and the model (qwen2.5:3b, about 2 GB) and Chroma for the answer memory (vecstore.py).
Settings in config.json "local_brain": {"enabled": false} turns it all off; "model", "chat",
"studio_drafts" ("trial", "on" or "off") and "max_memory" (0-1) change the rest.

`python local_brain.py stats` shows how many Claude calls and tokens it has saved."""
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
DATA = Path(os.environ.get("JARVIS_LOCAL_BRAIN_DATA") or HERE / "local_brain_data")
STATS = DATA / "stats.json"

DEFAULTS = {
    "enabled": True,             # only does anything once Ollama and the model are installed
    "url": "http://127.0.0.1:11434",
    "model": "qwen2.5:3b",
    "chat": True,                # small talk answered locally
    "studio_json": True,         # broken JSON fixed locally
    "studio_length": True,       # script word count fixed locally
    "studio_drafts": "trial",    # first drafts: "trial" (measured, switches itself off), "on" or "off"
    "answer_cache": True,        # vecstore.py: repeat questions answered from memory
    "max_memory": 0.85,          # skip the local model when more than this share of memory is in use
    "min_free_gb": 2.5,          # ... or when less than this much memory is free (and it isn't loaded yet)
    "keep_minutes": 10,          # how long Ollama keeps the model loaded after a use
}
# What one avoided Claude call is worth before Jarvis has measured his own (measured in the cloud
# 2026-11-23 with the lean Claude Code options: tokens Claude reads + writes for one call).
TYPICAL = {"brain": 11300, "studio_json": 6400, "studio_length": 7000, "studio_draft": 11000}

_lock = threading.Lock()
_state = {"up": None, "checked": 0.0, "models": [], "loaded_at": 0.0, "why": ""}
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------- settings ----------------

def cfg():
    out = dict(DEFAULTS)
    try:
        c = json.loads(CONFIG.read_text())
        mine = c.get("local_brain")
        if isinstance(mine, dict):
            out.update(mine)
        elif mine is False:
            out["enabled"] = False
    except Exception:
        pass
    return out


def enabled(part=None):
    if os.environ.get("JARVIS_NO_LOCAL_BRAIN"):
        return False
    c = cfg()
    if not c.get("enabled", True):
        return False
    return True if part is None else c.get(part, True) not in (False, "off")


# ---------------- talking to Ollama ----------------

def _http(path, body=None, timeout=5.0):
    url = cfg()["url"].rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    return _NO_PROXY.open(req, timeout=timeout)


def _refresh(force=False):
    """Is Ollama running, and which models does it have? (checked at most every 30 s)"""
    if not force and time.time() - _state["checked"] < 30:
        return _state["up"]
    try:
        with _http("/api/tags", timeout=1.5) as r:
            models = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        _state.update(up=True, models=models)
    except Exception:
        _state.update(up=False, models=[])
    _state["checked"] = time.time()
    return _state["up"]


def has_model(name=None):
    name = name or cfg()["model"]
    want = name if ":" in name else name + ":latest"
    return any(m == want or m == name for m in _state["models"])


def _memory():
    """(share of memory in use or None, free GB or None)."""
    used = free = None
    try:
        from studio.health import memory_pressure
        used = memory_pressure()
    except Exception:
        pass
    try:
        from studio.engines import free_gb
        free = free_gb()
    except Exception:
        pass
    return used, free


def heavy_running():
    """True while a heavy studio job (Chatterbox, whisperX, Manim, Remotion, a render) holds its lock."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        from studio import home
        path = home() / "heavy.lock"
        if not path.exists():
            return False
        if fcntl is None:                        # Windows: the studio locks the file's first byte
            import msvcrt
            with open(path, "a+") as f:
                f.seek(0)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                return False
        with open(path, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except Exception:
        return False


def _loaded_recently():
    return time.time() - _state["loaded_at"] < 60 * float(cfg().get("keep_minutes", 10))


def ready(part=None):
    """(ok, why not). Cheap enough to call before every use."""
    if not enabled(part):
        return False, "turned off"
    if not _refresh():
        return False, "Ollama isn't running"
    if not has_model():
        return False, f"the {cfg()['model']} model isn't downloaded"
    if heavy_running():
        return False, "a heavy studio job is using the memory"
    used, free = _memory()
    c = cfg()
    if used is not None and used > float(c["max_memory"]):
        return False, f"memory is {int(used * 100)}% used"
    if free is not None and free < float(c["min_free_gb"]) and not _loaded_recently():
        return False, f"only {free:.1f} GB of memory is free"
    return True, ""


def chat(messages, json_mode=False, max_tokens=300, timeout=30.0, first_timeout=8.0, num_ctx=4096, temperature=0.6):
    """The local model's answer as text, or None if it isn't available, too slow or failed.
    first_timeout: give up if nothing comes back this quickly (a cold or overloaded model)."""
    c = cfg()
    body = {"model": c["model"], "messages": messages, "stream": True,
            "keep_alive": f"{int(c.get('keep_minutes', 10))}m",
            "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": temperature}}
    if json_mode:
        body["format"] = "json"
    start, parts, done = time.time(), [], {}
    try:
        with _http("/api/chat", body, timeout=first_timeout) as r:
            for line in r:
                if time.time() - start > timeout:
                    print(f"(local brain: too slow, over {timeout:.0f}s)")
                    return None
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("error"):
                    print(f"(local brain error: {str(d['error'])[:200]})")
                    return None
                parts.append((d.get("message") or {}).get("content", ""))
                if d.get("done"):
                    done = d
                    break
    except Exception as e:
        print(f"(local brain unavailable: {type(e).__name__}: {str(e)[:120]})")
        _state["checked"] = 0.0                    # look again next time
        return None
    _state["loaded_at"] = time.time()
    text = "".join(parts).strip()
    _count("local", tokens=int(done.get("prompt_eval_count") or 0) + int(done.get("eval_count") or 0),
           seconds=time.time() - start)
    return text or None


def chat_json(messages, **kw):
    """chat() parsed as JSON (a dict), or None."""
    text = chat(messages, json_mode=True, **kw)
    if not text:
        return None
    try:
        out = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except ValueError:
            out = None
    return out if isinstance(out, dict) else None


def unload():
    """Frees the model's memory now (before heavy studio work). Harmless if it isn't loaded."""
    if not enabled() or not _state["up"]:
        return
    try:
        with _http("/api/generate", {"model": cfg()["model"], "keep_alive": 0}, timeout=3) as r:
            r.read()
        _state["loaded_at"] = 0.0
    except Exception:
        pass


def warm():
    """Loads the model in the background so the first quick reply isn't slow."""
    def go():
        time.sleep(20)                             # let Jarvis finish starting up first
        if ready("chat")[0]:
            chat([{"role": "user", "content": "hi"}], max_tokens=1, timeout=60, first_timeout=60)
    threading.Thread(target=go, daemon=True, name="local-brain-warm").start()


# ---------------- counting what it saves ----------------

def _load_stats():
    try:
        return json.loads(STATS.read_text())
    except Exception:
        return {}


def _save_stats(d):
    try:
        DATA.mkdir(exist_ok=True)
        tmp = STATS.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(STATS)
    except Exception:
        pass


def _count(key, n=1, tokens=0, seconds=0.0):
    with _lock:
        d = _load_stats()
        day = time.strftime("%Y-%m-%d")
        for bucket in (d.setdefault("total", {}), d.setdefault("days", {}).setdefault(day, {})):
            e = bucket.setdefault(key, {"n": 0, "tokens": 0, "seconds": 0.0})
            e["n"] += n
            e["tokens"] += int(tokens)
            e["seconds"] = round(e["seconds"] + seconds, 1)
        days = d["days"]
        for old in sorted(days)[:-30]:
            del days[old]
        _save_stats(d)


def claude_used(kind, tokens):
    """Records the real size of a Claude call (input + cache + output tokens), so savings use his own numbers."""
    if tokens > 0:
        _count("claude_" + kind, tokens=tokens)


def typical(kind):
    """What one Claude call of this kind costs on this computer (its average so far, else the cloud measurement)."""
    e = (_load_stats().get("total") or {}).get("claude_" + kind)
    if e and e.get("n", 0) >= 5:
        return int(e["tokens"] / e["n"])
    return TYPICAL.get(kind, 8000)


def saved(kind, calls=1):
    """A Claude call that didn't have to happen."""
    _count("saved_" + kind, n=calls, tokens=typical(kind) * calls)


def summary(days=None):
    """{"calls": n, "tokens": n, "by": {kind: calls}} saved in total (or over the last few days)."""
    d = _load_stats()
    buckets = [d.get("total") or {}] if not days else \
        [v for k, v in sorted((d.get("days") or {}).items())[-days:]]
    out = {"calls": 0, "tokens": 0, "by": {}, "local_calls": 0}
    for b in buckets:
        for k, e in b.items():
            if k.startswith("saved_"):
                out["calls"] += e["n"]
                out["tokens"] += e["tokens"]
                out["by"][k[6:]] = out["by"].get(k[6:], 0) + e["n"]
            elif k == "local":
                out["local_calls"] += e["n"]
    return out


NAMES = {"brain": "small-talk replies", "cache": "repeat questions", "studio_json": "JSON fixes",
         "studio_length": "script length fixes", "studio_draft": "first drafts"}


def report():
    """A spoken summary: 'how many tokens have you saved'."""
    ok, why = ready()
    s, today = summary(), summary(1)
    if not s["calls"]:
        if not ok:
            return (f"My local brain isn't working yet: {why}. Double-click Install Local Brain in my folder "
                    "and it'll start saving Claude tokens.")
        return "My local brain is ready, but it hasn't saved any Claude calls yet."
    parts = ", ".join(f"{n} {NAMES.get(k, k)}" for k, n in sorted(s["by"].items(), key=lambda x: -x[1]))
    words = (f"My local brain has saved {s['calls']} Claude calls so far, about {_round(s['tokens'])} tokens: {parts}. "
             f"Today it's {today['calls']} calls, about {_round(today['tokens'])} tokens.")
    if not ok:
        words += f" It's paused right now because {why}."
    return words


def _round(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} million"
    if n >= 1000:
        return f"{round(n / 1000)} thousand"
    return str(n)


# ---------------- small talk ----------------

CHAT_SYSTEM = """You are the quick-reply part of Jarvis, a voice assistant. The user's words come from speech
recognition. Decide whether you can reply yourself, or whether Jarvis's main brain must answer.

Reply yourself ("me") ONLY for: greetings, thanks, goodbyes, simple small talk (how are you, what's up),
reactions and banter (that's crazy, nice, lol), and speech that clearly wasn't meant for you (background
talk, song lyrics, noises, a half sentence). For background talk, a short friendly line is enough.

Everything else goes to the main brain ("brain"): any question that needs facts, numbers, knowledge, advice
or an opinion; anything about the user's projects, apps, stores, YouTube, the team, the clipper, files,
updates, settings or errors; any request to do something; any answer to a question Jarvis just asked; and
anything you are not sure about.

When you reply yourself: one or two short spoken sentences in Jarvis's voice. No facts, no numbers, no
lists, no links, no promises, and never say you did or will do something.

JSON only: {"handle": "me" or "brain", "reply": "what to say, only if handle is me"}"""

SOCIAL = re.compile(r"\b(?:how are you|how(?:'s| is) it going|how you doing|how are you doing|what'?s up|wh?at'?s good|"
                    r"wassup|sup|you good|how have you been|how(?:'s| was) your day|what are you up to|"
                    r"good (?:morning|night|evening|afternoon)|hello|hi|hey|yo|thanks|thank you|bye|goodbye|"
                    r"see you|later|love you|you're the best|nice|cool|wow|lol|haha)\b")
NEEDS_BRAIN = re.compile(
    r"\b(?:who|what|when|where|why|which|how|explain|tell me|define|meaning|mean|means|should|could|would|"
    r"can|does|do you know|is it|are there|is there|price|cost|weather|news|score|calculate|math|"
    r"store ?forge|store|team|clip|clipper|studio|youtube|shorts?|video|update|upgrade|report|preview|"
    r"stream|twitch|discord|spotify|obsidian|calendar|remind|timer|alarm|e-?mail|mail|file|folder|code|"
    r"error|bug|fix|install|token|claude|job|project|plan|money|business|channel|script|setting|"
    r"remember|forget|note|message|text|call|search|google|look up|find|show|open|play|set|make|build|"
    r"write|send|start|stop|check|read|turn|run|help|need|want|please|say|tell|close|closed|quit|restart|"
    r"forge|permission|rights|photos?|box|checkbox|checked|go through|go ahead|do it|confirm\w*|safari|terminal|ui|optimi[sz]e|blinker|"
    r"finished|stages?|generat\w*|fail\w*|respond\w*|certificat\w*|app|apps|screen|display)\b")
NUMBERS = re.compile(r"\d")
ACTED = re.compile(r"\b(?:i'?ve|i have|i just|done|opened|opening|playing|set|added|scheduled|sent|started|"
                   r"created|booked|reminded|saved|turned)\b", re.I)


def worth_asking(text, last_reply="", last_age=1e9):
    """Cheap first check, so real questions go straight to Claude with no extra wait."""
    t = re.sub(r"\s+", " ", (text or "").lower()).strip(" .!?,")
    words = t.split()
    if not words or len(words) > 14:
        return False
    if NUMBERS.search(t):
        return False
    if last_reply.strip().endswith("?") and last_age < 180:
        return False                                 # he asked something: the answer needs context
    if len(last_reply) > 150 and last_age < 90:
        return False                                 # in the middle of a real conversation: Claude has the thread
    rest = SOCIAL.sub(" ", t)
    return not NEEDS_BRAIN.search(rest)


def local_reply(text, history=(), persona="", last_age=1e9):
    """A reply from the local model if this is small talk it can handle, else None (Claude answers).
    history: [{"user", "jarvis"}] oldest first."""
    last = history[-1]["jarvis"] if history else ""
    if not worth_asking(text, last, last_age):
        return None
    if not ready("chat")[0]:
        return None
    past = "\n".join(f"User: {h['user'][:200]}\nJarvis: {h['jarvis'][:200]}" for h in list(history)[-3:])
    msgs = [{"role": "system", "content": (persona + "\n\n" if persona else "") + CHAT_SYSTEM},
            {"role": "user", "content": (f"Recent conversation:\n{past}\n\n" if past else "") + f"The user just said: {text}"}]
    t0 = time.time()
    ans = chat_json(msgs, max_tokens=90, timeout=6.0, first_timeout=4.0)
    if not ans or str(ans.get("handle", "")).lower() != "me":
        return None
    reply = re.sub(r"\s+", " ", str(ans.get("reply") or "")).strip().strip('"')
    if not reply or len(reply.split()) > 35 or NUMBERS.search(reply) or "http" in reply or ACTED.search(reply) \
            or "{" in reply or reply.lower().startswith(("as an ai", "i'm sorry, but")):
        return None
    print(f"(local brain answered in {time.time() - t0:.1f}s, no Claude call)")
    saved("brain")
    return reply


# ---------------- installing ----------------

MAC_ZIP = "https://ollama.com/download/Ollama-darwin.zip"
WIN_EXE = "https://ollama.com/download/OllamaSetup.exe"


def _ollama_installed():
    if sys.platform == "darwin":
        return any(Path(p).exists() for p in ("/Applications/Ollama.app", os.path.expanduser("~/Applications/Ollama.app")))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local, "Programs", "Ollama", "ollama.exe").exists()
    import shutil
    return bool(shutil.which("ollama"))


def _download(url, dest):
    print(f"Downloading {url.rsplit('/', 1)[-1]} ...")
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        total, got, last = int(r.headers.get("Content-Length") or 0), 0, 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and got - last > total / 10:
                last = got
                print(f"  {int(got * 100 / total)}%")


def _install_ollama():
    import subprocess
    import tempfile
    if _ollama_installed():
        print("Ollama is already installed.")
        return True
    tmp = Path(tempfile.mkdtemp())
    if sys.platform == "darwin":
        zipped = tmp / "Ollama-darwin.zip"
        _download(MAC_ZIP, zipped)
        dest = "/Applications" if os.access("/Applications", os.W_OK) else os.path.expanduser("~/Applications")
        os.makedirs(dest, exist_ok=True)
        subprocess.run(["ditto", "-x", "-k", str(zipped), dest], check=True)
        print(f"Installed Ollama in {dest}.")
        return True
    if os.name == "nt":
        exe = tmp / "OllamaSetup.exe"
        _download(WIN_EXE, exe)
        subprocess.run([str(exe), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], check=False, timeout=900)
        return _ollama_installed()
    print("On Linux, install Ollama with: curl -fsSL https://ollama.com/install.sh | sh")
    return False


def _start_ollama():
    import subprocess
    if _refresh(force=True):
        return True
    if sys.platform == "darwin":
        app = "/Applications/Ollama.app" if Path("/Applications/Ollama.app").exists() else \
            os.path.expanduser("~/Applications/Ollama.app")
        subprocess.run(["open", "-g", app], check=False)
    elif os.name == "nt":
        exe = Path(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama app.exe")
        if exe.exists():
            subprocess.Popen([str(exe)], close_fds=True)
    for _ in range(60):
        time.sleep(1)
        if _refresh(force=True):
            return True
    return False


def _pull(model):
    print(f"Downloading the {model} model (about 2 GB, one time only) ...")
    last = ""
    with _http("/api/pull", {"model": model, "stream": True}, timeout=3600) as r:
        for line in r:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("error"):
                raise RuntimeError(d["error"])
            status = d.get("status", "")
            if d.get("total") and d.get("completed"):
                pct = int(d["completed"] * 100 / d["total"])
                status = f"{status} {pct - pct % 10}%"
            if status != last:
                print("  " + status)
                last = status
    _refresh(force=True)
    return has_model(model)


def _install_chroma():
    import subprocess
    try:
        import chromadb  # noqa: F401
        print("Chroma is already installed.")
        return True
    except Exception:
        pass
    import shutil
    print("Installing Chroma (the answer memory) ...")
    home = Path.home()
    uv = shutil.which("uv") or next((str(p) for p in (home / ".local/bin/uv", home / ".local/bin/uv.exe",
                                                       home / ".cargo/bin/uv") if p.exists()), None)
    spec = "chromadb>=1.0,<2"
    cmd = [uv, "pip", "install", "--python", sys.executable, spec] if uv else \
        [sys.executable, "-m", "pip", "install", "-q", spec]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
    if r.returncode != 0:
        print("  Chroma didn't install, so Jarvis uses his built-in answer memory instead (works the same, a bit simpler).")
        print("  " + (r.stderr or r.stdout).strip()[-300:])
        return False
    return True


def install():
    import shutil
    free = shutil.disk_usage(str(HERE)).free / 2 ** 30
    print(f"Free disk: {free:.1f} GB")
    if free < 5:
        print("There isn't enough free disk for the local brain (it needs about 3 GB plus room to spare). "
              "Free some space and run this again.")
        return 1
    if heavy_running():
        print("A heavy studio job is running right now. Waiting for it to finish ...")
        while heavy_running():
            time.sleep(10)
    if not _install_ollama():
        print("Couldn't install Ollama. You can get it free from https://ollama.com/download and run this again.")
        return 1
    if not _start_ollama():
        print("Ollama is installed but didn't start. Open the Ollama app once, then run this again.")
        return 1
    model = cfg()["model"]
    if not has_model(model) and not _pull(model):
        print("The model didn't download. Check the internet connection and run this again.")
        return 1
    _install_chroma()
    print("Testing the local brain ...")
    t0 = time.time()
    said = chat([{"role": "user", "content": "Say hi in three words."}], max_tokens=20, timeout=120, first_timeout=120)
    print(f"  It said: {said!r} ({time.time() - t0:.1f}s)")
    if not said:
        print("The local brain didn't answer. Jarvis will keep using Claude for everything until it does.")
        return 1
    print("\nAll set. Jarvis now handles small talk and simple studio jobs on this computer, and still uses "
          "Claude for everything that needs real knowledge. Say 'how many tokens have you saved' any time.")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "install":
        return install()
    if cmd == "stats":
        print(json.dumps({"total": summary(), "today": summary(1)}, indent=1))
        print(report())
        return 0
    ok, why = ready()
    print("Local brain:", "ready" if ok else f"not ready ({why})", f"- model {cfg()['model']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
