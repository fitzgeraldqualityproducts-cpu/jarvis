"""The pre-production gate (Connor's Video 35 audit): checked after the visual plan and before any picture is
painted, so a problem is fixed in the one scene it sits in instead of making the whole Short again.

run(structure, hook, scenes, offered) -> (scenes, report)
    report = {"ok", "problems": [{"area", "scene", "reason", "fix"}], "fixed": [...], "storyboard": [...]}
    area is one of scientific, narrative, visual, technical. Every problem names its exact reason and the fix
    that was made ("fix") or that is needed. Free: plain Python, no Claude call.

Scientific  the script's own demonstration and cycle checks (studio/demo.py) and every on-screen calculation
            re-worked ("29.53 ÷ 2 ≈ 14.8 days" really is 29.53 / 2, rounded as shown).
Narrative   the hook asks a question, the payoff line exists, every scene has its line.
Visual      every line of a hands-on demonstration is drawn as the demonstration (the orbit diagram), its lit or
            dark ending matches the line, numbers on screen come from the checked script, and every claim has a
            visual that explains it (the storyboard below).
Technical   the export is set to 1080x1920 at the configured frame rate and the pictures' real size is known.

storyboard(scenes, lines, structure) -> [{scene, narration, concept, visual, framing, motion, on_screen_text,
    numbers, understanding, transition}] one entry per scene, saved with the video (meta["storyboard"])."""
import math
import re

from studio import demo

EXPLAINS = ("equation", "compare", "scale", "steps", "measure", "wave", "temperature", "number", "orbit")
CLAIM_BEATS = ("setup", "math", "payoff")
STOPWORDS = {"that", "this", "these", "those", "your", "with", "from", "into", "than", "then", "they", "there",
             "what", "when", "where", "which", "about", "just", "only", "over", "each", "every", "it's", "that's",
             "does", "have", "will", "would", "could", "much", "many", "more", "most", "some"}


def _words(t):
    return {w for w in re.findall(r"[a-z']+", str(t or "").lower()) if len(w) > 3 and w not in STOPWORDS}


def _nums(t):
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", str(t or ""))]


# ---------- the calculation on screen ----------

def _expr(left):
    e = str(left or "").replace("÷", "/").replace("×", "*").replace("−", "-").replace(",", "")
    e = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "*", e)
    return e if re.fullmatch(r"[\d\s.+\-*/()]+", e) else None


def calc_problem(scene):
    """None, or (the reason, the corrected right-hand side) for an equation card whose answer isn't its sum."""
    if not isinstance(scene, dict) or scene.get("type") != "equation":
        return None
    e = _expr(scene.get("left"))
    right = str(scene.get("right") or "")
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", right)
    if not e or not m:
        return None
    try:
        from studio import script
        val = float(script.calc(e))
    except Exception:
        return None
    shown = float(m.group(0).replace(",", ""))
    dec = len(m.group(0).split(".")[1]) if "." in m.group(0) else 0
    if abs(val - shown) <= 0.5 * 10 ** -dec + 1e-9 or math.isclose(val, shown, rel_tol=0.005):
        return None
    fixed = right[:m.start()] + (f"{val:,.{dec}f}") + right[m.end():]
    if not re.search(r"≈|~|about", fixed) and round(val, dec) != val:
        fixed = "≈ " + fixed.lstrip("= ")
    return (f"the card says {scene.get('left')} = {m.group(0)}, but it works out to {val:g}", fixed)


# ---------- the demonstration, drawn ----------

def _labels(dm):
    out = {}
    for o in dm.get("objects") or []:
        if not isinstance(o, dict):
            continue
        thing, means = str(o.get("thing") or "").strip(), str(o.get("stands_for") or "").strip()
        if not thing or not means:
            continue
        means = re.sub(r"^(the|a|an) ", "", means, flags=re.I)
        role = "lamp" if re.search(r"lamp|torch|flashlight|bulb|light|candle", thing, re.I) else \
            "ball" if re.search(r"ball|orange|apple|globe|marble|melon|grapefruit", thing, re.I) else None
        if role:
            out[role] = f"{thing.split()[-1].lower()} = {means}"[:26]
    obs = dm.get("observer")
    if obs:
        means = str(obs.get("stands_for") if isinstance(obs, dict) else obs)
        means = re.sub(r"^(you|the viewer)\s*(=|is|are|:)?\s*", "", means, flags=re.I)
        means = re.sub(r"^(an? |the )?(observer|someone|person|viewer) on ", "", means, flags=re.I)
        if means:
            out["you"] = f"you = {means}"[:26]
    mk = dm.get("marker")
    if isinstance(mk, dict) and mk.get("thing"):
        side = demo.marker_side(dm)
        what = "far-side spot" if side == "far" else "near-side spot" if side == "near" else \
            re.sub(r"^(a|the) ", "", str(mk.get("stands_for") or "spot"))
        out["marker"] = f"{str(mk['thing']).split()[-1].lower()} = {what}"[:26]
    return out


def demo_ready(structure):
    """The script's demonstration, when it is one the orbit diagram can draw (a ball circling the viewer, turning
    once per circle, lit by a lamp), else None."""
    model = structure.get("model") if isinstance(structure.get("model"), dict) else {}
    dm = demo.spec(model)
    if not dm or not demo.synchronous(dm):
        return None
    things = " ".join(str(o.get("thing") or "") for o in dm.get("objects") or [] if isinstance(o, dict))
    if not re.search(r"lamp|torch|flashlight|bulb|light|candle", things, re.I) or \
            not re.search(r"ball|orange|apple|globe|marble|melon|grapefruit", things, re.I):
        return None
    return dm


def demo_scenes(scenes, texts, structure, beats=None):
    """The scenes with every line of the demonstration drawn as the orbit diagram, each ending where its line says
    the spot is lit or dark. Returns (scenes, [fixes])."""
    dm = demo_ready(structure)
    if not dm:
        return scenes, []
    side = demo.marker_side(dm) or "far"
    labels = _labels(dm)
    used = set(demo.uses_model(texts)) | {i for i, t in enumerate(texts, 1) if demo.position(t)}
    out, fixes, at = list(scenes), [], "full"     # it starts beyond you, the marked spot facing away
    for i in sorted(used):
        k = i - 1
        if k <= 0 or k >= len(out) or not isinstance(out[k], dict) or (beats and k < len(beats) and
                                                                      beats[k] == "loop"):
            continue                                 # the hook keeps its title frame, the loop line its way back
        sc, t = out[k], texts[k]
        if sc.get("type") not in ("headline", "steps", "orbit", None):
            continue                                 # a number, sum or comparison already explains its line
        pos, said = demo.position(t), demo.claim(t)
        if pos:
            frm, to = at, pos
        elif demo.TURN.search(t) or re.search(r"\b(circle|orbit|lap|around you)\b", t, re.I):
            frm, to = at, "cycle"
        else:
            frm = to = at
        end = to if to != "cycle" else frm
        if said and side in ("far", "near"):
            truth = "lit" if demo.lit(demo.PHASES.get(end, 90.0), side) else "dark"
            if truth != said:
                end = to = "new" if (said == "lit") == (side == "far") else "full"
        new = dict(sc, type="orbit", **{"from": frm, "to": to}, marker=side, labels=labels,
                   label=" ".join(str(sc.get("label") or sc.get("text") or "").split())[:44])
        new.pop("shots", None)                       # the diagram is the shot; the picture behind it stays
        if sc.get("type") != "orbit" or (sc.get("from"), sc.get("to")) != (frm, to):
            fixes.append(f"scene {i}: drawn as the demonstration (ball {frm} to {to}, marker on the {side} side)")
        out[k] = new
        at = end
    return out, fixes


# ---------- the storyboard ----------

def _visual(sc):
    t = sc.get("type")
    if t == "orbit":
        return (f"demonstration from above: ball circling you from {sc.get('from')} to {sc.get('to')}, marker on the "
                f"{sc.get('marker', 'far')} side")
    if t == "equation":
        return f"calculation card: {sc.get('left')} {sc.get('right')}"
    if t in ("compare", "scale"):
        return f"{'bars' if t == 'compare' else 'sizes'}: " + ", ".join(
            f"{i.get('label')} {i.get('value')}" for i in sc.get("items") or [] if isinstance(i, dict))
    if t == "number":
        return f"the number {sc.get('value')} {sc.get('unit') or ''}".strip()
    if t == "steps":
        return "numbered steps: " + "; ".join(str(x) for x in sc.get("items") or [])
    return ("real photo of " + sc["real"]) if sc.get("real") else f"picture: {sc.get('art') or sc.get('text') or ''}"


def storyboard(scenes, lines, structure=None):
    """One storyboard entry per scene (Connor's fields)."""
    model = (structure or {}).get("model") if isinstance((structure or {}).get("model"), dict) else {}
    board = []
    for k, sc in enumerate(scenes):
        sc = sc if isinstance(sc, dict) else {}
        ln = lines[k] if k < len(lines) and isinstance(lines[k], dict) else {"text": str(lines[k]) if k < len(lines)
                                                                              else ""}
        shots = sc.get("shots") or []
        on_screen = [str(sc.get(f)) for f in ("text", "label", "left", "right", "value") if sc.get(f)]
        if sc.get("type") == "orbit":
            on_screen += list((sc.get("labels") or {}).values())
        board.append({
            "scene": k + 1,
            "narration": ln.get("text", ""),
            "concept": ln.get("learn") or (sc.get("purpose") or "").split(":", 1)[-1].strip() or
                       (model.get("question") if k == 0 else ""),
            "visual": _visual(sc),
            "framing": "centred diagram over the picture" if sc.get("type") in EXPLAINS else
                       (shots[0].get("camera") if shots and isinstance(shots[0], dict) else "push-in"),
            "motion": ("the ball moves round its circle, turning so the same side faces you"
                       if sc.get("type") == "orbit" else "the card builds term by term" if sc.get("type") == "equation"
                       else "bars grow" if sc.get("type") in ("compare", "scale") else
                       ", ".join(str(s.get("camera")) for s in shots if isinstance(s, dict)) or "slow push-in"),
            "on_screen_text": on_screen,
            "numbers": _nums(" ".join(on_screen)),
            "understanding": ln.get("learn") or "",
            "transition": (scenes[k + 1].get("transition") if k + 1 < len(scenes) and isinstance(scenes[k + 1], dict)
                           else "loop back to the first frame") or "cut",
        })
    return board


def coverage(scenes, lines):
    """[(scene, reason)] lines that make a claim but whose scene only decorates (a picture that shares nothing
    with the line and isn't a real photo or a diagram)."""
    out = []
    for k, sc in enumerate(scenes):
        if k == 0 or not isinstance(sc, dict):
            continue
        ln = lines[k] if k < len(lines) and isinstance(lines[k], dict) else {}
        if str(ln.get("beat") or "") not in CLAIM_BEATS:
            continue
        if sc.get("type") in EXPLAINS or sc.get("real"):
            continue
        pic = " ".join([str(sc.get("art") or ""), str(sc.get("text") or ""), str(sc.get("label") or "")] +
                       [str(s.get("art") or "") + " " + str(s.get("shows") or "") for s in sc.get("shots") or []
                        if isinstance(s, dict)])
        if not _words(pic) & _words(ln.get("text")):
            out.append((k + 1, f"line {k + 1} (\"{str(ln.get('text'))[:60]}\") has no visual that shows it"))
    return out


# ---------- the gate ----------

def run(structure, hook, scenes, offered=True, size=None, fps=None):
    """The gate. offered: the orbit diagram may be used (it ships with the other new looks)."""
    lines = [dict(ln) for ln in structure.get("lines") or [] if isinstance(ln, dict)]
    if lines and hook:
        lines[0] = dict(lines[0], text=hook)
    texts = [ln.get("text", "") for ln in lines]
    probs, fixed = [], []
    scenes = [dict(s) if isinstance(s, dict) else s for s in scenes]
    # scientific: the script's own checks, re-read (scripts made before them get a note, never a stop here)
    model = structure.get("model") if isinstance(structure.get("model"), dict) else None
    for it in demo.check(lines, model) + demo.cycles(lines):
        probs.append({"area": "scientific", "scene": it.get("line"), "reason": it["text"],
                      "fix": demo.ROOT.get(it["kind"], ("", ""))[1], "hard": it["hard"]})
    for k, sc in enumerate(scenes):
        cp = calc_problem(sc)
        if cp:
            scenes[k] = dict(sc, right=cp[1])
            fixed.append(f"scene {k + 1}: {cp[0]}; the card now says {cp[1]}")
    # narrative
    if texts and not re.search(r"\?|\b(how|why|what|which|when|where|does|is|can|could)\b", texts[0], re.I):
        probs.append({"area": "narrative", "scene": 1, "reason": "the hook doesn't raise a question",
                      "fix": "open with the question the Short answers", "hard": False})
    if not any(str(ln.get("beat")) == "payoff" for ln in lines):
        probs.append({"area": "narrative", "scene": None, "reason": "no payoff line answers the question",
                      "fix": "mark the line that answers it as the payoff", "hard": False})
    if len(scenes) != len(texts):
        probs.append({"area": "narrative", "scene": None, "reason": f"{len(scenes)} scenes for {len(texts)} lines",
                      "fix": "one scene per line", "hard": True})
    # visual
    if offered:
        scenes, f = demo_scenes(scenes, texts, structure, [str(ln.get("beat") or "") for ln in lines])
        fixed += f
    elif demo_ready(structure):
        probs.append({"area": "visual", "scene": None, "reason": "the demonstration can't be drawn (the orbit diagram "
                      "waits for Connor's OK of the new looks)", "fix": "run with --new-looks", "hard": False})
    for n, why in coverage(scenes, lines):
        nums = _nums(texts[n - 1])
        if nums and scenes[n - 1].get("type") == "headline":
            from studio import video
            scenes[n - 1] = dict(scenes[n - 1], **video._fallback(texts[n - 1]))
            fixed.append(f"scene {n}: {why}; it now shows the line's number")
        else:
            probs.append({"area": "visual", "scene": n, "reason": why, "fix": "show the line's object or a diagram",
                          "hard": False})
    # technical
    from studio import video
    want = (1080, 1920)
    have = size or (video.W, video.H)
    if tuple(have) != want:
        probs.append({"area": "technical", "scene": None, "reason": f"the export is set to {have[0]}x{have[1]}",
                      "fix": "render at 1080x1920", "hard": True})
    if (fps or video.FPS) != video.FPS:
        probs.append({"area": "technical", "scene": None, "reason": f"the frame rate is {fps}, not {video.FPS}",
                      "fix": f"render at {video.FPS} fps", "hard": True})
    rep = {"ok": not any(p["hard"] for p in probs), "problems": probs, "fixed": fixed,
           "storyboard": storyboard(scenes, lines, structure)}
    return scenes, rep
