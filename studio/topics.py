"""Content intelligence (Phase 5): finds, scores and picks Short topics inside the chosen niche.

discover()       per pillar, Claude (with web search) proposes evergreen topics, recent developments
                 and real audience questions, each with at least two sources. When the research key
                 is there, the top Shorts for the pillar on YouTube are shown to Claude as GAPS to
                 beat (never to copy). Every source URL is opened once to check it's real.
score_one()      deterministic score from 10 factors. Claude rates 8 of them (0-10, one reason
                 each); source reliability comes from the checked sources; audience interest is
                 blended with measured YouTube views for the best candidates when possible.
is_duplicate()   word-overlap check against every topic ever kept and every video ever made.
pick_next()      the best fresh idea, keeping pillars balanced and trending topics a minority.
maybe_discover() runs discovery in the background when the idea pool runs low (studio on only).

Everything lands in the topics / sources / discovery_runs tables of studio_data/studio.db."""
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from studio import config, db, guard

SCORING_VERSION = 1
WEIGHTS = {"relevance": 0.14, "interest": 0.14, "curiosity": 0.12, "value": 0.12, "novelty": 0.10,
           "feasibility": 0.10, "source_reliability": 0.10, "story": 0.08, "originality": 0.06,
           "copyright_safety": 0.04}
LABELS = {"relevance": "Relevance to the niche", "interest": "Audience interest", "curiosity": "Viewer curiosity",
          "value": "Educational value", "novelty": "Novelty", "feasibility": "Free production feasibility",
          "source_reliability": "Source reliability", "story": "Storytelling potential",
          "originality": "Originality", "copyright_safety": "Copyright safety"}
KINDS = ("evergreen", "trending", "question")
TRENDING_DAYS = 10            # a trending idea is only used while it's fresh
MIN_POOL = 12                 # ideas ready before background discovery stops
DISCOVER_EVERY_HOURS = 6
TRENDING_SHARE = 0.3          # at most this share of recent picks may be trending
DUPLICATE_AT = 0.5            # word overlap at which two topics count as the same

TIER3 = ("nasa.gov", "esa.int", "noaa.gov", "nist.gov", "nih.gov", "usgs.gov", "cern.ch", "nature.com",
         "science.org", "arxiv.org", "aps.org", "iop.org", "royalsocietypublishing.org", "pnas.org", "cell.com",
         "springer.com", "sciencedirect.com", "jpl.nasa.gov", "who.int", "britannica.com", "physics.info",
         "hyperphysics.phy-astr.gsu.edu", "ocw.mit.edu", "khanacademy.org")
TIER2 = ("wikipedia.org", "scientificamerican.com", "newscientist.com", "phys.org", "space.com",
         "quantamagazine.org", "nationalgeographic.com", "smithsonianmag.com", "bbc.com", "bbc.co.uk",
         "reuters.com", "apnews.com", "sciencenews.org", "livescience.com", "skyandtelescope.org", "nytimes.com",
         "theconversation.com", "eurekalert.org", "sciencedaily.com", "howstuffworks.com", "engineeringtoolbox.com")
STOP = set("a an the of to in on for and or is are was were be been it its this that these those how why what "
           "when where who which with without from by at as into than then so if do does did can could would "
           "should will just about your you we our they their there here not no yes more most very really "
           "actually real really vs versus".split())


# ---------- the idea database ----------

def _words(text):
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text).lower()):
        if w in STOP or (len(w) < 2 and not w.isdigit()):
            continue
        for suf in ("ing", "es", "ed", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def fingerprint(title):
    return " ".join(sorted(_words(title)))


def _overlap(a, b):
    a, b = _words(a), _words(b)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b)) if min(len(a), len(b)) >= 2 else len(a & b) / len(a | b)


def is_duplicate(title, angle=""):
    """(True, existing topic id or video title) when this topic was already kept or made."""
    fp = fingerprint(title)
    if not fp:
        return True, None
    for r in db.q("SELECT id, title, angle FROM topics"):
        if r["title"] and (fingerprint(r["title"]) == fp or _overlap(title, r["title"]) >= DUPLICATE_AT + 0.2
                           or _overlap(f"{title} {angle}", f"{r['title']} {r['angle'] or ''}") >= DUPLICATE_AT + 0.25):
            return True, r["id"]
    for r in db.q("SELECT id, title FROM videos WHERE title IS NOT NULL AND title<>''"):
        if _overlap(title, r["title"]) >= DUPLICATE_AT + 0.2:
            return True, f"video {r['id']}"
    return False, None


def niche():
    d = db.get("niche_decision")
    if not d:
        return None
    return json.loads(d) if isinstance(d, str) else d


def pillars():
    return [dict(r) for r in db.q("SELECT name, description FROM pillars WHERE active=1 ORDER BY id")]


def pool(now=None):
    """Ideas ready to make: kept, not used, and still fresh if trending."""
    now = now or time.time()
    return [dict(r) for r in db.q("SELECT * FROM topics WHERE status='idea' AND (fresh_until IS NULL OR fresh_until>?) "
                                  "ORDER BY score DESC", now)]


def topic(tid):
    r = db.one("SELECT * FROM topics WHERE id=?", tid)
    if not r:
        return None
    t = dict(r)
    t["sources"] = [dict(s) for s in db.q("SELECT * FROM sources WHERE topic_id=? ORDER BY id", tid)]
    t["scores"] = json.loads(t.get("scores") or "{}")
    return t


def set_status(tid, status=None, **kw):
    """Production / script / publish status, video link and performance live on the topic row."""
    cols = dict(kw)
    if status:
        cols["status"] = status
    for k in ("performance",):
        if k in cols and not isinstance(cols[k], str):
            cols[k] = json.dumps(cols[k])
    cols["updated"] = time.time()
    db.run(f"UPDATE topics SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", *cols.values(), tid)


# ---------- sources ----------

def domain(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def tier(url):
    d = domain(url)
    if not d:
        return 0
    if d.endswith(".gov") or d.endswith(".edu") or d.endswith(".mil") or any(d == t or d.endswith("." + t) for t in TIER3):
        return 3
    if any(d == t or d.endswith("." + t) for t in TIER2):
        return 2
    return 1


FETCH = None        # tests swap this: url -> HTTP status


def check_url(url):
    """Opens a source once (public page, read-only). Returns the HTTP status, or 0 if unreachable."""
    if not str(url).startswith(("http://", "https://")):
        return 0
    guard.allow("web.read", url[:200], record=False)
    if FETCH:
        return FETCH(url)
    from clipper import youtube
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) research-check",
                                               "Accept": "text/html,application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=youtube._ctx()) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


PAGE = None         # tests swap this: url -> the page's plain text


def page_text(url, limit=600_000):
    """The readable text of a public web page ("" if it can't be read, e.g. a PDF or a blocked site)."""
    if not str(url).startswith(("http://", "https://")):
        return ""
    guard.allow("web.read", url[:200], record=False)
    if PAGE:
        return PAGE(url)
    from clipper import youtube
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) research-check",
                                               "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=12, context=youtube._ctx()) as r:
            if "html" not in (r.headers.get("Content-Type") or "html").lower():
                return ""
            raw = r.read(limit).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""
    import html as _html
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", _html.unescape(raw)).strip()


def _nums(text):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text)) if len(n.replace(",", "")) >= 2}


def excerpt(text, claim, n=3, width=300):
    """Up to n sentences of the page that share the claim's numbers or words, in page order ("" if none)."""
    if not text:
        return ""
    nums, words = _nums(claim), {w for w in re.findall(r"[a-z]{4,}", str(claim).lower())}
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if 20 <= len(x.strip()) <= 1200]
    scored = []
    for i, x in enumerate(sents):
        sc = 3 * len(nums & _nums(x)) + len(words & set(re.findall(r"[a-z]{4,}", x.lower())))
        if sc >= 2:
            scored.append((sc, i))
    keep = sorted(i for _, i in sorted(scored, reverse=True)[:n])
    return " … ".join(sents[i][:width] for i in keep)


def excerpts(facts):
    """The source text behind each fact (same order), read from the pages at the same time."""
    from concurrent.futures import ThreadPoolExecutor
    if not facts:
        return []

    def one(f):
        try:
            return excerpt(page_text(f.get("url")), f.get("claim", ""))
        except Exception:
            return ""
    with ThreadPoolExecutor(max_workers=min(8, len(facts))) as pool:
        return list(pool.map(one, facts))


def _reachable(status):
    # 401/403/429 mean the site exists but turns away robots (common for journals): still a real source
    return 200 <= status < 400 or status in (401, 403, 429)


def _working(sources):
    """Reachable sources, each URL counted once."""
    seen, out = set(), []
    for s in sources:
        u = str(s.get("url") or "").rstrip("/").lower()
        if u and u not in seen and _reachable(s.get("http_status") or 0):
            seen.add(u)
            out.append(s)
    return out


def check_all(facts):
    """_check for many facts at once (up to 8 pages opened at the same time; same order back)."""
    facts = [f for f in facts if isinstance(f, dict)]
    if len(facts) < 2:
        return [_check(f) for f in facts]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(facts))) as pool:
        return list(pool.map(_check, facts))


def _check(fct):
    url = str(fct.get("url") or "")
    return {"claim": str(fct.get("claim", ""))[:400], "url": url[:500], "title": str(fct.get("source", ""))[:200],
            "domain": domain(url), "tier": tier(url), "http_status": check_url(url)}


def more_sources(items):
    """One Claude call (web on) that finds extra sources for topics with fewer than 2 working ones.
    items: [{"title", "angle", "_checked"}]. Adds the checked new sources to each item's "_checked"."""
    from studio import claude
    if not items:
        return 0
    lines = "\n".join(f"{i}. {c['title']}: {c.get('angle', '')} (already have: "
                      f"{', '.join(s['url'] for s in _working(c['_checked'])) or 'nothing that works'})"
                      for i, c in enumerate(items))
    ans = claude.ask_json(
        "Use web search. For each Shorts topic below, find 2 MORE reliable sources (primary sources such as NASA, "
        "universities, journals or standards bodies first, then reputable science media or Wikipedia) that support its "
        "key numbers. Only URLs you actually opened, different from the ones it already has.\n\n" + lines +
        '\n\nJSON object keyed by the number: {"0": [{"claim": "the fact this page supports", "url": "https://...", '
        '"source": "publisher"}], ...}', timeout=600, web=True, purpose="more sources")
    added = 0
    for k, facts in (ans.items() if isinstance(ans, dict) else []):
        if not str(k).isdigit() or int(k) >= len(items) or not isinstance(facts, list):
            continue
        have = {str(s["url"]).rstrip("/").lower() for s in items[int(k)]["_checked"]}
        new = [f for f in facts[:3] if isinstance(f, dict) and str(f.get("url") or "").rstrip("/").lower() not in have]
        items[int(k)]["_checked"] += check_all(new)
        added += len(new)
    return added


def retry_rejected(progress=print):
    """Ideas rejected ONLY for too few working sources get one more search for sources."""
    rows = [dict(r) for r in db.q("SELECT * FROM topics WHERE status='rejected' AND reject_reason LIKE 'only % working source%' "
                                  "AND reject_reason NOT LIKE '%;%' AND (scores NOT LIKE '%\"retried\": true%')")]
    if not rows:
        return []
    for t in rows:
        t["_checked"] = [dict(s) for s in db.q("SELECT claim, url, title, domain, tier, http_status FROM sources WHERE topic_id=?",
                                                t["id"])]
        t["_before"] = len(t["_checked"])
    progress(f"Looking for more sources for {len(rows)} earlier ideas")
    more_sources(rows)
    revived = []
    now = time.time()
    for t in rows:
        for s in t["_checked"][t["_before"]:]:
            db.insert("INSERT INTO sources(topic_id, claim, url, title, retrieved, verdict, note, domain, tier, http_status) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", t["id"], s["claim"], s["url"], s["title"], now,
                      "reachable" if _reachable(s["http_status"]) else "unreachable", "", s["domain"], s["tier"], s["http_status"])
        sc = json.loads(t["scores"] or "{}")
        sc["retried"] = True
        f = sc.get("factors") or {}
        r = {k: (v or {}).get("score") for k, v in f.items() if k not in ("source_reliability", "copyright_safety")}
        if (f.get("copyright_safety") or {}).get("score") is not None:
            r["copyright_risk"] = 10 - f["copyright_safety"]["score"]
        n = len(_working(t["_checked"]))
        if n >= 2:
            overall, factors = score_one(r, reliability(t["_checked"]))
            sc.update(factors=factors, version=SCORING_VERSION)
            db.run("UPDATE topics SET status='idea', reject_reason=NULL, score=?, scores=?, sources=?, updated=?, "
                   "fresh_until=CASE WHEN kind='trending' THEN ? ELSE fresh_until END WHERE id=?",
                   overall, json.dumps(sc), json.dumps([s["url"] for s in t["_checked"]]), now,
                   now + TRENDING_DAYS * 86400, t["id"])
            revived.append({"id": t["id"], "title": t["title"], "score": overall})
        else:
            db.run("UPDATE topics SET scores=?, updated=? WHERE id=?", json.dumps(sc), now, t["id"])
    progress(f"Brought back {len(revived)} of {len(rows)}")
    return revived


def reliability(sources):
    """0-10 from the checked sources: the best two reachable ones, by publisher tier."""
    tiers = sorted((s["tier"] for s in _working(sources)), reverse=True)[:2]
    if not tiers:
        return 0.0
    return round(10 * sum(tiers) / (3 * 2), 1)


# ---------- discovery ----------

def _existing_titles(limit=80):
    return [r["title"] for r in db.q("SELECT title FROM topics ORDER BY id DESC LIMIT ?", limit)]


def gaps(pillar, limit=12):
    """Titles and views of this pillar's top recent Shorts (official API, research key). [] without a key."""
    from studio import ytpublic
    if not ytpublic.available():
        return []
    try:
        found = ytpublic.search_shorts(pillar, days=60, limit=25)
        stats = ytpublic.videos([f["video_id"] for f in found])
    except Exception:
        return []
    rows = sorted(({"title": s["title"], "views": s["views"]} for s in stats.values() if s["seconds"] <= 180),
                  key=lambda r: -r["views"])
    return rows[:limit]


def propose(pillar, n=6, competitor=()):
    """Claude (web search on) proposes topics for one pillar, each with sources."""
    from studio import claude
    d = niche() or {}
    plan = d.get("plan") or {}
    avoid = "; ".join(plan.get("avoid") or [])
    comp = ""
    if competitor:
        comp = ("\nTop Shorts in this area right now (views). Treat them as GAPS: find angles they missed or got "
                "wrong. Never copy a title, script or idea:\n" + "\n".join(f"- {c['title']} ({c['views']:,})" for c in competitor))
    taken = "\n".join(f"- {t}" for t in _existing_titles()) or "- (none yet)"
    from studio import analytics
    learned = analytics.hint()
    if learned:
        comp += "\n" + learned
    from studio import lessons
    mistakes = lessons.advice("ideas")
    if mistakes:
        comp += "\n" + mistakes
    prompt = f"""You find topics for a YouTube Shorts channel (25-45 s, US English, narrated, original graphics).
Niche: {d.get('name', '')}. Positioning: {plan.get('positioning', '')}. Audience: {plan.get('audience', '')}.
Pillar: {pillar['name']}: {pillar.get('description', '')}
Never: {avoid or 'medical or financial advice'}; anything that needs film footage, celebrities or copyrighted clips.

Use web search. Propose {n} topics: at least 2 "evergreen", at least 1 "trending" (a real development or
discovery from the last 30 days, with its date) and at least 1 "question" (something people actually ask:
search suggestions, forums, Q&A sites; say where). Each must work with diagrams, charts, maps or simple
animation made in code, and must have a concrete number or calculation to show.{comp}

Already covered (do NOT repeat or rephrase these):
{taken}

JSON array of objects: {{"title": "short working title", "kind": "evergreen|trending|question",
"angle": "the specific claim or calculation the Short shows", "hook": "first spoken line",
"why": "why viewers care now (for trending: the date and what happened)",
"keywords": ["3-6 search words"], "facts": [{{"claim": "one checkable fact", "url": "https://...",
"source": "publisher"}}] (2-4 facts, real URLs you opened, primary or reputable sources)}}"""
    ans = claude.ask_json(prompt, timeout=600, web=True, purpose=f"topic ideas: {pillar['name']}")
    return [a for a in ans if isinstance(a, dict) and a.get("title")] if isinstance(ans, list) else []


def rate(cands):
    """Claude rates each candidate 0-10 on 8 factors with a short reason (no web). {index: {...}}"""
    from studio import claude
    d = niche() or {}
    lines = "\n".join(f"{i}. [{c.get('kind')}] {c['title']}: {c.get('angle', '')} (sources: "
                      f"{', '.join(domain(f.get('url', '')) for f in c.get('facts') or [])})" for i, c in enumerate(cands))
    prompt = f"""Rate these Shorts topic ideas for the niche "{d.get('name', '')}" (positioning: {(d.get('plan') or {}).get('positioning', '')}).
Be strict and honest; 5 is average. For each give integers 0-10:
relevance (fits the niche and pillar), interest (how many US viewers would want this), novelty (fresh vs
over-covered), curiosity (does the title/hook make you need the answer), value (viewer learns something real),
story (a clear setup, tension, payoff in 40 s), feasibility (doable with code-made graphics, no footage),
originality (our own angle, not a remake of a known video), copyright_risk (0 = none, 10 = needs others' footage/music/characters).
Also "avoid": true if it touches medical/financial advice, partisan politics or the niche's banned areas, and
"note": one sentence on the main strength or problem.

{lines}

JSON object keyed by the number: {{"0": {{"relevance":..,"interest":..,"novelty":..,"curiosity":..,"value":..,
"story":..,"feasibility":..,"originality":..,"copyright_risk":..,"avoid":false,"note":".."}}, ...}}"""
    ans = claude.ask_json(prompt, timeout=300, web=False, purpose="rate topic ideas")
    out = {}
    if isinstance(ans, dict):
        for k, v in ans.items():
            if str(k).isdigit() and isinstance(v, dict):
                out[int(k)] = v
    return out


def _num(v):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return None


def measured_interest(keywords):
    """0-10 from the median views of recent Shorts for these words (None without a key)."""
    from studio import ytpublic
    if not ytpublic.available() or not keywords:
        return None, None
    try:
        found = ytpublic.search_shorts(" ".join(keywords[:4]), days=90, limit=15)
        stats = ytpublic.videos([f["video_id"] for f in found])
    except Exception:
        return None, None
    views = sorted(s["views"] for s in stats.values() if s["seconds"] <= 180)
    if not views:
        return 0.0, 0
    med = views[len(views) // 2]
    return round(max(0.0, min(10.0, (math.log10(max(med, 1)) - 3) * 3)), 1), med    # 1k=0, 10k=3, 100k=6, 1M=9


def score_one(r, src_reliability, measured=None):
    """Deterministic overall score (0-100) from Claude's ratings, checked sources and measured views."""
    f = {}
    for k in ("relevance", "interest", "curiosity", "value", "novelty", "feasibility", "story", "originality"):
        f[k] = {"score": _num(r.get(k)), "basis": "estimate"}
    risk = _num(r.get("copyright_risk"))
    f["copyright_safety"] = {"score": None if risk is None else 10 - risk, "basis": "estimate"}
    f["source_reliability"] = {"score": src_reliability, "basis": "measured"}
    if measured is not None and f["interest"]["score"] is not None:
        f["interest"] = {"score": round(0.5 * f["interest"]["score"] + 0.5 * measured, 1), "basis": "estimate + measured"}
    have = {k: v["score"] for k, v in f.items() if v["score"] is not None}
    wsum = sum(WEIGHTS[k] for k in have)
    overall = round(10 * sum(WEIGHTS[k] * v for k, v in have.items()) / wsum, 1) if wsum else 0.0
    return overall, f


def gate(c, r, src_reliability, factors):
    """Reasons to reject outright (empty list = keep)."""
    why = []
    if r.get("avoid"):
        why.append("touches a banned area")
    if (factors["relevance"]["score"] or 0) < 5:
        why.append("not relevant enough to the niche")
    if (factors["copyright_safety"]["score"] if factors["copyright_safety"]["score"] is not None else 10) < 4:
        why.append("copyright risk")
    if (factors["feasibility"]["score"] or 0) < 4:
        why.append("can't be made with code-made graphics")
    good = _working(c.get("_checked", []))
    if len(good) < 2:
        why.append(f"only {len(good)} working source{'s' if len(good) != 1 else ''}")
    return why


def discover(pillar_names=None, per_pillar=6, measure_top=4, progress=print):
    """One discovery run over the given pillars (default: the 2 with the fewest ready ideas).
    Returns {"added": [...], "duplicates": n, "rejected": [...]}."""
    if not niche():
        raise RuntimeError("no niche has been chosen yet")
    all_p = {p["name"]: p for p in pillars()}
    if not all_p:
        raise RuntimeError("the niche has no pillars")
    if pillar_names is None:
        ready = {name: 0 for name in all_p}
        for t in pool():
            if t["pillar"] in ready:
                ready[t["pillar"]] += 1
        pillar_names = sorted(ready, key=lambda n: (ready[n], n))[:2]
    summary = {"added": [], "duplicates": 0, "rejected": [], "revived": retry_rejected(progress)}
    for name in pillar_names:
        p = all_p.get(name)
        if not p:
            continue
        t0, searches, calls = time.time(), 0, 2
        comp = gaps(p["name"])
        searches += 1 if comp else 0
        progress(f"{name}: asking for ideas" + (f" ({len(comp)} top Shorts as gaps)" if comp else ""))
        cands, dups = [], 0
        for c in propose(p, per_pillar, comp):
            c["kind"] = c.get("kind") if c.get("kind") in KINDS else "evergreen"
            dup, _ = is_duplicate(c["title"], c.get("angle", ""))
            if dup or any(_overlap(c["title"], k["title"]) >= DUPLICATE_AT + 0.2 for k in cands):
                dups += 1
                continue
            cands.append(c)
        progress(f"{name}: {len(cands)} new ideas ({dups} repeats dropped); checking sources")
        wanted = [(c, fct) for c in cands for fct in (c.get("facts") or [])[:4] if isinstance(fct, dict)]
        done = check_all([fct for _, fct in wanted])           # all the ideas' sources opened together
        for c in cands:
            c["_checked"] = []
        for (c, _), chk in zip(wanted, done):
            c["_checked"].append(chk)
        checked = [f["http_status"] for c in cands for f in c["_checked"]]
        if len(checked) >= 3 and not any(checked):
            from studio.jobs import Transient            # offline: keep nothing rather than reject good ideas
            raise Transient("couldn't reach any source website to check the facts (is the Mac offline?)")
        thin = [c for c in cands if len(_working(c["_checked"])) < 2]
        if thin:
            progress(f"{name}: finding more sources for {len(thin)} ideas")
            more_sources(thin)
            calls += 1
        ratings = rate(cands) if cands else {}
        scored = []
        for i, c in enumerate(cands):
            r = ratings.get(i) or {}
            rel = reliability(c["_checked"])
            overall, factors = score_one(r, rel)
            scored.append((overall, i, c, r, rel, factors))
        scored.sort(key=lambda x: -x[0])
        added, rejected = [], []
        for rank, (overall, i, c, r, rel, factors) in enumerate(scored):
            measured = med = None
            if rank < measure_top and not gate(c, r, rel, factors):
                measured, med = measured_interest([str(k) for k in c.get("keywords") or []])
                if measured is not None:
                    searches += 1
                    overall, factors = score_one(r, rel, measured)
            why = gate(c, r, rel, factors)
            now = time.time()
            status = "rejected" if why else "idea"
            scores = {"version": SCORING_VERSION, "factors": factors, "note": str(r.get("note", ""))[:300],
                      "median_views": med}
            tid = db.insert(
                "INSERT INTO topics(title, fingerprint, pillar, kind, sources, score, scores, status, discovered, updated, "
                "angle, hook, why, keywords, origin, fresh_until, reject_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                str(c["title"])[:200], fingerprint(c["title"]),
                name, c["kind"], json.dumps([s["url"] for s in c["_checked"]]), overall, json.dumps(scores), status, now, now,
                str(c.get("angle", ""))[:600], str(c.get("hook", ""))[:300], str(c.get("why", ""))[:600],
                json.dumps([str(k) for k in c.get("keywords") or []][:8]), "claude+web" + ("+youtube" if comp else ""),
                now + TRENDING_DAYS * 86400 if c["kind"] == "trending" else None, "; ".join(why) or None)
            for s in c["_checked"]:
                db.insert("INSERT INTO sources(topic_id, claim, url, title, retrieved, verdict, note, domain, tier, http_status) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", tid, s["claim"], s["url"], s["title"], now,
                          "reachable" if _reachable(s["http_status"]) else "unreachable", "", s["domain"], s["tier"],
                          s["http_status"])
            (rejected if why else added).append({"id": tid, "title": c["title"], "score": overall, "why": why})
        db.insert("INSERT INTO discovery_runs(t, pillar, found, added, duplicates, rejected, claude_calls, youtube_searches, detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", t0, name, len(cands) + dups, len(added), dups, len(rejected), calls, searches,
                  json.dumps({"seconds": round(time.time() - t0)}))
        guard.audit("topics.discover", True, {"pillar": name, "added": len(added), "rejected": len(rejected)})
        progress(f"{name}: kept {len(added)}, rejected {len(rejected)}")
        summary["added"] += added
        summary["rejected"] += rejected
        summary["duplicates"] += dups
    db.put("topics_last_discovery", time.time())
    return summary


def maybe_discover(now=None):
    """Background top-up: only with the studio on, a niche chosen, few ideas left and not too often."""
    now = now or time.time()
    if not config.enabled() or not niche() or not pillars():
        return None
    if len(pool(now)) >= MIN_POOL:
        return None
    if now - float(db.get("topics_last_discovery", 0) or 0) < DISCOVER_EVERY_HOURS * 3600:
        return None
    db.put("topics_last_discovery", now)              # even if it fails, don't retry every tick
    return discover(progress=lambda s: None)


# ---------- picking ----------

def pick_next(now=None, mark=True):
    """The best ready idea, nudged so pillars take turns and trending stays a minority. Marks it 'selected'."""
    from studio import analytics, lessons
    now = now or time.time()
    ideas = []
    for t in pool(now):
        if lessons.usable(t):
            ideas.append(t)
        elif mark:                                            # would fail for sure: don't spend a job on it
            set_status(t["id"], "rejected", reject_reason="fewer than 2 working sources (skipped before writing)")
            lessons.record("ideas", ["fewer than 2 working sources (skipped before writing)"], t)
    if not ideas:
        return None
    recent = [dict(r) for r in db.q("SELECT pillar, kind FROM topics WHERE selected IS NOT NULL ORDER BY selected DESC LIMIT 10")]
    last3 = [r["pillar"] for r in recent[:3]]
    trending_share = sum(r["kind"] == "trending" for r in recent) / len(recent) if recent else 0

    def effective(t):
        s = t["score"] or 0
        s -= 8 * last3.count(t["pillar"])                   # pillars take turns
        s += analytics.nudge(t["pillar"])                   # learned from real results (capped, 0 until measured)
        s -= lessons.risk(t, now)                           # learned from rejections (capped, fades after 30 days)
        if t["kind"] == "trending":
            if trending_share >= TRENDING_SHARE:
                s -= 15                                       # don't lean on trends
            elif t["fresh_until"]:
                s += 4 * max(0.0, (t["fresh_until"] - now) / (TRENDING_DAYS * 86400))   # use news while it's news
        return s

    best = max(ideas, key=lambda t: (effective(t), t["score"] or 0, -t["id"]))
    best["effective_score"] = round(effective(best), 1)
    if mark:
        set_status(best["id"], "selected", selected=now)
        db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", now, "topic",
                  f"picked #{best['id']}: {best['title']}",
                  json.dumps({"score": best["score"], "effective": best["effective_score"], "pillar": best["pillar"],
                              "kind": best["kind"], "recent_pillars": last3, "nudge": analytics.nudge(best["pillar"])}))
    return best


def expire(now=None):
    """Trending ideas that went stale are marked 'expired' (kept, so they never come back)."""
    now = now or time.time()
    return db.run("UPDATE topics SET status='expired', updated=? WHERE status='idea' AND fresh_until IS NOT NULL "
                  "AND fresh_until<=?", now, now)


def summary_text(limit=15):
    rows = pool()[:limit]
    out = [f"{len(pool())} ideas ready ({sum(1 for t in pool() if t['kind'] == 'trending')} trending)."]
    for t in rows:
        out.append(f"{t['score']:5.1f}  [{t['pillar']} / {t['kind']}] {t['title']}")
    return "\n".join(out)
