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
HOOK_MAX_WORDS = 12          # about 4 seconds; the grabbing part must come in the first few words
HOOK_GRAB_WORDS = 6
CLICKBAIT = ("you won't believe", "you wont believe", "shocking", "insane", "mind-blowing", "mind blowing",
             "nobody is talking about", "they don't want you to know", "100% true", "in this video", "hey guys",
             "make sure to subscribe", "wait for it", "gone wrong")
HOOK_WEIGHTS = {"curiosity": 0.3, "clarity": 0.2, "specificity": 0.2, "honesty": 0.2, "speed": 0.1}
CALC_TOLERANCE = 0.03                   # a stated result may be rounded by up to 3%
MAX_REVISIONS = 2                       # a draft and up to two rewrites with the reviewer's notes
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
    if low.startswith(("so,", "so ", "hi", "hello", "today", "in this video", "have you ever", "let's", "did you know")):
        problems.append("slow start")
    first = " ".join(h.split()[:HOOK_GRAB_WORDS])
    if not problems and not (re.search(r"\d", first) or "?" in first and len(h.split()) <= HOOK_GRAB_WORDS or
                             re.search(r"\b(why|how|what|which|if|never|only|every|no)\b", first.lower())):
        problems.append(f"the grabbing part comes too late (put the number or the question in the first {HOOK_GRAB_WORDS} words)")
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
    from studio import lessons
    learned = lessons.advice("script")
    learned = ("\n\n" + learned) if learned else ""
    fix = ""
    if feedback:
        tips = [p.split("(", 1)[1].rstrip(")") for p in feedback if p.startswith("originality ") and "(" in p]
        idea = ("\n\nThe reviewer found it too generic. Rebuild the Short around this idea, as one image that runs from "
                "the hook to the last line: " + tips[-1]) if tips else ""
        fix = ("\n\nYour previous draft failed review. Fix EVERY problem below and change nothing else that was fine. "
               "Don't add any new claim while fixing:\n"
               + "\n".join(f"- {p}" for p in feedback) + idea + "\n\nPrevious draft:\n" + json.dumps(previous)[:6000])
    prompt = f"""Write a YouTube Short script. Channel: {d.get('name', '')}. Positioning: {plan.get('positioning', '')}.
Audience: {plan.get('audience', 'curious US adults')}. Style: {plan.get('style', 'calm, clear, conversational')}.
Topic: {topic['title']}
Angle (the calculation the Short shows): {topic.get('angle', '')}
Suggested opening: {topic.get('hook', '')}

FACTS YOU MAY USE (nothing else; never invent numbers, dates, names or quotes):
{fact_list}

Rules:
- {WORDS_MIN}-{WORDS_MAX} words in total (aim for {WORDS_MIN + 20}-{WORDS_MAX - 10}; count them before answering),
  read aloud by one narrator (no stage directions in the text).
- Structure: hook (the first line, at most {HOOK_MAX_WORDS} words, with the surprising number or the question in its
  first {HOOK_GRAB_WORDS} words, no warm-up like "did you know" or "in this video"), the setup, the calculation step by
  step with real numbers, the payoff, and a LOOP ending: the last line must lead straight back into the first line,
  so when the Short repeats it plays as one continuous thought (for example the last line sets up the hook's
  question again, or ends mid-thought that the hook completes). No outro, no "like and subscribe".
- Every factual line lists the fact ids it relies on. Every number you work out must appear in "calcs"
  as a plain arithmetic expression (e.g. "384400/3474") with its result and unit, using only the facts' numbers.
- No made-up caveats or framing: never add a hedge, caveat or "honest uncertainty" line that claims anything the
  facts don't say (like "the forecast could change" or "this is an average"). A transition is a question or a plain
  link with no claim in it.
- Plain words, short sentences, US spelling and units with metric where useful. No hype words, no clickbait.
- Original: your own explanation and wording, not a summary of a known video. Make it yours with ONE concrete,
  unusual image or everyday example of your own (a specific object, place or moment, like a kitchen, a car,
  a stadium, a phone) that carries the calculation from the hook to the last line, and say the result in a way a
  textbook wouldn't. Not the standard textbook analogy or diagram for this topic: that scores 5/10. Avoid stock explainer lines ("here's the thing", "it turns out",
  "scientists say", "mind-blowing").
- Standard textbook science (a named law or formula such as Newton's laws, Kepler's third law, E = mc^2, or a
  well-known constant like g = 9.8 m/s^2 or the speed of light) may be used without a fact: mark that line's facts
  as ["general"]. Every number about THIS topic must still come from the facts above.
- No number, constant or reference value beyond the facts above, apart from the standard textbook science just
  described. Never call a value "typical" or "about" to get around this. If the explanation truly needs a value the
  facts don't give (like the wavelength of red light), write that line with the value, mark its facts ["needs"]
  and list it in "needs". Jarvis then looks for a real page that states it; if none does, the line is dropped. At
  most two needs, and only when the Short doesn't work without them.
- "visual" says what the screen shows for that line (diagram, chart, number, map, simple animation; no footage).
Also write 5 different hooks (each could replace line 1) and 3 title options (under 60 characters, honest).{learned}{fix}

JSON only: {{"hooks": ["..." x5], "lines": [{{"text": "...", "facts": ["F1"], "visual": "..."}}],
"calcs": [{{"expr": "...", "result": 0.0, "unit": "...", "says": "what it shows"}}], "titles": ["..." x3],
"needs": [{{"line": 4, "what": "the wavelength of red light", "value": "700 nm"}}]}} ("needs" is usually empty)"""
    ans = None
    if not feedback and not previous and _local_draft_turn():
        ans = _local_draft(prompt, facts)              # the trial: a first draft written on this computer
    if ans is None:
        ans = claude.ask_json(prompt, timeout=600, web=False, purpose=f"script: {topic['title']}")
    if not isinstance(ans, dict) or not ans.get("lines"):
        raise ScriptRejected("Claude didn't return a script")
    ans["lines"] = [ln for ln in ans["lines"] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    ans["hooks"] = [str(h) for h in (ans.get("hooks") or []) if str(h).strip()][:5]
    ans = fill_needs(ans, topic)
    for _ in range(2):                            # wrong length: fix it here, before a costly fact-check
        n = len(text_of(ans, ans["hooks"][0] if ans["hooks"] else None).split())
        if WORDS_MIN <= n <= WORDS_MAX:
            break
        ans = _fit_length(ans, n) or ans
    return ans


MAX_NEEDS = 2


def fill_needs(draft, topic):
    """Lines the writer marked "needs" use a value the topic's sources don't give. One web search looks for a
    reputable page that states each one; Jarvis then downloads that page itself and keeps the value only if the
    page's own text has its numbers. A kept page becomes one more source of the topic (with its link, cited in the
    description) and the line points at it, so the fact-checker checks it like any other line. Anything else is
    dropped: a line is never left resting on the writer's word."""
    from studio import lessons, topics
    marked = [i for i, ln in enumerate(draft.get("lines") or [], 1) if "needs" in [str(f) for f in ln.get("facts") or []]]
    asked = {}
    for nd in draft.get("needs") or []:
        if isinstance(nd, dict) and str(nd.get("line", "")).isdigit() and int(nd["line"]) in marked:
            asked.setdefault(int(nd["line"]), {"what": str(nd.get("what") or "")[:200], "value": str(nd.get("value") or "")[:60]})
    draft.pop("needs", None)
    if not marked:
        return draft
    found = {}
    todo = {k: v for k, v in list(asked.items())[:MAX_NEEDS] if v["what"] and topics._nums(v["value"])}
    if todo:
        from studio import claude
        ans = claude.ask_json(
            "Use web search. For each value below, find ONE reputable page (a university, government or science "
            "agency, standards body, encyclopedia or textbook publisher) whose own text states that value. Only "
            "pages you opened; if none states it, leave it out. Don't round or adjust the value.\n\n"
            + "\n".join(f"{k}. {v['what']}: {v['value']}" for k, v in todo.items())
            + '\n\nJSON object keyed by the number: {"4": {"url": "https://...", "quote": "the page\'s own sentence", '
            '"source": "publisher"}}', timeout=300, web=True, purpose=f"look up values: {topic['title']}")
        for k, v in todo.items():
            hit = ans.get(str(k)) if isinstance(ans, dict) else None
            url = str((hit or {}).get("url") or "").strip() if isinstance(hit, dict) else ""
            if not url.startswith(("http://", "https://")):
                continue
            text = topics.page_text(url)               # the value only counts if it's on the downloaded page
            if not text or not topics._nums(v["value"]) <= topics._nums(text):
                continue
            status = topics.check_url(url)
            if not topics._reachable(status):
                continue
            claim = f"{v['what']}: {v['value']}"
            row = {"topic_id": topic["id"], "claim": claim[:400], "url": url[:500], "title": str(hit.get("source") or "")[:200],
                   "domain": topics.domain(url), "tier": topics.tier(url), "http_status": status, "verdict": "reachable"}
            row["id"] = db.insert("INSERT INTO sources(topic_id, claim, url, title, domain, tier, http_status, verdict, retrieved) "
                                  "VALUES(?,?,?,?,?,?,?,?,?)", row["topic_id"], row["claim"], row["url"], row["title"],
                                  row["domain"], row["tier"], row["http_status"], "reachable", time.time())
            topic.setdefault("sources", []).append(row)      # same order as topics.topic() reloads them (by id)
            found[k] = f"F{len(_facts(topic))}"
    lines, dropped = [], []
    for i, ln in enumerate(draft["lines"], 1):
        if i in marked:
            if i not in found:
                dropped.append(asked.get(i, {}).get("what") or f"line {i}")
                continue
            ln = dict(ln, facts=[found[i] if str(f) == "needs" else f for f in ln.get("facts") or []])
        lines.append(ln)
    draft["lines"] = lines
    if dropped:
        lessons.record("script", [f"a line needed a value no source states ({w}), so it was dropped" for w in dropped],
                       topic)
    return draft


def _fit_length(draft, n):
    """Asks Claude to shorten (or lengthen) the lines to fit, keeping every fact id, number and calc."""
    from studio import claude
    goal = "Cut" if n > WORDS_MAX else "Add"
    target = f"{WORDS_MAX - 15}-{WORDS_MAX - 5}" if n > WORDS_MAX else f"{WORDS_MIN + 10}-{WORDS_MIN + 25}"
    fitted = _local_fit(draft, n, goal, target)        # on this computer first, checked before it's used
    if fitted:
        return fitted
    ans = claude.ask_json(f"""This Short script is {n} words with its first hook as line 1; it must be {WORDS_MIN}-{WORDS_MAX}.
{goal} words so the whole thing is {target} words. Keep every number, fact id and the calculation; keep the
meaning and the closing line. Change nothing else. Each hook must stay under {HOOK_MAX_WORDS} words.

{json.dumps(draft)[:6000]}

JSON only, the same shape: {{"hooks": [...], "lines": [{{"text": "...", "facts": [...], "visual": "..."}}],
"calcs": [...], "titles": [...]}}""", timeout=300, web=False, purpose="script length")
    if not isinstance(ans, dict) or not ans.get("lines"):
        return None
    lines = [ln for ln in ans["lines"] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    if not lines:
        return None
    out = dict(draft, lines=lines)
    hooks = [str(h) for h in (ans.get("hooks") or []) if str(h).strip()][:5]
    if hooks:
        out["hooks"] = hooks
    if ans.get("calcs"):
        out["calcs"] = ans["calcs"]
    return out


# ---------- the local brain (Ollama on this computer, local_brain.py) ----------
# Claude still fact-checks every script. The local model only writes some first drafts (a measured trial
# that turns itself off if its drafts do worse than Claude's) and fixes the word count, and both are
# checked here before they're used.

TRIAL_KEY = "local_draft_trial"
TRIAL_MIN = 5                     # finished scripts from each writer before the trial decides


def _local():
    try:
        from studio.claude import _local as get
        return get()
    except Exception:
        return None


def _numbers(text):
    return set(re.findall(r"\d+(?:[.,]\d+)*", str(text).replace(",", "")))


def _trial():
    d = db.get(TRIAL_KEY, {}) or {}
    for k in ("local", "claude"):
        d.setdefault(k, {"n": 0, "passed": 0, "reviews": 0})
    d.setdefault("invalid", 0)
    d.setdefault("tried", 0)
    return d


def _local_draft_turn():
    """Should the local model write this first draft? "on", "off", or "trial": every other draft until
    the trial has enough finished scripts to decide, then whatever it decided."""
    local = _local()
    if not local:
        return False
    mode = str(local.cfg().get("studio_drafts", "trial")).lower()
    if mode in ("off", "false") or not local.ready("studio_drafts")[0]:
        return False
    if mode in ("on", "true"):
        return True
    d = _trial()
    if d.get("verdict"):
        return d["verdict"] == "on"
    if d["tried"] >= 4 and d["invalid"] / d["tried"] > 0.6:
        return False                              # it mostly can't write a usable draft: not worth the wait
    turn = d.get("next", "local")
    d["next"] = "claude" if turn == "local" else "local"
    db.put(TRIAL_KEY, d)
    return turn == "local"


def _local_draft(prompt, facts):
    """A first draft from the local model, or None (Claude writes it). Only kept if it follows the rules
    the code can check: fact ids that exist, calculations that re-run correctly, no number from nowhere."""
    from studio import claude
    local = _local()
    d = _trial()
    d["tried"] += 1
    ans = local.chat_json([{"role": "system", "content": claude.SYSTEM.format(date=time.strftime("%B %d, %Y"))},
                           {"role": "user", "content": prompt}],
                          max_tokens=1600, timeout=300, first_timeout=90, num_ctx=8192, temperature=0.7)
    why = _draft_problem(ans, facts)
    if why:
        d["invalid"] += 1
        db.put(TRIAL_KEY, d)
        print(f"(local brain: its draft wasn't usable ({why}), Claude is writing it)")
        return None
    db.put(TRIAL_KEY, d)
    ans["by"] = "local"
    local.saved("studio_draft")
    print("(local brain wrote the first draft; Claude will fact-check it)")
    return ans


def _draft_problem(ans, facts):
    if not isinstance(ans, dict) or not isinstance(ans.get("lines"), list) or len(ans["lines"]) < 4:
        return "no script"
    ids = {f"F{i + 1}" for i in range(len(facts))} | {"general", "needs"}
    hooks = [str(h) for h in ans.get("hooks") or [] if str(h).strip()]
    if len(hooks) < 3:
        return "too few hooks"
    for ln in ans["lines"]:
        if not isinstance(ln, dict) or not str(ln.get("text", "")).strip():
            return "an empty line"
        used = [str(f) for f in ln.get("facts") or []]
        if any(f not in ids for f in used):
            return "a fact id that doesn't exist"
    calcs = check_calcs(ans.get("calcs"))
    if not calcs or not all(c["ok"] for c in calcs):
        return "a calculation that doesn't re-run"
    known = set().union(*[_numbers(f.get("claim", "")) for f in facts]) if facts else set()
    for c in calcs:
        known |= _numbers(c.get("expr", "")) | _numbers(c.get("result", ""))
        try:
            v = float(c.get("actual"))
            known |= {f"{v:g}", str(round(v)), str(int(v)), f"{v:.1f}", f"{v:.2f}", str(round(v * 100))}
        except (TypeError, ValueError):
            pass
    said = _numbers(" ".join(str(ln["text"]) for ln in ans["lines"]) + " " + " ".join(hooks))
    loose = {n for n in said if n not in known and not (n.isdigit() and int(n) <= 12)}
    if loose:
        return "numbers that aren't in the facts: " + ", ".join(sorted(loose)[:4])
    return None


def _local_fit(draft, n, goal, target):
    """The word count fixed on this computer, or None. Kept only if every number, fact id and the closing
    line's numbers survive and it now fits."""
    local = _local()
    if not local or not local.ready("studio_length")[0]:
        return None
    lines = "\n".join(f"L{i + 1}. {ln['text']}" for i, ln in enumerate(draft["lines"]))
    ans = local.chat_json([{"role": "user", "content": f"""This Short script is {n} words; it must be {target} words.
{goal} words: reword or {"shorten" if goal == "Cut" else "lengthen"} lines. Keep every number exactly, keep every line's
meaning and the last line. Don't add any new fact or number. Each hook must stay under {HOOK_MAX_WORDS} words.

Hooks:
""" + "\n".join(f"H{i}. {h}" for i, h in enumerate(draft["hooks"])) + f"""

Lines:
{lines}

JSON only: {{"hooks": ["..."], "lines": ["line 1 text", "line 2 text", ...]}} with the same number of lines ({len(draft["lines"])}) in order."""}],
                          max_tokens=900, timeout=120, first_timeout=60, num_ctx=4096, temperature=0.3)
    if not ans:
        return None
    new = [str(x).strip() for x in ans.get("lines") or []]
    hooks = [str(h).strip() for h in ans.get("hooks") or [] if str(h).strip()]
    if len(new) != len(draft["lines"]) or not all(new) or (hooks and len(hooks) != len(draft["hooks"])):
        return None
    for old, now in zip(draft["lines"], new):
        if _numbers(old["text"]) != _numbers(now):
            return None
    for old, now in zip(draft["hooks"], hooks):
        if _numbers(old) != _numbers(now) or len(now.split()) > HOOK_MAX_WORDS:
            return None
    out = dict(draft, lines=[dict(old, text=now) for old, now in zip(draft["lines"], new)])
    if hooks:
        out["hooks"] = hooks
    words = len(text_of(out, out["hooks"][0] if out.get("hooks") else None).split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        return None
    local.saved("studio_length")
    print(f"(local brain fitted the script to {words} words)")
    return out


def _trial_outcome(topic, video_id, passed):
    """Records how a finished script did, by who wrote its first draft, and decides the trial when there's enough."""
    try:
        first = db.one("SELECT structure FROM scripts WHERE video_id IS ? ORDER BY version LIMIT 1", video_id)
        if not first:
            return
        by = json.loads(first["structure"] or "{}").get("by") or "claude"
        reviews = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status IN ('passed','failed')",
                         video_id)["n"]
        d = _trial()
        key = f"{topic.get('id')}:{video_id}"
        done = d.setdefault("done", [])
        if key in done:
            return
        done.append(key)
        del done[:-100]
        e = d[by]
        e["n"] += 1
        e["passed"] += 1 if passed else 0
        e["reviews"] += reviews
        if not d.get("verdict") and d["local"]["n"] >= TRIAL_MIN and d["claude"]["n"] >= TRIAL_MIN:
            lr, cr = (d[k]["passed"] / d[k]["n"] for k in ("local", "claude"))
            lrev, crev = (d[k]["reviews"] / d[k]["n"] for k in ("local", "claude"))
            d["verdict"] = "on" if lr >= cr and lrev <= crev + 0.5 else "off"
            d["decided"] = time.time()
            print(f"(local draft trial decided: {d['verdict']}; local passed {lr:.0%} in {lrev:.1f} reviews, "
                  f"Claude {cr:.0%} in {crev:.1f})")
        db.put(TRIAL_KEY, d)
    except Exception as e:
        print(f"(local draft trial: couldn't record: {e})")


def trial_report():
    """The local-draft trial so far, for the dashboard and 'python -m studio usage'."""
    return _trial()


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
    from studio import topics
    facts = _facts(topic)
    seen = topics.excerpts(facts)                 # the pages' own words, so the checker needn't fetch them all
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} <{s['url']}>\n    page text: "
                          + (f'"{seen[i]}"' if seen[i] else "(couldn't read it here: open the page yourself)")
                          for i, s in enumerate(facts))
    lines = "\n".join(f"L{i + 1}. {ln['text']}  [uses: {', '.join(map(str, ln.get('facts') or [])) or 'none'}]"
                      for i, ln in enumerate(draft["lines"]))
    hooks = "\n".join(f"H{i}. {h}" for i, h in enumerate(draft["hooks"]))
    ans = claude.ask_json(f"""You are a strict fact-checker and editor for a science Shorts channel. Each source below comes with
text Jarvis just read from that page. Use it, and open the page or search the web whenever that text doesn't
clearly settle a claim, or a source has none. Do not trust the writer.

Sources:
{fact_list}

Script lines:
{lines}

Hooks:
{hooks}

For EVERY script line that states a fact or number, give a verdict: "supported" (a source says it, or it follows by
arithmetic from sourced numbers), "unsupported" (no source backs it) or "wrong" (a source contradicts it; give the
correct statement and the URL). A line that only states standard textbook science (a named law or formula, or a
well-known physical constant, stated correctly in its usual form) gets "standard" without needing a source; any
number specific to this topic never counts as standard. Opinion or transition lines get "n/a".
Rate each hook 0-10 on curiosity, clarity, specificity, honesty (10 = fully delivered by the script, no bait) and
speed (gets to the point at once).
Also: "loop" 0-10 (read the LAST line and then the FIRST line: does it flow back into the start as one continuous
thought, so a viewer would keep watching when it repeats?), "originality" 0-10 (own explanation and wording vs a generic or copied explainer), "originality_fix" (if originality is under 7: one
sentence naming what reads generic and a concrete idea to make it the channel's own), "inauthentic_risk"
"low/medium/high" (does it read like mass-produced template content?), and "problems": anything else a careful
editor would fix (confusing step, misleading simplification, unit mistake).

JSON only: {{"claims": [{{"line": 1, "verdict": "supported|standard|unsupported|wrong|n/a", "fact": "F1", "note": "..."}}],
"hooks": {{"0": {{"curiosity": 0, "clarity": 0, "specificity": 0, "honesty": 0, "speed": 0}}}},
"loop": 0, "originality": 0, "originality_fix": "...", "inauthentic_risk": "low", "problems": ["..."]}}""", timeout=900, web=True, purpose=f"fact-check: {topic['title']}")
    if not isinstance(ans, dict):
        raise ScriptRejected("the fact-check didn't return a result")
    problems = []
    claims = [c for c in ans.get("claims") or [] if isinstance(c, dict)]
    for c in claims:
        if c.get("verdict") in ("unsupported", "wrong"):
            problems.append(f"line {c.get('line')} is {c['verdict']}: {c.get('note', '')}".strip())
    factual = sum(1 for c in claims if c.get("verdict") in ("supported", "unsupported", "wrong"))  # "standard" alone verifies nothing
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
    if hi is not None and not WORDS_MIN <= len(text_of(draft, draft["hooks"][hi]).split()) <= WORDS_MAX:
        fits = [(hook_score(ratings.get(i) or {}), i) for i, h in enumerate(draft["hooks"])
                if not hook_checks(h) and WORDS_MIN <= len(text_of(draft, h).split()) <= WORDS_MAX]
        if fits:                                  # a slightly lower-rated hook that keeps it in length
            hs, hi = max(fits)
    hook = draft["hooks"][hi] if hi is not None else (draft["lines"][0]["text"] if draft["lines"] else "")
    body = text_of(draft, hook)
    words = len(body.split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        problems.append(f"{words} words; it must be {WORDS_MIN}-{WORDS_MAX} (25-45 seconds): "
                        + ("cut about " + str(words - WORDS_MAX + 8) if words > WORDS_MAX else
                           "add about " + str(WORDS_MIN - words + 8)) + " words")
    loop = ans.get("loop")
    if isinstance(loop, (int, float)) and loop < 5:
        problems.append(f"editor: the ending doesn't lead back into the hook (loop {loop}/10); make the last line set up the first")
    orig = ans.get("originality")
    if isinstance(orig, (int, float)) and orig < 6:
        tip = str(ans.get("originality_fix") or "").strip()
        problems.append(f"originality {orig}/10: needs its own explanation" + (f" ({tip[:300]})" if tip else ""))
    if str(ans.get("inauthentic_risk", "")).lower() == "high":
        problems.append("reads like mass-produced template content")
    sim, sim_vid = similar_script(body, video_id)
    if sim >= SIMILAR_SCRIPT:
        problems.append(f"too close to the script of video {sim_vid} ({sim:.0%} word overlap)")
    problems += [f"editor: {p}" for p in ans.get("problems") or [] if isinstance(p, str) and p.strip()][:5]
    hard = [p for p in problems if not p.startswith("editor: ")]
    return {"ok": not hard, "problems": problems, "hard_problems": hard, "claims": claims, "hook_ratings": ratings,
            "hook_index": hi, "hook_score": hs, "hook": hook, "body": body, "words": words, "calcs": calcs, "loop": loop,
            "originality": orig, "inauthentic_risk": ans.get("inauthentic_risk"), "similar": [sim, sim_vid]}


# ---------- the whole step (resumable: each Claude call is its own step) ----------

def _check_topic(topic):
    from studio import topics
    if len(topics._working(_facts(topic))) < 2:
        from studio import lessons
        lessons.record("ideas", ["the topic has fewer than 2 working sources"], topic)
        raise ScriptRejected("the topic has fewer than 2 working sources")


def draft_step(topic, video_id=None, feedback=None, previous=None, draft=None):
    """Writes (or revises) a draft and saves it as the next version. Returns the scripts row id."""
    _check_topic(topic)
    draft = draft or write(topic, feedback, previous)
    v = db.one("SELECT MAX(version) v FROM scripts WHERE video_id IS ?", video_id)["v"] or 0
    hook = draft["hooks"][0] if draft["hooks"] else draft["lines"][0]["text"]
    return db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", video_id, v + 1, json.dumps(draft["hooks"]), hook, text_of(draft),
                     json.dumps({"lines": draft["lines"], "calcs": draft.get("calcs") or [], "titles": draft.get("titles") or [],
                                 "by": draft.get("by") or "claude"}),
                     "{}", "draft", time.time())


def _draft_of(row):
    st = json.loads(row["structure"] or "{}")
    return {"hooks": json.loads(row["hooks"] or "[]"), "lines": st.get("lines") or [], "calcs": st.get("calcs") or [],
            "titles": st.get("titles") or []}


def check_step(topic, video_id=None, max_revisions=MAX_REVISIONS):
    """Fact-checks the newest draft. Returns ("passed", row) or ("revise", row); raises ScriptRejected
    when the last allowed revision still fails."""
    from studio import topics
    row = db.one("SELECT * FROM scripts WHERE video_id IS ? AND status='draft' ORDER BY version DESC LIMIT 1", video_id)
    if not row:
        raise ScriptRejected("there's no draft to check")
    draft = _draft_of(row)
    rev = review(draft, topic, video_id)
    checks = {k: rev[k] for k in ("ok", "problems", "hard_problems", "claims", "hook_ratings", "hook_index",
                                  "hook_score", "words", "loop", "originality", "inauthentic_risk", "similar")}
    st = json.loads(row["structure"] or "{}")
    st["calcs"] = rev["calcs"]
    db.run("UPDATE scripts SET hook=?, body=?, structure=?, checks=?, status=? WHERE id=?", rev["hook"], rev["body"],
           json.dumps(st), json.dumps(checks), "passed" if rev["ok"] else "failed", row["id"])
    row = dict(db.one("SELECT * FROM scripts WHERE id=?", row["id"]))
    if rev["ok"]:
        _trial_outcome(topic, video_id, True)
        _record_claims(topic, video_id, rev)
        topics.set_status(topic["id"], script_status="passed")
        guard.audit("script.passed", True, {"topic": topic["id"], "video": video_id, "version": row["version"]})
        return "passed", row
    from studio import lessons
    lessons.record("script", rev["hard_problems"], topic, video_id)
    tries = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    again = _repeated(video_id, row["id"], rev["hard_problems"])
    if again:                                     # the same kind of problem twice in a row: move on to the next idea
        _trial_outcome(topic, video_id, False)
        topics.set_status(topic["id"], script_status="failed")
        guard.audit("script.failed", True, {"topic": topic["id"], "video": video_id, "repeated": again,
                                            "problems": rev["hard_problems"][:5]})
        raise ScriptRejected(f"the same problem came back after a revision ({again.replace('_', ' ')}): "
                             + "; ".join(rev["hard_problems"][:3]))
    if tries > max_revisions:
        _trial_outcome(topic, video_id, False)
        topics.set_status(topic["id"], script_status="failed")
        guard.audit("script.failed", True, {"topic": topic["id"], "video": video_id, "problems": rev["hard_problems"][:5]})
        raise ScriptRejected("the script still failed the fact-check: " + "; ".join(rev["hard_problems"][:4]))
    return "revise", row


REPEAT_STOPS = ("unsupported", "wrong_fact", "originality", "missing_value")


def _repeated(video_id, row_id, problems):
    """The problem category (e.g. "unsupported") that this failed version shares with the one just before it, or
    None. A revision that brings back the same kind of problem rarely passes on a third try, so the job stops
    there and the autopilot moves on to the next idea instead of spending more Claude calls."""
    from studio import lessons
    prev = db.one("SELECT checks FROM scripts WHERE video_id IS ? AND status='failed' AND id<? ORDER BY id DESC LIMIT 1",
                  video_id, row_id)
    if not prev:
        return None
    before = {lessons.categorize(p) for p in (json.loads(prev["checks"] or "{}").get("hard_problems") or [])}
    now = {lessons.categorize(p) for p in problems}
    same = [c for c in REPEAT_STOPS if c in before and c in now]
    return same[0] if same else None


LINE_PROBLEM = re.compile(r"^line (\d+) is (unsupported|wrong)\b")


def _best_failed(video_id):
    """The failed version closest to passing (no originality problem, fewest hard problems, newest), so a rewrite
    never starts from a version that went backwards."""
    rows = [dict(r) for r in db.q("SELECT * FROM scripts WHERE video_id IS ? AND status='failed'", video_id)]
    def key(r):
        hard = json.loads(r["checks"] or "{}").get("hard_problems") or []
        return (any(not LINE_PROBLEM.match(h) for h in hard), len(hard), -r["version"])
    return min(rows, key=key) if rows else None


def _fix_lines(draft, topic, problems):
    """Repairs only the lines the fact-check flagged (reword to what the facts say, or drop), keeping every other
    line word for word, so a near-miss doesn't lose what already passed."""
    from studio import claude
    facts = _facts(topic)
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} (source: {s['url']})" for i, s in enumerate(facts))
    lines = "\n".join(f"L{i + 1}. {ln['text']}" for i, ln in enumerate(draft["lines"]))
    ans = claude.ask_json(f"""A fact-checker flagged some lines of this Short script. Fix ONLY those lines: reword each so it says
only what the facts below support (use the checker's correction), or delete it if the script reads fine without it.
Never add a new claim, caveat or framing line. Every other line stays exactly as it is, word for word.

Facts:
{fact_list}

Lines:
{lines}

Flagged:
""" + "\n".join(f"- {p}" for p in problems) + """

JSON only: {"lines": [{"line": 1, "text": "...", "delete": false}]} listing just the flagged lines.""",
                          timeout=300, web=False, purpose=f"fix lines: {topic['title']}")
    edits = {}
    for e in (ans.get("lines") if isinstance(ans, dict) else None) or []:
        if isinstance(e, dict) and str(e.get("line", "")).isdigit():
            edits[int(e["line"])] = e
    if not edits:
        return None
    out = []
    for i, ln in enumerate(draft["lines"], 1):
        e = edits.get(i)
        if e and (e.get("delete") or not str(e.get("text", "")).strip()):
            continue
        out.append(dict(ln, text=str(e["text"]).strip()) if e else ln)
    if not out:
        return None
    fixed = dict(draft, lines=out)
    n = len(text_of(fixed, fixed["hooks"][0] if fixed.get("hooks") else None).split())
    if not WORDS_MIN <= n <= WORDS_MAX:
        fixed = _fit_length(fixed, n) or fixed
    return fixed


def revise_step(topic, video_id=None):
    row = _best_failed(video_id)
    checks = json.loads(row["checks"] or "{}")
    hard = checks.get("hard_problems") or []
    if hard and all(LINE_PROBLEM.match(h) for h in hard):     # a near-miss: repair just those lines
        fixed = _fix_lines(_draft_of(row), topic, hard)
        if fixed:
            return draft_step(topic, video_id, draft=fixed)
    return draft_step(topic, video_id, checks.get("problems") or [], _draft_of(row))


def make(topic, video_id=None, progress=print, max_revisions=MAX_REVISIONS):
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
