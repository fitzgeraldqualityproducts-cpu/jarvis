"""YouTube Partner Program progress (Phase 10): a read-only tracker. No money data, ever.

It reads only the locked channel's own numbers (subscribers, Shorts views in the last 90 days, public
uploads in the last 90 days) and compares them with YouTube's published thresholds. Every value says
where it came from. A value YouTube doesn't expose (YouTube's own "valid public Shorts views" count) is
marked as an ESTIMATE with what it is based on, never shown as the real thing. Projections are labelled
projections. Nothing here applies to the program, accepts terms, or touches AdSense or revenue:
Connor does that himself if the channel ever qualifies.

RULES: the thresholds as read from YouTube's help pages (2026-11-23); dated, so a change on
2027-02-01 applies by itself. Check them again before relying on them."""
import datetime
import json
import time
import urllib.parse

from studio import analytics, db, guard, publish

CHECKED = "2026-11-23"
SOURCE = "https://support.google.com/youtube/answer/72851"
RULES = [
    # (applies from, tier, rules)
    ("2023-06-13", "fan funding", {"subscribers": 500, "public_uploads_90d": 3, "shorts_views_90d": 3_000_000}),
    ("2023-06-13", "ad revenue", {"subscribers": 1000, "shorts_views_90d": 10_000_000}),
    ("2027-02-01", "ad revenue", {"subscribers": 1000, "shorts_views_90d": 20_000_000}),
]
LABELS = {"subscribers": "subscribers", "public_uploads_90d": "public uploads in the last 90 days",
          "shorts_views_90d": "Shorts views in the last 90 days"}


def rules(now=None):
    """{tier: rules} in force on that day (the latest entry per tier that has started)."""
    day = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for start, tier, r in RULES:
        if start <= day:
            out[tier] = dict(r)
    return out


def upcoming(now=None):
    day = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc).strftime("%Y-%m-%d")
    return [(s, t, r) for s, t, r in RULES if s > day]


def _shorts_views(start, end):
    """Views of the channel's Shorts between two dates, from YouTube Analytics (engaged views when
    YouTube reports them: closer to the program's own count). (value, basis) or (None, why)."""
    guard.allow("youtube.analytics", "Shorts views for eligibility", record=False)
    for metric in ("engagedViews", "views"):
        try:
            status, _, body = publish._http("GET", analytics.ANALYTICS + "?" + urllib.parse.urlencode(
                {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metric,
                 "dimensions": "creatorContentType", "filters": "creatorContentType==SHORTS"}), publish._auth())
            r = publish._check(status, body, "analytics")
        except Exception as e:
            if metric == "views":
                return None, f"YouTube Analytics didn't answer ({str(e)[:120]})"
            continue
        rows = r.get("rows") or []
        heads = [h["name"] for h in r.get("columnHeaders") or []]
        if metric in heads:
            if not rows:
                return 0, f"YouTube Analytics {metric} (no Shorts views in this period)"
            return int(rows[0][heads.index(metric)] or 0), f"YouTube Analytics {metric}"
    return None, "YouTube Analytics has no Shorts views data yet"


def check(now=None, online=True):
    """Measures progress, saves it (eligibility table) and returns it."""
    now = now or time.time()
    ch = guard.locked_channel()
    if not ch:
        return None
    day = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d")
    values, basis, unavailable = {}, {}, []
    tot = None
    if online:
        try:
            tot = analytics.channel()
        except Exception as e:
            unavailable.append(f"channel totals: {str(e)[:120]}")
    if tot is None:
        tot = (analytics.latest(ch["youtube_channel_id"], "channel") or {}).get("metrics") or {}
    if "subscriberCount" in tot:
        values["subscribers"], basis["subscribers"] = int(tot["subscriberCount"]), "YouTube Data API (channel statistics, rounded by YouTube)"
    else:
        unavailable.append("subscribers (hidden or not read yet)")
    since = now - 90 * 86400
    ups = [v for v in analytics.videos() if v.get("privacy") == "public"
           and (analytics._iso_ts(v.get("published_at")) or 0) >= since]
    values["public_uploads_90d"], basis["public_uploads_90d"] = len(ups), "the studio's own published list, checked against YouTube"
    if online:
        v, why = _shorts_views(day(since), day(now))
        if v is None:
            unavailable.append(f"Shorts views in 90 days: {why}")
        else:
            values["shorts_views_90d"] = v
            basis["shorts_views_90d"] = f"ESTIMATE from {why}; YouTube's own count of qualifying views isn't available to apps"
            v28, _ = _shorts_views(day(now - 28 * 86400), day(now))
            if v28 is not None:
                values["shorts_views_28d"] = v28
    tiers = {}
    for tier, need in rules(now).items():
        rows = {}
        for k, target in need.items():
            have = values.get(k)
            rows[k] = {"have": have, "need": target, "met": None if have is None else have >= target,
                       "share": None if have is None else round(min(1.0, have / target), 4)}
        known = [r["met"] for r in rows.values()]
        tiers[tier] = {"rules": rows, "met": all(known) if None not in known else None}
    proj = {}
    if values.get("shorts_views_28d"):
        per_day = values["shorts_views_28d"] / 28.0
        for tier, need in rules(now).items():
            if "shorts_views_90d" in need and per_day > 0:
                proj[tier] = {"views_per_day_recent": round(per_day, 1),
                              "views_90d_at_this_pace": round(per_day * 90),
                              "label": "PROJECTION at the last 28 days' pace; not a promise"}
    rep = {"checked": now, "channel": ch["youtube_channel_id"], "values": values, "basis": basis, "tiers": tiers,
           "projection": proj, "unavailable": unavailable, "rules_checked": CHECKED, "rules_source": SOURCE,
           "upcoming": [{"from": s, "tier": t, "rules": r} for s, t, r in upcoming(now)]}
    db.insert("INSERT INTO eligibility(fetched, rules_checked, values_json, unavailable, source) VALUES(?,?,?,?,?)",
              now, json.dumps({"checked": CHECKED, "source": SOURCE, "tiers": tiers}), json.dumps(rep),
              json.dumps(unavailable), "YouTube Data API + YouTube Analytics (read-only)")
    return rep


def latest():
    r = db.one("SELECT values_json FROM eligibility ORDER BY id DESC LIMIT 1")
    return json.loads(r["values_json"]) if r else None


def text(rep=None):
    """Plain-words progress, saying what's real, what's an estimate and what's unknown."""
    rep = rep or latest()
    if not rep:
        return "No Partner Program progress measured yet."
    out = ["## YouTube Partner Program progress", ""]
    for tier, t in rep["tiers"].items():
        status = "requirements met (Connor applies himself in YouTube Studio)" if t["met"] else (
            "not yet" if t["met"] is False else "can't tell yet")
        out.append(f"**{tier.capitalize()}**: {status}")
        for k, r in t["rules"].items():
            have = "unknown" if r["have"] is None else f"{r['have']:,}"
            pct = "" if r["share"] is None else f" ({100 * r['share']:.1f}%)"
            out.append(f"- {LABELS.get(k, k)}: {have} of {r['need']:,}{pct}")
        out.append("")
    for tier, p in (rep.get("projection") or {}).items():
        out.append(f"- {tier}: about {p['views_90d_at_this_pace']:,} Shorts views per 90 days at the recent pace ({p['label']}).")
    for k, b in rep["basis"].items():
        if "ESTIMATE" in b:
            out.append(f"- Note on {LABELS.get(k, k)}: {b}.")
    for u in rep.get("unavailable") or []:
        out.append(f"- Not available: {u}")
    for u in rep.get("upcoming") or []:
        out.append(f"- From {u['from']} the {u['tier']} tier needs " +
                   ", ".join(f"{v:,} {LABELS.get(k, k)}" for k, v in u["rules"].items()) + ".")
    out.append(f"- Thresholds as read from {rep['rules_source']} on {rep['rules_checked']}. No revenue or AdSense data is read.")
    return "\n".join(out) + "\n"


def maybe_check(now=None):
    """Background: once a day, only after something is public."""
    from studio import config
    now = now or time.time()
    if not config.enabled() or not guard.locked_channel():
        return None
    if not db.q("SELECT 1 FROM published WHERE privacy='public' LIMIT 1"):
        return None
    if now - float(db.get("eligibility_last", 0) or 0) < 20 * 3600:
        return None
    db.put("eligibility_last", now)
    prev = latest()
    rep = check(now)
    if rep:
        from studio import health
        for tier, t in rep["tiers"].items():
            was = ((prev or {}).get("tiers") or {}).get(tier, {}).get("met")
            if t["met"] and not was:
                health.notify(f"ypp_{tier}", f"Good news: the business channel now meets YouTube's {tier} requirements, "
                              "by my numbers. You'd apply yourself in YouTube Studio; I don't touch money or AdSense.",
                              level="info")
    return rep
