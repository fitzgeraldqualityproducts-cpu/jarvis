"""Analytics and learning (Phase 9): read-only, the locked channel's own videos only, no revenue.

fetch()     for each video the studio published: public counts from the Data API (videos.list, 1 unit
            per 50 videos) and, once YouTube has them (they lag 1-3 days), watch time, average view
            percentage, shares and subscribers from the YouTube Analytics API, plus how many people were
            still watching at ~10% of the Short (the hook). Missing numbers are recorded as missing,
            never guessed or filled with zeros.
channel()   the channel's own totals (subscribers, views), once a day, for the progress report.
learn()     after a video has been public for 3+ days: a performance index (50 = typical for this
            channel), saved on its topic. With 3+ measured videos in a pillar, that pillar's ideas get a
            small, capped nudge (at most 6 points of a 100-point score) in pick_next.
report()    the weekly report (studio_data/reports/week-<date>.md), told to Connor once a week.
maybe_run() the background chore (studio on only, every few hours, cheap to skip)."""
import datetime
import json
import math
import statistics
import time
import urllib.parse

from studio import config, data_dir, db, guard, health, publish

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = ("views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "likes",
           "shares", "subscribersGained", "subscribersLost")
EVERY_HOURS = 6
MEASURE_AFTER_DAYS = 3        # a video's index is computed once it's been public this long
MIN_PER_PILLAR = 3            # measured videos a pillar needs before it gets a nudge
MAX_NUDGE = 6.0               # points (topic scores are 0-100)
WEIGHTS = {"views": 0.5, "avg_view_pct": 0.3, "subs_per_1k": 0.2}


def _date(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d")


def _iso_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def videos():
    """The studio's own uploaded videos on the locked channel."""
    ch = guard.locked_channel()
    if not ch:
        return []
    return [dict(r) for r in db.q("SELECT p.*, v.topic_id FROM published p LEFT JOIN videos v ON v.id=p.video_id "
                                  "WHERE p.channel_id=? ORDER BY p.id", ch["youtube_channel_id"])]


def _save(youtube_id, video_id, source, metrics, unavailable, period="lifetime"):
    db.insert("INSERT INTO analytics(youtube_id, video_id, fetched, period, source, metrics, unavailable) "
              "VALUES(?,?,?,?,?,?,?)", youtube_id, video_id, time.time(), period, source, json.dumps(metrics),
              json.dumps(sorted(unavailable)))


def latest(youtube_id, source):
    r = db.one("SELECT * FROM analytics WHERE youtube_id=? AND source=? ORDER BY fetched DESC, id DESC LIMIT 1",
               youtube_id, source)
    if not r:
        return None
    r = dict(r)
    r["metrics"], r["unavailable"] = json.loads(r["metrics"] or "{}"), json.loads(r["unavailable"] or "[]")
    return r


# ---------- reading YouTube ----------

def _public_counts(vids):
    out = {}
    for i in range(0, len(vids), 50):
        part = vids[i:i + 50]
        r = publish._get("videos", {"part": "statistics,status,snippet", "id": ",".join(v["youtube_id"] for v in part)})
        for it in r.get("items") or []:
            out[it["id"]] = it
    return out


def _report(params):
    status, _, body = publish._http("GET", ANALYTICS + "?" + urllib.parse.urlencode(params), publish._auth())
    return publish._check(status, body, "analytics")


def _video_analytics(v, start):
    """Lifetime numbers for one video, or {} while YouTube hasn't processed any yet."""
    r = _report({"ids": "channel==MINE", "startDate": start, "endDate": _date(time.time()),
                 "metrics": ",".join(METRICS), "filters": f"video=={v['youtube_id']}"})
    rows, heads = r.get("rows") or [], [h["name"] for h in r.get("columnHeaders") or []]
    if not rows:
        return {}
    return {h: rows[0][i] for i, h in enumerate(heads) if rows[0][i] is not None}


def _curve(v, start):
    """YouTube's audience-retention curve for one Short: [[share of the video, share of viewers still watching,
    relative retention or None], ...] (up to 100 points), or [] while YouTube has too few views to show one."""
    r = _report({"ids": "channel==MINE", "startDate": start, "endDate": _date(time.time()),
                 "metrics": "audienceWatchRatio,relativeRetentionPerformance", "dimensions": "elapsedVideoTimeRatio",
                 "filters": f"video=={v['youtube_id']}"})
    heads = [h.get("name") for h in r.get("columnHeaders") or []]
    col = {n: i for i, n in enumerate(heads)}
    out = []
    for row in r.get("rows") or []:
        x = row[col.get("elapsedVideoTimeRatio", 0)]
        w = row[col["audienceWatchRatio"]] if "audienceWatchRatio" in col else (row[1] if len(row) > 1 else None)
        rel = row[col["relativeRetentionPerformance"]] if "relativeRetentionPerformance" in col else None
        if x is not None and w is not None:
            out.append([round(float(x), 3), round(float(w), 4), None if rel is None else round(float(rel), 4)])
    return sorted(out)


def hold_at(curve, at=0.1):
    after = [p for p in curve or [] if p[0] >= at]
    return round(float(after[0][1]), 3) if after else None


def _hook_hold(v, start):
    """Share of viewers still watching at about 10% of the Short (YouTube's own retention curve)."""
    return hold_at(_curve(v, start))


def fetch(now=None):
    """Reads the numbers for every studio video. Returns how many videos were updated."""
    now = now or time.time()
    vids = [v for v in videos() if v.get("youtube_id")]
    if not vids:
        return 0
    guard.allow("youtube.read_video", f"{len(vids)} video(s) statistics", record=False)
    counts = _public_counts(vids)
    done = 0
    for v in vids:
        it = counts.get(v["youtube_id"])
        if not it:
            _save(v["youtube_id"], v["video_id"], "data_api", {}, ["video not found on YouTube"])
            continue
        if it["snippet"]["channelId"] != v["channel_id"]:
            raise guard.WrongChannel("a studio video reports a different channel; stopping analytics")
        st = it.get("statistics") or {}
        m = {k: int(st[k]) for k in ("viewCount", "likeCount", "commentCount") if k in st}
        m["privacy"] = (it.get("status") or {}).get("privacyStatus")
        if m["privacy"] and m["privacy"] != v.get("privacy"):      # e.g. a scheduled Short went public
            db.run("UPDATE published SET privacy=?, published_at=COALESCE(?, published_at) WHERE id=?",
                   m["privacy"], it["snippet"].get("publishedAt"), v["id"])
            v["privacy"] = m["privacy"]
        _save(v["youtube_id"], v["video_id"], "data_api", m,
              [k for k in ("viewCount", "likeCount", "commentCount") if k not in st])
        if m["privacy"] == "public":
            guard.allow("youtube.analytics", f"video {v['youtube_id']}", record=False)
            start = (v.get("published_at") or _date(now))[:10]
            a = _video_analytics(v, start)
            missing = [k for k in METRICS if k not in a]
            if a and (now - (_iso_ts(v.get("published_at")) or now)) >= 2 * 86400:
                curve = _curve(v, start)
                hold = hold_at(curve)
                if hold is None:
                    missing.append("hook_hold")
                else:
                    a["hook_hold"] = hold
                if curve:                                  # kept whole, for the line-by-line view (retention.py)
                    _save(v["youtube_id"], v["video_id"], "retention", {"curve": curve}, [])
            _save(v["youtube_id"], v["video_id"], "analytics", a, missing or [])
        done += 1
    return done


def channel():
    """The channel's own totals (1 unit). Hidden subscriber counts are recorded as unavailable."""
    ch = guard.locked_channel()
    if not ch:
        return None
    guard.allow("youtube.read_channel", "channel totals", record=False)
    r = publish._get("channels", {"part": "statistics", "id": ch["youtube_channel_id"]})
    items = r.get("items") or []
    if not items:
        return None
    st = items[0].get("statistics") or {}
    m = {k: int(st[k]) for k in ("subscriberCount", "viewCount", "videoCount") if k in st and
         not (k == "subscriberCount" and st.get("hiddenSubscriberCount"))}
    _save(ch["youtube_channel_id"], None, "channel", m,
          [k for k in ("subscriberCount", "viewCount", "videoCount") if k not in m])
    return m


# ---------- learning ----------

def _features(v):
    a = (latest(v["youtube_id"], "analytics") or {}).get("metrics") or {}
    d = (latest(v["youtube_id"], "data_api") or {}).get("metrics") or {}
    views = a.get("views", d.get("viewCount"))
    if views is None:
        return None
    subs = (a.get("subscribersGained") or 0) - (a.get("subscribersLost") or 0) if "subscribersGained" in a else None
    return {"views": float(views), "avg_view_pct": a.get("averageViewPercentage"),
            "subs_per_1k": (1000.0 * subs / views) if subs is not None and views else None,
            "hook_hold": a.get("hook_hold"), "avg_view_seconds": a.get("averageViewDuration")}


def _rel(x, med):
    if x is None or med is None:
        return None
    if med <= 0:
        return 1.0 if x <= 0 else 3.0
    return max(0.0, min(3.0, x / med))


def index_all(now=None):
    """{youtube_id: {"index": 0-100 (50 = this channel's typical video), features...}} for videos public 3+ days."""
    now = now or time.time()
    rows = []
    for v in videos():
        pub = _iso_ts(v.get("published_at"))
        if v.get("privacy") != "public" or not pub or now - pub < MEASURE_AFTER_DAYS * 86400:
            continue
        f = _features(v)
        if f:
            rows.append((v, f))
    if not rows:
        return {}
    meds = {}
    for k in WEIGHTS:
        vals = [f[k] for _, f in rows if f.get(k) is not None]
        meds[k] = statistics.median(vals) if vals else None
    out = {}
    for v, f in rows:
        parts = {k: _rel(f.get(k), meds[k]) for k in WEIGHTS}
        have = {k: p for k, p in parts.items() if p is not None}
        w = sum(WEIGHTS[k] for k in have)
        idx = round(min(100.0, 50.0 * sum(WEIGHTS[k] * p for k, p in have.items()) / w), 1) if w else None
        out[v["youtube_id"]] = dict(f, index=idx, video_id=v["video_id"], topic_id=v.get("topic_id"),
                                    title=v["title"], used=sorted(have))
    return out


def _hook_style(hook):
    h = str(hook or "")
    return "question" if "?" in h else ("number" if any(c.isdigit() for c in h) else "statement")


def learn(now=None):
    """Saves each measured video's index on its topic and works out the pillar nudges."""
    from studio import topics
    idx = index_all(now)
    groups = {"pillar": {}, "kind": {}, "hook": {}}
    for yid, r in idx.items():
        if r["index"] is None or not r.get("topic_id"):
            continue
        t = topics.topic(r["topic_id"]) or {}
        perf = {k: r.get(k) for k in ("index", "views", "avg_view_pct", "subs_per_1k", "hook_hold")}
        perf.update(youtube_id=yid, measured=time.time())
        db.run("UPDATE topics SET performance=? WHERE id=?", json.dumps(perf), r["topic_id"])
        s = db.one("SELECT hook FROM scripts WHERE video_id=? AND status='passed' ORDER BY version DESC LIMIT 1",
                   r["video_id"])
        for g, key in (("pillar", t.get("pillar")), ("kind", t.get("kind")), ("hook", _hook_style(s["hook"] if s else ""))):
            if key:
                groups[g].setdefault(key, []).append(r["index"])
    nudges = {}
    for p, vals in groups["pillar"].items():
        if len(vals) >= MIN_PER_PILLAR:
            nudges[p] = round(max(-MAX_NUDGE, min(MAX_NUDGE, (statistics.mean(vals) - 50) / 50 * MAX_NUDGE)), 1)
    old = db.get("studio_pillar_nudges", {}) or {}
    db.put("studio_pillar_nudges", nudges)
    summary = {g: {k: {"n": len(v), "mean": round(statistics.mean(v), 1)} for k, v in d.items()} for g, d in groups.items()}
    db.put("studio_insights", summary)
    if nudges != old:
        db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", time.time(), "learning",
                  "pillar nudges: " + (", ".join(f"{k} {v:+.1f}" for k, v in sorted(nudges.items())) or "none yet"),
                  json.dumps(summary))
    from studio import packaging, retention
    try:
        packaging.learn(idx)                       # which title and first-frame styles did better
    except Exception:
        pass
    ret = retention.analyze(now, idx)              # where real viewers left, line by line
    score = retention.score_review(idx)            # how well the quality score predicted real results
    return {"measured": len(idx), "nudges": nudges, "groups": summary, "retention": ret.get("analyzed", 0),
            "score_review": score.get("n", 0)}


def nudge(pillar):
    """Points added to (or taken from) an idea's score in pick_next, learned from real results."""
    try:
        return float((db.get("studio_pillar_nudges", {}) or {}).get(pillar, 0.0))
    except (TypeError, ValueError):
        return 0.0


def hint():
    """One line for the idea finder about what has worked (empty until there's real evidence)."""
    ins = db.get("studio_insights", {}) or {}
    good = [(k, v) for k, v in (ins.get("pillar") or {}).items() if v["n"] >= MIN_PER_PILLAR]
    if not good:
        return ""
    good.sort(key=lambda kv: -kv[1]["mean"])
    return ("Results so far on this channel (performance index, 50 = typical): " +
            ", ".join(f"{k} {v['mean']:.0f} over {v['n']} Shorts" for k, v in good) +
            ". Lean toward what works without repeating topics.")


# ---------- reporting ----------

def _fmt(x, pct=False, dec=0):
    if x is None:
        return "not available yet"
    return f"{x:.{dec}f}%" if pct else f"{x:,.{dec}f}"


def report(now=None, save=True):
    """The weekly report text (plain words; only real numbers; missing ones say so)."""
    now = now or time.time()
    ch = guard.locked_channel() or {}
    tot = (latest(ch.get("youtube_channel_id", ""), "channel") or {}).get("metrics") or {}
    vids = videos()
    week = [v for v in vids if (_iso_ts(v.get("published_at")) or 0) >= now - 7 * 86400 and v.get("privacy") == "public"]
    idx = index_all(now)
    lines = [f"# {ch.get('title', 'Business channel')}: weekly report ({_date(now)})", "",
             f"- Subscribers: {_fmt(tot.get('subscriberCount'))}",
             f"- Channel views: {_fmt(tot.get('viewCount'))}",
             f"- Shorts published this week: {len(week)} (all time: {sum(1 for v in vids if v.get('privacy') == 'public')})", ""]
    if vids:
        lines += ["## Shorts", "", "| Short | Views | Avg viewed | Still watching at 10% | Subs gained | Index |",
                  "|---|---|---|---|---|---|"]
        for v in reversed(vids[-20:]):
            a = (latest(v["youtube_id"], "analytics") or {}).get("metrics") or {}
            d = (latest(v["youtube_id"], "data_api") or {}).get("metrics") or {}
            i = (idx.get(v["youtube_id"]) or {}).get("index")
            lines.append(f"| {v['title']} ({v.get('privacy')}) | {_fmt(a.get('views', d.get('viewCount')))} | "
                         f"{_fmt(a.get('averageViewPercentage'), pct=True)} | "
                         f"{_fmt(None if a.get('hook_hold') is None else 100 * a['hook_hold'], pct=True)} | "
                         f"{_fmt(a.get('subscribersGained'))} | {_fmt(i, dec=0) if i is not None else 'after 3 days'} |")
        lines.append("")
    ins = db.get("studio_insights", {}) or {}
    nud = db.get("studio_pillar_nudges", {}) or {}
    lines += ["## What Jarvis learned", ""]
    if not idx:
        lines.append("Not enough data yet: a Short is measured once it has been public for 3 days.")
    else:
        for g, label in (("pillar", "Pillar"), ("kind", "Idea type"), ("hook", "Hook style")):
            for k, v in sorted((ins.get(g) or {}).items(), key=lambda kv: -kv[1]["mean"]):
                lines.append(f"- {label} {k}: index {v['mean']:.0f} over {v['n']} Short(s)")
        lines.append("- Topic picking: " + (", ".join(f"{k} {v:+.1f} points" for k, v in sorted(nud.items()))
                                            if nud else f"no changes yet (needs {MIN_PER_PILLAR}+ measured Shorts per pillar)"))
    from studio import obsidian, retention
    lines += [""] + obsidian.review(idx)
    lines += ["", "## Where viewers left", ""] + retention.text().rstrip("\n").split("\n")
    lines += ["", "## Does the quality score predict real results?", ""] + retention.score_text().rstrip("\n").split("\n")
    from studio import eligibility
    if eligibility.latest():
        lines += [""] + eligibility.text().rstrip("\n").split("\n")
    from studio import lessons, timing
    lines += ["", "## Best posting times", "", timing.text(now)]
    lines += ["", "## Mistakes Jarvis is learning from", "", lessons.summary(now)]
    lines += ["", "Numbers come from YouTube itself (Data API and YouTube Analytics). Revenue is never read."]
    text = "\n".join(lines) + "\n"
    if save:
        out = data_dir("reports") / f"week-{_date(now)}.md"
        out.write_text(text, encoding="utf-8")
        try:
            obsidian.write(text, now)
        except Exception as e:
            print(f"(couldn't write the report into Obsidian: {str(e)[:160]})")
    return text


def _spoken(now):
    vids = [v for v in videos() if v.get("privacy") == "public"]
    if not vids:
        return None
    week = [v for v in vids if (_iso_ts(v.get("published_at")) or 0) >= now - 7 * 86400]
    views = 0
    for v in week:
        a = (latest(v["youtube_id"], "analytics") or {}).get("metrics") or {}
        d = (latest(v["youtube_id"], "data_api") or {}).get("metrics") or {}
        views += int(a.get("views", d.get("viewCount")) or 0)
    return (f"Your weekly YouTube business report is ready: {len(week)} Short{'s' if len(week) != 1 else ''} "
            f"this week with {views:,} views so far. Say \"YouTube stats\" or open studio_data/reports for details.")


# ---------- background ----------

def maybe_run(now=None):
    """Background chore: fetch every few hours, channel totals daily, learn, and the weekly report."""
    now = now or time.time()
    if not config.enabled() or not guard.locked_channel():
        return None
    if not db.q("SELECT 1 FROM published LIMIT 1"):
        return None
    if now - float(db.get("analytics_last_fetch", 0) or 0) < EVERY_HOURS * 3600:
        return None
    db.put("analytics_last_fetch", now)                   # even if it fails, don't retry every tick
    n = fetch(now)
    if now - float(db.get("analytics_last_channel", 0) or 0) >= 20 * 3600:
        channel()
        db.put("analytics_last_channel", now)
    res = learn(now)
    if now - float(db.get("analytics_last_report", 0) or 0) >= 7 * 86400 - 3600:
        report(now)
        msg = _spoken(now)
        if msg:
            health.notify("weekly_report", msg, level="info")
        db.put("analytics_last_report", now)
    return {"videos": n, **res}
