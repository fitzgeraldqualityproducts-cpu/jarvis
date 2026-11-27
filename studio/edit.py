"""The edit: what moves on screen and what you hear, beyond the pictures and the voice. One list of cues drives
both the picture (Remotion) and the sound effects (studio/sound.py), so a whoosh always lands on its cut.

cues(...)      [{"t", "kind", "scene"}]: kinds
                 "cut"     a new scene starts (a soft whoosh)
                 "count"   the counter starts counting up (soft ticks while it runs, "until" = when it stops)
                 "pop"     the counter lands on its number (a small punch-in and a click)
                 "rehook"  the re-hook line (a quick light flash, with a short rising swell into it)
                 "payoff"  the payoff line (a punch-in, a short shake and a low hit)
                 "jump"    a jump zoom halfway through a long scene, on a word, so the screen never sits
                           still for more than about 3 seconds
overlays(...)  {scene index: overlay} animated diagrams over the picture in the cinematic look: growing bars for
               a comparison, a log scale for sizes far apart, a calculation that builds term by term, numbered
               steps that light up in turn, a standing wave with its peaks (hot spots) marked and the gap between
               them labelled, a ruler that grows between two things. Placed between the counter and the caption,
               inside the safe area. Reusable for any topic: Claude fills them from the line, nothing is per-topic.
"""
import math
import re

JUMP_AFTER = 3.2          # a scene longer than this gets a jump zoom near its middle
# Numbers on screen (Connor's Video 35 audit): a count runs only from a start the viewer already saw, takes at
# most COUNT_MAX seconds, and the final value then stays still for at least HOLD_MIN seconds (same as Short.tsx).
COUNT_SHARE = 0.3         # share of the scene a count may take...
COUNT_MAX = 0.8           # ...and never longer than this
HOLD_MIN = 1.5            # the final value stays readable this long after a count
READ_MIN = 1.0            # a number that just appears is on screen at least this long


def count_seconds(span):
    """How long a counter counts in a scene of span seconds (0 when the scene is too short to also hold it)."""
    c = min(COUNT_MAX, span * COUNT_SHARE)
    return round(c, 3) if span - c >= HOLD_MIN else 0.0
OVERLAY = {"x": 80, "y": 760, "w": 850, "h": 380}       # inside video.BOX (y 250-1150), above the caption


def _num(v):
    try:
        return float(str(v).replace(",", "").strip().split()[0])
    except (ValueError, IndexError):
        return None


def _fmt(v):
    if v is None:
        return ""
    if abs(v) >= 100 or v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def beats_of(script_lines, n):
    b = [str((ln or {}).get("beat") or "") for ln in (script_lines or [])][:n]
    return b + [""] * (n - len(b))


def cues(starts, beats, huds=None, words=None):
    """The edit's cues. starts: scene start times plus the end time; beats: one per scene; huds: video.huds()."""
    out = []
    n = len(starts) - 1
    words = words or []
    for k in range(n):
        s0, s1 = starts[k], starts[k + 1]
        span = max(0.5, s1 - s0)
        if k > 0:
            out.append({"t": round(s0, 3), "kind": "cut", "scene": k})
        h = (huds or [None] * n)[k] if huds else None
        mid_taken = False
        counts = count_seconds(span)
        if h and h.get("num") is not None and h.get("from") is not None and h["from"] != h["num"] and k > 0 \
                and counts:
            until = s0 + counts
            out.append({"t": round(s0, 3), "kind": "count", "scene": k, "until": round(until, 3)})
            out.append({"t": round(until, 3), "kind": "pop", "scene": k})
        elif h and h.get("num") is not None and not h.get("carried") and k > 0:
            out.append({"t": round(s0 + 0.05, 3), "kind": "pop", "scene": k})      # the number lands
        if beats[k] == "rehook" and k > 0:
            out.append({"t": round(s0, 3), "kind": "rehook", "scene": k})
        if beats[k] == "payoff" and k > 0:
            out.append({"t": round(s0 + 0.05, 3), "kind": "payoff", "scene": k})
        if span > JUMP_AFTER and not mid_taken and 0 < k < n - 1:     # never in the last scene (the loop blend)
            mid = s0 + span / 2
            ws = [float(w["s"]) for w in words if s0 + 1.2 < float(w.get("s") or 0) < s1 - 1.0]
            t = min(ws, key=lambda x: abs(x - mid)) if ws else mid
            out.append({"t": round(t, 3), "kind": "jump", "scene": k})
    return sorted(out, key=lambda c: c["t"])


def board_cues(cue_list, scenes):
    """The storyboard's sound and transition for each cut: a cut cue gets "sfx" (whoosh, pop, tick, rise or
    none) and "transition" (cut, fade or zoom) from its scene. Scenes without them keep the usual whoosh."""
    out = []
    for c in cue_list:
        c = dict(c)
        sc = scenes[c["scene"]] if c.get("kind") == "cut" and 0 <= c.get("scene", -1) < len(scenes) else None
        if isinstance(sc, dict):
            if sc.get("sfx") and sc["sfx"] != "whoosh":
                c["sfx"] = sc["sfx"]
            if sc.get("transition") in ("fade", "zoom"):
                c["transition"] = sc["transition"]
        out.append(c)
    return out


def _items(scene):
    items = []
    for it in (scene.get("items") or [])[:4]:
        if isinstance(it, dict):
            v = _num(it.get("value"))
            if v is not None and v >= 0:
                items.append({"label": " ".join(str(it.get("label") or "").split())[:24], "v": v})
    return items


WAVE_PEAKS = 4            # a standing wave shows this many peaks (antinodes) across the panel


def _whole_words(v, n):
    """The words of v that fit in n characters (at least the first word, cut to n)."""
    out = ""
    for w in str(v or "").split():
        if len(out) + len(w) + (1 if out else 0) > n:
            break
        out = f"{out} {w}" if out else w
    return out or str(v or "").strip()[:n]


def _short(v, n):
    return " ".join(str(v or "").split())[:n]


def wave_geometry(w, peaks=WAVE_PEAKS, span="gap"):
    """Where a standing wave's peaks sit across a panel w px wide, and the two the span arrow joins:
    {"peaks": [x...], "nodes": [x...], "a": x, "b": x}. Neighbouring peaks are half a wavelength apart, so
    "gap" joins peaks 1 and 2 and "wavelength" joins peaks 1 and 3 (a full wave is two gaps)."""
    step = w / peaks
    xs = [round((i + 0.5) * step, 1) for i in range(peaks)]
    nodes = [round(i * step, 1) for i in range(peaks + 1)]
    j = 2 if span == "wavelength" and peaks >= 3 else 1
    return {"peaks": xs, "nodes": nodes, "a": xs[0], "b": xs[j]}


def overlay(scene):
    """One scene's animated diagram (or None): {"kind": "bars"|"steps"|"equation"|"wave"|"measure", ...}
    with positions in px."""
    t = scene.get("type")
    unit = _whole_words(scene.get("unit"), 22)     # never cut mid-word ("11 x farther th" in Short 17)
    if t in ("compare", "scale"):
        items = _items(scene)
        if len(items) < 2:
            return None
        top = max(i["v"] for i in items) or 1.0
        lo = min(i["v"] for i in items if i["v"] > 0) if any(i["v"] > 0 for i in items) else top
        log = t == "scale" or top / max(lo, 1e-12) > 40          # sizes far apart: a log scale, so the small one shows
        for i in items:
            if log and i["v"] > 0 and top > lo:
                i["frac"] = round(0.12 + 0.88 * (math.log10(i["v"]) - math.log10(lo)) / max(1e-9, math.log10(top) - math.log10(lo)), 3)
            else:
                i["frac"] = round(max(0.04, i["v"] / top), 3)
            i["text"] = f"{_fmt(i['v'])} {unit}".strip()
            i["top"] = i["v"] == top
        return {"kind": "bars", "items": items, "log": log, **OVERLAY,
                "note": "log scale" if log and t != "scale" else ""}
    if t == "equation":
        left = " ".join(str(scene.get("left") or "").split())[:40]
        right = " ".join(str(scene.get("right") or "").split())[:20]
        if not left or not right:
            return None
        terms = [x for x in re.split(r"\s+", left) if x][:9]
        return {"kind": "equation", "terms": terms, "right": right, **OVERLAY}
    if t == "steps":
        items = [" ".join(str(i).split())[:40] for i in (scene.get("items") or [])[:4] if str(i).strip()]
        if len(items) < 2:
            return None
        return {"kind": "steps", "items": items, **OVERLAY}
    if t == "wave":
        span = "wavelength" if str(scene.get("span") or "").lower().startswith("wave") else "gap"
        inner = OVERLAY["w"] - 68
        return {"kind": "wave", "span": span, "value": _short(scene.get("value"), 16),
                "spot": _short(scene.get("spot"), 18) or "peak", "label": _short(scene.get("label"), 40),
                "geo": wave_geometry(inner, WAVE_PEAKS, span), **OVERLAY}
    if t == "temperature":
        v = _num(scene.get("value"))
        if v is None:
            return None
        lo, hi = _num(scene.get("low")), _num(scene.get("high"))
        lo = lo if lo is not None and lo < v else math.floor(min(v, 0) / 10) * 10 - 10
        hi = hi if hi is not None and hi > v else math.ceil(max(v, 0) / 10) * 10 + 10
        unit = _short(scene.get("unit"), 4) or "°C"
        return {"kind": "thermo", "value": f"{_fmt(v) if v >= 0 else '-' + _fmt(-v)} {unit}".strip(),
                "frac": round((v - lo) / (hi - lo), 3), "low": f"{_fmt(lo) if lo >= 0 else '-' + _fmt(-lo)}{unit}",
                "high": f"{_fmt(hi) if hi >= 0 else '-' + _fmt(-hi)}{unit}", "label": _short(scene.get("label"), 44),
                "cold": v < 0, **OVERLAY}
    if t == "orbit":
        return orbit_overlay(scene)
    if t == "measure":
        value = _short(scene.get("value"), 14)
        a, b = _short(scene.get("from"), 18), _short(scene.get("to"), 18)
        if not value or not a:
            return None
        return {"kind": "measure", "from": a, "to": b, "value": f"{value} {_short(scene.get('unit'), 10)}".strip(),
                "label": _short(scene.get("label"), 40), **OVERLAY}
    return None


# ---------- the hands-on demonstration, drawn (Video 35 audit) ----------
ORBIT = {"x": 80, "y": 300, "w": 850, "h": 820}         # a bigger panel: the demonstration IS the picture here
PHASE_WORDS = {"new": 180.0, "full": 0.0, "first": 90.0, "last": 270.0, "quarter": 90.0, "start": 0.0}
ORBIT_LABELS = {"lamp": "lamp = Sun", "you": "you = Earth", "ball": "ball = Moon", "marker": "note = far-side spot"}


def phase_angle(v, default=90.0):
    """Where the ball is on its circle, in degrees (180 = between you and the lamp, 0 = behind you)."""
    if v is None or str(v).strip() == "":
        return default
    t = str(v).lower()
    for k, a in PHASE_WORDS.items():
        if k in t:
            return a
    n = _num(v)
    return float(n) % 360 if n is not None else default


def orbit_overlay(scene):
    """A top-down demonstration: the lamp far off on the left, you in the middle, the ball on its circle turning
    once per circle (so the same side always faces you), the marked spot drawn on it and lit only when it faces
    the lamp. The ball moves from "from" to "to" over the scene ("to": "cycle" = one whole circle); "split" also
    colours the half of the circle where the spot is in sunlight."""
    from studio import demo
    marker = str(scene.get("marker") or "far").lower()
    marker = marker if marker in ("far", "near", "none") else "far"
    a0 = phase_angle(scene.get("from"), 0.0)
    to = str(scene.get("to") or "").lower()
    a1 = a0 + 360.0 if "cycle" in to or "lap" in to or "circle" in to else phase_angle(scene.get("to"), a0)
    if a1 < a0:
        a1 += 360.0                                  # always forward, the way the Moon goes
    labels = dict(ORBIT_LABELS)
    given = scene.get("labels") if isinstance(scene.get("labels"), dict) else {}
    for k in labels:
        v = _short(given.get(k), 26)
        if v:
            labels[k] = v
    if marker == "none":
        labels.pop("marker")
    spot = (labels.get("marker") or "spot").split("=")[0].strip() or "spot"
    return {"kind": "orbit", "a0": round(a0, 1), "a1": round(a1, 1), "marker": marker,
            "split": bool(scene.get("split")), "labels": labels,
            "litText": _short(f"{spot} in sunlight", 26), "darkText": _short(f"{spot} in darkness", 26),
            "litEnd": marker != "none" and demo.lit(a1 % 360, marker),
            "label": _short(scene.get("label"), 44), **ORBIT}


def shown_values(hud, span, fps=30):
    """Every number text the counter shows in a scene, frame by frame, the way Short.tsx draws it."""
    if not hud or hud.get("num") is None:
        return [hud["value"]] if hud else []
    frm, num = hud.get("from", hud["num"]), hud["num"]
    if frm == num:
        return [hud["value"]]
    c = count_seconds(span)
    out = []
    for f in range(int(span * fps)):
        t = f / fps
        k = min(1.0, t / c) if c else 1.0
        x = frm + (num - frm) * (1 - (1 - k) ** 3)
        v = hud["value"] if k >= 1 else f"{x:,.{int(hud.get('dec') or 0)}f}" + (f" {hud['unit']}" if hud.get("unit") else "")
        if not out or out[-1] != v:
            out.append(v)
    return out


def number_motion(hud_list, starts, approved=None):
    """Problems with the numbers on screen (Connor's rules): [str]. A count must start from a number the viewer
    already saw (or one in approved), be quick, and the final value must hold long enough to read, with its unit."""
    probs, seen = [], []
    approved = [float(a) for a in (approved or [])]
    for k, h in enumerate(hud_list or []):
        if not h or h.get("num") is None:
            continue
        span = (starts[k + 1] - starts[k]) if k + 1 < len(starts) else 0.0
        unit = str(h.get("unit") or "")
        if unit and unit not in str(h.get("value")):
            probs.append(f"scene {k + 1}: {h.get('value')} lost its unit ({unit})")
        if not h.get("carried"):
            if h.get("from") is not None and h["from"] != h["num"]:
                known = seen + approved
                if not any(abs(h["from"] - x) <= 1e-9 * max(1.0, abs(x)) for x in known):
                    probs.append(f"scene {k + 1}: counts up from {h['from']:g}, a value the viewer never saw, so it "
                                 f"flashes in-between numbers before {h['value']}")
                elif not count_seconds(span):
                    probs.append(f"scene {k + 1}: counts in a {span:.1f} s scene, leaving no time to read "
                                 f"{h['value']}")
            if span and span < READ_MIN:
                probs.append(f"scene {k + 1}: {h['value']} is on screen for only {span:.1f} s")
            seen.append(h["num"])
    return probs


def overlays(scenes, look="hud"):
    """{scene index: overlay} for the cinematic look (never on the hook scene, which carries the title)."""
    if look != "hud":
        return {}
    out = {}
    for k, sc in enumerate(scenes):
        if k == 0 or not isinstance(sc, dict):
            continue
        try:
            ov = overlay(sc)
        except Exception:
            ov = None
        if ov:
            out[k] = ov
    return out


def overlay_box(ov):
    return (ov["x"], ov["y"], ov["x"] + ov["w"], ov["y"] + ov["h"])
