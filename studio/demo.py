"""Physical demonstrations and named cycles: does a hands-on model in the narration show the real science?

Built after test Short 35 (2026-11-27, "How many days of sun does the Moon's far side get?"). Its script used a
ball (the Moon), a lamp (the Sun) and a sticky note (a spot on the far side), and the idea was right, but:
- the note's place was said as "at the center of a ball's far side", never relative to the viewer, and the
  pictures never showed it;
- the script called 29.53 days "a lap" ("lit half a lap: 29.53 divided by 2"), but one lap of the Moon measured
  against the stars is 27.3 days; 29.53 days is New Moon to New Moon (the synodic month), which is also sunrise
  to sunrise at any one spot on the Moon (its solar day).

So the writer now fills a "demo" next to its model whenever the narration uses a physical model, and these checks
run on it (plain Python, no Claude call):

check(lines, model)       [{"hard", "kind", "text", "line"}] for the narrative engine:
    demo_missing     the narration uses a hands-on model but the script has no "demo" spec
    demo_incomplete  the spec doesn't say what each thing stands for, the observer, the marker's place, what
                     moves, what rotates, what stays fixed, what it proves or its limits
    marker_vague     the line that places the marker doesn't say where it is relative to the viewer
                     ("on the side facing away from you")
    marker_unsaid    the spec's marker place is never said in the narration
    rotation         a line turns or spins something the spec doesn't explain (how much, and what that keeps facing)
    demo_wrong       a line says the marker is lit or dark at a position where the model says the opposite
                     (worked out below, so a marker on the wrong side is caught)
    limits           (note) the answer depends on place but the narration never says where it holds
cycles(lines)             a period said without naming its cycle, or the wrong cycle for its number.
lit(angle, marker)        is the marker in sunlight with the ball at this place on its circle? The lamp is far
                          away on the left; 180 degrees = the ball between you and the lamp (New Moon), 0 = you
                          between the lamp and the ball (Full Moon). The ball turns once per circle (synchronous),
                          so a "far" marker always points away from you and a "near" one at you.
position(text)            which place on the circle a line describes ("new", "full", "first", "last") or None."""
import math
import re

# a thing a viewer can hold or set up, and a verb that puts it to use
PROPS = r"ball|orange|apple|grapefruit|melon|lamp|torch|flashlight|bulb|candle|marble|globe|balloon|coin|bucket|" \
        r"sticky note|sticker|note|dot|pin|tape|thumbtack|string"
PROP = re.compile(r"\b(" + PROPS + r")s?\b", re.I)
ACT = re.compile(r"\b(stick|put|place|hold|tape|mark|circle|swing|spin|turn|carry|walk|shine|grab|take|set|draw|"
                 r"tie|stand|imagine|picture)s?\b", re.I)
MARKERS = re.compile(r"\b(sticky note|sticker|note|dot|pin|tape|thumbtack|mark|marker|x)\b", re.I)
# where the marker sits, said in plain words
PLACE = re.compile(r"\b(far side|near side|back|front|side facing|facing (away|you|the)|pointing (away|at)|"
                   r"top|bottom|north pole|south pole|pole|equator|rim|edge|hidden side|other side|"
                   r"away from you|toward you|towards you)\b", re.I)
# ...and tied to the viewer, so "far side" means far from YOU
ANCHOR = re.compile(r"\b(away from you|facing away|faces away|points? away|you (can't|cannot|never) see|"
                    r"hidden from you|facing you|faces you|toward you|towards you|you (always )?see)\b", re.I)
VAGUE = re.compile(r"\b(at|in|on) the (center|centre|middle)\b", re.I)
TURN = re.compile(r"\b(turn(?:s|ing|ed)?|spin(?:s|ning)?|spun|rotat(?:e|es|ing|ion|ed)|twist(?:s|ing)?)\b", re.I)
HOW_MUCH = re.compile(r"\b(once|twice|one turn|\d+ times|per (circle|lap|orbit|trip|loop)|each (circle|lap|orbit|"
                      r"trip|loop)|every (circle|lap|orbit|trip|loop)|same (side|face)|always fac|keeps? .{0,20}"
                      r"fac)", re.I)
SYNCHRONOUS = re.compile(r"once (per|each|every|a) (circle|lap|orbit|trip|loop|time around)|same (side|face)|"
                         r"always fac|synchronous|tidally locked|keeps? .{0,30}fac", re.I)
FIELDS = (("objects", "what each object stands for"), ("observer", "what the viewer stands for"),
          ("moves", "what moves"), ("rotates", "what rotates (or that nothing does)"), ("fixed", "what stays fixed"),
          ("proves", "what the demonstration proves"), ("limits", "what the model leaves out"))


def _txt(v):
    if isinstance(v, dict):
        return " ".join(str(x) for x in v.values() if x)
    if isinstance(v, list):
        return " ".join(_txt(x) for x in v)
    return " ".join(str(v or "").split())


def _texts(lines):
    return [str(ln.get("text") if isinstance(ln, dict) else ln or "") for ln in lines or []]


def uses_model(lines):
    """Line numbers (1-based) that use a hands-on physical model: a prop plus a verb that sets it up or moves it,
    or a later line that carries on with the same prop."""
    out, props = [], set()
    for i, t in enumerate(_texts(lines), 1):
        found = {m.group(1).lower() for m in PROP.finditer(t)}
        if found and (ACT.search(t) or found & props):
            out.append(i)
            props |= found
    return out


def spec(model):
    d = (model or {}).get("demo") if isinstance(model, dict) else None
    return d if isinstance(d, dict) else None


def marker_side(demo):
    """"far", "near" or None from the spec's marker place."""
    m = (demo or {}).get("marker")
    where = _txt(m.get("where") if isinstance(m, dict) else m).lower()
    if re.search(r"far side|facing away|faces away|away from|hidden|back|other side|you (can't|never) see", where):
        return "far"
    if re.search(r"near side|facing you|faces you|toward|front|you see", where):
        return "near"
    return None


def synchronous(demo):
    return bool(SYNCHRONOUS.search(_txt((demo or {}).get("rotates")) + " " + _txt((demo or {}).get("moves"))))


# ---------- the model, worked out ----------

PHASES = {"new": 180.0, "first": 90.0, "full": 0.0, "last": 270.0}


def lit(angle, marker="far"):
    """True when the marker faces the lamp. Top-down, the lamp is far off to the left (direction 180 degrees);
    the ball sits at `angle` on its circle around you and turns once per circle, so a far-side marker points
    straight away from you (the same direction as the ball's place) and a near-side one points at you."""
    a = math.radians(angle)
    out = (math.cos(a), math.sin(a))
    face = out if marker == "far" else (-out[0], -out[1])
    return face[0] * -1.0 > 1e-9                 # facing the lamp on the left


def position(text):
    """The place on the circle a line describes, or None."""
    t = str(text or "").lower()
    if re.search(r"\bnew moon\b|\b(ball|moon|it)('s| is)? (sits |hangs |goes )?between you and (the )?(lamp|light|sun|"
                 r"torch|bulb)", t):
        return "new"
    if re.search(r"\bfull moon\b|\byou('re| are)? (stand |sit )?between (the )?(lamp|light|sun|torch|bulb) and", t):
        return "full"
    if re.search(r"\bfirst quarter\b", t):
        return "first"
    if re.search(r"\b(last|third) quarter\b", t):
        return "last"
    return None


LIT = re.compile(r"\b(lit|sunlit|in (the )?(sun|light|daylight)|bright|glow(s|ing)?|daytime|day)\b", re.I)
DARK = re.compile(r"\b(dark|unlit|in (the )?(shadow|dark)|shadowed|night(time)?)\b", re.I)


def claim(text):
    """"lit", "dark" or None: what a line says about the marker (after its place, e.g. "New Moon, note lit")."""
    t = str(text or "")
    m = MARKERS.search(t)
    if not m:
        return None
    rest = t[m.start():]
    lt, dk = LIT.search(rest), DARK.search(rest)
    if lt and (not dk or lt.start() < dk.start()):
        return "lit"
    if dk:
        return "dark"
    return None


# ---------- the checks ----------

def check(lines, model):
    """The demonstration checks: [{"hard", "kind", "text", "line"}]."""
    used = uses_model(lines)
    if not used:
        return []
    texts = _texts(lines)
    demo = spec(model)
    out = []
    marker_lines = [i for i in used if MARKERS.search(texts[i - 1])]
    if model is not None and demo is None:
        out.append({"hard": True, "kind": "demo_missing", "line": used[0],
                    "text": f"line {used[0]} uses a hands-on model but the script never says what each part stands "
                            "for, where the marker is, what moves and what it proves"})
        return out
    if demo is None:
        return out
    missing = [why for key, why in FIELDS if not _txt(demo.get(key))]
    if marker_lines and not _txt(demo.get("marker")):
        missing.insert(2, "where the marker is and what it stands for")
    if missing:
        out.append({"hard": True, "kind": "demo_incomplete", "line": used[0],
                    "text": "the demonstration doesn't say " + ", ".join(missing[:4])})
    if marker_lines:
        n = marker_lines[0]
        t = texts[n - 1]
        prev = texts[n - 2] if n >= 2 else ""
        if not PLACE.search(t):
            out.append({"hard": True, "kind": "marker_vague", "line": n,
                        "text": f"line {n} places the marker without saying where on the object it is"})
        elif not (ANCHOR.search(t) or ANCHOR.search(prev)):
            out.append({"hard": True, "kind": "marker_vague", "line": n,
                        "text": f"line {n} says where the marker is but not relative to the viewer; say it plainly "
                                "(\"on the side facing away from you\")"})
        elif VAGUE.search(t) and VAGUE.search(t).start() < PLACE.search(t).start():
            out.append({"hard": False, "kind": "marker_vague", "line": n,
                        "text": f"line {n} says \"{VAGUE.search(t).group(0)}\" before saying which side; lead with "
                                "the side"})
        side = marker_side(demo)
        if side and not any(re.search(r"far side|facing away|faces away|away from you|other side|back" if side == "far"
                                      else r"near side|facing you|faces you|toward you|front", x, re.I)
                            for x in texts):
            out.append({"hard": True, "kind": "marker_unsaid", "line": n,
                        "text": f"the marker sits on the {side} side in the model but the narration never says so"})
    rot = _txt(demo.get("rotates")).lower()
    for i in used:
        t = texts[i - 1]
        if TURN.search(t):
            if not rot or re.fullmatch(r"(nothing|none|no|n/a)\.?", rot):
                out.append({"hard": True, "kind": "rotation", "line": i,
                            "text": f"line {i} turns something but the demonstration doesn't say what rotates or why"})
            elif not HOW_MUCH.search(t) and not HOW_MUCH.search(rot):
                out.append({"hard": True, "kind": "rotation", "line": i,
                            "text": f"line {i} turns the object without saying how much or what that keeps facing you"})
            break
    side = marker_side(demo)
    if side and synchronous(demo):
        for i in used:
            t = texts[i - 1]
            pos, said = position(t), claim(t)
            if pos and said:
                truth = "lit" if lit(PHASES[pos], side) else "dark"
                if said != truth:
                    out.append({"hard": True, "kind": "demo_wrong", "line": i,
                                "text": f"line {i} says the marker is {said} at {'New' if pos == 'new' else 'Full' if pos == 'full' else pos.title()} "
                                        f"{'Moon' if pos in ('new', 'full') else 'quarter'}, but with the marker on the "
                                        f"{side} side it is {truth} there"})
    lim = _txt(demo.get("limits")).lower()
    if lim and re.search(r"latitude|pole|terrain|crater|equator|place|where", lim) and \
            not any(re.search(r"equator|pole|latitude|most of|middle of|crater|near the", x, re.I) for x in texts[1:]):
        out.append({"hard": False, "kind": "limits", "line": None,
                    "text": "the answer depends on place (" + lim[:80] + "); say in a few words where it holds"})
    return out


# ---------- named cycles ----------

# a period the writer may quote, what it is, and the words that name it (or that would name the other cycle)
CYCLES = (
    {"near": 27.32, "tol": 0.06, "what": "one turn or lap of the Moon measured against the stars (sidereal month)",
     "other": "New Moon to New Moon, or sunrise to sunrise, is 29.53 days",
     "names": r"sidereal|against the stars|relative to the stars|star to star|one (full )?(turn|spin|rotation|lap|"
              r"orbit)|turn(s)? once|spins? once|rotat|orbit|lap|circle",
     "wrong": r"sunrise|sunset|new moon|full moon|phase|day and night|day-night|lunar day|solar day|synodic"},
    {"near": 29.53, "tol": 0.06, "what": "New Moon to New Moon (the synodic month), which is also sunrise to sunrise "
                                         "at one spot on the Moon (its solar day)",
     "other": "one lap measured against the stars is 27.3 days",
     "names": r"sunrise|sunset|sun to sun|new moon|full moon|phases?|lunar day|solar day|synodic|day and night|"
              r"day-night|daylight|one day there|a day on the moon|moon's day",
     "wrong": r"\blap\b|\borbit\b|\bcircle\b|\bturns? once\b|\bspins? once\b|\brotat\w*|against the stars|sidereal"},
)


def cycles(lines):
    """[{"hard", "kind": "cycle", "text", "line"}]: a known period said without its cycle, or with the wrong one."""
    from studio.narrative import spoken
    out = []
    texts = _texts(lines)
    for i, t in enumerate(texts, 1):
        for c in CYCLES:
            if not any(abs(x - c["near"]) <= c["tol"] for x in spoken(t)):
                continue
            prev = texts[i - 2] if i >= 2 else ""
            named = re.search(c["names"], t, re.I)
            wrong = re.search(c["wrong"], t, re.I)
            if wrong and not named:
                out.append({"hard": True, "kind": "cycle", "line": i,
                            "text": f"line {i} calls {c['near']:g} days \"{wrong.group(0)}\", but {c['near']:g} days "
                                    f"is {c['what']}; {c['other']}"})
            elif wrong and named and c["near"] == 29.53 and re.search(r"\blap\b|\borbit\b|\bcircle\b", t, re.I) and \
                    not re.search(r"new moon|sunrise|synodic|solar day|lunar day", t, re.I):
                out.append({"hard": True, "kind": "cycle", "line": i,
                            "text": f"line {i} ties 29.53 days to a lap or orbit; one lap against the stars is 27.3 "
                                    f"days, 29.53 days is {c['what']}"})
            elif not named and not re.search(c["names"], prev, re.I):
                out.append({"hard": False, "kind": "cycle", "line": i,
                            "text": f"line {i} says {c['near']:g} days without saying which cycle; it is {c['what']}"})
    return out


ROOT = {
    "demo_missing": ("a hands-on model was used without saying what each part stands for",
                     "write the demo spec first: each object's role, the observer, the marker's place, what moves, "
                     "what rotates, what stays fixed, what it proves and its limits"),
    "demo_incomplete": ("the demonstration left out part of how it works",
                        "fill every part of the demo spec before writing the narration"),
    "marker_vague": ("the marker's place wasn't said relative to the viewer",
                     "say where the marker is from the viewer's side (\"on the side facing away from you\")"),
    "marker_unsaid": ("the model's marker place never reached the narration",
                      "say the marker's side out loud in the line that places it"),
    "rotation": ("something turned in the demonstration without saying how or why",
                 "say what rotates, how much (\"once per circle\") and what that keeps facing you"),
    "demo_wrong": ("the demonstration's light and dark don't match where the marker is",
                   "work out from the marker's side and the ball's place which side faces the lamp"),
    "cycle": ("a period was said without naming its cycle, or with the wrong one",
              "name the cycle with its number: 27.3 days against the stars, 29.53 days New Moon to New Moon "
              "(sunrise to sunrise)"),
    "limits": ("the answer depends on place but the narration didn't say where it holds",
               "add a few words on where the answer holds (\"near the equator\")"),
}
