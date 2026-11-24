"""Smarter topics: what people are really reading (Wikipedia page views, free, no key) and a duplicate check
by meaning, not just by shared words.

reading_now()      the most-read English Wikipedia articles yesterday (Wikimedia's free pageviews API), shown
                   to Claude when it proposes topics, as hints only ("use one only if it fits the pillar").
wiki_interest()    for a candidate topic: its best Wikipedia article and that article's daily views over the
                   last 60 days -> a 0-10 interest score (100 a day = 0, 1,000 = 3.3, 10,000 = 6.7, 100,000 =
                   10) and whether it's rising (the last 7 days at least twice the 30 days before). It's
                   blended into "Audience interest" next to the YouTube views measure, like that one is.
same_meaning()     the closest earlier topic or video by meaning (Jarvis's own small embedding model from his
                   memory, potion-base-8M, on this computer): "Why is the sky blue" and "What makes daylight
                   look blue" share no words but mean the same. At config "duplicate_meaning_at" (0.9) or
                   above it counts as a repeat. `python -m studio similar` lists the closest pairs, to tune it.
Google Trends has no free official API, so it isn't used. Config "wiki_trends": false turns the Wikipedia
parts off."""
import datetime
import json
import math
import re
import time
import urllib.parse

from studio import config, db

VIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
SKIP_PAGES = re.compile(r"^(Main_Page|Special:|Wikipedia:|File:|Portal:|Help:|Talk:|Template:|Category:|User:|-$)")
RISING_AT = 2.0
MEANING_AT = 0.9
EMBED = None          # tests swap this: text -> unit vector (numpy) or None


def on():
    return config.general().get("wiki_trends", True) is not False


def _get(url):
    from studio import topics
    return json.loads(topics._api(url))


def _day(d):
    return d.strftime("%Y%m%d")


def reading_now(limit=60, today=None):
    """Titles of the most-read Wikipedia articles yesterday (cached for the day); [] when off or unreachable."""
    if not on():
        return []
    today = today or datetime.date.today()
    key = f"wiki_top_{today.isoformat()}"
    hit = db.get(key)
    if hit:
        return json.loads(hit)[:limit]
    day = today - datetime.timedelta(days=1)
    try:
        ans = _get(f"{VIEWS_API}/top/en.wikipedia/all-access/{day:%Y}/{day:%m}/{day:%d}")
        arts = (((ans.get("items") or [{}])[0]).get("articles") or [])
    except Exception:
        return []
    titles = [str(a.get("article") or "") for a in arts if not SKIP_PAGES.match(str(a.get("article") or ""))]
    titles = [t.replace("_", " ") for t in titles if t][:200]
    db.put(key, json.dumps(titles))
    return titles[:limit]


def hint(limit=60):
    """A line for the topic prompt, or ""."""
    titles = reading_now(limit)
    if not titles:
        return ""
    return ("\nMost-read on Wikipedia yesterday (hints only: use one only if it truly fits this pillar and has a real "
            "number to show; never force it): " + "; ".join(titles))


def _article(query):
    from studio import topics
    ans = _get(topics.WIKI_API + "?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": str(query)[:200], "srlimit": 1, "format": "json"}))
    hits = (ans.get("query") or {}).get("search") or []
    return str(hits[0].get("title") or "") if hits else ""


def wiki_interest(title, keywords=(), today=None):
    """(score 0-10, {"article", "daily", "recent", "rising"}) for a topic, or (None, None)."""
    if not on():
        return None, None
    try:
        art = _article(" ".join(str(k) for k in keywords[:4]) or title) or _article(title)
        if not art:
            return None, None
        today = today or datetime.date.today()
        end, start = today - datetime.timedelta(days=1), today - datetime.timedelta(days=60)
        ans = _get(f"{VIEWS_API}/per-article/en.wikipedia/all-access/user/"
                   f"{urllib.parse.quote(art.replace(' ', '_'), safe='')}/daily/{_day(start)}/{_day(end)}")
        views = [int(i.get("views") or 0) for i in ans.get("items") or []]
    except Exception:
        return None, None
    if len(views) < 14:
        return None, None
    daily = sum(views) / len(views)
    recent, before = sum(views[-7:]) / 7, sum(views[-37:-7]) / max(1, len(views[-37:-7]))
    score = round(max(0.0, min(10.0, (math.log10(max(daily, 1)) - 2) * 10 / 3)), 1)
    rising = before > 0 and recent >= RISING_AT * before
    return score, {"article": art, "daily": round(daily), "recent": round(recent), "rising": bool(rising)}


# ---------- duplicates by meaning ----------

_MODEL = []


def embed(text):
    if EMBED:
        return EMBED(text)
    import sys
    from studio import ROOT
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import recall
        if not _MODEL:
            _MODEL.append(bool(recall._load_model()))
        return recall._embed(text) if _MODEL[0] else None
    except Exception:
        return None


def threshold():
    try:
        return float(config.general().get("duplicate_meaning_at", MEANING_AT))
    except (TypeError, ValueError):
        return MEANING_AT


def _known():
    rows = [(f"topic {r['id']}", f"{r['title']}. {r['angle'] or ''}") for r in
            db.q("SELECT id, title, angle FROM topics WHERE title IS NOT NULL")]
    rows += [(f"video {r['id']}", r["title"]) for r in
             db.q("SELECT id, title FROM videos WHERE title IS NOT NULL AND title<>''")]
    return rows


def same_meaning(title, angle="", known=None):
    """(similarity, what it matches) for the closest earlier topic or video, or (0.0, None) without the model."""
    import numpy as np
    v = embed(f"{title}. {angle}")
    if v is None:
        return 0.0, None
    best = (0.0, None)
    for name, text in known if known is not None else _known():
        w = embed(text)
        if w is None:
            continue
        sim = float(np.dot(v, w))
        if sim > best[0]:
            best = (round(sim, 3), name)
    return best


def closest_pairs(limit=15):
    """The most alike pairs among earlier topics and videos: [(similarity, a, b)], to tune the threshold."""
    import numpy as np
    rows = _known()
    vecs = [(n, t, embed(t)) for n, t in rows]
    vecs = [x for x in vecs if x[2] is not None]
    pairs = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            pairs.append((round(float(np.dot(vecs[i][2], vecs[j][2])), 3), vecs[i][1][:90], vecs[j][1][:90]))
    return sorted(pairs, reverse=True)[:limit]
