"""Niche research and selection (Phase 3).

    research(run_id)  for every candidate niche:
        1. MEASURED (YouTube Data API, public data): the top recent Shorts for 2 searches, their
           views, length and channels; then the leading channels' last 30 uploads (median views,
           consistency, upload rate, channel age, subscribers).
        2. RESEARCHED (Claude on Connor's plan with web search, sources required): monetization,
           sustainability, free production feasibility, risks, original angles, saturation.
       Everything raw is saved in studio_data/research/<run_id>/evidence.json.
    score(evidence)   deterministic: the same evidence always gives the same scores (reproducible).
    report(run_id)    the Top 10 report (Markdown + HTML), with evidence, sources and confidence.
    choose(run_id)    picks ONE winner, defines pillars, audience and positioning, and saves it.
                      A niche, once chosen, is only replaced with force=True and a written reason.

Every number says where it came from: "measured" (API, with the retrieval time), "estimate"
(researched, with source URLs), "assumption" (a stated rule), or "missing". Nothing is made up:
no RPM, CPM or competitor earnings are invented; Shorts and long-form money are kept apart."""
import html
import json
import math
import statistics
import time
from pathlib import Path

from studio import config, data_dir, db, guard

WEIGHTS = {"monetization": 0.25, "demand": 0.20, "competition": 0.15, "viral": 0.15,
           "sustainability": 0.15, "feasibility": 0.10}
NAMES = {"monetization": "Monetization potential", "demand": "Audience demand", "competition": "Competition opportunity",
         "viral": "Viral potential", "sustainability": "Long-term sustainability", "feasibility": "Free production feasibility"}

# Candidates (id, name, two searches). Claude may add up to 4 more it finds evidence for.
CANDIDATES = [
    ("ai", "Artificial intelligence explained", ["AI explained", "how AI works"]),
    ("tech", "Technology and gadgets explained", ["how technology works", "tech facts"]),
    ("business", "Business stories and strategy", ["business explained", "how companies make money"]),
    ("finance", "Personal finance education", ["personal finance tips", "money explained"]),
    ("psychology", "Psychology and human behavior", ["psychology facts", "human behavior explained"]),
    ("science", "Science explained", ["science explained", "science facts"]),
    ("history", "History stories", ["history facts", "history explained"]),
    ("geography", "Geography and maps", ["geography facts", "maps explained"]),
    ("facts", "Interesting facts", ["interesting facts", "did you know"]),
    ("engineering", "Engineering and how things are built", ["engineering explained", "how it's made engineering"]),
    ("economics", "Economics and luxury", ["economics explained", "why luxury is expensive"]),
    ("productivity", "Productivity", ["productivity tips", "productivity hacks"]),
    ("selfdev", "Personal development", ["self improvement", "personal development tips"]),
    ("stories", "True storytelling", ["true story", "storytime unbelievable"]),
    ("space", "Space and astronomy", ["space facts", "astronomy explained"]),
    ("nature", "Nature and animals", ["animal facts", "nature facts"]),
    ("consumer", "Consumer education", ["consumer tips", "what companies dont tell you"]),
    ("trends", "Emerging trends and future tech", ["future technology", "emerging technology"]),
    ("health_science", "Body and health science (non-medical-advice)", ["human body facts", "how the body works"]),
    ("language", "Words and language origins", ["word origins", "etymology"]),
]

CLAUDE_FIELDS = """For EACH niche give an object with:
"id", "monetization" {"score": 0-100, "shorts": "what Shorts revenue depends on here", "long_form": "long-form ad potential", "advertiser_friendly": "yes/limited/no and why", "sources": [urls]},
"sustainability" {"score": 0-100, "evergreen_share": "rough share of evergreen topics", "topic_depth": "how many distinct Shorts this niche can support", "sources": [urls]},
"feasibility" {"score": 0-100, "visuals": "what visuals can be made with code (charts, maps, diagrams, kinetic text) or public-domain/CC images", "blockers": "what would need footage we can't legally get", "inauthentic_risk": "low/medium/high risk under YouTube's inauthentic-content rule and why"},
"demand_note", "saturation": "how crowded and with what", "original_angles": [3 concrete angles competitors miss],
"risks": [2-4], "publishing": "a realistic sustainable cadence for one Mac", "audience": "who watches (US, English)",
"confidence": "low/medium/high"
Score honestly. Do not invent RPM/CPM numbers or channel earnings; if you cite a figure, it must be on a page you opened and you must give its URL. Monetization for Shorts and long-form must be described separately."""


def _now():
    return time.time()


def run_dir(run_id):
    return data_dir("research", run_id)


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _clamp(x):
    return max(0.0, min(100.0, x))


# ---------- 1. measured evidence ----------

def measure(niche_id, queries, progress=print):
    """Public YouTube numbers for one niche. Returns a dict (all values measured, with time)."""
    from studio import ytpublic
    found = {}
    for q in queries:
        for v in ytpublic.search_shorts(q, days=30, order="viewCount", limit=50):
            found.setdefault(v["video_id"], v)
    stats = ytpublic.videos(list(found))
    shorts = {k: v for k, v in stats.items() if v["seconds"] and v["seconds"] <= 180}
    chans = ytpublic.channels(sorted({v["channel_id"] for v in shorts.values()}))
    by_chan = {}
    for v in shorts.values():
        by_chan.setdefault(v["channel_id"], []).append(v["views"])
    leaders = sorted(by_chan, key=lambda c: -sum(by_chan[c]))[:8]
    comp = []
    for cid in leaders:
        ch = chans.get(cid) or {}
        if not ch.get("uploads"):
            continue
        ids = ytpublic.recent_uploads(ch["uploads"], 30)
        recent = ytpublic.videos(ids)
        views = [r["views"] for r in recent.values()]
        dates = sorted(r["published"] for r in recent.values())
        weeks = None
        if len(dates) >= 2:
            span = (time.mktime(time.strptime(dates[-1][:19], "%Y-%m-%dT%H:%M:%S")) -
                    time.mktime(time.strptime(dates[0][:19], "%Y-%m-%dT%H:%M:%S"))) / (7 * 86400)
            weeks = round(len(dates) / max(span, 1 / 7), 2)
        mean = statistics.mean(views) if views else 0
        comp.append({"channel_id": cid, "title": ch.get("title"), "subs": ch.get("subs"), "created": ch.get("created"),
                     "videos": ch.get("videos"), "recent_n": len(views), "recent_median": _median(views),
                     "recent_mean": round(mean) if views else None,
                     "consistency": round(_median(views) / mean, 3) if views and mean else None,
                     "uploads_per_week": weeks,
                     "shorts_share": round(sum(1 for r in recent.values() if r["seconds"] <= 180) / len(recent), 2) if recent else None})
    progress(f"  {niche_id}: {len(shorts)} recent Shorts, {len(chans)} channels, {len(comp)} competitors measured")
    return {"retrieved": _now(), "queries": queries, "videos": shorts, "channels": chans, "competitors": comp}


def metrics(m):
    """Summary numbers from measured evidence (or {} when there is none)."""
    if not m or not m.get("videos"):
        return {}
    vids, chans = list(m["videos"].values()), m["channels"]
    views = sorted((v["views"] for v in vids), reverse=True)
    subs = lambda cid: (chans.get(cid) or {}).get("subs")
    big = [v for v in vids if (subs(v["channel_id"]) or 0) >= 1_000_000]
    hits = [v for v in vids if v["views"] >= 500_000]
    small_hits = [v for v in hits if subs(v["channel_id"]) is not None and subs(v["channel_id"]) < 100_000]
    now = m["retrieved"]
    young = [c for c in chans.values() if c.get("created") and
             now - time.mktime(time.strptime(c["created"][:19], "%Y-%m-%dT%H:%M:%S")) < 548 * 86400]
    outliers = [v for v in vids if subs(v["channel_id"]) and v["views"] > subs(v["channel_id"])]
    comp = m.get("competitors") or []
    return {
        "sample": len(vids), "channels": len(chans),
        "median_views": _median(views), "top10_median_views": _median(views[:10]),
        "share_over_1m": round(sum(1 for x in views if x >= 1_000_000) / len(views), 3),
        "big_channel_share": round(len(big) / len(vids), 3),
        "small_channel_hit_share": round(len(small_hits) / len(hits), 3) if hits else 0.0,
        "young_channel_share": round(len(young) / len(chans), 3) if chans else 0.0,
        "outlier_share": round(len(outliers) / len(vids), 3),
        "avg_seconds": round(statistics.mean(v["seconds"] for v in vids), 1),
        "competitor_recent_median": _median([c["recent_median"] for c in comp]),
        "competitor_consistency": _median([c["consistency"] for c in comp]),
        "competitor_uploads_per_week": _median([c["uploads_per_week"] for c in comp]),
    }


# ---------- 2. researched evidence ----------

def research_qualitative(niches, progress=print):
    """Claude (web search on) researches niches in batches of 4. {id: {...}}"""
    from studio import claude
    out = {}
    for i in range(0, len(niches), 4):
        batch = niches[i:i + 4]
        listing = "\n".join(f"- {nid}: {name}" for nid, name, _ in batch)
        prompt = ("You are researching YouTube Shorts niches for a NEW faceless, original, educational/entertainment "
                  "Shorts channel for a US English audience. It will use AI narration, code-made graphics and "
                  "public-domain/CC images only, made on one 8 GB Mac, at $0. Research with web search (official "
                  "YouTube pages, industry reports, reputable articles from 2025-2026).\n\nNiches:\n" + listing +
                  "\n\n" + CLAUDE_FIELDS + "\n\nAnswer with ONLY a JSON array.")
        ans = claude.ask_json(prompt, timeout=900, web=True, purpose=f"niche research {[b[0] for b in batch]}")
        for item in ans if isinstance(ans, list) else []:
            if isinstance(item, dict) and item.get("id"):
                out[item["id"]] = item
        progress(f"  researched {', '.join(b[0] for b in batch)}")
    return out


# ---------- 3. scoring (deterministic) ----------

def _log_score(x, lo, hi):
    if not x:
        return 0.0
    return _clamp((math.log10(max(x, 1)) - lo) / (hi - lo) * 100)


def _q(item, key):
    v = (item or {}).get(key) or {}
    try:
        return float(v.get("score")) if isinstance(v, dict) and v.get("score") is not None else None
    except (TypeError, ValueError):
        return None


def score_one(nid, mm, qual):
    """{factor: {score, basis, confidence, evidence}} and the weighted overall score."""
    f = {}
    n = mm.get("sample", 0) if mm else 0
    mconf = "high" if n >= 40 else "medium" if n >= 15 else "low"
    qconf = (qual or {}).get("confidence", "low") if qual else "missing"
    if mm:
        f["demand"] = {"score": round(_log_score(mm["median_views"], 3, 7), 1), "basis": "measured", "confidence": mconf,
                       "evidence": f"median {int(mm['median_views'] or 0):,} views across {n} top Shorts from the last 30 days"}
        comp = 100 * (0.40 * (1 - mm["big_channel_share"]) + 0.35 * min(mm["small_channel_hit_share"] * 2, 1)
                      + 0.25 * min(mm["young_channel_share"] * 2, 1))
        f["competition"] = {"score": round(_clamp(comp), 1), "basis": "measured", "confidence": mconf,
                            "evidence": f"{mm['big_channel_share']:.0%} of top Shorts from 1M+ subscriber channels; "
                                        f"{mm['small_channel_hit_share']:.0%} of 500k+ hits from channels under 100k; "
                                        f"{mm['young_channel_share']:.0%} of channels under 18 months old"}
        viral = 100 * (0.6 * min(mm["share_over_1m"] * 2, 1) + 0.4 * min(mm["outlier_share"] * 2, 1))
        f["viral"] = {"score": round(_clamp(viral), 1), "basis": "measured", "confidence": mconf,
                      "evidence": f"{mm['share_over_1m']:.0%} of top Shorts passed 1M views; "
                                  f"{mm['outlier_share']:.0%} got more views than their channel has subscribers"}
    else:
        for k in ("demand", "competition", "viral"):
            f[k] = {"score": None, "basis": "missing", "confidence": "none", "evidence": "no YouTube data for this run"}
    for k in ("monetization", "feasibility"):
        s = _q(qual, k)
        f[k] = {"score": s, "basis": "estimate" if s is not None else "missing", "confidence": qconf if s is not None else "none",
                "evidence": (qual or {}).get(k) or {}}
    s = _q(qual, "sustainability")
    cons = mm.get("competitor_consistency") if mm else None
    if s is not None and cons is not None:
        blended = 0.7 * s + 0.3 * _clamp(cons * 100 / 0.6)       # median/mean of 0.6+ = steady, not one-hit
        f["sustainability"] = {"score": round(blended, 1), "basis": "estimate + measured", "confidence": qconf,
                               "evidence": {"researched": qual.get("sustainability"), "competitor_consistency": cons}}
    else:
        f["sustainability"] = {"score": s, "basis": "estimate" if s is not None else "missing",
                               "confidence": qconf if s is not None else "none", "evidence": (qual or {}).get("sustainability") or {}}
    have = {k: v["score"] for k, v in f.items() if v["score"] is not None}
    weight = sum(WEIGHTS[k] for k in have)
    overall = round(sum(WEIGHTS[k] * have[k] for k in have) / weight, 1) if weight else None
    coverage = round(weight, 2)
    return {"factors": f, "overall": overall, "coverage": coverage}


def score(evidence):
    """Scores every niche in an evidence dict. Pure: same evidence -> same result."""
    out = {}
    for nid, e in evidence["niches"].items():
        s = score_one(nid, metrics(e.get("measured")), e.get("qualitative"))
        s.update(id=nid, name=e["name"], metrics=metrics(e.get("measured")))
        out[nid] = s
    ranked = sorted(out.values(), key=lambda r: (-(r["overall"] or -1), -r["coverage"], r["id"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


# ---------- the run ----------

def research(run_id=None, progress=print, candidates=None, skip_youtube=False):
    """Gathers evidence for every candidate, saves it, scores it, writes the report. Returns run_id."""
    from studio import ytpublic
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    folder = run_dir(run_id)
    path = folder / "evidence.json"
    ev = json.loads(path.read_text()) if path.exists() else {"run_id": run_id, "started": _now(), "niches": {},
                                                               "weights": WEIGHTS, "limits": []}
    cands = candidates or CANDIDATES
    for nid, name, qs in cands:
        ev["niches"].setdefault(nid, {"name": name, "queries": qs})
    yt = not skip_youtube and ytpublic.available()
    if not yt:
        ev["limits"].append("No YouTube research key was available, so demand, competition and viral potential are missing for this run.")
    for nid, name, qs in cands:
        e = ev["niches"][nid]
        if yt and not e.get("measured"):
            try:
                e["measured"] = measure(nid, qs, progress)
            except Exception as err:
                e["measured_error"] = guard.redact(str(err))[:300]
                if "quota" in str(err).lower():
                    ev["limits"].append("YouTube's daily quota ran out during this run; re-run tomorrow to finish.")
                    yt = False
            path.write_text(json.dumps(ev))                           # resumable: finished niches are kept
    todo = [c for c in cands if not ev["niches"][c[0]].get("qualitative")]
    if todo:
        qual = research_qualitative(todo, progress)
        for nid, item in qual.items():
            if nid in ev["niches"]:
                ev["niches"][nid]["qualitative"] = item
    ev["finished"] = _now()
    path.write_text(json.dumps(ev))
    ranked = score(ev)
    db.run("DELETE FROM niche_research WHERE run_id=?", run_id)
    for r in ranked:
        db.insert("INSERT INTO niche_research(run_id, niche, scores, overall, evidence, confidence, created) VALUES(?,?,?,?,?,?,?)",
                  run_id, r["id"], json.dumps(r["factors"]), r["overall"], json.dumps(r["metrics"]),
                  _overall_conf(r), _now())
        for c in ((ev["niches"][r["id"]].get("measured") or {}).get("competitors") or []):
            db.run("INSERT OR REPLACE INTO competitors(run_id, niche, youtube_channel_id, title, stats, fetched) VALUES(?,?,?,?,?,?)",
                   run_id, r["id"], c["channel_id"], c["title"], json.dumps(c), ev["niches"][r["id"]]["measured"]["retrieved"])
    db.put("last_niche_run", run_id)
    report(run_id)
    return run_id


def _overall_conf(r):
    confs = [v["confidence"] for v in r["factors"].values()]
    if "none" in confs or r["coverage"] < 1:
        return "low"
    return "high" if confs.count("high") >= 3 else "medium"


def load(run_id):
    return json.loads((run_dir(run_id) / "evidence.json").read_text())


# ---------- selection ----------

def pick(ranked):
    """The winner: best overall among niches with full coverage; a near-tie (within 3 points) goes
    to the higher-confidence niche, then the higher monetization score."""
    full = [r for r in ranked if r["overall"] is not None and r["coverage"] >= 0.99] or [r for r in ranked if r["overall"]]
    if not full:
        return None
    top = full[0]
    rank = {"high": 2, "medium": 1, "low": 0}
    close = [r for r in full if top["overall"] - r["overall"] <= 3]
    return sorted(close, key=lambda r: (-rank.get(_overall_conf(r), 0), -(r["factors"]["monetization"]["score"] or 0),
                                        -r["overall"]))[0]


def choose(run_id, force=False, reason=""):
    """Saves the winning niche with its pillars, audience and positioning (Claude writes those from
    the evidence). An existing niche is kept unless force=True with a reason."""
    from studio import claude
    current = db.one("SELECT niche FROM channel WHERE id=1")
    if current and current["niche"] and not force:
        return json.loads(db.get("niche_decision") or "null") or {"niche": current["niche"], "kept": True}
    if force and not reason:
        raise ValueError("changing the niche needs a written reason")
    ev = load(run_id)
    ranked = score(ev)
    win = pick(ranked)
    if not win:
        raise RuntimeError("no niche has enough evidence to choose from")
    q = ev["niches"][win["id"]].get("qualitative") or {}
    plan = claude.ask_json(
        "Our YouTube Shorts research picked this niche for a NEW original, faceless, US English channel "
        "(AI narration, code-made graphics, public-domain/CC images, $0, one Mac):\n"
        f"{win['name']}\nEvidence: {json.dumps({'metrics': win['metrics'], 'research': q})[:6000]}\n\n"
        "Define the channel. JSON object with: \"pillars\": [3-7 objects {\"name\", \"description\"}], "
        "\"audience\": one paragraph, \"positioning\": one sentence, \"style\": narration and visual style, "
        "\"value\": what viewers get, \"differentiation\": how it differs from the competitors measured, "
        "\"avoid\": [things that would make it look mass-produced or break YouTube policy]. Be specific.",
        timeout=600, web=False, purpose="define niche pillars")
    decision = {"run_id": run_id, "niche": win["id"], "name": win["name"], "overall": win["overall"],
                "confidence": _overall_conf(win), "chosen": _now(), "plan": plan, "reason": reason or "highest evidence-based score"}
    db.run("UPDATE niche_research SET chosen=CASE WHEN niche=? THEN 1 ELSE 0 END WHERE run_id=?", win["id"], run_id)
    db.run("INSERT INTO channel(id, niche, positioning, updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
           "niche=excluded.niche, positioning=excluded.positioning, updated=excluded.updated",
           win["name"], (plan or {}).get("positioning", ""), _now())
    db.run("UPDATE pillars SET active=0")
    for p in (plan or {}).get("pillars") or []:
        db.run("INSERT INTO pillars(name, description, active, created) VALUES(?,?,1,?) ON CONFLICT(name) DO UPDATE SET "
               "description=excluded.description, active=1", str(p.get("name"))[:80], str(p.get("description"))[:500], _now())
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", _now(), "niche",
              f"chose {win['name']}" + (f" (replacing {current['niche']}: {reason})" if current and current["niche"] else ""),
              json.dumps({"run_id": run_id, "overall": win["overall"], "runner_up": [r["id"] for r in ranked[1:3]]}))
    db.put("niche_decision", json.dumps(decision))
    guard.audit("niche.choose", True, {"niche": win["id"], "run": run_id})
    report(run_id)
    return decision


# ---------- the report ----------

def _fmt(x):
    if x is None:
        return "missing"
    if isinstance(x, float) and x < 1:
        return f"{x:.0%}"
    if isinstance(x, float):
        return f"{x:,.1f}" if x < 100 else f"{x:,.0f}"
    return f"{x:,}" if isinstance(x, int) else str(x)


def _sources(q):
    urls = []
    for k in ("monetization", "sustainability", "feasibility"):
        for u in ((q or {}).get(k) or {}).get("sources") or []:
            if isinstance(u, str) and u.startswith("http") and u not in urls:
                urls.append(u)
    return urls


def report(run_id):
    """Writes report.md and report.html in the run's folder and returns the Markdown path."""
    ev = load(run_id)
    ranked = score(ev)
    decision = json.loads(db.get("niche_decision") or "null")
    if decision and decision.get("run_id") != run_id:
        decision = None
    top = ranked[:10]
    L = [f"# Niche Selection Report ({run_id})", "",
         f"Evidence gathered {time.strftime('%Y-%m-%d %H:%M', time.localtime(ev.get('started', _now())))}. "
         "Scores are 0-100. Weights: " + ", ".join(f"{NAMES[k]} {int(w * 100)}%" for k, w in WEIGHTS.items()) + ".",
         "", "Every score is marked measured (YouTube Data API), estimate (web research with sources), or missing.",
         "Shorts revenue and long-form revenue are described separately; no RPM or earnings figures were invented.", ""]
    for lim in ev.get("limits") or []:
        L.append(f"> Limitation: {lim}")
    if decision:
        p = decision.get("plan") or {}
        L += ["", f"## Decision: {decision['name']}", "", f"Overall {decision['overall']}, confidence {decision['confidence']}.",
              "", f"**Positioning:** {p.get('positioning', '')}", "", f"**Audience:** {p.get('audience', '')}", "",
              f"**Style:** {p.get('style', '')}", "", f"**Value for viewers:** {p.get('value', '')}", "",
              f"**Differentiation:** {p.get('differentiation', '')}", "", "**Content pillars:**"]
        L += [f"- **{x.get('name')}**: {x.get('description')}" for x in p.get("pillars") or []]
        L += ["", "**Avoid:** " + "; ".join(p.get("avoid") or [])]
        win = next((r for r in ranked if r["id"] == decision["niche"]), None)
        if win:
            L += ["", "**Why it beats the others:**"]
            if win["rank"] != 1:
                L.append(f"- It scored within 3 points of #{1} ({ranked[0]['name']}, {ranked[0]['overall']}), so the tie-break "
                         "went to higher confidence, then higher monetization potential.")
            for r in ranked[:4]:
                if r["id"] == win["id"]:
                    continue
                diffs = sorted(((win["factors"][k]["score"] or 0) - (r["factors"][k]["score"] or 0), k) for k in WEIGHTS)
                best, worst = diffs[-1], diffs[0]
                L.append(f"- vs {r['name']} ({r['overall']}): stronger on {NAMES[best[1]].lower()} (+{best[0]:.0f})"
                         + (f", weaker on {NAMES[worst[1]].lower()} ({worst[0]:.0f})" if worst[0] < 0 else ""))
    L += ["", "## Top 10", "", "| # | Niche | Overall | " + " | ".join(NAMES[k] for k in WEIGHTS) + " | Confidence |",
          "|" + "---|" * (len(WEIGHTS) + 4)]
    for r in top:
        L.append(f"| {r['rank']} | {r['name']} | {_fmt(r['overall'])} | " +
                 " | ".join(_fmt(r["factors"][k]["score"]) + f" ({r['factors'][k]['basis']})" for k in WEIGHTS) +
                 f" | {_overall_conf(r)} |")
    for r in top:
        e = ev["niches"][r["id"]]
        q = e.get("qualitative") or {}
        m = r["metrics"]
        L += ["", f"## {r['rank']}. {r['name']} ({_fmt(r['overall'])})", ""]
        if m:
            L += [f"- **Market demand (measured):** median {_fmt(m['median_views'])} views, top-10 median "
                  f"{_fmt(m['top10_median_views'])}, across {m['sample']} Shorts from the last 30 days; average length {m['avg_seconds']} s.",
                  f"- **Competition (measured):** {m['channels']} channels; {m['big_channel_share']:.0%} of top Shorts from 1M+ "
                  f"subscriber channels; {m['small_channel_hit_share']:.0%} of 500k+ hits from channels under 100k; "
                  f"{m['young_channel_share']:.0%} of channels under 18 months old.",
                  f"- **Leading competitors (measured):** recent median views {_fmt(m['competitor_recent_median'])}, consistency "
                  f"(median/mean) {m['competitor_consistency']}, uploads a week {m['competitor_uploads_per_week']}."]
            comps = (e.get("measured") or {}).get("competitors") or []
            if comps:
                L.append("- **Competitors:** " + "; ".join(f"{c['title']} ({_fmt(c['subs'])} subs, median {_fmt(c['recent_median'])})"
                                                           for c in comps[:5]))
        else:
            L.append("- **Market demand / competition:** missing (no YouTube data in this run).")
        mon = q.get("monetization") or {}
        L += [f"- **Saturation:** {q.get('saturation', 'missing')}",
              f"- **Monetization (estimate):** Shorts: {mon.get('shorts', 'missing')} Long-form: {mon.get('long_form', 'missing')} "
              f"Advertiser-friendly: {mon.get('advertiser_friendly', 'missing')}",
              f"- **Shorts suitability / viral potential:** {r['factors']['viral']['evidence']}",
              f"- **Production (estimate):** {(q.get('feasibility') or {}).get('visuals', 'missing')} Blockers: "
              f"{(q.get('feasibility') or {}).get('blockers', 'missing')} Inauthentic-content risk: "
              f"{(q.get('feasibility') or {}).get('inauthentic_risk', 'missing')}",
              "- **Free resources:** Kokoro narration, code-made graphics and charts, public-domain/CC images (NASA, Wikimedia) "
              "where they fit; no paid stock.",
              "- **Original angles:** " + "; ".join(q.get("original_angles") or ["missing"]),
              "- **Risks:** " + "; ".join(q.get("risks") or ["missing"]),
              f"- **Publishing:** {q.get('publishing', 'missing')}",
              f"- **Sustainability (estimate):** {(q.get('sustainability') or {}).get('topic_depth', 'missing')}; evergreen share "
              f"{(q.get('sustainability') or {}).get('evergreen_share', 'missing')}",
              f"- **Audience:** {q.get('audience', 'missing')}"]
        src = _sources(q)
        if src:
            L.append("- **Sources:** " + " ".join(f"<{u}>" for u in src[:8]))
        if e.get("measured"):
            L.append(f"- **YouTube data retrieved:** {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['measured']['retrieved']))} "
                     f"(searches: {', '.join(e['measured']['queries'])})")
    rest = ranked[10:]
    if rest:
        L += ["", "## Also scored", ""] + [f"- {r['name']}: {_fmt(r['overall'])}" for r in rest]
    md = "\n".join(L) + "\n"
    folder = run_dir(run_id)
    (folder / "report.md").write_text(md)
    (folder / "report.html").write_text(_html(md, run_id))
    return folder / "report.md"


def _html(md, run_id):
    import re
    out, table = [], []

    def inline(s):
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        return re.sub(r"&lt;(https?://[^&]+)&gt;", r'<a href="\1">\1</a>', s)
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            if not set(line) <= set("|-"):
                table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        if table:
            out.append("<table>" + "".join("<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{inline(c)}</{'th' if i == 0 else 'td'}>"
                                                             for c in row) + "</tr>" for i, row in enumerate(table)) + "</table>")
            table = []
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("- "):
            out.append(f"<li>{inline(line[2:])}</li>")
        elif line.startswith("> "):
            out.append(f"<p class=note>{inline(line[2:])}</p>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    css = ("body{font:15px/1.5 -apple-system,Helvetica,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#111}"
           "table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left}"
           "th{background:#f2f2f2}.note{background:#fff6d6;padding:6px 10px}li{margin:3px 0}")
    return f"<!doctype html><meta charset=utf-8><title>Niche report {run_id}</title><style>{css}</style>" + "\n".join(out)
