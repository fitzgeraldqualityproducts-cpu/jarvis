"""Learning from mistakes: when Jarvis can't do something, he remembers it.

Each time a command fails or he has to say he can't help, it's written to lessons.json.
When he's idle, he asks Claude Code (read-only: it can look at his code but not change it)
whether a small upgrade would fix it. If so, next time Connor talks to him he suggests it,
and Connor says yes or no. A yes goes through the normal voice self-edit (selfedit.py),
with its snapshot and "undo that".

  "any upgrade ideas" / "how could you improve"   hear his suggestion now
  "what have you been struggling with"            hear what's gone wrong lately
Set "self_improve": false in config.json to turn it off."""
import json
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "lessons.json"
CONFIG = HERE / "config.json"
_lock = threading.RLock()
_thinking = threading.Event()

# What he says when he couldn't help.
CANT = re.compile(
    r"\b(i can'?t|i cannot|i'?m (?:not able|unable)|i (?:don'?t|do not) (?:have (?:a way|access|the ability)|know how)|"
    r"i (?:couldn'?t|could not|wasn'?t able)|(?:is|isn'?t|not) something i can|no way for me|beyond what i can|"
    r"something (?:glitched|went wrong)|didn'?t (?:answer|work|finish)|won'?t start|isn'?t on my list|"
    r"(?:not|n't) allowed to|wouldn'?t let me|i (?:don'?t|do not) know (?:how|what|which|an? )|"
    r"(?:i )?(?:can'?t|couldn'?t) find)\b", re.I)
# Things that are fine to say no to and aren't worth an upgrade.
NOT_A_GAP = re.compile(r"private stuff|only \w+ can|clashes with changes|already up to date|"
                       r"nothing to undo|leave myself as i am|hold off|taking (me )?too long", re.I)
MAX_OFFERS_PER_DAY = 2


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def enabled():
    return _cfg().get("self_improve", True)


def _load():
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {"misses": [], "ideas": [], "offers": []}


def _save(data):
    data["misses"] = data.get("misses", [])[-60:]
    data["ideas"] = data.get("ideas", [])[-30:]
    data["offers"] = data.get("offers", [])[-20:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(FILE)


def couldnt_help(reply):
    """True if what he said sounds like he couldn't do it."""
    return bool(reply) and bool(CANT.search(reply)) and not NOT_A_GAP.search(reply)


def note(asked, reply, kind="cant", detail=""):
    """Remember something he couldn't do."""
    if not enabled() or not asked:
        return
    with _lock:
        data = _load()
        data.setdefault("misses", []).append({"t": time.time(), "asked": asked[:300], "reply": (reply or "")[:400],
                                              "kind": kind, "detail": (detail or "")[:400], "looked": False})
        _save(data)
    print(f"(noted something I couldn't do: {asked[:80]!r})")


PROMPT = """You are helping improve Jarvis, a Python voice assistant whose code is in this folder (macOS and
Windows; see README.md and jarvis.py: commands are routed in jarvis.py handle -> organize / computer / music,
the brain is brain.py, speech is voice.py and ears.py). You can read the code but not change it.

Recently the owner asked Jarvis for these things and he couldn't do them (what was said, what Jarvis replied,
and any error):
{misses}

Ideas already suggested before (don't repeat them): {old}

Pick the ONE most useful upgrade that a small, safe code change in this folder would make work, the kind of
thing that would have let Jarvis do what was asked. Look at the code to check it's really missing or broken
and to work out the likely fix. Skip things that only need the owner to log in or connect an account, things
no code could do (like predicting the future), and things that are just general questions.

Reply with ONLY one line of JSON, no markdown:
{{"worth_it": true or false,
  "about": "the request it fixes, in a few words",
  "spoken": "one short friendly sentence Jarvis will say out loud to offer it, e.g. 'Earlier you asked me to
    turn on Do Not Disturb and I couldn't. I could add a command for that.'",
  "request": "precise instructions for the code change, written as the owner asking Jarvis to change himself"}}"""


def _ask_claude(misses, old):
    import brain
    claude = brain.find_claude()
    if not claude:
        return None
    listing = "\n".join(f"- Asked: {m['asked']!r}. Jarvis said: {m['reply']!r}."
                        + (f" Error: {m['detail']}" if m.get("detail") else "") for m in misses)
    prompt = PROMPT.format(misses=listing, old="; ".join(old) or "none")
    out = brain.run_claude([claude, "-p", prompt, "--allowedTools", "Read,Glob,Grep",
                            "--disallowedTools", "Edit Write NotebookEdit Bash WebSearch WebFetch Task"],
                           timeout=300, cwd=str(HERE))
    m = re.search(r"\{.*\}", out.stdout or "", re.S)
    if not m:
        print(f"(upgrade idea: no answer: {(out.stderr or '')[-200:]})")
        return None
    return json.loads(m.group(0))


def think_about_it():
    """Looks at the misses he hasn't looked at yet and works out an upgrade idea. Slow (a minute or so)."""
    if _thinking.is_set():
        return None
    _thinking.set()
    try:
        with _lock:
            data = _load()
            fresh = [m for m in data.get("misses", []) if not m.get("looked")]
            old = [i.get("about", "") for i in data.get("ideas", [])]
        if not fresh:
            return None
        print(f"(thinking about how to fix {len(fresh)} thing(s) I couldn't do)")
        try:
            idea = _ask_claude(fresh[-8:], old)
        except Exception as e:
            print(f"(upgrade idea failed: {e})")
            idea = None
        with _lock:
            data = _load()
            stamps = {m["t"] for m in fresh}
            for m in data.get("misses", []):
                if m["t"] in stamps:
                    m["looked"] = True
            if idea and idea.get("worth_it") and idea.get("request") and idea.get("spoken"):
                idea.update(t=time.time(), status="new", id=int(time.time()))
                data.setdefault("ideas", []).append(idea)
                print(f"(upgrade idea: {idea.get('about')}: {idea['request'][:150]})")
            else:
                print("(no upgrade idea worth suggesting)")
                idea = None
            _save(data)
        return idea
    finally:
        _thinking.clear()


def next_offer(any_time=False):
    """The upgrade idea to suggest now, or None. Unless asked (any_time), holds back so it
    doesn't nag: at most twice a day, and not within half an hour of the last one."""
    if not enabled() and not any_time:
        return None
    with _lock:
        data = _load()
        offers = data.get("offers", [])
        today = time.strftime("%Y-%m-%d")
        if not any_time and (sum(1 for o in offers if o.get("day") == today) >= MAX_OFFERS_PER_DAY
                             or (offers and time.time() - offers[-1].get("t", 0) < 1800)):
            return None
        for idea in reversed(data.get("ideas", [])):
            if idea.get("status") == "new" or (any_time and idea.get("status") == "offered"):
                return idea
    return None


def offered(idea_id):
    with _lock:
        data = _load()
        data.setdefault("offers", []).append({"id": idea_id, "day": time.strftime("%Y-%m-%d"), "t": time.time()})
        for idea in data.get("ideas", []):
            if idea.get("id") == idea_id:
                idea["status"] = "offered"
        _save(data)


def answered(idea_id, yes):
    """Connor said yes or no to an idea."""
    with _lock:
        data = _load()
        for idea in data.get("ideas", []):
            if idea.get("id") == idea_id:
                idea["status"] = "accepted" if yes else "declined"
        _save(data)


def struggles():
    """What's gone wrong lately, to say out loud."""
    data = _load()
    week = time.time() - 7 * 86400
    misses = [m for m in data.get("misses", []) if m["t"] > week]
    if not misses:
        return "Nothing's tripped me up this week. I'm on a roll."
    asks = list(dict.fromkeys(m["asked"] for m in reversed(misses)))[:3]
    return (f"This week {len(misses)} thing{'s' if len(misses) != 1 else ''} tripped me up, like: "
            + "; ".join(f"'{a}'" for a in asks) + ". Say any upgrade ideas and I'll tell you how I'd fix it.")


def start(is_busy):
    """Background: a little while after he's been stuck, and he's idle, work out an upgrade idea."""
    def loop():
        time.sleep(120)
        while True:
            try:
                if enabled() and not is_busy():
                    with _lock:
                        fresh = [m for m in _load().get("misses", []) if not m.get("looked")]
                    if fresh and time.time() - fresh[-1]["t"] > 60:
                        think_about_it()
            except Exception as e:
                print(f"(self-improvement check failed: {e})")
            time.sleep(90)
    threading.Thread(target=loop, daemon=True, name="learn").start()
