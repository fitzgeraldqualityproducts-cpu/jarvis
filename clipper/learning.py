"""Learning from real results, carefully.

Every published clip's numbers (views, average % viewed, likes, shares, subscribers gained) are
compared within the same channel. For each choice the clipper makes (hook type, topic, caption
style, length, framing, publish hour) it works out how clips with that choice did against the
channel's typical clip, shrunk toward "no difference" when there are few clips (so 3 lucky videos
don't change anything), with an honest label:
    "likely real"   at least 8 clips and the gap is over twice its uncertainty
    "maybe"         a gap, but it could easily be chance
    "not enough data"
Only "likely real" findings nudge future scores, by at most 0.5 points (and 0.75 in total) on the
10-point scale. Context checks, accuracy and permissions are applied before any nudge and can't
be overridden by it. This is correlation, not proof: the report says so."""
import math
import time

from clipper import db, moments, settings

FEATURES = ["hook_type", "topic", "caption_style", "duration", "layout", "hour"]
MIN_N = 8
SHRINK = 5.0


def _rows(channel):
    rows = db.q("""SELECT c.id, c.hook_type, c.topic, c.caption_style, c.duration, c.layout, c.publish_at, c.updated,
                          m.views, m.avg_view_pct, m.likes, m.shares, m.subs_gained, m.fetched
                   FROM clips c JOIN metrics m ON m.id = (SELECT id FROM metrics WHERE clip_id=c.id ORDER BY fetched DESC LIMIT 1)
                   WHERE c.channel=? AND c.youtube_id IS NOT NULL""", channel)
    return rows


def _features(r):
    hour = None
    if r.get("publish_at"):
        try:
            hour = int(str(r["publish_at"])[11:13])
        except ValueError:
            hour = None
    return {"hook_type": r.get("hook_type") or "other", "topic": (r.get("topic") or "").lower() or "unknown",
            "caption_style": r.get("caption_style") or "unknown", "duration": moments.duration_bucket(r.get("duration") or 0),
            "layout": r.get("layout") or "unknown", "hour": f"{hour:02d}:00" if hour is not None else "unknown"}


def performance(r):
    """One number per clip: half how much of it people watched, half how many people saw it (log)."""
    pct = float(r.get("avg_view_pct") or 0) / 100.0
    views = math.log10(float(r.get("views") or 0) + 1)
    eng = (float(r.get("likes") or 0) + 3 * float(r.get("shares") or 0) + 10 * float(r.get("subs_gained") or 0)) / \
        max(1.0, float(r.get("views") or 0))
    return pct * 2.0 + views * 0.5 + min(1.0, eng * 20)


def analyze(channel="main", last=None):
    rows = _rows(channel)
    if last:
        rows = sorted(rows, key=lambda r: -(r["id"]))[:int(last)]
    if not rows:
        return {"n": 0, "features": {}, "best": [], "worst": []}
    perf = [performance(r) for r in rows]
    mean = sum(perf) / len(perf)
    sd = (sum((p - mean) ** 2 for p in perf) / max(1, len(perf) - 1)) ** 0.5 or 1.0
    z = [(p - mean) / sd for p in perf]
    out = {}
    for f in FEATURES:
        groups = {}
        for r, zz in zip(rows, z):
            groups.setdefault(_features(r)[f], []).append(zz)
        table = {}
        for k, vals in groups.items():
            n = len(vals)
            raw = sum(vals) / n
            shrunk = raw * n / (n + SHRINK)
            se = 1.0 / math.sqrt(n)
            label = "not enough data" if n < 3 else ("likely real" if n >= MIN_N and abs(raw) > 2 * se else "maybe")
            table[k] = {"n": n, "effect": round(shrunk, 3), "raw": round(raw, 3), "label": label}
        out[f] = table
    ranked = sorted(zip(rows, perf), key=lambda x: -x[1])
    return {"n": len(rows), "features": out, "mean": mean,
            "best": [r["id"] for r, _ in ranked[:5]], "worst": [r["id"] for r, _ in ranked[-5:]]}


def preferences(channel="main"):
    """Score nudges and style picks for future clips (only from 'likely real' findings)."""
    try:
        learned = settings.channel(channel).get("learned") or {}
    except KeyError:
        return {}
    return learned.get("prefs", {})


def update(channel="main"):
    """Recomputes the channel's learned preferences from its results."""
    a = analyze(channel)
    prefs = {}
    for f in ("hook_type", "topic", "duration"):
        nudges = {k: round(max(-0.5, min(0.5, v["effect"] * 0.6)), 2)
                  for k, v in a["features"].get(f, {}).items() if v["label"] == "likely real"}
        if nudges:
            prefs[f] = nudges
    for f, key in (("caption_style", "caption_style"), ("layout", "layout")):
        real = {k: v for k, v in a["features"].get(f, {}).items() if v["label"] == "likely real" and k != "unknown"}
        if real:
            best = max(real.items(), key=lambda kv: kv[1]["effect"])
            if best[1]["effect"] > 0:
                prefs[key] = best[0]
    settings.update_channel(channel, learned={"prefs": prefs, "updated": time.time(), "n": a["n"]})
    return prefs


def use_best_style(channel="main"):
    """'Use the highest-performing editing style': sets the channel's caption style to the best one,
    and says how sure that is."""
    a = analyze(channel)
    table = {k: v for k, v in a["features"].get("caption_style", {}).items() if k != "unknown"}
    if not table:
        return "I don't have performance numbers for any caption style yet."
    best, v = max(table.items(), key=lambda kv: kv[1]["effect"])
    settings.update_channel(channel, caption_style=best)
    sure = {"likely real": "and that looks like a real difference",
            "maybe": "but that could still be chance",
            "not enough data": "but there's too little data to be sure"}[v["label"]]
    return f"Switched to {best} captions. They've done best across {v['n']} clips, {sure}."


def report(channel="main", last=20):
    a = analyze(channel, last)
    if a["n"] == 0:
        return "There are no performance numbers yet. Once clips are published and YouTube Analytics is connected, I'll track them."
    parts = [f"Looking at {a['n']} published clips."]
    best = db.clip(a["best"][0]) if a["best"] else None
    if best:
        m = db.one("SELECT views, avg_view_pct FROM metrics WHERE clip_id=? ORDER BY fetched DESC LIMIT 1", best["id"])
        parts.append(f"Best: \"{best['title']}\" with {m['views']} views and {m['avg_view_pct'] or 0:.0f}% watched.")
    names = {"hook_type": "opening", "caption_style": "caption style", "duration": "length", "layout": "framing", "hour": "upload time"}
    for f, label in names.items():
        t = {k: v for k, v in a["features"].get(f, {}).items() if v["n"] >= 3 and k != "unknown"}
        if len(t) < 2:
            continue
        k, v = max(t.items(), key=lambda kv: kv[1]["effect"])
        if v["effect"] > 0.05:
            parts.append(f"{label.capitalize()}: {k.replace('_', ' ')} does best ({v['label']}, {v['n']} clips).")
    parts.append("These are patterns, not proof that one choice caused the difference.")
    return " ".join(parts)
