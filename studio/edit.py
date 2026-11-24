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
               steps that light up in turn. Placed between the counter and the caption, inside the safe area.
"""
import math
import re

JUMP_AFTER = 3.2          # a scene longer than this gets a jump zoom near its middle
COUNT_SHARE = 0.7         # the counter counts over this share of the scene (same as Short.tsx HudBox)
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
        if h and h.get("num") is not None and h.get("from") is not None and h["from"] != h["num"] and k > 0:
            until = s0 + max(0.4, span * COUNT_SHARE)
            out.append({"t": round(s0, 3), "kind": "count", "scene": k, "until": round(until, 3)})
            out.append({"t": round(until, 3), "kind": "pop", "scene": k})
            mid_taken = span * COUNT_SHARE > JUMP_AFTER / 2
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


def _items(scene):
    items = []
    for it in (scene.get("items") or [])[:4]:
        if isinstance(it, dict):
            v = _num(it.get("value"))
            if v is not None and v >= 0:
                items.append({"label": " ".join(str(it.get("label") or "").split())[:24], "v": v})
    return items


def overlay(scene):
    """One scene's animated diagram (or None): {"kind": "bars"|"steps"|"equation", ...} with positions in px."""
    t = scene.get("type")
    unit = " ".join(str(scene.get("unit") or "").split())[:12]
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
    return None


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
