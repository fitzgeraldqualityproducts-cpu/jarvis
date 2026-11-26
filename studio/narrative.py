"""Narrative Integrity Engine: does the Short tell ONE story a newcomer can follow, with every number explained?

Built after test Short 20 (paper folding), whose script passed the fact-check while the story didn't make sense:
it said "Fold twelve loses 2,798,250 times that" and never said where 2,798,250 came from (it is
(2^12 + 4)(2^12 - 1) / 6 from a published folding formula), used "1,219 meters" for both the tissue and "your
street", and ended on "72 percent of your street, leaving 340 meters" without saying what was left of what.

Free: almost all of it is plain Python on the script the writer already returns. The parts that need judgement
(is the physics model the right one, does every line follow from what came before, can a stranger retell it)
ride along in the fact-check call that already happens, so no extra Claude call is spent.

The writer now returns, next to its drafts, a "model": the central question, the premise, the starting values with
units and where each comes from, the assumptions, the formula, the result and the answer. Each line also says what
the viewer learns there ("learn") and which model values it relies on ("uses"). That is the storyboard the
checks below read.

evaluate(draft, review_answer=None, facts=None) -> {"hard": [...], "advice": [...], "score": 0-100, "parts", "issues"}
    hard   : problems that block the script (a number the viewer can't trace, a unit slip, a model that doesn't
             give its own result, a physics model the checker says is wrong, a conclusion that doesn't follow,
             a step that leans on something never said, a stranger who can't retell the story).
    advice : softer notes for the rewrite (a repeated line, an ambiguous "that", one number with two meanings).
    score  : the weighted story score. It never passes a script with a hard problem.
    issues : one dict per problem, with the exact line, what was missing, the root cause and the prevention rule
             (saved to the narrative_failures table so the same mistake is not made again).

trace(lines, calcs)          every spoken worked-out number, broken down to the numbers the viewer has heard.
units_check(lines)           "0.1 millimeters, or 0.001 meters" style conversions, recomputed.
model_check(model, lines)    the model's formula re-run with units (dimension check) and compared with its result.
visual_purpose(scenes, lines) how well the visual plan explains the maths (QA "visuals explain the story")."""
import ast
import math
import re

# ---------- numbers the viewer hears ----------

WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
         "million": 1e6, "billion": 1e9, "half": 0.5, "twice": 2, "double": 2, "doubles": 2, "doubled": 2,
         "doubling": 2, "triple": 3, "triples": 3, "quarter": 0.25, "dozen": 12}
SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(thousand|million|billion|trillion)\b)?",
                 re.I)
# conversion factors a narrator never has to say ("0.1 millimeters" goes into a formula as 0.0001 m)
CONVERSIONS = (60, 3600, 24, 365, 365.25, 86400, 2.54, 0.3048, 1609.344, 0.4536)
TRIVIAL = (0, 1, 2, 0.5, 100)            # 100 turns a fraction into a percent
TOL = 0.03                                 # a spoken number may be rounded by 3%
SMALL = 12                                 # like the script module: whole numbers up to 12 are ordinary words
CIRCLE_WORDS = re.compile(r"\b(circle|circular|semicircle|semi-circle|half-circle|half circle|curve|curved|round|"
                          r"rounded|arc|bend|bends|loop|wrap|wraps|wrapped|orbit|sphere|disc|disk|radius|"
                          r"diameter|circumference)\b", re.I)
PRONOUN_START = re.compile(r"^(it|this|that|they|these|those|its|it's)\b", re.I)


class _Word(float):
    """A number heard as a word ("one", "ten", "percent"): it matches only itself, never a power-of-ten rescale
    (so "fold one" doesn't count as having said 0.1 mm)."""


def spoken(text):
    """[float] every number a listener hears in a line: digits ("1,219", "0.1", "4 million") and number words
    ("twelve", "half", "twice")."""
    t = str(text or "")
    out = []
    for m in NUM.finditer(t):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out.append(v)
        if m.group(2):
            out.append(v * SCALE[m.group(2).lower()])
    for w in re.findall(r"[a-z]+", t.lower()):
        if w in WORDS:
            out.append(_Word(WORDS[w]))
    if re.search(r"\bpercent\b|%", t, re.I):
        out.append(_Word(100.0))
    return out


def _close(a, b, tol=TOL):
    return math.isclose(a, b, rel_tol=tol, abs_tol=1e-9)


def _pow10(a, b):
    """True when a and b are the same number in different units of ten (0.1 mm and 0.0001 m)."""
    if a == 0 or b == 0 or (a < 0) != (b < 0):
        return False
    r = math.log10(abs(a) / abs(b))
    return abs(r - round(r)) < 0.004


def heard(x, nums, scaled=True):
    return any(_close(x, s) or (scaled and not isinstance(s, _Word) and _pow10(x, s) and abs(x) > 1e-12) for s in nums)


def fmt(x):
    if x is None:
        return "?"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:.4g}"


def _says_pi(text):
    return bool(re.search(r"\bpi\b|π", str(text), re.I))


# ---------- tracing spoken results back to what the viewer heard ----------

class _Node:
    def __init__(self, value, literals):
        self.value, self.literals = value, literals


def _eval(node):
    """(value, [(literal value, label)]) for an arithmetic expression node (same rules as script.calc)."""
    from studio import script
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in script._CONSTS:
        return float(script._CONSTS[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in script._OPS:
        return float(script._OPS[type(node.op)](_eval(node.left), _eval(node.right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in script._OPS:
        return float(script._OPS[type(node.op)](_eval(node.operand)))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in script._FUNCS:
        return float(script._FUNCS[node.func.id](*[_eval(a) for a in node.args]))
    raise ValueError("not plain arithmetic")


def _parse(expr):
    expr = str(expr).replace("^", "**").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expr)
    expr = re.sub(r"(?<=\d)_(?=\d)", "", expr)
    return ast.parse(expr, mode="eval").body


def _unexplained(node, nums, text, others, depth=0, top=True):
    """The parts of an expression a viewer couldn't have worked out from what they heard: [label]. A part is
    explained when its value was said out loud, when it is a trivial number or a unit conversion, or when every
    piece of it is explained. A literal that is another calculation's result is traced through that one."""
    from studio import script
    try:
        val = _eval(node)
    except Exception:
        return []
    if isinstance(node, ast.Name):
        if node.id == "pi":
            if not _says_pi(text):
                return ["pi (never said)"]
            if not CIRCLE_WORDS.search(text):
                return ["pi (no circle or curve was described first)"]
            return []
        if node.id == "g" and not re.search(r"\bgravity|\bg\b|9\.8", text, re.I):
            return ["g, gravity's pull (never said)"]
        if node.id == "c" and not re.search(r"speed of light|light", text, re.I):
            return ["c, the speed of light (never said)"]
        return []
    if not top and not isinstance(node, (ast.Constant, ast.Name)) and val is not None and heard(val, nums) and \
            not any(_close(val, t) for t in TRIVIAL):
        return []                                    # the viewer heard this whole part's value
    if isinstance(node, ast.Constant):
        v = float(node.value)
        if any(_close(v, t, 1e-9) for t in TRIVIAL) or any(_close(v, c, 1e-6) for c in CONVERSIONS):
            return []
        if v != 0 and _pow10(v, 1.0):              # 1000, 0.001: a change of unit
            return []
        if heard(v, nums):
            return []
        for oth in others:                          # another calculation's result, used as an input
            if depth < 4 and oth.get("actual") is not None and _close(v, float(oth["actual"])):
                try:
                    return _unexplained(_parse(oth["expr"]), nums, text, [o for o in others if o is not oth],
                                        depth + 1, top=False)
                except Exception:
                    return [fmt(v)]
        return [fmt(v)]
    if isinstance(node, ast.BinOp):
        return (_unexplained(node.left, nums, text, others, depth, False)
                + _unexplained(node.right, nums, text, others, depth, False))
    if isinstance(node, ast.UnaryOp):
        return _unexplained(node.operand, nums, text, others, depth, False)
    if isinstance(node, ast.Call):
        out = []
        for a in node.args:
            out += _unexplained(a, nums, text, others, depth, False)
        if out or node.func.id in ("sqrt", "log", "log10", "exp", "sin", "cos", "tan"):
            return out
        return []
    return [ast.unparse(node)[:30]]


def _texts(lines):
    return [str((ln or {}).get("text") if isinstance(ln, dict) else ln or "") for ln in lines]


def trace(lines, calcs):
    """[{"line", "number", "expr", "missing", "kind"}] for every worked-out number the narration says whose
    derivation the viewer never hears. A number said only in the hook (a teaser) must be explained by the end;
    otherwise by the first ordinary line that says it. kind "too_late" when the missing numbers are said, but
    only after the line that needs them."""
    from studio import script
    texts = _texts(lines)
    said = [spoken(t) for t in texts]
    out = []
    calcs = [dict(c) for c in calcs or [] if isinstance(c, dict) and c.get("expr")]
    for c in calcs:
        try:
            c["actual"] = script.calc(c["expr"])
        except Exception:
            c["actual"] = None
    for c in calcs:
        val = c.get("actual")
        if val is None or any(_close(val, t, 1e-9) for t in TRIVIAL) or abs(val) <= SMALL and float(val).is_integer():
            continue
        where = [i for i, nums in enumerate(said) if heard(val, nums, scaled=False)]
        if not where:
            continue                                     # never said out loud: an intermediate step
        body = [i for i in where if i > 0]
        k = body[0] if body else len(texts) - 1          # a hook teaser must be paid off by the end
        upto = [x for nums in said[:k + 1] for x in nums]
        text = " ".join(texts[:k + 1])
        try:
            node = _parse(c["expr"])
        except Exception:
            continue
        if not isinstance(node, (ast.BinOp, ast.Call, ast.UnaryOp)):
            continue
        missing = list(dict.fromkeys(_unexplained(node, upto, text, [o for o in calcs if o is not c])))
        if not missing:
            continue
        everything = [x for nums in said for x in nums]
        later = [m for m in missing if re.fullmatch(r"[\d,.]+", m) and heard(float(m.replace(",", "")), everything)]
        out.append({"line": k + 1, "number": val, "expr": str(c["expr"]), "missing": missing,
                    "kind": "too_late" if later and len(later) == len(missing) else "unexplained",
                    "says": str(c.get("says") or "")[:120]})
    return out


# ---------- units ----------

# unit word -> (dimensions {L, M, T}, factor to SI)
_U = {}
for names, dims, f in [
        (("nm", "nanometer", "nanometre"), {"L": 1}, 1e-9),
        (("micrometer", "micrometre", "micron", "µm", "um"), {"L": 1}, 1e-6),
        (("mm", "millimeter", "millimetre"), {"L": 1}, 1e-3),
        (("cm", "centimeter", "centimetre"), {"L": 1}, 1e-2),
        (("m", "meter", "metre"), {"L": 1}, 1.0),
        (("km", "kilometer", "kilometre"), {"L": 1}, 1e3),
        (("in", "inch", "inche"), {"L": 1}, 0.0254),
        (("ft", "foot", "feet"), {"L": 1}, 0.3048),
        (("yd", "yard"), {"L": 1}, 0.9144),
        (("mi", "mile"), {"L": 1}, 1609.344),
        (("mg", "milligram"), {"M": 1}, 1e-6),
        (("g", "gram"), {"M": 1}, 1e-3),
        (("kg", "kilogram"), {"M": 1}, 1.0),
        (("t", "ton", "tonne"), {"M": 1}, 1e3),
        (("lb", "pound"), {"M": 1}, 0.45359237),
        (("ms", "millisecond"), {"T": 1}, 1e-3),
        (("s", "sec", "second"), {"T": 1}, 1.0),
        (("min", "minute"), {"T": 1}, 60.0),
        (("h", "hr", "hour"), {"T": 1}, 3600.0),
        (("day",), {"T": 1}, 86400.0),
        (("year", "yr"), {"T": 1}, 3.15576e7),
        (("mph",), {"L": 1, "T": -1}, 0.44704),
        (("kph", "km/h"), {"L": 1, "T": -1}, 1 / 3.6),
        (("l", "liter", "litre"), {"L": 3}, 1e-3),
        (("ml", "milliliter", "millilitre"), {"L": 3}, 1e-6),
        (("", "count", "times", "x", "layer", "fold", "percent", "%", "fraction", "ratio", "none", "number",
          "dimensionless", "rad", "radian"), {}, 1.0)]:
    for n in names:
        _U[n] = (dims, f)


def _unit_word(w):
    w = w.strip().lower().rstrip(".")
    if w in _U:
        return _U[w]
    if w.endswith("es") and w[:-2] in _U:
        return _U[w[:-2]]
    if w.endswith("s") and w[:-1] in _U:
        return _U[w[:-1]]
    return None


def parse_unit(u):
    """(dims, factor) for a unit string like "mm", "m/s", "kg*m/s^2", "switches per mm^2". Words that aren't units
    of length, mass or time are things being counted (switches, squares, layers) and count as plain numbers.
    None only when the string can't be read at all."""
    u = str(u or "").strip().lower().replace("·", "*").replace("²", "^2").replace("³", "^3").replace("per ", "/")
    if _unit_word(u):
        return _unit_word(u)
    num, _, den = u.partition("/")
    dims, f = {}, 1.0
    for part, sign in ((num, 1), (den, -1)):
        for tok in re.split(r"[*\s]+", part.strip()):
            if not tok:
                continue
            m = re.fullmatch(r"([a-zµ%]+)(?:\^?(-?\d+))?", tok)
            if not m:
                return None
            got = _unit_word(m.group(1)) or ({}, 1.0)    # a count noun (switches, squares, transistors): a plain number
            p = int(m.group(2) or 1) * sign
            for k, v in got[0].items():
                dims[k] = dims.get(k, 0) + v * p
            f *= got[1] ** p
    return {k: v for k, v in dims.items() if v}, f


UNIT_NEAR = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(nanometers?|nanometres?|micrometers?|micrometres?|microns?|"
                       r"millimeters?|millimetres?|centimeters?|centimetres?|kilometers?|kilometres?|meters?|"
                       r"metres?|inch(?:es)?|feet|foot|yards?|miles?|milligrams?|kilograms?|grams?|tons?|tonnes?|"
                       r"pounds?|milliseconds?|seconds?|minutes?|hours?|days?|years?|mm|cm|km|kg|mg|mph|ft)\b", re.I)


def units_check(lines):
    """[{"line", "said", "should"}]: a line that gives one amount in two units ("0.1 millimeters, or 0.001
    meters") where the two don't agree."""
    out = []
    for i, t in enumerate(_texts(lines), 1):
        found = [(float(m.group(1).replace(",", "")), m.group(2), m.start(), m.end()) for m in UNIT_NEAR.finditer(t)]
        for (a, ua, _, ea), (b, ub, sb, _) in zip(found, found[1:]):
            pa, pb = parse_unit(ua), parse_unit(ub)
            if not pa or not pb or pa[0] != pb[0] or _unit_word(ua) == _unit_word(ub):
                continue
            between = t[ea:sb].lower()
            if not re.fullmatch(r"[\s,(]*(or|is|equals|=|that's|that is|about|roughly|which is|or about)?[\s,(]*",
                                between):
                continue                           # two different amounts, not one amount said twice
            should = a * pa[1] / pb[1]
            if not _close(should, b, 0.05):
                out.append({"line": i, "said": f"{fmt(a)} {ua} = {fmt(b)} {ub}", "should": f"{fmt(should)} {ub}"})
    return out


class DimError(ValueError):
    pass


def _dim_eval(node, env, bare=None):
    """(SI value, dims) of a formula over named quantities, raising DimError when it adds unlike units. A plain
    number written into the formula (not a named quantity, not 2 or a power of ten) has no unit Jarvis can know:
    it is added to `bare`, and then the formula's units can't be judged."""
    from studio import script
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        v = float(node.value)
        if bare is not None and not (any(_close(v, t, 1e-9) for t in TRIVIAL) or v == 0 or
                                     v.is_integer() and abs(v) <= SMALL):
            bare.append(v)
        return v, {}
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in script._CONSTS:
            return float(script._CONSTS[node.id]), {"g": {"L": 1, "T": -2}, "c": {"L": 1, "T": -1}}.get(node.id, {})
        raise DimError(f"\"{node.id}\" isn't one of the model's quantities")
    if isinstance(node, ast.UnaryOp):
        v, d = _dim_eval(node.operand, env, bare)
        return (-v if isinstance(node.op, ast.USub) else v), d
    if isinstance(node, ast.BinOp):
        a, da = _dim_eval(node.left, env, bare)
        b, db = _dim_eval(node.right, env, bare)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if da != db:
                raise DimError(f"it adds or subtracts unlike things ({_dims_txt(da)} and {_dims_txt(db)})")
            return (a + b if isinstance(node.op, ast.Add) else a - b), da
        if isinstance(node.op, ast.Mult):
            return a * b, _dims_join(da, db, 1)
        if isinstance(node.op, ast.Div):
            return a / b, _dims_join(da, db, -1)
        if isinstance(node.op, ast.Pow):
            if db:
                raise DimError("a power with units")
            if abs(b) > 100:
                raise DimError("exponent too large")
            return a ** b, {k: v * b for k, v in da.items()}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = [_dim_eval(x, env, bare) for x in node.args]
        f = node.func.id
        if f == "sqrt" and args:
            return math.sqrt(args[0][0]), {k: v / 2 for k, v in args[0][1].items()}
        if f in ("log", "log10", "exp", "sin", "cos", "tan") and args:
            if args[0][1]:
                raise DimError(f"{f}() of something with units")
            return float(script._FUNCS[f](args[0][0])), {}
        if f in script._FUNCS and args:
            return float(script._FUNCS[f](*[a[0] for a in args])), args[0][1]
    raise DimError("not plain arithmetic")


def _dims_join(a, b, sign):
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + sign * v
    return {k: v for k, v in out.items() if abs(v) > 1e-9}


def _dims_txt(d):
    names = {"L": "length", "M": "mass", "T": "time"}
    if not d:
        return "a plain number"
    return " ".join(f"{names.get(k, k)}" + (f"^{v:g}" if v != 1 else "") for k, v in sorted(d.items()))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def is_conversion(q):
    """A model "quantity" that is only a change of unit (1e12 nm^2 per mm^2, 1000 m per km): nothing about the
    topic, so the narrator needn't say it (the converted value is what gets said)."""
    unit = str(q.get("unit") or "").lower()
    val = _num(q.get("value"))
    words = f"{q.get('means') or ''} {q.get('name') or ''}".lower()
    if re.search(r"\bconver|\bper (milli|centi|kilo|nano|micro)|in a (metre|meter|kilo|milli)", words):
        return True
    pu = parse_unit(unit) if ("/" in unit or " per " in f" {unit} ") else None
    if pu is not None and pu[0] == {} and pu[1] != 1.0 and val is not None and _close(val * pu[1], 1.0, 0.01):
        return True                                  # nm^2 per mm^2 with value 1e12: exactly one unit in the other
    return False


def model_check(model, lines):
    """Hard and soft problems with the writer's model: [{"hard", "kind", "text", "line"}]."""
    out = []
    if not isinstance(model, dict) or not model:
        return [{"hard": False, "kind": "no_model", "line": None,
                 "text": "no model: say the question, the starting values with units, the formula and the answer "
                         "before writing"}]
    for key, label in (("question", "the central question"), ("premise", "what physically happens"),
                       ("answer", "the answer in plain words")):
        if not str(model.get(key) or "").strip():
            out.append({"hard": False, "kind": "model_gap", "line": None, "text": f"the model doesn't state {label}"})
    qs = [q for q in model.get("quantities") or [] if isinstance(q, dict) and str(q.get("name") or "").strip()]
    env, texts = {}, _texts(lines)
    said_all = [spoken(t) for t in texts]
    for q in qs:
        name = re.sub(r"\W", "_", str(q["name"]).strip())
        val = _num(q.get("value"))
        pu = parse_unit(q.get("unit"))
        if val is None:
            continue
        if pu is None:
            out.append({"hard": False, "kind": "units", "line": None,
                        "text": f"the model's {q.get('means') or name} has a unit Jarvis can't read ({q.get('unit')})"})
            pu = ({"?" + str(q.get("unit")): 1}, 1.0)
        env[name] = (val * pu[1], pu[0])
        src = str(q.get("from") or "").lower()
        if src.startswith("calc") or src.startswith("result") or is_conversion(q):
            continue
        if val is not None and not (float(val).is_integer() and abs(val) <= SMALL) and \
                not any(heard(val, s) for s in said_all):
            out.append({"hard": True, "kind": "assumption", "line": None,
                        "text": f"the starting value {q.get('means') or name} = {fmt(val)} {q.get('unit') or ''} is "
                                "used in the formula but never told to the viewer"})
    seen = {}
    for q in qs:
        val, unit = _num(q.get("value")), str(q.get("unit") or "").strip().lower()
        if val is None or not unit or abs(val) <= SMALL:
            continue
        key = (round(val, 6), unit)
        if key in seen and str(seen[key].get("means") or "").strip().lower() != str(q.get("means") or "").strip().lower():
            out.append({"hard": False, "kind": "two_meanings", "line": None,
                        "text": f"one number, two meanings: {fmt(val)} {unit} is both \"{seen[key].get('means')}\" "
                                f"and \"{q.get('means')}\"; say plainly which one each line means"})
        seen.setdefault(key, q)
    f = model.get("formula") if isinstance(model.get("formula"), dict) else {"expr": model.get("formula")}
    expr = str((f or {}).get("expr") or "").strip()
    res = model.get("result") if isinstance(model.get("result"), dict) else {}
    if expr:
        try:
            node = _parse(expr)
            bare = []
            val, dims = _dim_eval(node, env, bare)
            rv, ru = _num(res.get("value")), parse_unit(res.get("unit"))
            if bare:                       # numbers typed into the formula: their units are unknown, so only the
                raw = None                 # value is compared, as written, and a mismatch is a note, not a stop
                try:
                    raw, _ = _dim_eval(node, {k: (_num(q.get("value")), {}) for k, q in
                                              ((re.sub(r"\W", "_", str(q["name"]).strip()), q) for q in qs)
                                              if _num(q.get("value")) is not None})
                except Exception:
                    pass
                ok = rv is None or any(x is not None and _close(x, c, 0.05) for x in (raw, val)
                                       for c in (rv, rv * (ru[1] if ru else 1.0)))
                if not ok:
                    out.append({"hard": False, "kind": "model_result", "line": None,
                                "text": f"the formula {expr} works out to {fmt(raw if raw is not None else val)}, not "
                                        f"the {fmt(rv)} {res.get('unit') or ''} the model says; write its numbers "
                                        "as named quantities with units"})
            elif ru is not None and ru[0] != dims and not any(k.startswith("?") for k in dims):
                out.append({"hard": True, "kind": "units", "line": None,
                            "text": f"the formula {expr} gives {_dims_txt(dims)}, but the answer is in "
                                    f"{res.get('unit')} ({_dims_txt(ru[0])})"})
            elif rv is not None and ru is not None and not _close(val, rv * ru[1], 0.05):
                out.append({"hard": True, "kind": "model_result", "line": None,
                            "text": f"the formula {expr} works out to {fmt(val / ru[1])} {res.get('unit')}, not the "
                                    f"{fmt(rv)} {res.get('unit')} the script says"})
        except DimError as e:
            out.append({"hard": True, "kind": "units", "line": None, "text": f"the formula {expr} doesn't work: {e}"})
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
            out.append({"hard": False, "kind": "model_gap", "line": None,
                        "text": f"couldn't re-run the model's formula {expr!r} ({str(e)[:60]})"})
    elif qs:
        out.append({"hard": False, "kind": "model_gap", "line": None, "text": "the model has no formula"})
    return out


# ---------- the storyboard: each line's job ----------

STOP = {"that", "this", "these", "those", "your", "with", "from", "into", "than", "then", "they", "their", "there",
        "what", "when", "where", "which", "about", "just", "only", "over", "each", "every", "it's", "that's"}


def _overlap(a, b):
    """Shared content words over the shorter line's (0-1); lines with under 4 content words never count."""
    wa = {w for w in re.findall(r"[a-z']+", a.lower()) if len(w) > 3 and w not in STOP}
    wb = {w for w in re.findall(r"[a-z']+", b.lower()) if len(w) > 3 and w not in STOP}
    if len(wa) < 4 or len(wb) < 4:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def storyboard_check(lines, model):
    """Each line's "learn" and "uses" against the model: [{"hard", "kind", "text", "line"}]."""
    out = []
    qs = {re.sub(r"\W", "_", str(q.get("name") or "").strip()): q for q in (model or {}).get("quantities") or []
          if isinstance(q, dict) and q.get("name")}
    texts = _texts(lines)
    said = [spoken(t) for t in texts]
    learned = []
    for i, ln in enumerate(lines, 1):
        if not isinstance(ln, dict):
            continue
        beat = str(ln.get("beat") or "")
        learn = " ".join(str(ln.get("learn") or "").split())
        if "learn" in ln and not learn and beat in ("setup", "math", "payoff"):
            out.append({"hard": False, "kind": "idle_line", "line": i,
                        "text": f"line {i} teaches the viewer nothing new; give it one job or cut it"})
        for j, prev in learned:
            if learn and _overlap(learn, prev) >= 0.8 and len(learn.split()) > 3:
                out.append({"hard": False, "kind": "repeat", "line": i,
                            "text": f"line {i} teaches the same thing as line {j} (\"{learn[:80]}\")"})
                break
        if learn:
            learned.append((i, learn))
        for u in ln.get("uses") or []:
            name = re.sub(r"\W", "_", str(u).strip())
            q = qs.get(name)
            if not q:
                continue
            val = _num(q.get("value"))
            if val is None or float(val).is_integer() and abs(val) <= SMALL:
                continue
            if str(q.get("from") or "").lower().startswith(("calc", "result")) or is_conversion(q):
                continue
            if any(heard(val, s) for s in said[:i]):
                continue
            first = next((k for k, s in enumerate(said, 1) if heard(val, s)), None)
            out.append({"hard": True, "kind": "too_late", "line": i,
                        "text": f"line {i} relies on {q.get('means') or name} ({fmt(val)} {q.get('unit') or ''}) "
                                + (f"but the viewer only hears it in line {first}" if first else
                                   "but the viewer never hears it")})
    return out


def confusion(lines):
    """What a newcomer trips on, found by rules (no model): [{"hard", "kind", "text", "line"}]."""
    out = []
    texts = _texts(lines)
    if texts and PRONOUN_START.match(texts[0].strip()):
        out.append({"hard": False, "kind": "pronoun", "line": 1,
                    "text": f"the first line starts with \"{texts[0].split()[0]}\" before anything has been named"})
    for i in range(1, len(texts)):
        t = texts[i]
        prev_nums = [x for x in spoken(texts[i - 1]) if not (x.is_integer() and x <= SMALL)]
        if re.search(r"\b(times|of|than|from)\s+(that|this|it)\b", t, re.I) and len(set(prev_nums)) >= 2:
            out.append({"hard": False, "kind": "ambiguous", "line": i + 1,
                        "text": f"line {i + 1} says \"{re.search(r'(times|of|than|from) (that|this|it)', t, re.I).group(0)}\" "
                                f"after a line with {len(set(prev_nums))} numbers; name the one it means"})
        big = [x for x in spoken(t) if not (x.is_integer() and x <= SMALL) and x != 100]
        if len(set(big)) >= 4:
            out.append({"hard": False, "kind": "crowded", "line": i + 1,
                        "text": f"line {i + 1} has {len(set(big))} numbers; the ear can hold two"})
        for j in range(i):
            if len(t.split()) > 5 and _overlap(t, texts[j]) >= 0.75:
                out.append({"hard": False, "kind": "repeat", "line": i + 1,
                            "text": f"line {i + 1} repeats line {j + 1}"})
                break
    return out


# ---------- the checker's judgement (asked inside the fact-check call) ----------

REVIEW_ASK = """
STORY CHECK (judge the whole narration as one story, read by someone who knows nothing about the subject):
- "premise_clear" 0-10: after the first two lines, does the viewer know exactly what physical thing is happening
  (what object, what is done to it, what is being measured) and what question is being answered?
- "model_valid": true/false. Does the model's formula really describe what the narration says it does? A
  correct equation applied to the wrong situation is false (a full-circle formula where the bend is a half circle,
  a formula for the minimum length needed used as "length lost", an average used as a maximum). Arithmetic being
  right is not enough. "model_problem": one sentence when false.
- "continuity": for every line that needs something the viewer hasn't been told yet (a number, a term, a
  multiplier, an assumption) or that doesn't follow from the line before, {"line": n, "missing": "what was
  never established", "why": "what the viewer can't work out"}. Ask of each line: could an intelligent viewer
  understand why this sentence follows from the previous one? Most good scripts have none; [] if none.
- "conclusion_follows": true/false. Can the final answer (said in the PAYOFF line) be worked out from the lines
  before it, and does it answer the opening question (not a different one)? The last line is a loop line that
  leads back into the first line when the Short repeats, so it may be unfinished on purpose: never judge the
  conclusion on it, and never list it under "continuity" for being unfinished. Two different lengths, times or amounts must never be
  treated as the same thing (the length of a sheet and the length of a street, material used and material left).
- "stranger": answer these five from the narration alone, as you'd explain it to a stranger, "" when the
  narration doesn't let you: {"1": "what are we trying to understand?", "2": "what do we need to know first?",
  "3": "how do we get from the starting point to the answer?", "4": "why is the answer surprising?",
  "5": "what did the viewer learn?"}"""
REVIEW_SHAPE = (', "story": {"premise_clear": 0, "model_valid": true, "model_problem": "", "continuity": [], '
                '"conclusion_follows": true, "stranger": {"1": "", "2": "", "3": "", "4": "", "5": ""}}')
PREMISE_MIN = 5          # premise clarity under this blocks; under 7 is a note
STRANGER = ("what we're trying to understand", "what we need to know first", "how we get from the start to the answer",
            "why the answer is surprising", "what the viewer learned")


def judged(story, lines):
    """Problems from the checker's story answer: [{"hard", "kind", "text", "line"}] plus its parts for the score."""
    out, parts = [], {}
    if not isinstance(story, dict):
        return out, parts
    beats = [str((ln or {}).get("beat") or "") if isinstance(ln, dict) else "" for ln in lines]
    pc = _num(story.get("premise_clear"))
    if pc is not None:
        parts["premise"] = max(0.0, min(1.0, pc / 10))
        if pc < PREMISE_MIN:
            out.append({"hard": True, "kind": "premise", "line": 1,
                        "text": f"unclear premise ({pc:g}/10): in the first two lines say what physical thing "
                                "happens, to what, and the one question the Short answers"})
        elif pc < 7:
            out.append({"hard": False, "kind": "premise", "line": 1,
                        "text": f"the premise could be plainer ({pc:g}/10)"})
    mv = story.get("model_valid")
    if mv is False or str(mv).lower() == "false":
        parts["model"] = 0.0
        out.append({"hard": True, "kind": "model", "line": None,
                    "text": "the physics model is wrong for what the narration claims: "
                            + (" ".join(str(story.get("model_problem") or "").split())[:240] or "see the checker")})
    elif mv is not None:
        parts["model"] = 1.0
    gaps = []
    for c in story.get("continuity") or []:
        if not isinstance(c, dict):
            continue
        try:
            n = int(c.get("line"))
        except (TypeError, ValueError):
            continue
        miss = " ".join(str(c.get("missing") or "").split())[:160]
        why = " ".join(str(c.get("why") or "").split())[:160]
        if not miss or not 1 <= n <= len(lines):
            continue
        hard = beats[n - 1] in ("math", "payoff", "rehook")     # the loop line may end mid-thought on purpose
        gaps.append(n)
        out.append({"hard": hard, "kind": "continuity", "line": n, "missing": miss,
                    "text": f"line {n} leans on something never established ({miss})" + (f": {why}" if why else "")})
    if story.get("continuity") is not None:
        parts["continuity"] = max(0.0, 1 - 0.34 * len(set(gaps)))
    cf = story.get("conclusion_follows")
    if cf is False or str(cf).lower() == "false":
        parts["conclusion"] = 0.0
        out.append({"hard": True, "kind": "conclusion", "line": len(lines) or None,
                    "text": "the conclusion doesn't follow from the explanation, or answers a different question "
                            "than the opening one"})
    elif cf is not None:
        parts["conclusion"] = 1.0
    st = story.get("stranger")
    if isinstance(st, dict):
        blank = [k for k in ("1", "2", "3", "4", "5") if len(" ".join(str(st.get(k) or "").split())) < 8]
        parts["stranger"] = 1 - len(blank) / 5
        if blank:
            out.append({"hard": True, "kind": "stranger", "line": None,
                        "text": "explain-it-to-a-stranger test: the narration doesn't tell a newcomer "
                                + ", ".join(STRANGER[int(k) - 1] for k in blank)})
    return out, parts


# ---------- everything together ----------

ROOT = {   # kind -> (root cause, prevention rule) for the failure memory
    "unexplained": ("a worked-out number was said without the steps that produce it",
                    "before saying a calculated number, say each input it comes from and what the operation means"),
    "too_late": ("a line used a value before the viewer was told it",
                 "introduce every starting value (with its unit) before the first line that uses it"),
    "units": ("a unit conversion or formula mixed up units",
              "state each value with its unit and convert in one explicit step"),
    "model_result": ("the formula doesn't produce the result the script states",
                     "work the result out from the model's own formula and values"),
    "model": ("the equation doesn't describe the physical situation in the narration",
              "establish the physical setup (what bends, how, what is measured) before picking the formula"),
    "assumption": ("an assumption was used in the maths but never said",
                   "say every assumed value out loud as an assumption (\"say the tissue is 0.1 mm thick\")"),
    "continuity": ("a line depended on information never established",
                   "make every line follow from what the viewer already knows"),
    "conclusion": ("the ending did not follow from the explanation or answered a different question",
                   "write the answer to the opening question first, then build every line toward it"),
    "premise": ("the opening didn't say what physically happens or what is asked",
                "open with the object, what is done to it and the one question"),
    "stranger": ("the story couldn't be retold to a newcomer",
                 "check the five stranger questions before writing the hook"),
    "origin": ("a number on screen had no traceable source or calculation",
               "every number must come from a fact, a labelled example or a shown calculation"),
}


def mode():
    """Config "narrative_check": "block" (default: hard problems stop the script), "advise" (they become editor
    notes) or False (off)."""
    try:
        from studio import config
        v = config.general().get("narrative_check", "block")
    except Exception:
        v = "block"
    if v is False or str(v).lower() in ("off", "false", "0", "no"):
        return "off"
    return "advise" if str(v).lower() == "advise" else "block"


def origins(lines, calcs, facts=None, model=None):
    """[{"hard", "kind", "text", "line"}]: a number on a maths or payoff line that no fact, calculation, model
    value or labelled example accounts for."""
    if facts is None:
        return []
    from studio import script
    known = []
    for f in facts:
        known += spoken(f.get("claim") if isinstance(f, dict) else f)
    for c in calcs or []:
        if not isinstance(c, dict):
            continue
        known += spoken(str(c.get("expr") or "")) + spoken(str(c.get("result") or ""))
        try:
            known.append(script.calc(c.get("expr")))
        except Exception:
            pass
    for q in (model or {}).get("quantities") or []:
        if isinstance(q, dict) and _num(q.get("value")) is not None:
            known.append(_num(q.get("value")))
    out = []
    for i, ln in enumerate(lines, 1):
        if not isinstance(ln, dict) or str(ln.get("beat") or "") not in ("math", "payoff"):
            continue
        if {"example", "general"} & {str(f) for f in ln.get("facts") or []}:
            continue
        for x in spoken(ln.get("text")):
            if x.is_integer() and x <= SMALL or x in TRIVIAL or 1500 <= x <= 2100 and x.is_integer():
                continue
            if not heard(x, known):
                out.append({"hard": True, "kind": "origin", "line": i,
                            "text": f"line {i} says {fmt(x)}, but no fact, calculation or labelled example gives it"})
    return out


WEIGHTS = {"trace": 0.25, "units": 0.1, "model": 0.15, "continuity": 0.15, "premise": 0.1, "conclusion": 0.1,
           "stranger": 0.1, "clarity": 0.05}


def evaluate(draft, story=None, facts=None):
    """The whole check. story: the checker's "story" answer (None before the fact-check: then only the code's
    own checks run, as the free screen of the side-by-side drafts)."""
    lines = draft.get("lines") or []
    model = draft.get("model") if isinstance(draft.get("model"), dict) else None
    hooks = draft.get("hooks") or []
    if lines and hooks and isinstance(lines[0], dict):            # the narration opens with the chosen hook
        lines = [dict(lines[0], text=draft.get("hook") or lines[0].get("text"))] + list(lines[1:])
    texts = _texts(lines)
    issues = []
    for t in trace(lines, draft.get("calcs")):
        sent = texts[t["line"] - 1] if 0 < t["line"] <= len(texts) else ""
        miss = ", ".join(t["missing"][:4])
        if t["kind"] == "too_late":
            text = (f"line {t['line']} says {fmt(t['number'])} before the viewer has heard {miss}, which it comes from "
                    f"({t['expr']})")
        else:
            text = (f"unexplained number: line {t['line']} says {fmt(t['number'])}, but the viewer never hears where "
                    f"{miss} come{'s' if len(t['missing']) == 1 else ''} from (it is {t['expr']}); show each step "
                    "before the result, or drop the number")
        issues.append({"hard": True, "kind": t["kind"], "line": t["line"], "sentence": sent, "missing": miss,
                       "math": f"{t['expr']} = {fmt(t['number'])}", "text": text})
    for u in units_check(lines):
        issues.append({"hard": True, "kind": "units", "line": u["line"], "math": f"{u['said']} (should be {u['should']})",
                       "text": f"unit slip in line {u['line']}: {u['said']}, but it should be {u['should']}"})
    issues += model_check(model, lines) if model is not None else []
    issues += storyboard_check(lines, model)
    issues += confusion(lines)
    issues += origins(lines, draft.get("calcs"), facts, model)
    found, parts = judged(story, lines)
    issues += found
    for it in issues:
        n = it.get("line")
        if n and not it.get("sentence") and 0 < n <= len(texts):
            it["sentence"] = texts[n - 1]
        if n and 0 < n <= len(lines) and isinstance(lines[n - 1], dict):
            it.setdefault("beat", lines[n - 1].get("beat"))
        cause, fix = ROOT.get(it["kind"], ("", ""))
        it.setdefault("root_cause", cause)
        it.setdefault("fix", fix)
    derived = sum(1 for c in draft.get("calcs") or [] if isinstance(c, dict))
    untraced = sum(1 for it in issues if it["kind"] in ("unexplained", "too_late", "origin"))
    parts["trace"] = max(0.0, 1 - untraced / max(1, derived))
    parts["units"] = 0.0 if any(it["kind"] == "units" and it["hard"] for it in issues) else 1.0
    if model is None:
        parts.setdefault("model", 0.5)
    elif any(it["kind"] in ("model_result", "assumption") for it in issues):
        parts["model"] = min(parts.get("model", 1.0), 0.3)
    soft = sum(1 for it in issues if not it["hard"] and it["kind"] in ("pronoun", "ambiguous", "crowded", "repeat",
                                                                         "idle_line", "two_meanings"))
    parts["clarity"] = max(0.0, 1 - 0.2 * soft)
    w = sum(WEIGHTS[k] for k in parts if k in WEIGHTS)
    score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items() if k in WEIGHTS) / w, 1) if w else None
    hard = [f"narrative: {it['text']}" + (f" (fix: {it['fix']})" if it.get("fix") else "") for it in issues
            if it["hard"]]
    advice = [it["text"] for it in issues if not it["hard"]]
    if hard and score is not None:
        score = min(score, 49.0)                 # a story with a hard problem never scores as a pass
    return {"hard": hard, "advice": advice[:6], "score": score, "parts": {k: round(v, 2) for k, v in parts.items()},
            "issues": issues}


# ---------- visuals that explain (QA) ----------

EXPLAIN_TYPES = ("equation", "compare", "scale", "steps", "measure", "wave", "temperature", "number")


def visual_purpose(scenes, lines):
    """{"score": 0-1, "problems": [...]}: maths and payoff lines should get a scene that explains (an equation,
    a comparison, a scale, steps, a measurement), and a line about what's left of something should show the
    whole, the used part and the rest side by side."""
    problems, need, good = [], 0, 0
    texts = _texts(lines)
    for i, (sc, ln) in enumerate(zip(scenes or [], lines or []), 1):
        beat = str((ln or {}).get("beat") or "") if isinstance(ln, dict) else ""
        t = texts[i - 1]
        if beat not in ("math", "payoff") and not re.search(r"\btimes\b|percent|%|\bleaving\b|\bleft\b|remain", t, re.I):
            continue
        need += 1
        typ = str((sc or {}).get("type") or "")
        if typ in EXPLAIN_TYPES:
            good += 1
            if re.search(r"\bleaving\b|\bleft over\b|\bremain", t, re.I) and typ not in ("compare", "scale", "steps",
                                                                                       "measure"):
                problems.append(f"scene {i} shows one number for a leftover; show the whole, the used part and "
                                "the rest side by side")
        else:
            problems.append(f"scene {i} is a {typ or 'plain'} picture for a line that explains maths")
    return {"score": round(good / need, 2) if need else None, "problems": problems[:4]}


def summary(nar):
    """One plain line for reports."""
    if not nar:
        return "story not checked"
    if nar.get("hard"):
        return f"story check failed ({len(nar['hard'])} problem(s)), score {nar.get('score')}"
    return f"story check passed, score {nar.get('score')}"
