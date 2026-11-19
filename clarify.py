"""'Did you mean?': when Jarvis isn't sure what he heard or what you meant, he asks a quick question
instead of doing the wrong thing.

  - He wasn't confident he heard a command right:   "Did you say close Discord?"
  - Two apps fit what you said about as well:        "Open Discord or Spotify?"
  - Only a weak match:                               "Did you mean Discord?"

Answer with the option ("Discord", "the second one"), yes/no, or just say something else and he
moves on. Config: "did_you_mean": false turns it off; "unsure_below" (default -0.8) is how unsure
the speech model must be before he checks (lower means he asks less)."""
import difflib
import json
import re
import time
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
WAITING = {}      # the question he's waiting on

YES = re.compile(r"\b(yes|yeah|yep|yup|ya|yea|sure|correct|right|exactly|that's it|that's right|you got it|"
                 r"do it|go ahead|please|ok|okay|bet|affirmative|uh huh)\b")
NO = re.compile(r"(no|nope|nah|neither|none|neither of them|none of them|wrong|not that|cancel|never mind|"
                r"forget it|no thanks)( (one|thanks|jarvis|of those))?")
ORDINALS = {"first": 0, "1st": 0, "one": 0, "former": 0, "second": 1, "2nd": 1, "two": 1, "latter": 1,
            "third": 2, "3rd": 2, "three": 2, "last": -1}
# Commands that do something (rather than questions he'd just answer).
ACTION = re.compile(r"^(?:open|close|quit|exit|kill|shut down|play|pause|skip|next|set|remind|add|turn|switch|run|"
                    r"start|launch|text|send|message|cancel|delete|remove|clear|put|schedule|make|book|join|leave|"
                    r"call|post|stop|undo|forget|upgrade|update|change|go to|take|write|note)\b")


def enabled():
    try:
        return json.loads(CONFIG.read_text()).get("did_you_mean", True)
    except Exception:
        return True


def _join(options):
    return options[0] if len(options) == 1 else ", ".join(options[:-1]) + " or " + options[-1]


def ask(question, options, template="{}", owner=True):
    """Remember what he's asking. options are what Connor might pick; template turns the chosen
    one into the command to run (e.g. "open {}"). Returns the question to say."""
    WAITING.clear()
    WAITING.update(question=question, options=list(options), template=template, owner=owner, t=time.time())
    return question


def ask_choice(verb, options, owner=True):
    """ "Open Discord or Spotify?" or, for one option, "Did you mean Discord?" """
    if len(options) == 1:
        return ask(f"Did you mean {options[0]}?", options, verb + " {}", owner)
    return ask(f"{verb.capitalize()} {_join(options)}?", options, verb + " {}", owner)


def ask_heard(text, owner=True):
    """ "Did you say close Discord?" """
    return ask(f"Sorry, did you say: {text.strip().rstrip('.?!')}?", [text], "{}", owner)


def waiting(owner=True):
    """True if he's waiting on an answer from this person (and it hasn't gone stale)."""
    if WAITING and time.time() - WAITING.get("t", 0) > 90:
        WAITING.clear()
    return bool(WAITING) and WAITING.get("owner", True) == owner


def resolve(t):
    """Connor's answer. Returns (command to run or None, what to say or None).
    (None, None) means he didn't answer the question, so just handle what he said."""
    if not WAITING:
        return None, None
    options, template = WAITING["options"], WAITING["template"]
    t = t.lower().strip(" .!?,")
    words = set(re.findall(r"[a-z0-9']+", t))
    pick = None
    for i, opt in enumerate(options):               # he named one
        name = opt.lower()
        if len(options) > 1 and (name in t or (len(name) > 3 and difflib.get_close_matches(name, list(words), 1, 0.8))):
            pick = opt if pick is None else pick
    if pick is None and len(options) > 1:
        for word, i in ORDINALS.items():
            if re.search(rf"\b{re.escape(word)}\b", t) and (len(t.split()) <= 4):
                pick = options[i]
                break
    if pick is None and YES.search(t) and len(t.split()) <= 5:
        if len(options) == 1:
            pick = options[0]
        else:
            WAITING["t"] = time.time()
            return None, f"Which one? {_join(options)}?"
    if pick is not None:
        WAITING.clear()
        return template.format(pick), None
    if NO.fullmatch(t) or (re.match(r"no\b", t) and len(t.split()) <= 3):
        WAITING.clear()
        return None, "My bad. Say it again?"
    WAITING.clear()                                  # he said something else: treat it as new
    return None, None


def check_heard(text, unsure, owner=True):
    """If he wasn't sure he heard a command right, the question to ask instead of doing it."""
    if not unsure or not enabled() or WAITING:
        return None
    t = re.sub(r"^\s*(?:hey |ok |okay )?jarvis[,.]?\s*", "", text.lower()).strip()
    t = re.sub(r"^(?:please|can you|could you|would you)\s+", "", t)
    if not ACTION.match(t) or len(t.split()) > 14:
        return None                                  # a question: the brain can ask if it's unclear
    return ask_heard(text, owner)


def choices(spoken, names, cutoff=0.5, close=0.12):
    """For a name that didn't match anything exactly: the names it could be, when he should ask.
    [] when it's clear (or nothing fits). Two or three names when they fit about equally well,
    one name when it's only a weak match."""
    if not enabled() or not names:
        return []
    spoken = spoken.lower().strip(" .?!")
    lower = {n.lower(): n for n in names}
    if spoken in lower or any(low in spoken or spoken in low for low in lower):
        return []
    scored = sorted(((difflib.SequenceMatcher(None, spoken, low).ratio(), real) for low, real in lower.items()),
                    reverse=True)
    scored = [(s, n) for s, n in scored if s >= cutoff]
    if not scored:
        return []
    best = scored[0][0]
    near = [n for s, n in scored[:3] if best - s <= close]
    if len(near) > 1:
        return near
    return [scored[0][1]] if best < 0.75 else []
