"""Scripts (Phase 6): write, pick a hook, fact-check, revise.

write(topic)         Claude (no web) writes 5 hooks and a 25-45 s script from the topic's CHECKED
                     sources only. Every line says which facts it uses; every number that's worked
                     out is given as a calculation (an expression we can re-run here).
hook_checks(hook)    the hard rules for an opening line (short, concrete, honest, no clickbait).
review(draft, topic) a separate Claude pass WITH web search: each claim checked against its source,
                     the hooks scored, originality judged. Calculations are re-run in Python, so a
                     wrong number can't slip through on Claude's word.
make(topic, vid)     write -> review -> (one revision with the problems listed) -> review again.
                     Saves every version to the scripts table and claim verdicts to sources.
                     Raises ScriptRejected when the script still fails, so nothing unchecked moves on."""
import ast
import json
import math
import operator
import re
import time

from studio import db, guard

WORDS_MIN, WORDS_MAX = 60, 105          # 25-45 s: Kokoro am_michael at 1.05 reads about 143 words a minute
HOOK_MAX_WORDS = 16
CLICKBAIT = ("you won't believe", "you wont believe", "shocking", "insane", "mind-blowing", "mind blowing",
             "nobody is talking about", "they don't want you to know", "100% true", "in this video", "hey guys",
             "make sure to subscribe", "wait for it", "gone wrong")
HOOK_WEIGHTS = {"curiosity": 0.3, "clarity": 0.2, "specificity": 0.2, "honesty": 0.2, "speed": 0.1}
CALC_TOLERANCE = 0.03                   # a stated result may be rounded by up to 3%
SIMILAR_SCRIPT = 0.6                    # word overlap with an earlier script that counts as a remake


from studio.jobs import Permanent  # noqa: E402


class ScriptRejected(Permanent):
    """The script can't be made right; the job stops instead of retrying."""


# ---------- re-running calculations ----------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "log10": math.log10, "log": math.log, "exp": math.exp, "sin": math.sin,
          "cos": math.cos, "tan": math.tan, "radians": math.radians, "abs": abs, "round": round,
          "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e, "g": 9.81, "c": 299_792_458.0}


def calc(expr):
    """Safely evaluates a plain arithmetic expression (numbers, + - * / **, a few maths functions)."""
    expr = str(expr).replace("^", "**").replace("×", "*").replace("÷", "/")
    expr = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expr)                # 1,000 -> 1000 (not f(a, b))
    expr = re.sub(r"(?<=\d)_(?=\d)", "", expr)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(f"not plain arithmetic: {ast.dump(n)[:80]}")
    return float(ev(ast.parse(expr, mode="eval")))


def check_calcs(calcs):
    """[{expr, result, unit, ok, actual, problem}] for the script's worked-out numbers."""
    out = []
    for c in calcs or []:
        c = dict(c) if isinstance(c, dict) else {"expr": str(c)}
        try:
            actual = calc(c.get("expr", ""))
            stated = float(str(c.get("result")).replace(",", ""))
            ok = math.isclose(actual, stated, rel_tol=CALC_TOLERANCE, abs_tol=1e-9)
            c.update(actual=round(actual, 6), ok=ok,
                     problem=None if ok else f"{c.get('expr')} is {actual:.4g}, not {stated:g}")
        except Exception as e:
            c.update(actual=None, ok=False, problem=f"couldn't re-run {c.get('expr')!r}: {e}")
        out.append(c)
    return out


# ---------- hooks ----------

def hook_checks(hook):
    """Problems with an opening line (empty list = passes the hard rules)."""
    h = " ".join(str(hook).split())
    low = h.lower()
    problems = []
    words = len(h.split())
    if not h:
        return ["empty"]
    if words > HOOK_MAX_WORDS:
        problems.append(f"{words} words (max {HOOK_MAX_WORDS}, about 4 seconds)")
    for c in CLICKBAIT:
        if c in low:
            problems.append(f"clickbait phrase \"{c}\"")
    if not (re.search(r"\d", h) or "?" in h or re.search(r"\b(why|how|what|which|if)\b", low)):
        problems.append("no concrete number or question")
    if low.startswith(("so,", "so ", "hi", "hello", "today")):
        problems.append("slow start")
    return problems


def hook_score(rating):
    vals = {k: rating.get(k) for k in HOOK_WEIGHTS}
    have = {k: float(v) for k, v in vals.items() if isinstance(v, (int, float))}
    w = sum(HOOK_WEIGHTS[k] for k in have)
    return round(10 * sum(HOOK_WEIGHTS[k] * v for k, v in have.items()) / w, 1) if w else 0.0


def best_hook(hooks, ratings):
    """The highest-scoring hook that passes the hard rules: (index, score) or (None, 0)."""
    best = (None, 0.0)
    for i, h in enumerate(hooks):
        if hook_checks(h):
            continue
        s = hook_score(ratings.get(i) or {})
        if s > best[1]:
            best = (i, s)
    return best


# ---------- writing ----------

def _facts(topic):
    return [s for s in topic["sources"] if s.get("verdict") == "reachable"]


def _niche():
    from studio import topics
    d = topics.niche() or {}
    return d, d.get("plan") or {}


def write(topic, feedback=None, previous=None):
    """A draft: {"hooks": [5], "lines": [{"text", "facts": [ids], "visual"}], "calcs": [...], "titles": [...]}"""
    from studio import claude
    d, plan = _niche()
    facts = _facts(topic)
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} (source: {s['url']})" for i, s in enumerate(facts))
    fix = ""
    if feedback:
        fix = ("\n\nYour previous draft failed review. Fix EVERY problem below and change nothing else that was fine:\n"
               + "\n".join(f"- {p}" for p in feedback) + "\n\nPrevious draft:\n" + json.dumps(previous)[:6000])
    prompt = f"""Write a YouTube Short script. Channel: {d.get('name', '')}. Positioning: {plan.get('positioning', '')}.
Audience: {plan.get('audience', 'curious US adults')}. Style: {plan.get('style', 'calm, clear, conversational')}.
Topic: {topic['title']}
Angle (the calculation the Short shows): {topic.get('angle', '')}
Suggested opening: {topic.get('hook', '')}

FACTS YOU MAY USE (nothing else; never invent numbers, dates, names or quotes):
{fact_list}

Rules:
- {WORDS_MIN}-{WORDS_MAX} words in total, read aloud by one narrator (no stage directions in the text).
- Structure: hook (the first line, under 4 seconds), the setup, the calculation step by step with real
  numbers, the payoff, and a closing line that makes people want to rewatch or think (no "like and subscribe").
- Every factual line lists the fact ids it relies on. Every number you work out must appear in "calcs"
  as a plain arithmetic expression (e.g. "384400/3474") with its result and unit, using only the facts' numbers.
- Plain words, short sentences, US spelling and units with metric where useful. No hype words, no clickbait.
- Original: your own explanation and wording, not a summary of a known video.
- "visual" says what the screen shows for that line (diagram, chart, number, map, simple animation; no footage).
Also write 5 different hooks (each could replace line 1) and 3 title options (under 60 characters, honest).{fix}

JSON only: {{"hooks": ["..." x5], "lines": [{{"text": "...", "facts": ["F1"], "visual": "..."}}],
"calcs": [{{"expr": "...", "result": 0.0, "unit": "...", "says": "what it shows"}}], "titles": ["..." x3]}}"""
    ans = claude.ask_json(prompt, timeout=600, web=False, purpose=f"script: {topic['title']}")
    if not isinstance(ans, dict) or not ans.get("lines"):
        raise ScriptRejected("Claude didn't return a script")
    ans["lines"] = [ln for ln in ans["lines"] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    ans["hooks"] = [str(h) for h in (ans.get("hooks") or []) if str(h).strip()][:5]
    return ans


def text_of(draft, hook=None):
    lines = [str(ln["text"]).strip() for ln in draft["lines"]]
    if hook is not None and lines:
        lines[0] = hook
    return " ".join(lines)


def _overlap(a, b):
    from studio import topics
    wa, wb = topics._words(a), topics._words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def similar_script(text, video_id=None):
    """(score, video_id) of the most similar earlier passed script."""
    best = (0.0, None)
    for r in db.q("SELECT video_id, body FROM scripts WHERE status='passed' AND video_id IS NOT ?", video_id):
        s = _overlap(text, r["body"] or "")
        if s > best[0]:
            best = (round(s, 2), r["video_id"])
    return best


# ---------- review ----------

def review(draft, topic, video_id=None):
    """Independent check. Returns {"ok", "problems", "claims", "hooks", "hook", "calcs", ...}."""
    from studio import claude
    facts = _facts(topic)
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} <{s['url']}>" for i, s in enumerate(facts))
    lines = "\n".join(f"L{i + 1}. {ln['text']}  [uses: {', '.join(map(str, ln.get('facts') or [])) or 'none'}]"
                      for i, ln in enumerate(draft["lines"]))
    hooks = "\n".join(f"H{i}. {h}" for i, h in enumerate(draft["hooks"]))
    ans = claude.ask_json(f"""You are a strict fact-checker and editor for a science Shorts channel. Use web search to open the
sources and confirm. Do not trust the writer.

Sources:
{fact_list}

Script lines:
{lines}

Hooks:
{hooks}

For EVERY script line that states a fact or number, give a verdict: "supported" (a source says it, or it follows by
arithmetic from sourced numbers), "unsupported" (no source backs it) or "wrong" (a source contradicts it; give the
correct statement and the URL). Opinion or transition lines get "n/a".
Rate each hook 0-10 on curiosity, clarity, specificity, honesty (10 = fully delivered by the script, no bait) and
speed (gets to the point at once).
Also: "originality" 0-10 (own explanation and wording vs a generic or copied explainer), "inauthentic_risk"
"low/medium/high" (does it read like mass-produced template content?), and "problems": anything else a careful
editor would fix (confusing step, misleading simplification, unit mistake).

JSON only: {{"claims": [{{"line": 1, "verdict": "supported|unsupported|wrong|n/a", "fact": "F1", "note": "..."}}],
"hooks": {{"0": {{"curiosity": 0, "clarity": 0, "specificity": 0, "honesty": 0, "speed": 0}}}},
"originality": 0, "inauthentic_risk": "low", "problems": ["..."]}}""", timeout=900, web=True, purpose=f"fact-check: {topic['title']}")
    if not isinstance(ans, dict):
        raise ScriptRejected("the fact-check didn't return a result")
    problems = []
    claims = [c for c in ans.get("claims") or [] if isinstance(c, dict)]
    for c in claims:
        if c.get("verdict") in ("unsupported", "wrong"):
            problems.append(f"line {c.get('line')} is {c['verdict']}: {c.get('note', '')}".strip())
    factual = sum(1 for c in claims if c.get("verdict") in ("supported", "unsupported", "wrong"))
    if factual == 0:
        problems.append("the fact-check found no checkable claims, so nothing was verified")
    calcs = check_calcs(draft.get("calcs"))
    problems += [c["problem"] for c in calcs if not c["ok"]]
    if not calcs:
        problems.append("no worked calculation, but this channel always shows the math")
    ratings = {}
    for k, v in (ans.get("hooks") or {}).items():
        if str(k).lstrip("H").isdigit() and isinstance(v, dict):
            ratings[int(str(k).lstrip("H"))] = v
    hi, hs = best_hook(draft["hooks"], ratings)
    if hi is None:
        problems.append("no hook passes the rules: " + "; ".join(f"\"{h}\": {', '.join(hook_checks(h))}"
                                                              for h in draft["hooks"][:3]))
    hook = draft["hooks"][hi] if hi is not None else (draft["lines"][0]["text"] if draft["lines"] else "")
    body = text_of(draft, hook)
    words = len(body.split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        problems.append(f"{words} words; it must be {WORDS_MIN}-{WORDS_MAX} (25-45 seconds)")
    orig = ans.get("originality")
    if isinstance(orig, (int, float)) and orig < 6:
        problems.append(f"originality {orig}/10: needs its own explanation")
    if str(ans.get("inauthentic_risk", "")).lower() == "high":
        problems.append("reads like mass-produced template content")
    sim, sim_vid = similar_script(body, video_id)
    if sim >= SIMILAR_SCRIPT:
        problems.append(f"too close to the script of video {sim_vid} ({sim:.0%} word overlap)")
    problems += [f"editor: {p}" for p in ans.get("problems") or [] if isinstance(p, str) and p.strip()][:5]
    hard = [p for p in problems if not p.startswith("editor: ")]
    return {"ok": not hard, "problems": problems, "hard_problems": hard, "claims": claims, "hook_ratings": ratings,
            "hook_index": hi, "hook_score": hs, "hook": hook, "body": body, "words": words, "calcs": calcs,
            "originality": orig, "inauthentic_risk": ans.get("inauthentic_risk"), "similar": [sim, sim_vid]}


# ---------- the whole step (resumable: each Claude call is its own step) ----------

def _check_topic(topic):
    from studio import topics
    if len(topics._working(_facts(topic))) < 2:
        raise ScriptRejected("the topic has fewer than 2 working sources")


def draft_step(topic, video_id=None, feedback=None, previous=None):
    """Writes (or revises) a draft and saves it as the next version. Returns the scripts row id."""
    _check_topic(topic)
    draft = write(topic, feedback, previous)
    v = db.one("SELECT MAX(version) v FROM scripts WHERE video_id IS ?", video_id)["v"] or 0
    hook = draft["hooks"][0] if draft["hooks"] else draft["lines"][0]["text"]
    return db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", video_id, v + 1, json.dumps(draft["hooks"]), hook, text_of(draft),
                     json.dumps({"lines": draft["lines"], "calcs": draft.get("calcs") or [], "titles": draft.get("titles") or []}),
                     "{}", "draft", time.time())


def _draft_of(row):
    st = json.loads(row["structure"] or "{}")
    return {"hooks": json.loads(row["hooks"] or "[]"), "lines": st.get("lines") or [], "calcs": st.get("calcs") or [],
            "titles": st.get("titles") or []}


def check_step(topic, video_id=None, max_revisions=1):
    """Fact-checks the newest draft. Returns ("passed", row) or ("revise", row); raises ScriptRejected
    when the last allowed revision still fails."""
    from studio import topics
    row = db.one("SELECT * FROM scripts WHERE video_id IS ? AND status='draft' ORDER BY version DESC LIMIT 1", video_id)
    if not row:
        raise ScriptRejected("there's no draft to check")
    draft = _draft_of(row)
    rev = review(draft, topic, video_id)
    checks = {k: rev[k] for k in ("ok", "problems", "hard_problems", "claims", "hook_ratings", "hook_index",
                                  "hook_score", "words", "originality", "inauthentic_risk", "similar")}
    st = json.loads(row["structure"] or "{}")
    st["calcs"] = rev["calcs"]
    db.run("UPDATE scripts SET hook=?, body=?, structure=?, checks=?, status=? WHERE id=?", rev["hook"], rev["body"],
           json.dumps(st), json.dumps(checks), "passed" if rev["ok"] else "failed", row["id"])
    row = dict(db.one("SELECT * FROM scripts WHERE id=?", row["id"]))
    if rev["ok"]:
        _record_claims(topic, video_id, rev)
        topics.set_status(topic["id"], script_status="passed")
        guard.audit("script.passed", True, {"topic": topic["id"], "video": video_id, "version": row["version"]})
        return "passed", row
    tries = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    if tries > max_revisions:
        topics.set_status(topic["id"], script_status="failed")
        guard.audit("script.failed", True, {"topic": topic["id"], "video": video_id, "problems": rev["hard_problems"][:5]})
        raise ScriptRejected("the script still failed the fact-check: " + "; ".join(rev["hard_problems"][:4]))
    return "revise", row


def revise_step(topic, video_id=None):
    row = db.one("SELECT * FROM scripts WHERE video_id IS ? AND status='failed' ORDER BY version DESC LIMIT 1", video_id)
    return draft_step(topic, video_id, json.loads(row["checks"] or "{}").get("problems") or [], _draft_of(row))


def make(topic, video_id=None, progress=print, max_revisions=1):
    """Write -> fact-check -> (revise -> fact-check). Returns the passed script row."""
    guard.allow("claude.ask", f"script for topic {topic['id']}", record=False)
    progress("Writing the script")
    draft_step(topic, video_id)
    while True:
        progress("Fact-checking every claim and number")
        result, row = check_step(topic, video_id, max_revisions)
        if result == "passed":
            return row
        progress(f"Revising ({len(json.loads(row['checks'])['problems'])} problems found)")
        revise_step(topic, video_id)


def _record_claims(topic, video_id, rev):
    facts = _facts(topic)
    now = time.time()
    for c in rev["claims"]:
        if c.get("verdict") not in ("supported", "unsupported", "wrong"):
            continue
        m = re.match(r"F(\d+)", str(c.get("fact") or ""))
        src = facts[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(facts) else {}
        db.insert("INSERT INTO sources(video_id, topic_id, claim, url, title, retrieved, verdict, note, domain, tier, http_status) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)", video_id, topic["id"], f"line {c.get('line')}", src.get("url"),
                  src.get("title"), now, c["verdict"], str(c.get("note", ""))[:400], src.get("domain"), src.get("tier"),
                  src.get("http_status"))


def latest(video_id, status="passed"):
    r = db.one("SELECT * FROM scripts WHERE video_id IS ? AND status=? ORDER BY version DESC LIMIT 1", video_id, status)
    return dict(r) if r else None


def report(video_id):
    """A readable Markdown page of the passed script, its checks and sources."""
    row = latest(video_id, "passed")
    if not row:
        return ""
    st, ck = json.loads(row["structure"]), json.loads(row["checks"])
    lines = [f"# {db.video(video_id)['title']}", "", f"**Hook** (score {ck.get('hook_score')}): {row['hook']}", "",
             "## Script", ""]
    for i, ln in enumerate(st["lines"], 1):
        text = row["hook"] if i == 1 else ln["text"]
        lines.append(f"{i}. {text}  \n   _screen: {ln.get('visual', '')}_")
    lines += ["", f"{ck.get('words')} words. Originality {ck.get('originality')}/10, template risk {ck.get('inauthentic_risk')}.",
              "", "## Calculations (re-run by Jarvis)", ""]
    lines += [f"- {c.get('says', '')}: `{c.get('expr')}` = {c.get('actual')} {c.get('unit', '')} {'✓' if c.get('ok') else '✗'}"
              for c in st.get("calcs") or []]
    lines += ["", "## Fact-check", ""]
    lines += [f"- line {c.get('line')}: **{c.get('verdict')}** {c.get('note', '')}" for c in ck.get("claims") or []]
    lines += ["", "## Title ideas", ""] + [f"- {t}" for t in st.get("titles") or []]
    lines += ["", "## Other hooks", ""] + [f"- {h}" for h in json.loads(row["hooks"]) if h != row["hook"]]
    if ck.get("problems"):
        lines += ["", "## Editor notes", ""] + [f"- {p}" for p in ck["problems"]]
    return "\n".join(lines) + "\n"


def hook_checks_title(title):
    """Problems with a video title (empty list = fine): honest, short, no clickbait."""
    t = " ".join(str(title).split())
    problems = []
    if not t:
        return ["empty"]
    if len(t) > 70:
        problems.append(f"{len(t)} characters (max 70)")
    low = t.lower()
    problems += [f"clickbait phrase \"{c}\"" for c in CLICKBAIT if c in low]
    if t.isupper() and len(t) > 12:
        problems.append("all capitals")
    return problems
