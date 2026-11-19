"""Self-repair for the AI team. When an agent hits an error (a StoreForge tool fails, StoreForge won't
start, an agent comes back with nothing, or the whole mission crashes), Jarvis sends in a mechanic:
Claude Code (Connor's plan, no API key) reads the error and the code, fixes the cause, and the task
is tried again. He says out loud what broke and what he did.

Safety:
- Fixes to Jarvis's own code go through the same snapshot / self-check / rollback as a voice edit,
  and are committed as a voice edit, so "undo that" reverses them.
- In the StoreForge folder the mechanic can read, and run only a few setup commands (npm install,
  npm rebuild, prisma generate). It can't edit StoreForge's code, touch Shopify or delete anything.
- Things only Connor can do (confirming photo rights, approving a push) are never "fixed": the team
  stops retrying them and tells him instead.
- At most 2 repairs per mission, and an error that a repair didn't fix isn't tried again for a day.
"""
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "repairs.json"
CONFIG = HERE / "config.json"
PER_MISSION = 2

# Waiting on the owner: nothing to repair, just ask him.
OWNER = re.compile(r"hasn'?t confirmed|confirm(?:ed)? (?:the |that you have the )?rights|request_rights_confirmation|"
                   r"needs? (?:your|the owner'?s|connor'?s) (?:ok|okay|approval|yes)|waiting (?:for|on) (?:the owner|you)",
                   re.I)
# Not a bug: Claude plan limits and outages. Wait and tell him.
LIMIT = re.compile(r"usage limit|rate.?limit|overloaded|too many requests|limit will reset|529|credit balance", re.I)
# Ordinary web noise and tools the agent isn't meant to have.
NOISE_TOOLS = re.compile(r"^(WebFetch|WebSearch|Bash|Read|Glob|Grep|Edit|Write|Task|NotebookEdit)$")

PROMPT = """You are Jarvis's mechanic. Jarvis is a Python voice assistant in this folder; his AI team runs
missions with Claude Code agents (workforce.py). Store missions use StoreForge, Connor's store-builder
app ({sf}), through storeforge.py (the client) and storeforge_mcp.py (the agents' tools).

During the mission "{goal}", the {role} hit this error:
{error}

What the agent was doing: {task}
{extra}
Find the real cause and fix it so this can't happen again.
- You may edit Jarvis's code in this folder (smallest change that fixes the cause, in the existing style,
  Python 3.9+, macOS and Windows). Jarvis checks it and rolls back if it breaks.
- In the StoreForge folder you may read files and run only: npm install, npm rebuild, npm run build,
  npx prisma generate. Don't edit StoreForge's files.
- Never touch .venv, models/, memory.json, config.json, tokens, .git, Shopify, or anything to do with
  money. Never delete files.
- If the cause is something only Connor can do (confirm photo rights, approve something, log in, pay),
  or it's a website blocking us, change nothing and say so.

End with ONLY this JSON on the last line:
{{"cause": "<the cause, short>", "fixed": true or false, "retry": true if trying the task again now could work,
"owner_needs": "<what Connor must do, or empty>", "say": "<one or two plain spoken sentences for Connor: what
broke and what you did about it>"}}"""

_lock = threading.Lock()
_say = []
_restart = threading.Event()


def _cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def enabled():
    return _cfg().get("self_repair", True) is not False


def set_enabled(on):
    c = _cfg()
    c["self_repair"] = bool(on)
    CONFIG.write_text(json.dumps(c, indent=2), encoding="utf-8")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"repairs": []}


def _save(d):
    d["repairs"] = d["repairs"][-100:]
    FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _sig(role, error):
    """Same error, ignoring ids, numbers and paths."""
    e = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}|\d+|/[^\s'\"]+", "#", error.lower())[:300]
    return hashlib.sha1(f"{role}|{e}".encode()).hexdigest()[:12]


def kind(tool, error):
    """'owner' (only Connor can fix it), 'limit' (plan limit / outage), 'noise' (normal, ignore) or 'bug'."""
    if OWNER.search(error or ""):
        return "owner"
    if LIMIT.search(error or ""):
        return "limit"
    if tool and NOISE_TOOLS.match(tool):
        return "noise"
    if re.search(r"no such tool available|not allowed|permission to use|requires approval", error or "", re.I):
        return "noise"
    return "bug"


def say(text, first=False):
    """Queue something for Jarvis to say out loud (speak_up_loop picks it up)."""
    with _lock:
        _say.insert(0, text) if first else _say.append(text)


def pop_say():
    with _lock:
        return _say.pop(0) if _say else None


def needs_restart():
    return _restart.is_set()


def restarted():
    _restart.clear()


def _parse(text):
    for m in reversed(list(re.finditer(r"\{[^{}]*\"fixed\"[^{}]*\}", text or "", re.S))):
        try:
            return json.loads(m.group(0))
        except ValueError:
            continue
    return None


def fix(goal, role, task, error, extra="", mission=None):
    """Diagnose and fix one error. Returns a dict: fixed, retry, owner_needs, say, changed (files), or
    None when there's nothing to do (repairs off, already tried, budget used)."""
    import brain
    import selfedit
    if not enabled():
        return None
    sig = _sig(role, error)
    d = _load()
    day = time.time() - 86400
    if any(r["sig"] == sig and not r.get("fixed") and r["t"] > day for r in d["repairs"]):
        print(f"(self-repair: already tried {sig} today without luck)")
        return None
    if mission is not None and sum(1 for r in d["repairs"] if r.get("mission") == str(mission)) >= PER_MISSION:
        print("(self-repair: used this mission's repairs)")
        return None
    claude = brain.find_claude()
    if not claude:
        return None
    try:
        import storeforge
        sf = str(storeforge.folder())
    except Exception:
        sf = "~/storeforge"
    say(f"Heads up, my {role} hit an error. I'm looking into it.")
    print(f"(self-repair: {role}: {error[:200]})")
    with selfedit_lock():
        selfedit.ensure_repo()
        npm = ["npm install", "npm ci", "npm rebuild", "npm run build", "npx prisma generate"]
        allowed = ["Read", "Edit", "Write", "Glob", "Grep"] + [f"Bash({c}:*)" for c in npm] + [f"Bash({c})" for c in npm]
        try:
            out = brain.run_claude(
                [claude, "-p", PROMPT.format(sf=sf, goal=goal[:300], role=role, error=error[:3000],
                                             task=(task or "")[:600], extra=(extra or "")[:3000]),
                 "--permission-mode", "acceptEdits", "--add-dir", sf,
                 "--allowedTools", ",".join(allowed),
                 "--disallowedTools", "WebSearch,WebFetch,Task,NotebookEdit"],
                timeout=900, cwd=HERE)
            text = out.stdout or ""
        except subprocess.TimeoutExpired:
            text = ""
        res = _parse(text) or {"fixed": False, "retry": False, "cause": "", "owner_needs": "",
                               "say": "I couldn't work out what broke."}
        changed = [f for f in selfedit._changed_files() if not f.startswith((".night", "team/"))]
        if changed:
            problem = selfedit._check()
            if problem:
                print(f"(self-repair broke something, rolling back: {problem})")
                selfedit._rollback()
                changed, res["fixed"], res["retry"] = [], False, False
                res["say"] = "I tried a fix, but it broke part of me, so I put everything back."
            else:
                selfedit._git("add", "-A")
                selfedit._git("commit", "-q", "-m", f"Voice edit: (self-repair) {str(res.get('cause') or error)[:120]}")
                print(f"(self-repair changed: {', '.join(changed)})")
                if any(f.endswith(".py") and f != "storeforge_mcp.py" for f in changed):
                    _restart.set()           # loaded already, so it takes effect after a restart
    res["changed"] = changed
    res["fixed"] = bool(res.get("fixed"))
    res["retry"] = bool(res.get("retry")) and res["fixed"]
    d = _load()
    d["repairs"].append({"t": time.time(), "sig": sig, "mission": str(mission) if mission is not None else None,
                         "role": role, "error": error[:400], "cause": str(res.get("cause", ""))[:300],
                         "fixed": res["fixed"], "changed": changed, "say": str(res.get("say", ""))[:400]})
    _save(d)
    spoken = str(res.get("say") or "").strip()
    if res["fixed"]:
        spoken += " Trying again now." if res["retry"] else ""
        if changed:
            spoken += " Say undo that if you don't want the change."
    else:
        try:
            import learn
            learn.note(goal, f"My {role} hit an error I couldn't fix.", "error", f"team: {error[:300]}")
        except Exception:
            pass
    say(spoken or "I couldn't fix it myself.")
    print(f"(self-repair: fixed={res['fixed']} retry={res['retry']}: {res.get('cause')})")
    return res


_edit_lock = threading.Lock()


def selfedit_lock():
    """One self-repair at a time (agents run in parallel)."""
    return _edit_lock


def last():
    r = _load()["repairs"]
    if not r:
        return "My team hasn't needed any repairs yet."
    x = r[-1]
    when = time.strftime("%A at %-I:%M %p" if not __import__("sys").platform == "win32" else "%A at %I:%M %p",
                         time.localtime(x["t"]))
    return f"Last repair was {when}. {x.get('say') or x.get('cause') or 'No details.'}"
