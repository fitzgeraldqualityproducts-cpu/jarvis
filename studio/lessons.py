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
JUDGE_ADVICE = {
    "hook": "the hook: the number or the question must land in the first three words, concrete and specific",
    "payoff": "the payoff: end on a result that genuinely surprises, said as a concrete comparison the viewer can picture",
    "clarity": "clarity: one step per line, round numbers with their units, nothing the ear has to hold for long",
    "momentum": "momentum: an honest re-hook halfway and no line that repeats or idles",
    "you": "talking to the viewer: put the calculation in their own world with \"you\"",
    "original": "originality: not the standard textbook analogy, one unusual image of your own",
}
RULES = [
    *[(f"weak_{k}", "script", rf"^judge: weaker {k}\b",
       f"Side-by-side drafts lost on {v.split(': ')[0]} {{n}} time(s): {v.split(': ', 1)[1]}.")
      for k, v in JUDGE_ADVICE.items()],
    ("one_question", "script", r"^one question:",
     "Scripts were sent back {n} time(s) because the payoff didn't answer the hook's question: pick the one question "
     "first, and make the payoff answer it in plain words."),
    ("viewer_lost", "script", r"^clarity: a first-time viewer",
     "A first-time viewer got lost {n} time(s): one step per line, no skipped step, and tie every line to the main point."),
    ("story_math", "script", r"^narrative: (unexplained number|line \d+ says .* before the viewer|line \d+ relies on|"
                             r"line \d+ says .*no fact, calculation)|^narrative: the starting value",
     "Stories used numbers the viewer couldn't trace {n} time(s): say every input before a worked-out number, derive "
     "every multiplier out loud, and introduce each starting value (with its unit) before the line that uses it."),
    ("story_units", "script", r"^narrative: (unit slip|the formula .* (doesn't work|gives|works out))",
     "Units or formulas didn't add up {n} time(s): give every value its unit and say each conversion in one step."),
    ("story_model", "script", r"^narrative: the physics model",
     "The physics model was wrong for the situation {n} time(s): establish what physically happens before choosing "
     "the formula, and check the formula describes THAT situation."),
    ("story_logic", "script", r"^narrative: (line \d+ leans on|the conclusion|unclear premise|explain-it-to-a-stranger)",
     "Stories didn't follow from line to line {n} time(s): open with what physically happens and the one question, "
     "make every line follow from the one before, and end on the answer to that question."),
    ("stock_phrase", "script", r"stock phrase",
     "Drafts were dropped {n} time(s) for stock explainer phrases: say the re-hook and transitions in your own words."),
    ("too_long", "script", r"\d+ words; it must be .*cut|words; it must be \d+-\d+.*cut",
     "Scripts ran too long {n} time(s) recently: aim for 85-95 words in total, counting the hook."),
    ("too_short", "script", r"words; it must be .*add",
     "Scripts ran too short {n} time(s): aim for 75-95 words."),
    ("originality", "script", r"originality \d+(\.\d+)?/10|mass-produced template|too close to the script",
     "Scripts were judged unoriginal {n} time(s), usually 5/10 for the standard textbook explanation: build the whole "
     "Short around ONE concrete, unusual image (a specific object, place or everyday moment) that carries the "
     "calculation; never the usual analogy or diagram for the topic, no stock explainer phrasing."),
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
    ("retention", "retention", r"^retention:",
     "Real viewers left fastest during some lines {n} time(s): see the writer's real-viewer advice."),
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
    if area == "script":
        since = (now or time.time()) - WINDOW_DAYS * 86400
        notes = [r["detail"].split(" | ", 1)[1] for r in db.q(
            "SELECT detail FROM lessons WHERE stage='draft' AND category LIKE 'weak_%' AND detail LIKE '% | %' AND t>=? "
            "ORDER BY id DESC LIMIT 12", since)]
        notes = list(dict.fromkeys(n.strip() for n in notes if n.strip()))[:3]
        if notes:
            lines.append("Recent judge notes on drafts that lost: " + " / ".join(f"\"{n[:160]}\"" for n in notes))
    if area == "script":
        try:
            from studio import storymemory
            story = storymemory.advice(now=now)
        except Exception:
            story = ""
        if story:
            lines.append(story)
    if area == "script":
        try:
            from studio import retention
            real = retention.advice()
        except Exception:
            real = ""
        if real:
            lines.append(real)
        try:
            from studio import packaging
            pk = packaging.advice()
            ops = packaging.openings_advice()
        except Exception:
            pk = ops = ""
        if pk:
            lines.append(pk)
        if ops:
            lines.append(ops)
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
    real = c.pop("retention", 0)              # not a stop: lines real viewers left during (retention.py)
    if not c and real:
        return (f"Nothing has been rejected in the last 30 days. Real viewers left fastest during {real} line(s), "
                "and that is told to the writer.")
    if not c:
        return "Nothing has been rejected in the last 30 days, so there are no lessons yet."
    total = sum(c.values())
    names = {"too_long": "too long", "too_short": "too short", "originality": "not original enough",
             "unsupported": "a line not backed by sources", "wrong_fact": "a line contradicting a source",
             "calc": "calculation problems", "hook": "weak hooks", "no_checkable": "no checkable facts",
             "thin_sources": "thin sources", "stock_phrase": "stock phrases in drafts",
             **{f"weak_{k}": f"drafts that lost on {k}" for k in JUDGE_ADVICE}, "qa": "failed video checks", "youtube": "YouTube rejections",
             "retention": "lines where real viewers left fastest",
             "story_math": "numbers the viewer couldn't trace", "story_units": "unit or formula slips",
             "story_model": "the wrong physics model", "story_logic": "stories that didn't follow",
             "other": "other problems"}
    parts = [f"{names.get(k, k)} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
    return (f"In the last 30 days the checks stopped work {total} time(s): " + ", ".join(parts) +
            ". Each of these is now told to the writer and the idea finder so it doesn't repeat.")
