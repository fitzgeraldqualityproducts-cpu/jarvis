"""Learning from mistakes: when Jarvis can't do something, he remembers it.

Each time a command fails or he has to say he can't help, it's written to lessons.json.
When he's idle, he asks Claude Code (read-only: it can look at his code but not change it)
whether a small upgrade would fix it. If so, next time Connor talks to him he suggests it,
and Connor says yes or no. A yes goes through the normal voice self-edit (selfedit.py),
with its snapshot and "undo that".

He also keeps score of how things go: when Connor corrects him ("no, that's wrong", "that's not what I
asked"), whether Connor takes up the routines he predicts, how well his AI team's projects turn out
(the team's critic scores them), and whether his own upgrades work. Once a week, when he's idle, he
reviews all of it and works out one improvement. An improvement is either a code change (done through
selfedit, as above) or a new rule for how he works ("keep weather answers to one sentence"). Rules
need no code change: they go into every answer he gives and every job his team does, once Connor says yes.

  "any upgrade ideas" / "how could you improve"   hear his suggestion now
  "what have you been struggling with"            hear what's gone wrong lately
  "review yourself" / "how have you been doing"   his report card and an improvement idea
  "what rules have you learned"                   the rules he follows
  "forget your rules"                             clear them
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


CORRECTION = re.compile(
    r"^(?:no,? )?(?:that'?s|that is|you'?re|you are) (?:wrong|not right|incorrect|not what i (?:asked|meant|said|wanted))|"
    r"^(?:no,? )?(?:not what i (?:asked|meant|said|wanted)|you (?:got|heard) (?:it|that|me) wrong|wrong answer|"
    r"that'?s not it|you misunderstood|you messed (?:it|that) up|that didn'?t work|it didn'?t work)\b|"
    r"^no,? (?:i said|i meant|i asked)\b")


def is_correction(text):
    """True if Connor is telling him he got the last thing wrong."""
    return bool(text) and bool(CORRECTION.search(text.lower().strip()))


def _save(data):
    data["misses"] = data.get("misses", [])[-60:]
    data["ideas"] = data.get("ideas", [])[-30:]
    data["offers"] = data.get("offers", [])[-20:]
    data["results"] = data.get("results", [])[-300:]
    data["rules"] = data.get("rules", [])[-20:]
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


def record_result(kind, about, ok, score=None, detail=""):
    """Keep score of how something went: kind is prediction, team, upgrade, ..."""
    if not enabled():
        return
    with _lock:
        data = _load()
        data.setdefault("results", []).append({"t": time.time(), "kind": kind, "about": str(about)[:200], "ok": bool(ok),
                                               "score": score, "detail": str(detail or "")[:300]})
        _save(data)


# ---------- rules: instructions he's learned to follow ----------

def rules():
    return [r["rule"] for r in _load().get("rules", [])]


def add_rule(rule):
    rule = rule.strip()
    with _lock:
        data = _load()
        if rule and rule.lower() not in (r["rule"].lower() for r in data.get("rules", [])):
            data.setdefault("rules", []).append({"rule": rule, "t": time.time()})
        _save(data)
    print(f"(new rule for myself: {rule})")


def forget_rules():
    with _lock:
        data = _load()
        n = len(data.get("rules", []))
        data["rules"] = []
        _save(data)
    return (f"Done, I've dropped the {n} rules I'd learned." if n > 1 else "Done, I've dropped the rule I'd learned.") \
        if n else "I hadn't learned any rules yet."


def rules_summary():
    rs = rules()
    if not rs:
        return "I haven't learned any rules yet. When I spot a better way to work, I'll suggest one."
    return f"I follow {len(rs)} rule{'s' if len(rs) != 1 else ''} I've learned: " + "; ".join(r.rstrip(".") for r in rs) + "."


def rules_for_prompt():
    rs = rules()
    return ("Rules you've learned about how the owner likes things (follow them): " + " ".join(
        r if r.endswith(".") else r + "." for r in rs)) if rs else ""


def _score_card(days=7):
    data = _load()
    since = time.time() - days * 86400
    res = [r for r in data.get("results", []) if r["t"] > since]
    misses = [m for m in data.get("misses", []) if m["t"] > since]
    card = {"misses": len([m for m in misses if m.get("kind") != "corrected"]),
            "corrected": len([m for m in misses if m.get("kind") == "corrected"])}
    for kind in ("prediction", "team", "upgrade"):
        rs = [r for r in res if r["kind"] == kind]
        card[kind] = (sum(1 for r in rs if r["ok"]), len(rs))
    scores = [r["score"] for r in res if r["kind"] == "team" and isinstance(r.get("score"), (int, float))]
    card["team_score"] = round(sum(scores) / len(scores), 1) if scores else None
    return card, res, misses


PROMPT = """You are helping improve Jarvis, a Python voice assistant whose code is in this folder (macOS and
Windows; see README.md and jarvis.py: commands are routed in jarvis.py handle -> organize / computer / music,
the brain is brain.py, speech is voice.py and ears.py). You can read the code but not change it.

Recently the owner asked Jarvis for these things and he couldn't do them (what was said, what Jarvis replied,
and any error):
{misses}

How his other work went lately (results he keeps score of):
{results}

Rules he already follows: {rules}

Ideas already suggested before (don't repeat them): {old}

Pick the ONE most useful improvement to his workflows, tools or instructions. It can be either:
- "code": a small, safe code change in this folder that would have let Jarvis do what was asked, or fix
  something that keeps going wrong (look at the code to check it's really missing or broken and to work
  out the likely fix), or
- "rule": a one-sentence instruction for how Jarvis should answer or work from now on, when the problem is
  how he answered (too long, wrong tone, misunderstood what the owner usually means, suggestions the owner
  keeps turning down) rather than missing code.
Skip things that only need the owner to log in or connect an account, things no code could do, and things
that are just general questions. If nothing is worth changing, say so.

Reply with ONLY one line of JSON, no markdown:
{{"worth_it": true or false,
  "type": "code" or "rule",
  "about": "the problem it fixes, in a few words",
  "spoken": "one short friendly sentence Jarvis will say out loud to offer it, e.g. 'Earlier you asked me to
    turn on Do Not Disturb and I couldn't. I could add a command for that.'",
  "request": "for code: precise instructions for the change, written as the owner asking Jarvis to change himself",
  "rule": "for a rule: the instruction itself, e.g. 'Keep weather answers to one sentence.'"}}"""


def _ask_claude(misses, old, results=()):
    import brain
    claude = brain.find_claude()
    if not claude:
        return None
    listing = "\n".join(f"- Asked: {m['asked']!r}. Jarvis said: {m['reply']!r}."
                        + (f" Error: {m['detail']}" if m.get("detail") else "") for m in misses)
    res = "\n".join(f"- {r['kind']}: {r['about']!r} {'went well' if r['ok'] else 'did not go well'}"
                    + (f" (score {r['score']}/10)" if r.get("score") is not None else "")
                    + (f": {r['detail']}" if r.get("detail") else "") for r in list(results)[-25:])
    prompt = PROMPT.format(misses=listing or "- nothing this time", old="; ".join(old) or "none",
                           results=res or "- nothing recorded yet", rules="; ".join(rules()) or "none")
    out = brain.run_claude([claude, "-p", prompt, "--allowedTools", "Read,Glob,Grep",
                            "--disallowedTools", "Edit Write NotebookEdit Bash WebSearch WebFetch Task"],
                           timeout=300, cwd=str(HERE))
    m = re.search(r"\{.*\}", out.stdout or "", re.S)
    if not m:
        print(f"(upgrade idea: no answer: {(out.stderr or '')[-200:]})")
        return None
    return json.loads(m.group(0))


def think_about_it(review=False):
    """Looks at the misses he hasn't looked at yet and works out an upgrade idea. Slow (a minute or so).
    review=True: the weekly review, which also looks at the week's results and older misses."""
    if _thinking.is_set():
        return None
    _thinking.set()
    try:
        with _lock:
            data = _load()
            fresh = [m for m in data.get("misses", []) if not m.get("looked")]
            old = [i.get("about", "") for i in data.get("ideas", [])]
            week = time.time() - 7 * 86400
            results = [r for r in data.get("results", []) if r["t"] > week] if review else \
                [r for r in data.get("results", []) if r["t"] > week and not r["ok"]][-8:]
            if review:
                data["last_review"] = time.time()
                _save(data)
        if review:
            fresh = fresh or [m for m in data.get("misses", []) if m["t"] > week]
        if not fresh and not (review and results):
            return None
        print(f"(thinking about how to fix {len(fresh)} thing(s) I couldn't do"
              + (f" and {len(results)} results" if review else "") + ")")
        if not review:
            _count_think()
        try:
            idea = _ask_claude(fresh[-12:], old, results)
        except Exception as e:
            print(f"(upgrade idea failed: {e})")
            idea = None
        with _lock:
            data = _load()
            stamps = {m["t"] for m in fresh}
            for m in data.get("misses", []):
                if m["t"] in stamps:
                    m["looked"] = True
            if idea and idea.get("type") == "rule":
                idea["request"] = idea.get("request") or ""
                ok = bool(idea.get("rule"))
            else:
                idea = idea and dict(idea, type="code")
                ok = bool(idea and idea.get("request"))
            if idea and idea.get("worth_it") and ok and idea.get("spoken"):
                idea.update(t=time.time(), status="new", id=int(time.time()))
                data.setdefault("ideas", []).append(idea)
                print(f"(upgrade idea ({idea['type']}): {idea.get('about')}: {(idea.get('rule') or idea['request'])[:150]})")
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
    """Connor said yes or no to an idea. Returns the idea."""
    found = None
    with _lock:
        data = _load()
        for idea in data.get("ideas", []):
            if idea.get("id") == idea_id:
                idea["status"] = "accepted" if yes else "declined"
                found = idea
        _save(data)
    return found


def review():
    """His report card for the week, to say out loud (the idea, if any, is offered after)."""
    card, res, misses = _score_card()
    parts = []
    if card["misses"] or card["corrected"]:
        parts.append(f"{card['misses']} thing{'s' if card['misses'] != 1 else ''} I couldn't do"
                     + (f" and {card['corrected']} time{'s' if card['corrected'] != 1 else ''} you corrected me"
                        if card["corrected"] else ""))
    ok, n = card["prediction"]
    if n:
        parts.append(f"you took {ok} of my {n} suggestions")
    ok, n = card["team"]
    if n:
        parts.append(f"my team finished {n} project{'s' if n != 1 else ''}"
                     + (f" averaging {card['team_score']} out of 10" if card["team_score"] else ""))
    ok, n = card["upgrade"]
    if n:
        parts.append(f"{ok} of {n} upgrades worked first time")
    if not parts:
        return "Quiet week. Nothing went wrong that I noticed, and there's not much to score yet."
    return "This week: " + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."


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


def _last_review():
    with _lock:
        data = _load()
        if "last_review" not in data:            # first run: the first review is in a day's time
            data["last_review"] = time.time() - 6 * 86400
            _save(data)
        return data["last_review"]


# Saving his Claude plan: wait until he's been fine for a while so a run of misses is looked at in one go,
# and don't think up more ideas than he can be offered (twice a day) anyway.
BATCH_WAIT = 15 * 60
MAX_THINKS_PER_DAY = 3


def _worth_thinking():
    with _lock:
        data = _load()
        waiting = sum(1 for i in data.get("ideas", []) if i.get("status") == "new")
        today = time.strftime("%Y-%m-%d")
        done = data.get("thinks", {}).get(today, 0)
    return waiting < MAX_OFFERS_PER_DAY and done < MAX_THINKS_PER_DAY


def _count_think():
    with _lock:
        data = _load()
        today = time.strftime("%Y-%m-%d")
        data["thinks"] = {today: data.get("thinks", {}).get(today, 0) + 1}
        _save(data)


def start(is_busy):
    """Background: a little while after he's been stuck, and he's idle, work out an upgrade idea."""
    def loop():
        time.sleep(120)
        while True:
            try:
                if enabled() and not is_busy():
                    with _lock:
                        fresh = [m for m in _load().get("misses", []) if not m.get("looked")]
                    if fresh and time.time() - fresh[-1]["t"] > BATCH_WAIT and _worth_thinking():
                        think_about_it()
                    elif time.time() - _last_review() > 7 * 86400:
                        think_about_it(review=True)        # the weekly review
            except Exception as e:
                print(f"(self-improvement check failed: {e})")
            time.sleep(90)
    threading.Thread(target=loop, daemon=True, name="learn").start()
