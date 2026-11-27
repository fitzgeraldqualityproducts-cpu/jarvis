"""Connor's core rules for every Short (2026-11-27, "Core Rules (Never Break These)"), in one place.

Most of them are plain Python checks on what the studio already makes, so they cost no Claude calls. The few that
need judgement (the critic scores, the phone check) ride inside calls that already happen: the fact-check and
the final-cut review.

1 Script      75-90 words; the hook's grab in its first 7 words; hook -> setup -> key insight or calculation ->
              punchline answer -> CTA; one primary big number; the last line is a CTA of exactly one of three
              types (comment challenge, opinion question, series tease); no stiff or robotic phrasing.
2 Critic      hook strength, pacing and CTA clarity, each 1-10, scored by the fact-check; under 8 = rewrite.
              A weak hook is first swapped for a stronger checked one and a weak CTA for a comment challenge on
              the Short's own number (both free); only pacing needs a real rewrite.
3 Visuals     every shot gets an asset category (wide surface, close detail, orbital/diagram, scale reference,
              lighting change, starfield); never two wide surface shots in a row; the key diagram early; big
              numbers held 2-3 s; camera movement on most shots; a clean end card readable on pause.
4 Audio       expressive voice with short pauses (Chatterbox, already); a low ambient bed that ducks under the
              voice (ACE-Step, or a soft pad made here when it's off); only 1-2 subtle sound effects, at the
              diagram reveal, the big number or the final answer.
5 Timing      hook said by 3 s; a pattern interrupt between 12 and 18 s; captions on screen slightly before the
              words; no long still holds; 35-45 s long.
6 Metadata    3 titles, the strongest picked; the cover shows the big number and a short question; the
              description has a one-sentence hook, the key fact and a comment prompt.
7 Phone check before finishing: the first 3 s would stop a scroller, no awkward AI phrasing, a clear CTA, the
              runtime in target (studio/finalreview.py asks; the runtime is measured here).
8 Feedback    after a Short is measured: average view duration, retention and engagement are kept with the
              hook style, the CTA type and the structure, and the writer is told which ones did best.

Config "core_rules": "block" (default: a broken rule stops or fixes the Short), "advise" (notes only) or "off".
The rendering changes (sound effects cut to 1-2, the end card, number hold, caption lead, shot variety) follow
the new looks (--new-looks / "shots" on), like every other new style, until Connor approves a sample; config
"core_rules_look": true switches them on for every Short."""
import re

WORDS = (75, 90)
HOOK_GRAB_WORDS = 7                 # the question or hook in the first 7 words
HOOK_MAX_WORDS = 8                  # ... and fully said by 3 seconds (about 2.4 words a second)
HOOK_BY = 3.0
SECONDS = (35.0, 45.0)
INTERRUPT = (12.0, 18.0)
CRITIC_MIN = 8
CRITIC = ("hook", "pacing", "cta")
NUMBER_HOLD = (2.0, 3.0)
CAPTION_LEAD = 0.12                 # captions appear this much before the word is said
SFX_MAX = 2
STILL_MAX = 3.5                     # no still hold longer than this
MOVING_SHARE = 0.6                  # most shots move
PACE = 2.44                         # words a second (test 36: 101 words in 41.4 s)
END_CARD = (1.2, 3.0)               # the end card's time on screen: at least, at most

CATEGORIES = ("wide surface", "close detail", "orbital/diagram", "scale reference", "lighting change", "starfield")
CTA_TYPES = {"comment": "comment challenge", "opinion": "opinion question", "series": "series tease"}

ROBOTIC = ("delve", "fascinating", "in conclusion", "it's important to note", "it is important to note",
           "it's worth noting", "it is worth noting", "let's explore", "let's dive", "dive into", "embark",
           "testament to", "in the realm of", "unveil", "intricate", "tapestry", "ever wondered", "imagine a world",
           "buckle up", "in today's video", "without further ado", "furthermore", "moreover", "additionally",
           "one must", "remarkable", "captivating", "the answer may surprise you", "stay tuned", "journey through",
           "a myriad", "plethora", "in essence", "ultimately,", "it is evident", "notably,", "thus,", "hence,",
           "serves as a reminder", "nestled", "boasts")
GENERIC_CTA = ("like and subscribe", "subscribe for more", "follow for more", "hit the like", "smash that",
               "don't forget to", "let me know in the comments", "thanks for watching")
STIFF = (("it is", "it's"), ("do not", "don't"), ("does not", "doesn't"), ("you are", "you're"),
         ("that is", "that's"), ("there is", "there's"), ("cannot", "can't"), ("we are", "we're"),
         ("is not", "isn't"), ("would not", "wouldn't"), ("will not", "won't"))
SMALL = 12


def mode():
    try:
        from studio import config
        v = config.general().get("core_rules", "block")
    except Exception:
        v = "block"
    if v is False or str(v).lower() in ("off", "false", "0", "no"):
        return "off"
    return "advise" if str(v).lower() == "advise" else "block"


def on():
    return mode() != "off"


def look_on():
    """The rendering changes: with the new looks (Connor approves a sample first), or "core_rules_look": true."""
    if not on():
        return False
    try:
        from studio import art, config
        return art.shots_on() or config.general().get("core_rules_look", False) is True
    except Exception:
        return False


def _t(x):
    return " ".join(str(x or "").split())


def numbers(text):
    """The numbers in a line that a viewer would notice (not "two steps", not the 2 in "Part 2")."""
    t = re.sub(r"\bpart\s+\d+\b", " ", str(text or ""), flags=re.I)
    out = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", t):
        try:
            v = float(m.replace(",", ""))
        except ValueError:
            continue
        if "." in m or v > SMALL:
            out.append(m.replace(",", ""))
    return out


# ---------- 1: the script ----------

def cta_type(text):
    """"comment", "opinion", "series" or None for a last line."""
    t = _t(text)
    low = t.lower()
    if not t:
        return None
    if re.search(r"\bpart\s*(2|two|ii|3|three)\b|\bnext (short|time|episode|up)\b|\bcoming next\b|\bin the next one\b",
                 low):
        return "series"
    if re.match(r"(comment|type|drop)\b", low) or re.search(r"\bin the comments\b|\bcomment (your|a|the|if|below|\d)",
                                                            low):
        return "comment"
    if t.endswith("?") and re.search(r"\b(would you|which would|do you|what would you|could you|will you|"
                                     r"are you|which one|you rather|your pick|you'd)\b", low):
        return "opinion"
    return None


def cta_problems(text):
    """Problems with the CTA line: [] when it is one of Connor's three types, specific and short."""
    t = _t(text)
    low = t.lower()
    probs = [f"generic call to action \"{g}\"" for g in GENERIC_CTA if g in low]
    kind = cta_type(t)
    if not kind:
        probs.append("the last line must be a CTA: a comment challenge (\"Comment 14.8 if this surprised you\"), "
                     "an opinion question (\"Would you rather...?\") or a series tease (\"Part 2: ...\")")
    elif kind == "comment" and not (re.search(r"\d", t) or re.search(r"[\"“'‘][^\"”'’]+[\"”'’]", t) or
                                    re.search(r"\b(if|your guess|yes or no|which)\b", low)):
        probs.append("the comment challenge must say exactly what to comment (a number, a word, or \"if...\")")
    if len(t.split()) > 16:
        probs.append(f"the CTA is {len(t.split())} words; keep it under 16 so it's clear at a glance")
    return probs


def comment_challenge(number):
    return f"Comment {number} if this surprised you."


def primary_number(lines):
    """The Short's one big number: the payoff line's, else the hook's, else the last worked-out one."""
    lines = [ln for ln in lines or [] if isinstance(ln, dict)]
    for beat in ("payoff", "hook"):
        for ln in lines:
            if str(ln.get("beat") or "") == beat and numbers(ln.get("text")):
                return numbers(ln["text"])[0]
    for ln in reversed(lines[:-1]):
        if numbers(ln.get("text")):
            return numbers(ln["text"])[0]
    return None


def fix_cta(draft):
    """A draft whose last line isn't a clear CTA gets a comment challenge on its own big number (free). Returns
    (draft, note or "")."""
    lines = draft.get("lines") or []
    if not on() or len(lines) < 3:
        return draft, ""
    last = lines[-1]
    if not cta_problems(last.get("text")):
        return draft, ""
    num = primary_number(lines)
    if not num:
        return draft, ""
    new = comment_challenge(_fmt_num(num))
    d = dict(draft, lines=[dict(ln) for ln in lines])
    d["lines"][-1] = dict(last, text=new, facts=["general"], beat="loop", visual="end card: the answer and the CTA")
    return d, f"the last line \"{_t(last.get('text'))[:80]}\" wasn't one of the three CTA types: now \"{new}\""


def _fmt_num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return str(s)
    if v == int(v):
        return f"{int(v):,}"
    return str(s)


def robotic(text):
    """Stiff or robotic phrasing in the narration: [phrase]."""
    low = " " + _t(text).lower() + " "
    found = [p for p in ROBOTIC if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low)]
    stiff = [a for a, _ in STIFF if re.search(r"\b" + a + r"\b", low)]
    if len(stiff) >= 3:
        found.append("no contractions (" + ", ".join(f"\"{a}\"" for a in stiff[:3]) + "): say it the way people talk")
    return found


def script_problems(lines, hook=None, words=True):
    """The rule checks on a finished draft (the hook as line 1): [str], each starting "rule: " (it blocks in
    "block" mode) or "editor: rule: " (a note). words=False leaves the word count to the caller's own check."""
    if not on():
        return []
    lines = [dict(ln) for ln in lines or [] if isinstance(ln, dict)]
    if hook and lines:
        lines[0] = dict(lines[0], text=hook)
    if not lines:
        return []
    probs = []
    n = sum(len(_t(ln.get("text")).split()) for ln in lines)
    if words and not WORDS[0] <= n <= WORDS[1]:
        probs.append(f"rule: {n} words; Connor's limit is {WORDS[0]}-{WORDS[1]}")
    beats = [str(ln.get("beat") or "") for ln in lines]
    if "payoff" not in beats:
        probs.append("rule: no punchline answer (a payoff line that answers the hook)")
    if not any(b == "math" for b in beats):
        probs.append("editor: rule: no line marked as the key insight or calculation")
    if len(lines) > 2:
        probs += [f"rule: {p}" for p in cta_problems(lines[-1].get("text"))]
    body = [ln for ln in lines[:-1]]
    nums = []
    for ln in body:
        for n in numbers(ln.get("text")):
            if n not in nums:
                nums.append(n)
    if len(nums) > 4:
        probs.append(f"rule: {len(nums)} different numbers ({', '.join(nums[:6])}); keep ONE primary big number "
                     "and only the inputs it needs")
    rob = robotic(" ".join(ln.get("text", "") for ln in lines))
    hard = [r for r in rob if not r.startswith("no contractions")]
    if hard:
        probs.append("rule: stiff or robotic phrasing: " + "; ".join(hard[:4]))
    if len(hard) < len(rob):
        probs.append("editor: rule: " + rob[-1])
    return probs


def est_seconds(words, pace=None):
    return round(words / (pace or PACE), 1)


# ---------- 2: the critic gate ----------

CRITIC_ASK = """CRITIC (score as a tough short-form editor, 1-10 each, 8+ means ready to film): "critic": {"hook": how hard
the best hook stops a scrolling thumb in its first 5-7 words, "pacing": does every line move on at a spoken
rhythm with no dead or stiff spot, natural spoken English, "cta": is the LAST line one clear, specific call to
action of these types only: a comment challenge ("Comment 14.8 if this surprised you"), an opinion question
("Would you rather...?") or a series tease ("Part 2: ..."), "note": one sentence on the weakest of the three}.
Also rate each title option: "titles": {"0": 0-10} (would it make a viewer tap, honestly?)."""
CRITIC_SHAPE = ', "critic": {"hook": 0, "pacing": 0, "cta": 0, "note": "..."}, "titles": {"0": 0}'


def _score(v):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return None


def critic(ans, last_line=""):
    """({"hook", "pacing", "cta", "note"}, [problems]) from the fact-check's critic answer. A missing score isn't
    a failure (an older checker answer); a last line that isn't a CTA at all caps "cta" at 4."""
    c = (ans or {}).get("critic") if isinstance(ans, dict) else None
    c = c if isinstance(c, dict) else {}
    out = {k: _score(c.get(k)) for k in CRITIC}
    if cta_problems(last_line):
        out["cta"] = min(out["cta"] if out["cta"] is not None else 4.0, 4.0)
    out["note"] = _t(c.get("note"))[:200]
    probs = []
    if on():
        low = [k for k in CRITIC if out[k] is not None and out[k] < CRITIC_MIN]
        if low:
            probs.append("critic: " + ", ".join(f"{k} {out[k]:g}/10" for k in low) + f" (every score must be "
                         f"{CRITIC_MIN}+)" + (f": {out['note']}" if out["note"] else "") +
                         ("; make the hook stop a scroller in its first 7 words" if "hook" in low else "") +
                         ("; tighten the rhythm: every line one new thing, short spoken sentences" if "pacing" in low
                          else "") +
                         ("; end on one clear comment challenge, opinion question or series tease" if "cta" in low
                          else ""))
    return out, probs


def strong_hook(hooks, ratings, checks_fn):
    """The index of the hook the checker rated strongest (strength 8+) that passes the hard rules, or None."""
    best = None
    for i, h in enumerate(hooks or []):
        r = ratings.get(i) or {}
        s = _score(r.get("strength"))
        if s is None or s < CRITIC_MIN or checks_fn(h):
            continue
        if best is None or s > best[0]:
            best = (s, i)
    return best[1] if best else None


def title_order(titles, scores):
    """The titles, highest-rated first (a stable sort: unrated keep their order behind the rated ones)."""
    def key(it):
        i, _ = it
        s = _score((scores or {}).get(str(i)))
        return -(s if s is not None else -1)
    return [t for _, t in sorted(enumerate(titles or []), key=key)]


# ---------- 3: visuals ----------

_CAT_WORDS = (
    ("starfield", r"\b(stars?|starfield|milky way|galaxy|night sky|cosmos|nebula)\b"),
    ("orbital/diagram", r"\b(orbit|diagram|from above|top-down|seen from space|globe|planet from space|"
                        r"satellite view|cross-section|cutaway)\b"),
    ("scale reference", r"\b(next to|beside|compared|scale|tiny|towering|dwarf|stadium|football field|bus|"
                        r"skyscraper|person standing|human figure)\b"),
    ("lighting change", r"\b(sunrise|sunset|dawn|dusk|shadow|terminator|eclipse|glow|lit|darkness|night falls|"
                        r"half lit|light and dark)\b"),
    ("close detail", r"\b(close-up|close up|macro|detail|surface texture|crater rim|grain|up close|closer)\b"),
    ("wide surface", r"\b(landscape|surface|plain|horizon|desert|ocean|field|valley|terrain|ground|vista|"
                     r"wide|panorama)\b"),
)
DIAGRAMS = ("equation", "compare", "scale", "steps", "measure", "wave", "temperature", "orbit", "number")


def category(sc, shot=None):
    """The asset category of a scene (or one of its shots): the planner's own "category" when it gave one,
    else read from its picture words and type."""
    src = shot if isinstance(shot, dict) else sc if isinstance(sc, dict) else {}
    given = _t(src.get("category")).lower()
    if given in CATEGORIES:
        return given
    if shot is None and isinstance(sc, dict) and sc.get("type") in ("orbit", "equation", "steps", "wave"):
        return "orbital/diagram"
    if shot is None and isinstance(sc, dict) and sc.get("type") in ("scale", "compare", "measure"):
        return "scale reference"
    text = " ".join([_t(src.get("art")), _t(src.get("shows")), _t(src.get("camera"))]).lower()
    for cat, pat in _CAT_WORDS:
        if re.search(pat, text):
            return cat
    return "wide surface" if text else "close detail"


def vary_shots(scenes):
    """Never two similar wide surface shots in a row: the second becomes a close detail of the same subject
    (its picture words and camera). Returns (scenes, [what changed])."""
    out, changed, prev = [], [], None
    for k, sc in enumerate(scenes or []):
        if not isinstance(sc, dict):
            out.append(sc)
            continue
        sc = dict(sc)
        shots = [dict(s) for s in sc.get("shots") or [] if isinstance(s, dict)]
        if shots:
            for j, sh in enumerate(shots):
                cat = category(sc, sh)
                if cat == "wide surface" and prev == "wide surface":
                    sh["art"] = _closer(sh.get("art"))
                    sh["camera"] = "close-up"
                    cat = "close detail"
                    changed.append(f"scene {k + 1} shot {j + 1}: a second wide surface shot in a row is now a close "
                                   "detail")
                    if j == 0 and sc.get("art"):
                        sc["art"] = sh["art"]
                sh["category"] = cat
                prev = cat
            sc["shots"] = shots
            sc["category"] = shots[0]["category"]
        else:
            cat = category(sc)
            if cat == "wide surface" and prev == "wide surface" and sc.get("art"):
                sc["art"] = _closer(sc["art"])
                cat = "close detail"
                changed.append(f"scene {k + 1}: a second wide surface shot in a row is now a close detail")
            sc["category"] = cat
            prev = cat
        out.append(sc)
    return out, changed


def _closer(art):
    a = _t(art)
    a = re.sub(r"\b(wide|panorama(ic)?|landscape|vista|horizon)\b", "", a, flags=re.I)
    return ("close-up detail of " + " ".join(a.split()))[:160] if a else "close-up detail of the surface"


def first_diagram(scenes):
    """The first scene (1-based) that draws a diagram, or None."""
    for k, sc in enumerate(scenes or []):
        if isinstance(sc, dict) and k > 0 and sc.get("type") in DIAGRAMS and sc.get("type") != "number":
            return k + 1
    return None


def visual_problems(scenes, moves=None):
    """[str] notes on Connor's visual rules for a plan (and, after render, which scenes had a camera move)."""
    probs = []
    cats = [category(sc) for sc in scenes or []]
    for k in range(1, len(cats)):
        if cats[k] == cats[k - 1] == "wide surface":
            probs.append(f"scenes {k} and {k + 1} are both wide surface shots")
    fd = first_diagram(scenes)
    if scenes and len(scenes) > 3 and (fd is None or fd > max(3, len(scenes) // 2)):
        probs.append("the key diagram comes late" if fd else "no key diagram")
    if moves is not None and scenes:
        share = len(moves) / len(scenes)
        if share < MOVING_SHARE:
            probs.append(f"only {len(moves)} of {len(scenes)} shots have a camera move")
    return probs


def hud_hold(hud_list):
    """Big numbers on screen 2-3 s: each number gets "holdFor" (it fades after 3 s; Short.tsx)."""
    return [dict(h, holdFor=NUMBER_HOLD[1]) if isinstance(h, dict) and h.get("value") else h for h in hud_list or []]


def end_card(lines, speech_end):
    """The final frame (clean and readable on pause): the answer's number and the CTA, on its own backing.
    {"text", "sub", "start", "seconds"}: on screen after the last word, long enough to reach 35 s (1.2-3 s)."""
    lines = [ln for ln in lines or [] if isinstance(ln, dict)]
    if not lines:
        return None
    num = primary_number(lines)
    cta = _t(lines[-1].get("text"))
    if not cta:
        return None
    seconds = max(END_CARD[0], min(END_CARD[1], SECONDS[0] - speech_end))
    return {"text": cta[:90], "sub": num or "", "start": round(speech_end, 3), "seconds": round(seconds, 2)}


# ---------- 4: audio ----------

def sfx_cues(cue_list, scenes, beats, overlays=None):
    """Only 1-2 subtle sound effects, on the key moments: the diagram reveal, the big number, the final answer.
    The picture keeps every cue; this is the list the sound effects are made from."""
    pick = []
    payoff = next((c for c in cue_list if c.get("kind") == "payoff"), None)
    if payoff:
        pick.append(dict(payoff, subtle=True))
    reveal = None
    ovs = overlays or {}
    for c in cue_list:
        k = c.get("scene", -1)
        if c.get("kind") == "cut" and (k in ovs or (0 <= k < len(scenes or []) and isinstance(scenes[k], dict) and
                                                    scenes[k].get("type") in DIAGRAMS and
                                                    scenes[k].get("type") != "number")):
            reveal = dict(c, sfx="pop", subtle=True)
            break
    if reveal and not (payoff and abs(reveal["t"] - payoff["t"]) < 1.0):
        pick.append(reveal)
    if not pick:
        pop = next((c for c in cue_list if c.get("kind") == "pop"), None)
        if pop:
            pick.append(dict(pop, subtle=True))
    return sorted(pick[:SFX_MAX], key=lambda c: c["t"])


SFX_LEVELS = {"whoosh": 0.08, "tick": 0.0, "pop": 0.08, "riser": 0.0, "hit": 0.16}   # subtle: about half as loud


# ---------- 5: timing ----------

def interrupt_cue(starts, beats, words=None):
    """A visual pattern interrupt between 12 and 18 s: the re-hook's flash when the re-hook starts in that window,
    else one at the scene cut (or word) nearest 15 s. Returns a cue or None (the re-hook already does it)."""
    n = len(starts) - 1
    for k in range(n):
        if beats[k] == "rehook" and INTERRUPT[0] <= starts[k] <= INTERRUPT[1]:
            return None
    end = starts[-1] if starts else 0
    if end < INTERRUPT[0] + 1:
        return None
    cuts = [(abs(starts[k] - 15.0), k, starts[k]) for k in range(1, n) if INTERRUPT[0] <= starts[k] <= INTERRUPT[1]]
    if cuts:
        _, k, t = min(cuts)
        return {"t": round(t, 3), "kind": "rehook", "scene": k, "interrupt": True}
    ws = [float(w["s"]) for w in words or [] if INTERRUPT[0] <= float(w.get("s") or 0) <= INTERRUPT[1]]
    t = min(ws, key=lambda x: abs(x - 15.0)) if ws else 15.0
    k = max([i for i in range(n) if starts[i] <= t] or [0])
    return {"t": round(t, 3), "kind": "rehook", "scene": k, "interrupt": True}


def lead_pages(pages, lead=CAPTION_LEAD):
    """Caption pages shown slightly before their words (never before the page in front of them starts)."""
    out = [dict(p) for p in pages or []]
    for i, p in enumerate(out):
        if not p.get("text"):
            continue
        floor = out[i - 1]["s"] + 0.05 if i else 0.0
        s = max(floor, float(p["s"]) - lead)
        if i and out[i - 1].get("e", 0) > s:
            out[i - 1]["e"] = round(s, 3)
        p["s"] = round(s, 3)
    return out


def timing(words, lines, starts, duration, cues=None):
    """Connor's timing rules measured on the finished edit: {"hook_end", "interrupt", "duration", "problems"}."""
    probs = []
    n_hook = len(_t(lines[0]).split()) if lines else 0
    hook_end = float(words[n_hook - 1]["e"]) if words and 0 < n_hook <= len(words) else None
    if hook_end is not None and hook_end > HOOK_BY + 0.05:
        probs.append(f"the hook ends at {hook_end:.1f} s (it must be said by {HOOK_BY:g} s)")
    beats = [c for c in cues or [] if c.get("kind") == "rehook"]
    at = next((c["t"] for c in beats if INTERRUPT[0] <= c["t"] <= INTERRUPT[1]), None)
    if duration >= INTERRUPT[1] and at is None:
        probs.append(f"no pattern interrupt between {INTERRUPT[0]:g} and {INTERRUPT[1]:g} s")
    if not SECONDS[0] <= duration <= SECONDS[1]:
        probs.append(f"{duration:.1f} s long (target {SECONDS[0]:g}-{SECONDS[1]:g} s)")
    return {"hook_end": None if hook_end is None else round(hook_end, 2), "interrupt": at,
            "duration": round(duration, 2), "problems": probs}


# ---------- 6: metadata ----------

def description_parts(hook, lines):
    """(one-sentence hook, the key fact, the comment prompt) for the description."""
    lines = [ln for ln in lines or [] if isinstance(ln, dict)]
    h = _t(hook)
    if h and h[-1] not in ".?!":
        h += "."
    pay = next((_t(ln.get("text")) for ln in lines if str(ln.get("beat") or "") == "payoff"), "")
    if not pay and len(lines) > 2:
        pay = _t(lines[-2].get("text"))
    last = _t(lines[-1].get("text")) if lines else ""
    kind = cta_type(last)
    if kind in ("comment", "opinion"):
        prompt = last
    else:
        num = primary_number(lines)
        prompt = (f"Comment {_fmt_num(num)} if this surprised you." if num else
                  "Comment the one thing that surprised you most.")
        if kind == "series":
            prompt += " " + last
    return h, pay, prompt


def description_problems(desc, lines, hook):
    h, pay, prompt = description_parts(hook, lines)
    d = str(desc or "")
    probs = []
    if h and h not in d:
        probs.append("no one-sentence hook")
    if pay and pay not in d:
        probs.append("no key fact")
    if not re.search(r"\bcomment\b|\?", d.split("Sources:")[0], re.I):
        probs.append("no comment prompt")
    return probs


def cover_question(hook, titles):
    """A short question for the cover (at most 8 words, like the hook), from the hook or a title, else ""."""
    for t in [hook] + list(titles or []):
        t = _t(re.sub(r"#\w+", "", str(t or "")))
        if t.endswith("?") and len(t.split()) <= 8:
            return t
    for t in [hook] + list(titles or []):
        t = _t(t)
        m = re.search(r"([^.!?]*\?)", t)
        if m and len(m.group(1).split()) <= 8:
            return m.group(1).strip()
    return ""


# ---------- 8: feedback ----------

def structure_of(lines):
    """The shape of a script, as kept for learning: hook style, CTA type and the beat order."""
    lines = [ln for ln in lines or [] if isinstance(ln, dict)]
    hook = _t(lines[0].get("text")) if lines else ""
    style = "question" if "?" in hook else ("number" if re.search(r"\d", hook) else "statement")
    beats = []
    for ln in lines:
        b = str(ln.get("beat") or "")
        if not beats or beats[-1] != b:
            beats.append(b)
    return {"hook": style, "cta": cta_type(lines[-1].get("text")) if lines else None,
            "shape": "-".join(b for b in beats if b)}


ADVICE_MIN = 2          # Shorts per kind before the writer is told which did best
ADVICE_GAP = 8.0        # index points between the best and the rest before it counts


def feedback_advice(groups):
    """One writer line from studio_insights (analytics.learn): the CTA types, hook styles and structures that
    did best and worst here. Empty until there's enough real evidence."""
    parts = []
    for g, name in (("cta", "CTA types"), ("hook", "hook styles"), ("shape", "structures")):
        d = {k: v for k, v in (groups or {}).get(g, {}).items() if isinstance(v, dict) and v.get("n", 0) >= ADVICE_MIN}
        if len(d) < 2:
            continue
        best = max(d, key=lambda k: d[k]["mean"])
        worst = min(d, key=lambda k: d[k]["mean"])
        if d[best]["mean"] - d[worst]["mean"] < ADVICE_GAP:
            continue
        label = (lambda k: CTA_TYPES.get(k, k)) if g == "cta" else (lambda k: k)
        parts.append(f"{name}: prefer {label(best)} (index {d[best]['mean']:g} over {d[best]['n']} Shorts); "
                     f"avoid {label(worst)} ({d[worst]['mean']:g}, {d[worst]['n']} Shorts)")
    if not parts:
        return ""
    return "What real viewers rewarded on this channel (average view duration, retention, engagement): " + \
        "; ".join(parts) + "."


def remember_tease(video_id, text):
    """A series tease is a promise: it is kept on a list of follow-up ideas (kv "studio_series_teases") so a
    Part 2 can be made, and shown in the Short's pack."""
    from studio import db
    if cta_type(text) != "series":
        return False
    teases = db.get("studio_series_teases", []) or []
    if any(t.get("video") == video_id for t in teases if isinstance(t, dict)):
        return False
    teases.append({"video": video_id, "tease": _t(text)[:160]})
    db.put("studio_series_teases", teases[-50:])
    return True

