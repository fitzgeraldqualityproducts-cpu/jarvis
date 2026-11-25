"""Titles and first frames that learn from real results.

YouTube's API gives no click-through rate or impressions for Shorts, so packaging is judged on what it does
report: the performance index (50 = typical for the channel) and the hook hold (the share of viewers still
watching 10% in, the closest thing to "didn't swipe away").

style(title)        what kind of title it is: question, number, you (talks to the viewer), comparison, statement
frame_style(frame)  what the first frame looks like: dark/bright, calm/busy, number on screen
record(video_id)    saves both with the Short when its upload details are made
learn(idx)          after each analytics run: the mean index and hook hold for every style, with how many Shorts
advice()            a line for the writer once a style is clearly ahead (at least MIN_SHORTS Shorts on each side
                    and a gap of INDEX_GAP index points, or HOLD_GAP hook hold for first frames)
pick(titles)        the writer's titles in order, the style that's clearly ahead first (unchanged until then)"""
import json
import re
import statistics

from studio import db

MIN_SHORTS = 3
INDEX_GAP = 8.0
HOLD_GAP = 0.05
TITLE_STYLES = ("question", "number", "you", "comparison", "statement")
NAMES = {"question": "titles asked as a question", "number": "titles with a number in them",
         "you": "titles that talk to the viewer (\"you\")", "comparison": "titles that compare two things",
         "statement": "plain statement titles", "dark": "a dark first frame", "bright": "a bright first frame",
         "busy": "a busy first frame (lots of detail)", "calm": "a simple, uncluttered first frame",
         "number on screen": "a big number on screen in the first frame"}


def style(title):
    t = str(title or "").strip()
    low = t.lower()
    out = []
    if "?" in t or re.match(r"(why|how|what|which|who|where|when|can|could|is|are|do|does|would|will)\b", low):
        out.append("question")
    if re.search(r"\d", t):
        out.append("number")
    if re.search(r"\byou(r|'re)?\b", low):
        out.append("you")
    if re.search(r"\b(vs\.?|versus|than|compared)\b", low):
        out.append("comparison")
    return out or ["statement"]


def frame_style(frame, hook=""):
    """Labels for a first frame (BGR array)."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (270, 480))
    lum = float(np.mean(small))
    edges = float(np.mean(cv2.Canny(small, 80, 160) > 0))
    out = ["dark" if lum < 70 else "bright" if lum > 140 else "mid", "busy" if edges > 0.09 else "calm"]
    if re.search(r"\d", str(hook)):
        out.append("number on screen")
    return out


def record(video_id, title, path, hook=""):
    """Saves the title's and first frame's styles with the Short. Returns them."""
    from studio import qa
    pk = {"title": style(title)}
    try:
        first, _ = qa.edge_frames(path)
        if first is not None:
            pk["frame"] = frame_style(first, hook)
    except Exception:
        pass
    v = db.video(video_id)
    meta = json.loads(v["meta"] or "{}")
    db.set_video(video_id, meta=json.dumps(dict(meta, packaging=pk)))
    return pk


def learn(idx):
    """Mean index / hook hold per style over the measured Shorts. Saves and returns
    {"title": {style: {...}}, "frame": {...}, "n": shorts}."""
    rows = []
    for r in (idx or {}).values():
        if r.get("index") is None or not r.get("video_id"):
            continue
        v = db.video(r["video_id"])
        pk = json.loads((v or {}).get("meta") or "{}").get("packaging") if v else None
        if pk:
            rows.append((pk, float(r["index"]), r.get("hook_hold")))
    out = {"n": len(rows), "openings": _openings(idx)}
    for part in ("title", "frame"):
        labels = sorted({lb for pk, _, _ in rows for lb in pk.get(part) or []})
        d = {}
        for lb in labels:
            yes = [(i, h) for pk, i, h in rows if lb in (pk.get(part) or [])]
            no = [(i, h) for pk, i, h in rows if part in pk and lb not in (pk.get(part) or [])]
            d[lb] = {"n": len(yes), "index": _mean([i for i, _ in yes]), "hold": _mean([h for _, h in yes]),
                     "n_other": len(no), "index_other": _mean([i for i, _ in no]), "hold_other": _mean([h for _, h in no])}
        out[part] = d
    db.put("studio_packaging", out)
    return out


OPENINGS = 3                       # how many best-holding openings the writer and the hook rater see


def _openings(idx):
    """The opening lines of measured Shorts, best hook hold (share still watching 10% in) first."""
    out = []
    for r in (idx or {}).values():
        if r.get("hook_hold") is None or not r.get("video_id"):
            continue
        s = db.one("SELECT hook FROM scripts WHERE video_id=? AND status='passed' ORDER BY version DESC LIMIT 1",
                   r["video_id"])
        if s and s["hook"]:
            out.append({"hook": s["hook"], "hold": round(float(r["hook_hold"]), 3), "video_id": r["video_id"]})
    return sorted(out, key=lambda o: -o["hold"])


def best_openings(n=OPENINGS):
    """The n openings that held viewers best, once at least MIN_SHORTS Shorts have a measured hook hold (and the
    best clearly beat the typical one); [] until then."""
    ops = (db.get("studio_packaging", {}) or {}).get("openings") or []
    if len(ops) < MIN_SHORTS:
        return []
    mid = statistics.median(o["hold"] for o in ops)
    return [o for o in ops[:n] if o["hold"] >= mid + HOLD_GAP / 2] or []


def openings_advice():
    best = best_openings()
    if not best:
        return ""
    return ("Openings that kept the most viewers past the first seconds on this channel: "
            + "; ".join(f"\"{b['hook']}\" ({100 * b['hold']:.0f}% still watching)" for b in best)
            + ". Give every hook that same kind of pull (never the same words or topic).")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def _clear(s, key, gap):
    """+1 / -1 when this style is clearly ahead / behind the rest, else 0."""
    a, b = s.get(key), s.get(key + "_other")
    hold_n = s["n"] >= MIN_SHORTS and s["n_other"] >= MIN_SHORTS
    if not hold_n or a is None or b is None:
        return 0
    return 1 if a - b >= gap else -1 if b - a >= gap else 0


def winners():
    """{"title": [ahead styles], "frame": [ahead labels], "behind": [...]} from the last learn()."""
    d = db.get("studio_packaging", {}) or {}
    out = {"title": [], "frame": [], "behind": []}
    for lb, s in (d.get("title") or {}).items():
        c = _clear(s, "index", INDEX_GAP)
        (out["title"] if c > 0 else out["behind"] if c < 0 else []).append(lb)
    for lb, s in (d.get("frame") or {}).items():
        c = _clear(s, "hold", HOLD_GAP)
        (out["frame"] if c > 0 else out["behind"] if c < 0 else []).append(lb)
    return out


def advice():
    d = db.get("studio_packaging", {}) or {}
    w = winners()
    lines = []
    for lb in w["title"]:
        s = d["title"][lb]
        lines.append(f"{NAMES.get(lb, lb).capitalize()} did better: index {s['index']:.0f} over {s['n']} Shorts vs "
                     f"{s['index_other']:.0f} for the rest")
    for lb in w["frame"]:
        s = d["frame"][lb]
        lines.append(f"Shorts opening on {NAMES.get(lb, lb)} kept more viewers past the start: {100 * s['hold']:.0f}% vs "
                     f"{100 * s['hold_other']:.0f}% over {s['n']} Shorts")
    if not lines:
        return ""
    return ("Packaging that worked on this channel (real results; there's no click-through data for Shorts): "
            + "; ".join(lines) + ". Lean toward it in your titles and the hook's visual, without forcing it.")


def pick(titles):
    """The titles, best learned style first (a stable sort, so nothing changes without clear evidence)."""
    w = winners()
    ahead, behind = set(w["title"]), set(w["behind"])

    def rank(t):
        st = set(style(t))
        return -len(st & ahead) + len(st & behind)
    return sorted(titles, key=rank)
