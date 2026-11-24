"""Learning from real viewers: where they left, line by line, and whether the quality score predicts results.

YouTube's audience-retention curve (YouTube Analytics, elapsedVideoTimeRatio x audienceWatchRatio, read-only) is laid
over each Short's own line timings, so every script line gets the share of viewers it lost and how fast. A line that
lost viewers much faster than the rest of its Short is a "leak". Leaks become lessons for the writer, and once 3+
measured Shorts agree on a kind of line (hook, setup, math, re-hook, payoff, loop), the advice names it.

Honest limits: YouTube shows a curve only once a Short has enough views, so many Shorts have none; those are listed
as "not enough data", never guessed. The share of people who swiped away in Studio's Shorts feed ("viewed vs swiped
away") isn't in the API, so the closest real number used is how many were still watching at 10% (hook hold).

score_review()  after each measured upload: the weighted quality score (qa.quality, made before upload) next to the
                real performance index (analytics.index_all, 50 = typical). With 5+ Shorts: how well the score and
                each of its parts ranked the real results. A suggestion only: the score never gates a Short, and its
                weights are only changed by an update Connor approves.

    python -m studio retention"""
import json
import statistics
import time

from studio import db

LEAK_FACTOR = 1.5          # a line losing viewers this many times faster than its Short's typical line
LEAK_MIN_DROP = 0.03       # and at least 3 points of the audience
ADVICE_MIN_SHORTS = 3      # Shorts that must agree before the writer is told about a kind of line
SCORE_MIN = 5              # measured Shorts before the score check says anything
BEAT_NAMES = {"hook": "opening (hook)", "setup": "setup", "math": "calculation", "rehook": "re-hook",
              "payoff": "payoff", "loop": "last (loop)", "": "unlabelled"}


def _curve(youtube_id):
    from studio import analytics
    r = analytics.latest(youtube_id, "retention")
    return (r or {}).get("metrics", {}).get("curve") or []


def _at(curve, x):
    """The share still watching at x (0-1) on the curve, read between its points."""
    if not curve:
        return None
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0, *_), (x1, y1, *_) in zip(curve, curve[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


def lines_of(video_id):
    """[{"n", "text", "beat", "start", "end"}] for the Short's script lines as rendered, or [] without timings."""
    v = db.video(video_id)
    if not v:
        return []
    meta = json.loads(v["meta"] or "{}")
    rend = meta.get("render") or {}
    starts, dur = rend.get("line_starts"), rend.get("duration")
    row = db.one("SELECT hook, structure FROM scripts WHERE video_id=? AND status='passed' ORDER BY version DESC LIMIT 1",
                 video_id)
    if not starts or not dur or not row:
        return []
    st = json.loads(row["structure"] or "{}")
    lines = st.get("lines") or []
    if len(starts) != len(lines) + 1:
        return []
    out = []
    for i, ln in enumerate(lines):
        out.append({"n": i + 1, "text": row["hook"] if i == 0 and row["hook"] else ln.get("text", ""),
                    "beat": str(ln.get("beat") or ("hook" if i == 0 else "")), "start": float(starts[i]),
                    "end": float(starts[i + 1])})
    return out


def by_line(video_id, curve, duration):
    """Each line with "kept_from", "kept_to", "lost" (points of the audience) and "per_sec", plus "leak"."""
    rows = []
    for ln in lines_of(video_id):
        a, b = _at(curve, ln["start"] / duration), _at(curve, min(1.0, ln["end"] / duration))
        secs = max(0.3, ln["end"] - ln["start"])
        if a is None or b is None:
            continue
        lost = max(0.0, a - b)
        rows.append(dict(ln, kept_from=round(a, 3), kept_to=round(b, 3), lost=round(lost, 3),
                         per_sec=round(lost / secs, 4)))
    if not rows:
        return []
    typical = statistics.median(r["per_sec"] for r in rows) or 1e-9
    for r in rows:
        r["leak"] = r["lost"] >= LEAK_MIN_DROP and r["per_sec"] >= LEAK_FACTOR * typical
        r["vs_typical"] = round(r["per_sec"] / typical, 2) if typical > 1e-9 else None
    return rows


def analyze(now=None, idx=None):
    """Line-by-line view for every measured Short that has a curve; new leaks become lessons once per Short."""
    from studio import analytics, lessons, topics
    now = now or time.time()
    idx = idx if idx is not None else analytics.index_all(now)
    done, no_curve, no_timing = 0, [], []
    for yid, r in idx.items():
        vid = r.get("video_id")
        if not vid:
            continue
        curve = _curve(yid)
        if not curve:
            no_curve.append(vid)
            continue
        dur = (json.loads((db.video(vid) or {}).get("meta") or "{}").get("render") or {}).get("duration")
        rows = by_line(vid, curve, float(dur)) if dur else []
        if not rows:
            no_timing.append(vid)
            continue
        db.put(f"retention:{vid}", {"lines": rows, "at": now, "youtube_id": yid})
        done += 1
        if not db.get(f"retention_learned:{vid}"):
            t = topics.topic(r["topic_id"]) if r.get("topic_id") else None
            leaks = [x for x in rows if x["leak"]]
            lessons.record("retention", [f"retention: viewers left fastest during the {x['beat'] or 'unlabelled'} "
                                         f"line \"{x['text'][:120]}\" ({100 * x['lost']:.0f} points in "
                                         f"{x['end'] - x['start']:.1f} s, Short #{vid})" for x in leaks], t, vid)
            db.put(f"retention_learned:{vid}", now)
    summary = beat_summary()
    db.put("retention_summary", dict(summary, no_curve=no_curve, no_timing=no_timing, at=now))
    return {"analyzed": done, "no_curve": no_curve, "no_timing": no_timing, **summary}


def beat_summary():
    """{"beats": {beat: {"shorts", "leaks", "vs_typical"}}, "shorts": n} over every analysed Short."""
    beats, n = {}, 0
    for r in db.q("SELECT key, value FROM kv WHERE key LIKE 'retention:%'"):
        rows = json.loads(r["value"]).get("lines") or []
        if not rows:
            continue
        n += 1
        seen = {}
        for x in rows:
            b = x.get("beat") or ""
            d = seen.setdefault(b, {"leak": False, "vs": []})
            d["leak"] = d["leak"] or bool(x.get("leak"))
            if x.get("vs_typical") is not None:
                d["vs"].append(x["vs_typical"])
        for b, d in seen.items():
            agg = beats.setdefault(b, {"shorts": 0, "leaks": 0, "vs": []})
            agg["shorts"] += 1
            agg["leaks"] += int(d["leak"])
            agg["vs"] += d["vs"]
    out = {b: {"shorts": a["shorts"], "leaks": a["leaks"],
               "vs_typical": round(statistics.mean(a["vs"]), 2) if a["vs"] else None} for b, a in beats.items()}
    return {"beats": out, "shorts": n}


def advice():
    """One line for the writer once 3+ measured Shorts agree on where viewers leave (else "")."""
    s = db.get("retention_summary") or {}
    worst = [(b, a) for b, a in (s.get("beats") or {}).items()
             if a["leaks"] >= ADVICE_MIN_SHORTS and (a.get("vs_typical") or 0) >= 1.2]
    if not worst:
        return ""
    worst.sort(key=lambda ba: -(ba[1]["vs_typical"] or 0))
    names = ", ".join(f"{BEAT_NAMES.get(b, b)} lines ({a['leaks']} of {a['shorts']} Shorts)" for b, a in worst[:2])
    return (f"Real viewers of this channel leave fastest during {names}: keep those lines short and concrete, "
            "with a number or a question in each, and reach the payoff sooner.")


def text():
    s = db.get("retention_summary") or {}
    out = []
    for r in sorted(db.q("SELECT key, value FROM kv WHERE key LIKE 'retention:%'"), key=lambda r: r["key"]):
        d = json.loads(r["value"])
        vid = r["key"].split(":", 1)[1]
        leaks = [x for x in d.get("lines") or [] if x.get("leak")]
        if leaks:
            out.append(f"- Short #{vid}: " + "; ".join(
                f"line {x['n']} ({BEAT_NAMES.get(x['beat'], x['beat'])}) lost {100 * x['lost']:.0f} points in "
                f"{x['end'] - x['start']:.1f} s" for x in leaks[:3]))
        else:
            out.append(f"- Short #{vid}: no line lost viewers much faster than the rest")
    if s.get("no_curve"):
        out.append(f"- Not enough views yet for YouTube to show a retention curve: Short(s) #"
                   + ", #".join(str(v) for v in s["no_curve"]))
    if s.get("no_timing"):
        out.append("- Made before line timings were saved (can't be laid over the curve): Short(s) #"
                   + ", #".join(str(v) for v in s["no_timing"]))
    if not out:
        return "No measured Shorts yet: a Short is read once it has been public for 3 days.\n"
    adv = advice()
    out.append("- Told to the writer: " + adv if adv else
               f"- Nothing told to the writer yet (it needs {ADVICE_MIN_SHORTS}+ Shorts that agree)")
    return "\n".join(out) + "\n"


# ---------- does the quality score predict real results? ----------

def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(xs, ys):
    """Rank correlation, -1..1 (None with fewer than 3 pairs or no spread)."""
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 2) if den else None


def score_review(idx=None):
    """Predicted (quality score made before upload) vs real (performance index) for every measured Short."""
    from studio import analytics
    idx = idx if idx is not None else analytics.index_all()
    rows = []
    for yid, r in idx.items():
        v = db.video(r.get("video_id")) if r.get("video_id") else None
        q = (json.loads(v["meta"] or "{}").get("quality") or {}) if v else {}
        if r.get("index") is None or q.get("score") is None:
            continue
        rows.append({"video_id": r["video_id"], "predicted": q["score"], "real": r["index"], "parts": q.get("parts") or {}})
    out = {"n": len(rows), "shorts": sorted(rows, key=lambda x: x["video_id"])}
    if len(rows) >= SCORE_MIN:
        out["score"] = spearman([x["predicted"] for x in rows], [x["real"] for x in rows])
        parts = {}
        for name in sorted({k for x in rows for k in x["parts"]}):
            pairs = [(x["parts"][name], x["real"]) for x in rows if name in x["parts"]]
            if len(pairs) >= SCORE_MIN:
                parts[name] = spearman([a for a, _ in pairs], [b for _, b in pairs])
        out["parts"] = parts
    db.put("score_review", out)
    return out


def score_text():
    s = db.get("score_review") or {}
    rows = s.get("shorts") or []
    if not rows:
        return "No Short has both a quality score and real results yet.\n"
    out = [f"- Short #{x['video_id']}: predicted {x['predicted']:.0f}, real index {x['real']:.0f}"
           + (" (better than predicted)" if x["real"] - 50 > x["predicted"] - 50 + 15 else
              " (worse than predicted)" if x["real"] - 50 < x["predicted"] - 50 - 15 else "") for x in rows[-10:]]
    if s.get("score") is None:
        out.append(f"- How well the score ranks real results: needs {SCORE_MIN}+ measured Shorts (have {len(rows)})")
    else:
        sc = s["score"]
        word = "well" if sc >= 0.5 else "a little" if sc >= 0.2 else "not at all" if sc > -0.2 else "backwards"
        out.append(f"- The score ranks real results {word} (rank correlation {sc:+.2f} over {len(rows)} Shorts)")
        good = [k for k, c in (s.get("parts") or {}).items() if c is not None and c >= 0.3]
        weak = [k for k, c in (s.get("parts") or {}).items() if c is not None and c < 0.1]
        if good:
            out.append("- Parts that predicted results: " + ", ".join(good))
        if weak:
            out.append("- Parts with no link to results so far (a weight change would be suggested, never made "
                       "silently): " + ", ".join(weak))
    return "\n".join(out) + "\n"
