"""Best posting times: learned from this channel's own results.

YouTube's API doesn't share when viewers are online (that chart is only in YouTube Studio), so Jarvis
finds out the honest way: he rotates public Shorts across a few time slots (Central time) and compares
how many views each Short had about 24 hours after going public. Once every slot has enough Shorts,
he mostly posts in the best one and keeps testing the others now and then (1 in 4), because audiences
change. All numbers come from the studio's own YouTube snapshots; nothing is guessed.

hour()      the hour (0-23, Central) for the next public Short.
results()   per slot: how many Shorts were measured and their average views at ~24 h."""
import datetime
import statistics
import time

from studio import config, db

SLOTS = (12, 17, 20)          # noon, 5 PM and 8 PM Central (when US viewers are usually free)
MIN_PER_SLOT = 3              # Shorts measured in a slot before it's trusted
EXPLORE_EVERY = 4             # once learned, every 4th Short still tests another slot
CENTRAL = datetime.timezone(datetime.timedelta(hours=-6))


def slots():
    s = config.general().get("posting_slots") or SLOTS
    return tuple(int(h) for h in s if 0 <= int(h) <= 23) or SLOTS


def _views_at_24h(youtube_id, published):
    """viewCount from the first snapshot 20-40 h after going public (None if none fell in that window)."""
    import json
    rows = db.q("SELECT fetched, metrics FROM analytics WHERE youtube_id=? AND source='data_api' AND fetched BETWEEN ? AND ? "
                "ORDER BY fetched LIMIT 1", youtube_id, published + 20 * 3600, published + 40 * 3600)
    if not rows:
        return None
    v = json.loads(rows[0]["metrics"] or "{}").get("viewCount")
    return int(v) if v is not None else None


def results(now=None):
    from studio import analytics
    out = {h: [] for h in slots()}
    for v in analytics.videos():
        if v.get("privacy") != "public":
            continue
        pub = analytics._iso_ts(v.get("published_at"))
        if not pub:
            continue
        h = datetime.datetime.fromtimestamp(pub, CENTRAL).hour
        slot = min(slots(), key=lambda s: min(abs(s - h), 24 - abs(s - h)))
        n = _views_at_24h(v["youtube_id"], pub)
        if n is not None:
            out[slot].append(n)
    return {h: {"measured": len(vals), "avg_views_24h": round(statistics.mean(vals), 1) if vals else None}
            for h, vals in out.items()}


def hour(now=None):
    """Explore until every slot has MIN_PER_SLOT Shorts, then mostly use the best one."""
    if not config.general().get("learn_posting_time", True):
        from studio import publish
        return int(publish.settings()["publish_hour"])
    res = results(now)
    scheduled = db.one("SELECT COUNT(*) n FROM publish_jobs WHERE publish_at IS NOT NULL")["n"] or 0
    short = [h for h, r in res.items() if r["measured"] < MIN_PER_SLOT]
    if short:
        return short[scheduled % len(short)]
    best = max(res, key=lambda h: res[h]["avg_views_24h"] or 0)
    if scheduled % EXPLORE_EVERY == EXPLORE_EVERY - 1:
        others = [h for h in res if h != best]
        return others[(scheduled // EXPLORE_EVERY) % len(others)]
    return best


def text(now=None):
    res = results(now)
    parts = []
    for h, r in sorted(res.items()):
        label = datetime.time(h).strftime("%-I %p")
        parts.append(f"{label}: " + (f"{r['avg_views_24h']:,.0f} views at 24 h over {r['measured']} Short(s)"
                                     if r["measured"] else "not tested yet"))
    learned = all(r["measured"] >= MIN_PER_SLOT for r in res.values())
    return ("Posting times (Central): " + "; ".join(parts) + ". " +
            ("Jarvis now posts mostly at the best time and still tests the others now and then."
             if learned else f"Still testing: each time needs {MIN_PER_SLOT} measured Shorts."))
