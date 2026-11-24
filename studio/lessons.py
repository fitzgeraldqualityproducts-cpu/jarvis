"""Learning from mistakes: every time a check stops something, Jarvis writes down why, sorts it
into a category, and the next attempts are told about it. Free: plain rules on the studio's own
records, no extra Claude calls.

record()       saves one rejection (script review, a topic that couldn't be used, a failed QA check,
               a YouTube rejection) with its category.
advice(area)   short instructions for the next writer / idea finder / visual planner, built from the
               categories that happened recently ("Scripts ran too long 2 times: aim for 85-95 words").
               A category that keeps happening gets firmer advice.
risk(topic)    0-10 points taken off an idea in pick_next when its pillar / type keeps failing.
usable(topic)  False for ideas that would fail for sure (fewer than 2 working sources), so a job
               isn't spent finding that out.
summary()      what's been learned, for the weekly report and `python -m studio lessons`."""
import re
import time

from studio import db

WINDOW_DAYS = 30
# category -> (area, regex over the problem text, advice; {n} = times in the window)
RULES = [
    ("too_long", "script", r"\d+ words; it must be .*cut|words; it must be \d+-\d+.*cut",
     "Scripts ran too long {n} time(s) recently: aim for 85-95 words in total, counting the hook."),
    ("too_short", "script", r"words; it must be .*add",
     "Scripts ran too short {n} time(s): aim for 75-95 words."),
    ("originality", "script", r"originality \d+(\.\d+)?/10|mass-produced template|too close to the script",
     "Scripts were judged unoriginal {n} time(s): explain it your own way with a specific worked example or "
     "comparison nobody else uses; no stock explainer phrasing."),
    ("unsupported", "script", r"is unsupported",
     "Lines were unsupported by the sources {n} time(s): every factual line must come straight from a listed fact. "
     "No constants, typical values or extra details the facts don't give (mark a truly needed value \"needs\")."),
    ("missing_value", "script", r"needed a value no source states",
     "Lines were dropped {n} time(s) because no page stated the value they needed: build the Short from the listed "
     "facts' own numbers."),
    ("wrong_fact", "script", r"is wrong",
     "Lines contradicted their sources {n} time(s): copy numbers and dates exactly as the sources give them."),
    ("calc", "script", r"no worked calculation|couldn't re-run|\bis [-\d.e+]+, not ",
     "Calculations failed {n} time(s): put every computed number in calcs with an expression that uses only fact numbers."),
    ("hook", "script", r"no hook passes",
     "Hooks failed the rules {n} time(s): under 16 words, a concrete number or a question, no clickbait."),
    ("no_checkable", "script", r"no checkable claims",
     "A script had no checkable facts {n} time(s): use at least three sourced numbers."),
    ("thin_sources", "ideas", r"fewer than 2 working sources|only one source|working sources",
     "Ideas were dropped for thin sources {n} time(s): give 3-4 facts per idea from different reputable sites "
     "(agencies, universities, journals), each a page you opened."),
    ("qa", "video", r"^qa:",
     "Finished videos failed QA {n} time(s) ({what}): keep on-screen text short and inside the frame."),
    ("youtube", "publish", r"youtube rejected",
     "YouTube rejected {n} upload(s): check the details before the next one."),
]
FIRM_AT = 3          # this many in the window -> the advice is marked MUST
RISK_PER_FAIL = 2.0
MAX_RISK = 8.0


def categorize(text):
    t = str(text).lower()
    for cat, _area, rx, _adv in RULES:
        if re.search(rx, t):
            return cat
    return "other"


def record(stage, problems, topic=None, video_id=None):
    """Saves each problem as a lesson. Returns the categories saved."""
    topic = topic or {}
    now, cats = time.time(), []
    for p in problems or []:
        if not str(p).strip() or str(p).startswith("editor: "):
            continue
        cat = categorize(p)
        db.insert("INSERT INTO lessons(t, stage, category, topic_id, video_id, pillar, kind, detail) "
                  "VALUES(?,?,?,?,?,?,?,?)", now, stage, cat, topic.get("id"), video_id, topic.get("pillar"),
                  topic.get("kind"), str(p)[:400])
        cats.append(cat)
    return cats


def counts(area=None, days=WINDOW_DAYS, now=None):
    since = (now or time.time()) - days * 86400
    areas = {c: a for c, a, _r, _v in RULES}
    out = {}
    for r in db.q("SELECT category, COUNT(*) n FROM lessons WHERE t>=? GROUP BY category", since):
        if area is None or areas.get(r["category"]) == area:
            out[r["category"]] = r["n"]
    return out


def advice(area, now=None):
    """Lines for a prompt (empty string until something has gone wrong)."""
    c = counts(area, now=now)
    lines = []
    for cat, a, _rx, adv in RULES:
        n = c.get(cat)
        if a != area or not n:
            continue
        what = ""
        if cat == "qa":
            since = (now or time.time()) - WINDOW_DAYS * 86400
            names = [r["detail"].split(":", 1)[1].split("(")[0].strip()
                     for r in db.q("SELECT detail FROM lessons WHERE category='qa' AND t>=? ORDER BY id DESC LIMIT 5", since)]
            what = ", ".join(dict.fromkeys(names)) or "various checks"
        lines.append(("MUST: " if n >= FIRM_AT else "") + adv.format(n=n, what=what))
    if not lines:
        return ""
    return "Lessons from this channel's own rejected work (follow them):\n" + "\n".join(f"- {l}" for l in lines)


def risk(topic, now=None):
    """Points off an idea whose pillar and type keep failing scripts (capped, fades after 30 days)."""
    since = (now or time.time()) - WINDOW_DAYS * 86400
    r = db.one("SELECT COUNT(DISTINCT topic_id) n FROM lessons WHERE t>=? AND stage IN ('script','ideas') AND "
               "pillar IS ? AND kind IS ?", since, topic.get("pillar"), topic.get("kind"))
    n = (r["n"] if r else 0) or 0
    ok = db.one("SELECT COUNT(*) n FROM topics WHERE pillar IS ? AND kind IS ? AND script_status='passed' "
                "AND updated>=?", topic.get("pillar"), topic.get("kind"), since)["n"] or 0
    return min(MAX_RISK, max(0.0, (n - ok) * RISK_PER_FAIL))


def usable(topic):
    """False when an idea is sure to fail before any Claude call (judged from its stored sources)."""
    from studio import topics
    srcs = topic.get("sources")
    if not isinstance(srcs, list) or not srcs or not isinstance(srcs[0], dict) or "http_status" not in srcs[0]:
        srcs = [dict(s) for s in db.q("SELECT * FROM sources WHERE topic_id=?", topic["id"])]
    return len(topics._working(srcs)) >= 2


def summary(now=None):
    c = counts(now=now)
    if not c:
        return "Nothing has been rejected in the last 30 days, so there are no lessons yet."
    total = sum(c.values())
    names = {"too_long": "too long", "too_short": "too short", "originality": "not original enough",
             "unsupported": "a line not backed by sources", "wrong_fact": "a line contradicting a source",
             "calc": "calculation problems", "hook": "weak hooks", "no_checkable": "no checkable facts",
             "thin_sources": "thin sources", "qa": "failed video checks", "youtube": "YouTube rejections",
             "other": "other problems"}
    parts = [f"{names.get(k, k)} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
    return (f"In the last 30 days the checks stopped work {total} time(s): " + ", ".join(parts) +
            ". Each of these is now told to the writer and the idea finder so it doesn't repeat.")
