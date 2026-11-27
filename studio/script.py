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
HOOK_WEIGHTS = {"curiosity": 0.3, "clarity": 0.2, "specificity": 0.2, "honesty": 0.2, "speed": 0.1,
                "proven": 0.2}            # "proven": vs this channel's best-holding openings (only once there are some)
ANSWERED_MIN = 4                        # the payoff must answer the hook's question at least this well (0-10)
PROVEN_WEAK = 5                         # a chosen hook rated under this against the best openings gets an editor note
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


def newcomer_text(topic, facts):
    """The writer's "say these first" list: the background facts the idea's rating said a newcomer needs, each with
    the fact id that states it (topics.background found them for free). "" when the idea has none."""
    sc = topic.get("scores") if isinstance(topic.get("scores"), dict) else {}
    bg = sc.get("background") if isinstance(sc.get("background"), dict) else {}
    need = [str(f) for f in sc.get("newcomer") or []]
    if not need:
        return ""
    ids = {str(s.get("claim")): f"F{i + 1}" for i, s in enumerate(facts)}
    rows = []
    for fact in need[:4]:
        fid = ids.get(str((bg.get("found") or {}).get(fact) or ""))
        rows.append(f"- {fact}" + (f" ({fid})" if fid else " (no page states it: only say it if it is everyday "
                                                              "knowledge; otherwise answer so it isn't needed)"))
    return ("\n\nSAY THESE FIRST: a newcomer must hear each of these plain facts before the line that relies on it "
            "(in the hook's question or the setup lines, in your own words, with its fact id). Put each in the model's "
            "\"background\" if you write a model:\n" + "\n".join(rows))


def _niche():
    from studio import topics
    d = topics.niche() or {}
    return d, d.get("plan") or {}


DRAFTS = 3                              # first drafts written side by side; the strongest goes on (config studio.drafts)
BEATS = ("hook", "setup", "math", "rehook", "payoff", "loop")
STOCK_REHOOKS = ("here's the thing", "here's the weird part", "but wait", "wait for it", "it turns out", "plot twist")

# Connor's Video 20 audit (2026-11-25): the story must be scientifically defensible BEFORE it is made exciting.
STORY_FIRST = """

WORK IN THIS ORDER (the story must make sense before it is made exciting; never write a hook first and invent the
explanation afterwards):
1. The central question: one plain question a curious viewer would ask.
2. The physical model: what object, what happens to it, what is measured, and the assumptions (each with why it is
   fair). Check that the formula really describes THIS situation (a half-circle bend is not a full circle; a minimum
   length needed is not a length "lost"); arithmetic being right is not enough.
3. The maths: starting values with units and where each comes from (a fact id, "example" for your own labelled
   pick, "standard" for textbook science), the formula, the substitution, the result, and what it means.
4. Only then the narration. Every beat depends only on what the viewer was already told: never use a number,
   term, multiplier or assumption before the line that introduces it. Every multiplier is derived out loud before
   it is used ("4,096 layers, plus 4, times 4,096 minus 1, over 6" before "that's 2,798,250"). Every unit
   conversion is said. Every assumption is said with why it is fair, in a few words ("the tightest packing
   possible, proven by Kepler", "say it's 0.1 mm thick, my pick"). Never use a technical label (a band name like
   "Ks", an abbreviation, a term of art) unless a line before says in plain words what it is; plain words are
   better. Two different quantities (a sheet's length and a street's, material used and material
   left) never share a number or a word. The last line's answer follows from the lines before it and answers the
   opening question, not a different one. Say the answer plainly in the PAYOFF line (the line before the last);
   the last line is then free to loop back into the hook.
5. A hands-on model (a ball, a lamp, a sticky note, a torch...) is a demonstration, and it must show the real
   science (Connor's Video 35 audit): put "demo" in the model with what each object stands for, what the viewer
   (observer) stands for, the marker or reference point (what it stands for and exactly where it sits), what
   moves, what rotates and how much ("once per circle, so the same side always faces you"; or "nothing"), what
   stays fixed, what it proves and its limits. The narration says where the marker sits relative to the viewer
   ("a note on the side facing away from you: the ball's far side"), never just "at the center", and every lit
   or dark moment it describes must follow from that place.
6. A repeating period says which cycle it is, the first time its number is heard. For the Moon: 27.3 days is one
   turn (or one lap) measured against the stars (sidereal); 29.53 days is New Moon to New Moon (the synodic
   month), which is also sunrise to sunrise at any spot on the Moon (its solar day): never call 29.53 days a lap
   or a turn. Use the technical name only when it helps (never in the hook); plain words come first. When the
   answer depends on place (latitude, the poles, terrain), say in a few words where it holds ("near the equator").
7. Plan what a newcomer must already know (Connor, 2026-11-27: most stopped scripts left out a background fact,
   like "the Moon always shows us the same face" or "the Sun lights half the Moon at all times"). Before writing
   lines, list in the model's "background" every plain fact (not a number) the answer depends on that a curious
   14-year-old might not know. Each must be said in a line before (or in) the first line that relies on it; if
   it can't fit, pick a simpler question. A fraction or share you work out is said as the calculation it comes
   from ("a quarter of the 29.53-day cycle", "half of 29.53"), never as a bare 0.25 or "a quarter" on its own.
   Every constant in the formula (the speed of light, the gravitational constant, gravity's 9.8) is named aloud
   before the line that uses it; its digits are optional. A size word ("atom-sized", "car-sized") says the size
   it stands for the first time it is used ("an atom, about a tenth of a nanometer").
Write the model ONCE as "model" (before the drafts), and for every line say "learn" (what the viewer learns there,
under 12 words), "uses" (the model quantity names that line relies on), "tells" (the background keys that line
states) and "needs" (the background keys it relies on: each must be in "tells" of that line or an earlier one)."""
MODEL_SHAPE = ('"model": {"question": "...", "premise": "what physically happens, to what", "assumptions": [{"what": '
               '"...", "why": "..."}], "quantities": [{"name": "t", "means": "tissue thickness", "value": 0.1, "unit": '
               '"mm", "from": "F2|example|standard|calc"}], "background": [{"key": "one_face", "fact": "the Moon always '
               'shows Earth the same face", "from": "F3|standard"}], "formula": {"expr": "pi*t*(2**n+4)*(2**n-1)/6", '
               '"means": "...", "applies_because": "..."}, "result": {"value": 879, "unit": "m"}, "answer": "...", '
               '"demo": {"objects": [{"thing": "ball", "stands_for": "the Moon"}], "observer": "you = someone on '
               'Earth", "marker": {"thing": "sticky note", "stands_for": "a spot on the far side", "where": "the side '
               'facing away from you"}, "moves": "...", "rotates": "...", "fixed": "...", "proves": "...", '
               '"limits": "..."} (only when the narration uses a hands-on model; else leave "demo" out)},\n')


def _narrative_on():
    from studio import narrative
    return narrative.mode() != "off"


def _drafts_wanted(feedback, previous):
    if feedback or previous:
        return 1                                # a revision fixes the one draft that was checked
    try:
        from studio import config
        n = int(config.general().get("drafts") or DRAFTS)
    except Exception:
        n = DRAFTS
    return max(1, min(3, n))


def write(topic, feedback=None, previous=None, only_originality=False, keep_original=False):
    """A draft: {"hooks": [5], "lines": [{"text", "facts": [ids], "visual", "beat"}], "calcs": [...], "titles": [...]}.
    A first draft is written three ways at once (each built around a different everyday image); the code drops the
    ones that break a hard rule and a short judging call picks the strongest of the rest."""
    from studio import claude
    d, plan = _niche()
    facts = _facts(topic)
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} (source: {s['url']})" for i, s in enumerate(facts))
    from studio import experiments, lessons
    learned = lessons.advice("script")
    learned = ("\n\n" + learned) if learned else ""
    test = experiments.instruction(topic) if not feedback else ""      # a revision keeps the arm it was written in
    if test:
        learned += "\n\n" + test
    fix = ""
    if feedback:
        tips = [p.split("(", 1)[1].rstrip(")") for p in feedback if p.startswith("originality ") and "(" in p]
        idea = ("\n\nThe reviewer found it too generic. Rebuild the Short around this idea, as one image that runs from "
                "the hook to the last line: " + tips[-1]) if tips else ""
        story_fix = ""
        if any(str(p).startswith("narrative: ") for p in feedback):
            story_fix = ("\n\nFor each story problem (\"narrative: ...\"): if a value is \"never told\" or heard too late, "
                         "add it in plain words (with its unit) to a line BEFORE the line that uses it, or take it out of "
                         "the model and the maths. If a number is \"unexplained\", say each of its inputs and what the "
                         "operation means before saying the result, or drop that number. If a line \"leans on something "
                         "never established\", add the missing step to the line before it. An assumption needs a few "
                         "words on why it is fair. To stay in length, shorten or cut a line that explains nothing "
                         "(a teaser, a restatement), never the step that was missing. Say the answer plainly in the "
                         "payoff line. Update \"model\" to match, with every formula input a named quantity with its unit.")
        fix = ("\n\nYour previous draft failed review. Fix EVERY problem below and change nothing else that was fine. "
               "Don't add any new claim while fixing:\n"
               + "\n".join(f"- {p}" for p in feedback) + idea + story_fix + "\n\nPrevious draft:\n"
               + json.dumps(previous)[:6000])
        if keep_original and not tips:            # the previous draft passed originality: don't lose it while fixing
            fix += ("\n\nThe previous draft's everyday image and way of explaining PASSED the originality check. "
                    "Keep that same image and framing from the hook to the loop while you fix the problems above; "
                    "never fall back on the standard explainer of this question (Test 28 lost its 6/10 that way).")
        if only_originality:                      # the last, originality-only rewrite (Connor, 2026-11-26)
            fix = ("\n\nYour previous draft failed review twice for the same reason: it reads like the standard "
                   "explainer of this question. Its facts, numbers and maths were fine, so this rewrite is ONLY about "
                   "originality. Keep every fact, number, calculation and the answer; keep each value said before the "
                   "line that uses it. Change the everyday image, the way the viewer discovers the answer and the "
                   "wording, so it is our own explanation from the hook to the loop."
                   + (" Build it on the reviewer's own suggestion: " + tips[-1] if tips else "")
                   + "\nThe review said:\n" + "\n".join(f"- {p}" for p in feedback)
                   + "\n\nPrevious draft:\n" + json.dumps(previous)[:6000])
    story_first = STORY_FIRST if _narrative_on() else ""
    newcomer = newcomer_text(topic, facts)
    prompt = f"""Write a YouTube Short script. Channel: {d.get('name', '')}. Positioning: {plan.get('positioning', '')}.
Audience: {plan.get('audience', 'curious US adults')}. Style: {plan.get('style', 'calm, clear, conversational')}.
Topic: {topic['title']}
Angle (the calculation the Short shows): {topic.get('angle', '')}
Suggested opening: {topic.get('hook', '')}

FACTS YOU MAY USE (nothing else; never invent numbers, dates, names or quotes):
{fact_list}{newcomer}

Rules:
- {WORDS_MIN}-{WORDS_MAX} words in total (aim for {WORDS_MIN + 20}-{WORDS_MAX - 10}; count them before answering),
  read aloud by one narrator (no stage directions in the text).
- Structure: hook (the first line, at most {HOOK_MAX_WORDS} words, with the surprising number or the question in its
  first {HOOK_GRAB_WORDS} words, no warm-up like "did you know" or "in this video"), the setup, the calculation step by
  step with real numbers, the payoff, and a LOOP ending: the last line must lead straight back into the first line,
  so when the Short repeats it plays as one continuous thought (for example the last line sets up the hook's
  question again, or ends mid-thought that the hook completes). No outro, no "like and subscribe".
- ONE STEP (Connor, 2026-11-26): one surprising number and at most ONE calculation from numbers the viewer has
  just heard (one multiply or divide, then what it means). Never a chain where a worked-out number feeds the next
  calculation: in 40 seconds a viewer can't follow it. If the angle needs more, keep only its most surprising step.
- ONE QUESTION: the whole Short answers one clear question that the hook raises (asked out loud or plainly
  implied). Every line moves toward the answer, and the payoff answers it in plain words, so a viewer can say the
  answer back after one watch. Nothing that belongs to a different question.
- EVERY LINE EARNS ITS PLACE: each line adds one new thing (a fact, a step, a twist); never restate a line, never
  spend two lines on simple arithmetic. Explain the mechanism in plain words, building step by step, so a viewer
  with no background follows it. Write for the screen: each line names something a viewer could SEE (an object, a
  place, a drawn diagram), so the visuals can show exactly what is said.
- PLAIN COMPARISONS: every surprising number gets a comparison to something familiar the moment it appears
  ("127 C, hotter than boiling water", "as heavy as 3 cars"). The comparison must follow from the facts or be
  everyday knowledge (water boils at 100 C), never a new number about this topic. Never compare to an animal's or
  object's speed, size or weight unless a fact states it (a fact-checker rejects "slower than a snail" when no
  source says how fast a snail is); instead put the number in a unit a viewer can picture, worked out in "calcs"
  ("2 micrometres a second is about 7 millimetres an hour").
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
- If this question already has a famous explainer (a Space.com, NASA or Wikipedia page, a popular video), don't
  retell that page's answer in its order with its example: the reviewer marks that "reads like the standard
  explainer" (5/10). Ask it through the viewer's own world instead (their walk, their car, their kitchen) and let
  the famous number be a step, not the whole Short.
- Standard textbook science (a named law or formula such as Newton's laws, Kepler's third law, E = mc^2, or a
  well-known constant like g = 9.8 m/s^2 or the speed of light) may be used without a fact: mark that line's facts
  as ["general"]. Every number about THIS topic must still come from the facts above.
- No number, constant or reference value beyond the facts above, apart from the standard textbook science just
  described. Never call a value "typical" or "about" to get around this. If the explanation truly needs a value the
  facts don't give (like the wavelength of red light), write that line with the value, mark its facts ["needs"]
  and list it in "needs". Jarvis then looks for a real page that states it; if none does, the line is dropped. At
  most two needs, and only when the Short doesn't work without them.
- A worked example may use ONE input you choose yourself (a turn's radius, a car's length, a glass of water), but
  only when the line plainly says it's your pick ("say the car is 4.5 m long", "picture a 10 m circle"), with a
  realistic round value, and mark that line's facts ["example"]. Never state a chosen value as a fact, never as
  "typical", and never pick one that happens to equal a result (that reads as a trick).
- Talk to the viewer: say "you" and put the calculation in their world (your kitchen, your phone, your commute)
  wherever it fits naturally. Never "we" as in "today we'll look at".
- RE-HOOK: about halfway, after the setup and before the payoff, one short line (under 10 words) that raises the
  stakes or opens a fresh question, so a viewer who is drifting stays for the answer. Your own words: not a stock
  phrase like "but here's the thing", "wait for it" or "it turns out". It must be honest (the script delivers it).
- Give every line a "beat": "hook" (line 1), "setup", "math" (a step of the calculation), "rehook", "payoff" (the
  line that lands the surprising result; exactly one) or "loop" (the last line). The beats drive the edit: the
  payoff gets the big on-screen moment and the narrator leans on it.
- "visual" says what the screen shows for that line (diagram, chart, number, map, simple animation; no footage).
Also write 5 different hooks (each could replace line 1) and 3 title options (under 60 characters, honest).{story_first}{learned}{fix}

{{shape}}"""
    model_shape = MODEL_SHAPE if story_first else ""
    line_extra = ', "learn": "...", "uses": ["t"], "tells": ["one_face"], "needs": []' if story_first else ""
    one = ('JSON only: {' + model_shape + '"hooks": ["..." x5], "lines": [{"text": "...", "facts": ["F1"], "visual": "...", '
           '"beat": "setup"' + line_extra + '}],\n'
           '"calcs": [{"expr": "...", "result": 0.0, "unit": "...", "says": "what it shows"}], "titles": ["..." x3],\n'
           '"needs": [{"line": 4, "what": "the wavelength of red light", "value": "700 nm"}]} ("needs" is usually empty)')
    n = _drafts_wanted(feedback, previous)
    first = not feedback and not previous
    ans = None
    if first and _local_draft_turn():
        ans = _local_draft(prompt.replace("{shape}", one), facts)   # the trial: a first draft written on this computer
    if ans is None and n == 1:
        ans = claude.ask_json(prompt.replace("{shape}", one), timeout=600, web=False, purpose=f"script: {topic['title']}")
    if ans is None:
        many = (f"Write {n} COMPLETE drafts that follow every rule above. Each is built around a DIFFERENT everyday image "
                "or angle (say which in \"angle\"), so they are real alternatives, not rewordings. Same facts, same "
                "honesty rules" + (", and all of them tell the SAME model (one model, written once, first)" if story_first
                                   else "") + ".\n\n"
                'JSON only: {' + model_shape + '"drafts": [{"angle": "...", "hooks": ["..." x5], "lines": [{"text": "...", '
                '"facts": ["F1"], "visual": "...", "beat": "setup"' + line_extra + '}], "calcs": [{"expr": "...", '
                '"result": 0.0, "unit": "...", "says": '
                f'"..."}}], "titles": ["..." x3], "needs": []}} x{n}]}}')
        raw = claude.ask_json(prompt.replace("{shape}", many), timeout=900, web=False,
                              purpose=f"script ({n} drafts): {topic['title']}")
        ans = pick_draft(raw, topic)
    if not isinstance(ans, dict) or not ans.get("lines"):
        raise ScriptRejected("Claude didn't return a script")
    if feedback and previous and isinstance(previous.get("model"), dict) and not isinstance(ans.get("model"), dict):
        ans["model"] = previous["model"]          # a rewrite keeps the model unless it fixed it
    ans = _clean(ans)
    if previous:
        ans = _carry_plan(ans, previous)
    if number_check_on():
        ans, rep = tag_numbers(ans, topic)
        if rep["tagged"] or rep["looked_up"]:
            ans["number_check"] = rep
    ans = fill_needs(ans, topic)
    for _ in range(2):                            # wrong length: fix it here, before a costly fact-check
        n = len(text_of(ans, ans["hooks"][0] if ans["hooks"] else None).split())
        if WORDS_MIN <= n <= WORDS_MAX:
            break
        ans = _kept_story(ans, _fit_length(ans, n))
    return ans


def narrative_hard(d):
    """The code's own story stops for a draft (no Claude call), [] when the story check is off."""
    if not _narrative_on():
        return []
    from studio import narrative
    try:
        return narrative.evaluate(d)["hard"]
    except Exception:
        return []


def _story_hard(d):
    if not _narrative_on():
        return 0
    from studio import narrative
    try:
        return len(narrative.evaluate(d)["hard"])
    except Exception:
        return 0


def _kept_story(before, after):
    """The length-fixed draft, unless fixing the length broke the story (a cut step, a number now said before
    its inputs): then the original, which the fact-check sends back for its length instead."""
    if not after:
        return before
    return after if _story_hard(after) <= _story_hard(before) else before


JUDGE_WEIGHTS = {"hook": 0.3, "payoff": 0.2, "clarity": 0.2, "momentum": 0.15, "you": 0.05, "original": 0.1}


def _clean(d):
    """A draft with empty lines dropped, at most 5 hooks and a known beat on every line."""
    d = dict(d)
    d["lines"] = [dict(ln) for ln in d.get("lines") or [] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    last = len(d["lines"]) - 1
    for i, ln in enumerate(d["lines"]):
        b = str(ln.get("beat") or "").strip().lower()
        ln["beat"] = "hook" if i == 0 else b if b in BEATS and b != "hook" else ("loop" if i == last else "setup")
    d["hooks"] = [str(h) for h in (d.get("hooks") or []) if str(h).strip()][:5]
    return d


def _screen(d):
    """Hard problems the code can see before any judging (empty list = worth judging)."""
    problems = []
    if len(d.get("lines") or []) < 4:
        problems.append("too few lines")
    hooks = [h for h in d.get("hooks") or [] if not hook_checks(h)]
    if not hooks:
        problems.append("no hook passes the rules")
    calcs = check_calcs(d.get("calcs"))
    if not calcs:
        problems.append("no calculation")
    problems += [c["problem"] for c in calcs if not c["ok"]]
    n = len(text_of(d, hooks[0] if hooks else None).split()) if d.get("lines") else 0
    if not WORDS_MIN - 20 <= n <= WORDS_MAX + 25:          # a little over or under is fixed later; far off isn't
        problems.append(f"{n} words; it must be {WORDS_MIN}-{WORDS_MAX}: " + ("cut" if n > WORDS_MAX else "add"))
    low = " ".join(str(ln["text"]).lower() for ln in d.get("lines") or [])
    problems += [f"stock phrase \"{p}\"" for p in STOCK_REHOOKS if p in low]
    if _narrative_on():                         # free: an unexplained number or unit slip drops a draft before judging
        from studio import narrative
        try:
            problems += narrative.evaluate(dict(d, hook=hooks[0] if hooks else None))["hard"][:3]
        except Exception as e:
            print(f"(story check skipped: {str(e)[:120]})")
    return problems


def judge_score(scores):
    have = {k: float(v) for k, v in (scores or {}).items() if k in JUDGE_WEIGHTS and isinstance(v, (int, float))}
    w = sum(JUDGE_WEIGHTS[k] for k in have)
    return round(sum(JUDGE_WEIGHTS[k] * v for k, v in have.items()) / w, 2) if w else 0.0


EDITOR_ORIGINAL_MIN = 6           # the fact-check rejects under this, so the editor's pick is held to it too
EDITOR_GAPS_MAX = 2               # more story gaps than this (or one in the hook/payoff) gets the repair first


def editor_on():
    """Pipeline order review (2026-11-27): the draft-picking call also judges story and originality with the
    fact-check's own rules, and a weak pick is repaired once before the paid fact-check. Config editor_first."""
    try:
        from studio import config
        return config.general().get("editor_first", True) is not False
    except Exception:
        return True


def read_problems(read):
    """What the local newcomer read couldn't follow, as problem texts ([] when it followed it all)."""
    if not read:
        return []
    out = [f"narrative: line {c['line']} leans on something never established ({c['why']})"
           for c in read.get("confusing") or [] if isinstance(c, dict) and c.get("line")]
    if isinstance(read.get("match"), int) and read["match"] <= EXPLAIN_MIN_MATCH and read.get("takeaway"):
        out.append(f"clarity: a first-time viewer would come away with \"{read['takeaway']}\" instead of the point")
    return out


def editor_penalty(ed):
    """Points off a draft's judge score for what the fact-check would stop it for."""
    if not ed:
        return 0.0
    pen = 0.0
    o = ed.get("original")
    if isinstance(o, (int, float)) and o < EDITOR_ORIGINAL_MIN:
        pen += 2.0 * (EDITOR_ORIGINAL_MIN - o)
    pen += 0.75 * len(ed.get("gaps") or [])
    p = ed.get("premise")
    if isinstance(p, (int, float)) and p < 5:
        pen += 5 - p
    return pen


def editor_problems(ed, lines):
    """The problems the fact-check would stop the picked draft for, from the editor's verdict ([] = none)."""
    if not ed or not editor_on():
        return []
    out = []
    o = ed.get("original")
    if isinstance(o, (int, float)) and o < EDITOR_ORIGINAL_MIN:
        tip = str(ed.get("original_fix") or "").strip()
        out.append(f"originality {o:g}/10: needs its own explanation" + (f" ({tip[:300]})" if tip else ""))
    gaps = [g for g in ed.get("gaps") or [] if isinstance(g, dict) and isinstance(g.get("line"), int)]
    beats = {i: str(ln.get("beat") or "") for i, ln in enumerate(lines or [], 1)}
    key = [g for g in gaps if g["line"] <= 2 or beats.get(g["line"]) == "payoff"]
    if len(gaps) > EDITOR_GAPS_MAX or key:
        out += [f"narrative: line {g['line']} leans on something never established ({str(g.get('missing') or '')[:160]})"
                for g in gaps if beats.get(g["line"]) != "loop"]
    return out


def _judge(pool, topic):
    """({letter: scores}, why, notes, {letter: editor verdict}) from one short Claude call (no web) that compares
    the drafts as a viewer would and, with editor_first on, judges story and originality the way the fact-check
    will, so the pick is a draft that passes it."""
    from studio import claude
    letters = "ABCDE"
    parts = []
    for k, (_, d) in enumerate(pool):
        hook = next((h for h in d["hooks"] if not hook_checks(h)), d["lines"][0]["text"])
        body = "\n".join(f"  L{i + 1} [{ln.get('beat')}] {hook if i == 0 else ln['text']}" for i, ln in enumerate(d["lines"]))
        parts.append(f"Draft {letters[k]} (angle: {str(d.get('angle') or '')[:120]}):\n{body}")
    ed_on = editor_on()
    ed_ask = ed_shape = ""
    if ed_on:
        ed_ask = """
Also judge each draft the way the fact-checker will, so the one you pick passes it:
"original" uses the fact-checker's scale: own explanation and wording vs a generic or copied explainer; the
standard textbook explainer or analogy of this question (the one a Space.com, NASA or Wikipedia page gives) is 5,
and anything under 6 is rejected. "original_fix": for a draft under 7, one sentence naming what reads generic and a
concrete idea to make it the channel's own. "premise" 0-10: after the first two lines, does a viewer who knows
nothing about this know what physical thing is happening and what question is answered? "gaps": every line that
needs something a newcomer hasn't been told yet (a term, a value, a background fact, a step) or doesn't follow
from the line before, as {"line": n, "missing": "what was never said"}; [] if none. The last line may end on an
open question: never list it."""
        ed_shape = ', "editor": {"A": {"original": 0, "original_fix": "", "premise": 0, "gaps": []}}'
    ans = claude.ask_json(f"""Judge these drafts of one YouTube Short about "{topic['title']}" as a sharp short-form editor would.
Facts are checked later, so judge only how well each one would hold a scrolling viewer to the end.

""" + "\n\n".join(parts) + """

Score each 0-10: "hook" (would a scroller stop in the first second?), "payoff" (how surprising and satisfying the
result is), "clarity" (can you follow the math by ear, once?), "momentum" (a real re-hook halfway, no dead or
repeated lines), "you" (puts it in the viewer's own world), "original" (not the standard textbook explainer).
""" + ed_ask + """

For every draft that is NOT best, "notes": one sentence on what held it back, written as advice a writer could
follow next time (e.g. "the hook gave the answer away before the question").

JSON only: {"scores": {"A": {"hook": 0, "payoff": 0, "clarity": 0, "momentum": 0, "you": 0, "original": 0}}, "best": "A",
"why": "one sentence", "notes": {"B": "..."}""" + ed_shape + "}",
                          timeout=300, web=False, purpose=f"pick a draft: {topic['title']}")
    out, notes, editor = {}, {}, {}
    if isinstance(ans, dict):
        for key, into in (("scores", out), ("notes", notes), ("editor", editor)):
            for k, v in (ans.get(key) or {}).items() if isinstance(ans.get(key), dict) else []:
                k = str(k).strip().upper()[:1]
                if k in letters[:len(pool)] and (isinstance(v, dict) if key != "notes" else str(v).strip()):
                    into[letters.index(k)] = " ".join(str(v).split())[:200] if key == "notes" else v
    for k, v in list(editor.items()):
        editor[k] = _editor_verdict(v)
    return out, (str(ans.get("why") or "")[:300] if isinstance(ans, dict) else ""), notes, editor


def _editor_verdict(v):
    def num(x):
        try:
            return max(0.0, min(10.0, float(x)))
        except (TypeError, ValueError):
            return None
    gaps = []
    for g in v.get("gaps") or []:
        try:
            gaps.append({"line": int(g.get("line")), "missing": " ".join(str(g.get("missing") or "").split())[:160]})
        except (AttributeError, TypeError, ValueError):
            continue
    return {"original": num(v.get("original")), "original_fix": " ".join(str(v.get("original_fix") or "").split())[:300],
            "premise": num(v.get("premise")), "gaps": gaps[:6]}


def _learn_from_drafts(topic, drafts, good, scores, notes, best):
    """The drafts that didn't go through become lessons the writer reads next time (studio/lessons.py), kept apart
    from real script rejections (stage "draft", so an idea's pillar isn't marked as failing):
    - each hard problem that screened a draft out (a calculation that doesn't re-run, no usable hook, far off length,
      a stock phrase);
    - each judge score where a losing draft was clearly weaker than the winner (2+ points behind and 6 or under),
      with the judge's own note on what held it back."""
    from studio import lessons
    try:
        for i, d in enumerate(drafts):
            if not any(d is g for _, g in good):
                lessons.record("draft", [f"draft screened out: {p}" for p in _screen(d)[:3]], topic)
        win = scores.get(best) or {}
        for k, sc in scores.items():
            if k == best or not isinstance(sc, dict):
                continue
            weak = []
            for dim in JUDGE_WEIGHTS:
                a, b = sc.get(dim), win.get(dim)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a <= 6 and b - a >= 2:
                    weak.append((b - a, f"judge: weaker {dim} ({a:g} vs {b:g})" + (f" | {notes[k]}" if notes.get(k) else "")))
            weak = [w for _, w in sorted(weak, key=lambda x: -x[0])]   # the biggest gaps first
            if not weak and notes.get(k):
                weak.append(f"judge: weaker {min(JUDGE_WEIGHTS, key=lambda d: sc.get(d, 10) if isinstance(sc.get(d), (int, float)) else 10)} | {notes[k]}")
            lessons.record("draft", weak[:2], topic)
    except Exception as e:                        # learning never stops a script
        print(f"(couldn't save what the losing drafts taught: {str(e)[:120]})")


def _find_drafts(raw, depth=0):
    """Every draft (a dict with a list of "lines") in Claude's answer, wherever it sits: {"drafts": [...]},
    {"drafts": {"1": {...}}}, a bare list of drafts, one draft, or one level deeper (test 36's first try came
    back in a shape the old code couldn't read and the whole call was lost)."""
    if depth > 3:
        return []
    if isinstance(raw, dict):
        if isinstance(raw.get("lines"), list) and raw["lines"]:
            return [raw]
        out = []
        for key in ("drafts", "scripts", "options", "versions"):
            v = raw.get(key)
            if isinstance(v, dict):
                v = list(v.values())
            if isinstance(v, list):
                out += [d for x in v for d in _find_drafts(x, depth + 1)]
        if out:
            return out
        for v in raw.values():
            if isinstance(v, (dict, list)) and v is not raw.get("model"):
                out += _find_drafts(v, depth + 1)
        return out
    if isinstance(raw, list):
        return [d for x in raw for d in _find_drafts(x, depth + 1)]
    return []


def _log_unusable(raw, topic):
    """Says what came back when no draft could be read, and keeps it for a look (studio_data/research)."""
    shape = (f"a {type(raw).__name__} with keys {sorted(raw)[:8]}" if isinstance(raw, dict) else
             f"a list of {len(raw)}" if isinstance(raw, list) else type(raw).__name__)
    print(f"(script: no readable draft in Claude's answer: {shape})")
    try:
        from studio import data_dir
        (data_dir("research") / "last_unreadable_script.json").write_text(json.dumps(
            {"topic": (topic or {}).get("title"), "shape": shape, "answer": raw}, indent=1, default=str)[:200000])
    except Exception:
        pass


def pick_draft(raw, topic):
    """The strongest of the side-by-side drafts (or the only one). Drafts that break a hard rule the code can
    check are dropped first; a short judging call ranks the rest. Never fails because of the judge."""
    drafts = [_clean(d) for d in _find_drafts(raw)]
    if not drafts:
        _log_unusable(raw, topic)
        return None
    if isinstance(raw, dict) and isinstance(raw.get("model"), dict):
        drafts = [d if isinstance(d.get("model"), dict) else dict(d, model=raw["model"]) for d in drafts]
    screened = {i: _screen(d) for i, d in enumerate(drafts)}
    good = [(i, d) for i, d in enumerate(drafts) if not screened[i]]
    fewest = min(len(v) for v in screened.values())          # none clean: judge only the ones with fewest problems
    pool = good or [(i, d) for i, d in enumerate(drafts) if len(screened[i]) == fewest]
    reads = {}
    for i, d in pool:                             # free: the local newcomer read, before anything is paid for
        try:
            r = newcomer_read(topic, d)
        except Exception as e:
            print(f"(newcomer read skipped: {str(e)[:120]})")
            break
        if r is None:
            break
        reads[i] = r
    clear = [(i, d) for i, d in pool if i in reads and not read_problems(reads[i])]
    if reads and clear and len(clear) < len(pool):
        pool = clear                              # drafts a newcomer could follow go on; the others are dropped
    scores, why, notes, editor = {}, "", {}, {}
    if len(pool) > 1:
        try:
            scores, why, notes, editor = _judge(pool, topic)
        except Exception as e:
            print(f"(couldn't judge the drafts: {str(e)[:120]}; using the first good one)")
    best = max(range(len(pool)), key=lambda k: (judge_score(scores.get(k)) - editor_penalty(editor.get(k)), -k)) \
        if scores else 0
    if len(drafts) > 1:
        _learn_from_drafts(topic, drafts, good, scores, notes, best)
    chosen = dict(pool[best][1])
    chosen.pop("angle", None)
    if isinstance(raw, dict) and isinstance(raw.get("model"), dict) and not isinstance(chosen.get("model"), dict):
        chosen["model"] = raw["model"]
    if pool[best][0] in reads:
        chosen["newcomer_read"] = reads[pool[best][0]]
    if editor.get(best):
        chosen["editor"] = editor[best]
    chosen["picked"] = {"of": len(drafts), "passed_screen": len(good), "angle": str(pool[best][1].get("angle") or "")[:160],
                        "score": judge_score(scores.get(best)) if scores else None, "why": why,
                        "dropped": {str(i): screened[i][:3] for i in screened if screened[i]}}
    return chosen


MAX_NEEDS = 2


def number_check_on():
    try:
        from studio import config
        return config.general().get("number_check", True) is not False
    except Exception:
        return True


def look_up(asked, topic, most=MAX_NEEDS):
    """{key: {"what", "value"}} -> {key: "F<n>"}: one web search looks for a reputable page that states each value;
    Jarvis downloads that page itself and keeps it only if the page's own text has the value's numbers. A kept page
    becomes one more source of the topic. Values without a number of 2+ digits can't be verified and are skipped."""
    from studio import topics
    found = {}
    todo = {k: v for k, v in list(asked.items())[:most] if v.get("what") and topics._nums(v.get("value"))}
    if not todo:
        return found
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
    return found


SMALL = 12                          # whole numbers up to this are ordinary words ("two steps"), not claims


def _known_numbers(draft, facts):
    known = set().union(*[_numbers(f.get("claim", "")) for f in facts]) if facts else set()
    for c in draft.get("calcs") or []:
        if isinstance(c, dict):
            known |= _numbers(c.get("expr", "")) | _numbers(c.get("result", ""))
            try:
                v = float(c.get("result"))
                known |= {f"{v:g}", str(round(v)), f"{v:.1f}", f"{v:.2f}"}
            except (TypeError, ValueError):
                pass
    return known


def tag_numbers(draft, topic):
    """The free number check, before any fact-check call. A line that says a number but lists no fact gets the
    fact whose own words have that number. A number found in no fact and no calculation, on a line not marked as
    textbook science, is turned into a "needs" lookup (fill_needs then finds a page that states it, or drops the
    line), so it never reaches the fact-check unsourced. Returns (draft, report)."""
    facts = _facts(topic)
    known = _known_numbers(draft, facts)
    rep = {"tagged": [], "looked_up": []}
    needs = list(draft.get("needs") or [])
    lines = []
    for i, ln in enumerate(draft.get("lines") or [], 1):
        nums = {n for n in _numbers(ln.get("text", "")) if not (n.isdigit() and int(n) <= SMALL)}
        tags = [str(f) for f in ln.get("facts") or []]
        if nums and not [t for t in tags if t not in ("none", "")]:
            hits = [f"F{k + 1}" for k, f in enumerate(facts) if nums & _numbers(f.get("claim", ""))]
            if hits:
                ln = dict(ln, facts=hits[:2])
                rep["tagged"].append(i)
        tags = [str(f) for f in ln.get("facts") or []]
        loose = sorted(n for n in nums if n not in known)
        beat = str(ln.get("beat") or "")
        if loose and not {"general", "needs", "example"} & set(tags) and i > 1 and beat not in PRUNE_KEEP_BEATS:
            ln = dict(ln, facts=tags + ["needs"])
            needs.append({"line": i, "what": " ".join(str(ln.get("text", "")).split())[:200], "value": loose[0]})
            rep["looked_up"].append(i)
        lines.append(ln)
    return dict(draft, lines=lines, needs=needs), rep


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
    found = look_up(asked, topic, MAX_NEEDS)
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
meaning and the closing line. Never cut a step of the explanation or the line that introduces a number before
it is used: cut words, not steps. Change nothing else. Each hook must stay under {HOOK_MAX_WORDS} words.

{json.dumps(draft)[:6000]}

JSON only, the same shape (keep each line's "beat"): {{"hooks": [...], "lines": [{{"text": "...", "facts": [...], "visual": "...", "beat": "..."}}],
"calcs": [...], "titles": [...]}}""", timeout=300, web=False, purpose="script length")
    if not isinstance(ans, dict) or not ans.get("lines"):
        return None
    lines = [ln for ln in ans["lines"] if isinstance(ln, dict) and str(ln.get("text", "")).strip()]
    if not lines:
        return None
    out = _clean(dict(draft, lines=lines))
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
    ids = {f"F{i + 1}" for i in range(len(facts))} | {"general", "needs", "example"}
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

def review(draft, topic, video_id=None, recheck=None):
    """Independent check. Returns {"ok", "problems", "claims", "hooks", "hook", "calcs", ...}. recheck: after
    "fix lines" changed a few lines of a script that was checked in full, only those lines are checked again (see
    _recheck); everything else keeps its verdicts and ratings from that check."""
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
    if recheck:
        ans = _recheck(draft, topic, fact_list, lines, recheck, unread={f"F{i + 1}" for i in range(len(facts))
                                                                        if not seen[i]})
        if ans is not None:
            return _judged(draft, ans, video_id, rechecked=sorted(recheck["changed"]), facts=facts)
    story_ask = story_shape = model_txt = ""
    if _narrative_on():
        from studio import narrative
        story_ask, story_shape = narrative.REVIEW_ASK, narrative.REVIEW_SHAPE
        m = draft.get("model") if isinstance(draft.get("model"), dict) else None
        model_txt = ("\nThe writer's model (the physics and maths the story rests on):\n" + json.dumps(m)[:2500] + "\n"
                     if m else "\nThe writer gave no model: judge the physics from the narration.\n")
    from studio import packaging
    best = packaging.best_openings()
    proven_txt = proven_key = ""
    if best:
        proven_txt = ("\nThese openings kept the most real viewers past the first seconds on this channel:\n"
                      + "\n".join(f"- \"{b['hook']}\" ({100 * b['hold']:.0f}% still watching at 10%)" for b in best)
                      + "\nAlso rate each hook \"proven\" 0-10: would it hold a viewer as well as those openings? "
                        "(the same pull, not the same words or topic)")
        proven_key = ', "proven": 0'
    ans = claude.ask_json(f"""You are a strict fact-checker and editor for a science Shorts channel. Each source below comes with
text Jarvis just read from that page. Use it, and open the page or search the web whenever that text doesn't
clearly settle a claim, or a source has none. Do not trust the writer.

Sources:
{fact_list}

Script lines:
{lines}
{model_txt}
Hooks:
{hooks}

For EVERY script line that states a fact or number, give a verdict: "supported" (a source says it, or it follows by
arithmetic from sourced numbers), "unsupported" (no source backs it) or "wrong" (a source contradicts it; give the
correct statement and the URL). A line that only states standard textbook science (a named law or formula, or a
well-known physical constant, stated correctly in its usual form) gets "standard" without needing a source; any
number specific to this topic never counts as standard. A value the line plainly presents as the narrator's own
chosen example ("say the car is 4.5 m long", "picture a 10 m circle") is "standard" when it is realistic and only
used to work the example; stated as a fact, as "typical", or equal to a result it is "unsupported". Leave opinion and transition lines out (no entry).
Check the EXPLANATION too, not just the numbers: a line whose numbers are sourced but whose reasoning or
relationship is wrong (for example calling the gap between two hot spots the distance from a hot spot to a cold
spot, mixing up a wavelength and half of it, or a cause that isn't the real cause) is "wrong", with the correct
statement. Any calculation in the narration must follow from the line before it. When a claim is uncertain or
debated, the line must say so plainly; a guess stated as a fact is "unsupported".
Rate each hook 0-10 on curiosity, clarity, specificity, honesty (10 = fully delivered by the script, no bait) and
speed (gets to the point at once).{proven_txt}
Also: "loop" 0-10 (read the LAST line and then the FIRST line: does it flow back into the start as one continuous
thought, so a viewer would keep watching when it repeats?), "rehook" 0-10 (is there an honest line about halfway
that raises the stakes or opens a new question, so a drifting viewer stays for the payoff?), "question" (the one
question the hook raises, in your words), "answered" 0-10 (does the payoff answer exactly that question plainly,
with nothing left hanging?), "bare_numbers" (the surprising numbers said with no plain comparison a viewer can
picture, e.g. ["127 C"]; [] if none), "originality" 0-10 (own explanation and wording vs a generic or copied explainer), "originality_fix" (if originality is under 7: one
sentence naming what reads generic and a concrete idea to make it the channel's own), "inauthentic_risk"
"low/medium/high" (does it read like mass-produced template content?), and "problems": anything else a careful
editor would fix (confusing step, misleading simplification, unit mistake).
{story_ask}

For each "supported" or "wrong" claim also give "quote": the source's own words that settle it (at most 25 words,
copied exactly), "confidence": 0.0-1.0 (how sure you are of the verdict), and "source_date": the page's
published or updated date if it shows one (YYYY-MM-DD), else "". Give "note" only for "unsupported", "wrong" and
"standard" (what is missing or wrong and the correct statement, or which law or constant): leave it out for
"supported", where the quote already says it.

JSON only: {{"claims": [{{"line": 1, "verdict": "supported|standard|unsupported|wrong", "fact": "F1",
"quote": "...", "confidence": 0.9, "source_date": ""}}, {{"line": 2, "verdict": "unsupported", "note": "..."}}],
"hooks": {{"0": {{"curiosity": 0, "clarity": 0, "specificity": 0, "honesty": 0, "speed": 0{proven_key}}}}},
"loop": 0, "rehook": 0, "question": "...", "answered": 0, "bare_numbers": [], "originality": 0, "originality_fix": "...", "inauthentic_risk": "low", "problems": ["..."]{story_shape}}}""", timeout=900, web=True, purpose=f"fact-check: {topic['title']}")
    if not isinstance(ans, dict):
        raise ScriptRejected("the fact-check didn't return a result")
    return _judged(draft, ans, video_id, facts=facts)


def plain_question(text):
    """A line that only asks (a transition or the loop question: "So what does infrared actually do?") makes no
    claim, so "unsupported" can't apply to it (test 23 was stopped twice on one). A question that states a
    number or a fact on the way ("Why is the Sun 5,500 C?") is still checked."""
    t = " ".join(str(text or "").split())
    if not t.endswith("?") or len(t.split()) > 16:
        return False
    if t.count("?") > 1 or re.search(r"[.!:;]\s", t):
        return False                                 # a statement before the question
    return not [n for n in _numbers(t) if not (n.isdigit() and int(n) <= SMALL)]


def _judged(draft, ans, video_id, rechecked=None, facts=None):
    """The code's half of the review: the checker's answer plus the hard rules the code checks itself."""
    problems = []
    claims = [c for c in ans.get("claims") or [] if isinstance(c, dict)]
    texts = [str(ln.get("text") or "") for ln in draft.get("lines") or []]
    asks = {i for i, t in enumerate(texts, 1) if plain_question(t)}
    beats = [str(ln.get("beat") or "") for ln in draft.get("lines") or []]
    for i, t in enumerate(texts, 1):                 # the loop line hands back to the hook: no new claim of its own
        if (i == len(texts) or beats[i - 1] == "loop") and i > 1 and \
                not [n for n in _numbers(t) if not (n.isdigit() and int(n) <= SMALL)]:
            asks.add(i)
    claims = [c for c in claims if not (c.get("verdict") == "unsupported" and c.get("line") in asks)]
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
    proven = (ratings.get(hi) or {}).get("proven") if hi is not None else None
    if isinstance(proven, (int, float)) and proven < PROVEN_WEAK:
        problems.append(f"editor: the hook is weaker than the openings that held viewers best here (proven "
                        f"{proven}/10); open with the same kind of pull")
    body = text_of(draft, hook)
    words = len(body.split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        problems.append(f"{words} words; it must be {WORDS_MIN}-{WORDS_MAX} (25-45 seconds): "
                        + ("cut about " + str(words - WORDS_MAX + 8) if words > WORDS_MAX else
                           "add about " + str(WORDS_MIN - words + 8)) + " words")
    loop = ans.get("loop")
    if isinstance(loop, (int, float)) and loop < 5:
        problems.append(f"editor: the ending doesn't lead back into the hook (loop {loop}/10); make the last line set up the first")
    rehook = ans.get("rehook")
    if isinstance(rehook, (int, float)) and rehook < 5:
        problems.append(f"editor: no real re-hook halfway (rehook {rehook}/10); add one short line that raises the stakes")
    answered = ans.get("answered")
    q = " ".join(str(ans.get("question") or "").split())[:200]
    if isinstance(answered, (int, float)) and answered < ANSWERED_MIN:
        problems.append(f"one question: the payoff doesn't answer the hook's question ({q or 'unclear question'}; "
                        f"answered {answered}/10); make the payoff answer it plainly and cut what belongs elsewhere")
    elif isinstance(answered, (int, float)) and answered < 6:
        problems.append(f"editor: the answer to \"{q}\" could be plainer (answered {answered}/10)")
    bare = [str(b)[:40] for b in ans.get("bare_numbers") or [] if str(b).strip()][:4]
    if bare:
        problems.append("editor: give these numbers a plain comparison a viewer can picture (from the facts' own "
                        "numbers or a unit they can picture, never an unsourced object or animal): " + ", ".join(bare))
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
    nar = None
    if _narrative_on():                           # the Narrative Integrity Engine (studio/narrative.py)
        from studio import narrative
        try:
            nar = narrative.evaluate(dict(draft, hook=hook), ans.get("story"), facts)
        except Exception as e:
            print(f"(story check skipped: {str(e)[:120]})")
        if nar:
            nar["story"] = ans.get("story") if isinstance(ans.get("story"), dict) else None
            if narrative.mode() == "block":
                problems += nar["hard"]
            else:
                problems += [f"editor: {h[len('narrative: '):]}" for h in nar["hard"]]
            problems += [f"editor: story: {a}" for a in nar["advice"][:3]]
    hard = [p for p in problems if not p.startswith("editor: ")]
    hold = originality_hold(orig, hard, loop, rehook, answered, nar, video_id)
    if hold:                                      # an originality 5 that is otherwise excellent: Connor decides
        problems = [f"editor: held for Connor's review: {p}" if p.startswith("originality ") else p for p in problems]
        hard = [p for p in hard if not p.startswith("originality ")]
    return {"ok": not hard, "problems": problems, "hard_problems": hard, "claims": claims, "hook_ratings": ratings,
            "hook_index": hi, "hook_score": hs, "hook": hook, "body": body, "words": words, "calcs": calcs, "loop": loop, "rehook": rehook,
            "originality": orig, "inauthentic_risk": ans.get("inauthentic_risk"), "similar": [sim, sim_vid],
            **({"originality_hold": hold} if hold else {}),
            "question": q or None, "answered": answered,
            **({"narrative": nar} if nar else {}),
            **({"rechecked": rechecked} if rechecked else {})}


# Lines whose change can move the hook, loop, re-hook or originality ratings: a fix there gets a full check.
RECHECK_FULL_BEATS = ("hook", "rehook", "payoff", "loop")


def recheck_plan(old_draft, new_draft, mapping, prior_checks):
    """{"changed": {new line numbers}, "kept": {new line: old line}, "prior": checks} when a partial re-check is
    safe, else None. Safe only when: the hooks are the same, every changed line is an ordinary setup or math line,
    the earlier check's only hard problems were flagged lines, and its ratings are all there."""
    if not mapping or new_draft.get("hooks") != old_draft.get("hooks"):
        return None
    hard = prior_checks.get("hard_problems") or []
    if not hard or not all(LINE_PROBLEM.match(h) for h in hard):
        return None
    if not prior_checks.get("hook_ratings") or prior_checks.get("loop") is None or prior_checks.get("originality") is None:
        return None
    flagged = {int(LINE_PROBLEM.match(h).group(1)) for h in hard}
    changed, kept = set(), {}
    for new_i, ln in enumerate(new_draft["lines"], 1):
        old_i = mapping.get(new_i)
        if old_i is None:
            return None
        if ln.get("text") == old_draft["lines"][old_i - 1].get("text") and old_i not in flagged:
            kept[new_i] = old_i                    # (a flagged line left as it was is checked again)
        else:
            changed.add(new_i)
    gone = set(range(1, len(old_draft["lines"]) + 1)) - set(mapping.values())
    for i in changed:
        if str(new_draft["lines"][i - 1].get("beat") or "") in RECHECK_FULL_BEATS:
            return None
    for i in gone:
        if str(old_draft["lines"][i - 1].get("beat") or "") in RECHECK_FULL_BEATS:
            return None
    if not changed and not gone:
        return None
    return {"changed": changed, "kept": kept, "prior": prior_checks}


def story_recheck_plan(old_draft, new_draft, prior_checks):
    """After a story repair (set-up lines added or a few lines reworded, the rest kept word for word): the same
    plan shape as recheck_plan, with lines matched by their exact words. Safe only when the hooks, the hook line,
    the re-hook and the loop line are unchanged and the earlier check's ratings are all there; the story itself
    (and whether the payoff answers the question) is always judged again."""
    if new_draft.get("hooks") != old_draft.get("hooks") or not new_draft.get("lines") or not old_draft.get("lines"):
        return None
    if not prior_checks.get("hook_ratings") or prior_checks.get("loop") is None or prior_checks.get("originality") is None:
        return None
    if new_draft["lines"][0].get("text") != old_draft["lines"][0].get("text"):
        return None
    flagged = {int(m.group(1)) for h in prior_checks.get("hard_problems") or [] for m in [LINE_PROBLEM.match(h)] if m}
    free = {}
    for i, ln in enumerate(old_draft["lines"], 1):
        free.setdefault(str(ln.get("text")), []).append(i)
    changed, kept = set(), {}
    for new_i, ln in enumerate(new_draft["lines"], 1):
        olds = free.get(str(ln.get("text"))) or []
        if olds and olds[0] not in flagged:
            kept[new_i] = olds.pop(0)
        else:
            if olds:
                olds.pop(0)
            changed.add(new_i)
    gone = set(range(1, len(old_draft["lines"]) + 1)) - set(kept.values()) - {
        i for i in range(1, len(old_draft["lines"]) + 1) if i in flagged}
    for i in changed:
        if str(new_draft["lines"][i - 1].get("beat") or "") in ("hook", "rehook", "loop"):
            return None
    for i in gone:
        if str(old_draft["lines"][i - 1].get("beat") or "") in ("hook", "rehook", "loop", "payoff"):
            return None
    if not changed and not gone:
        return None
    return {"changed": changed, "kept": kept, "prior": prior_checks, "story": True}


def _recheck(draft, topic, fact_list, lines, plan, unread=()):
    """The checker's answer for a fixed script, asking only about the changed lines (the whole script is shown, so
    each is judged in context). Kept lines keep their verdicts; hook, loop, re-hook and originality ratings carry
    over, since none of those lines changed. None if the answer is unusable (then the full check runs)."""
    from studio import claude
    prior = plan["prior"]
    story = bool(plan.get("story")) and _narrative_on()
    story_ask = story_shape = ""
    if story:                                     # after a story repair the story itself is judged again
        from studio import narrative
        m = draft.get("model") if isinstance(draft.get("model"), dict) else None
        story_ask = (("\nThe writer's model (the physics and maths the story rests on):\n" + json.dumps(m)[:2500] + "\n"
                      if m else "") + narrative.REVIEW_ASK + '\nAlso "question" (the one question the hook raises, in your words) and '
                     '"answered" 0-10 (does the payoff answer exactly that question plainly?).')
        story_shape = narrative.REVIEW_SHAPE + ', "question": "...", "answered": 0'
    if not plan["changed"] and not story:
        ans = {"claims": []}
    else:
        ask = ", ".join(f"L{i}" for i in sorted(plan["changed"])) or "no line (only the story questions below)"
        cited = {str(f) for i in plan["changed"] for f in (draft["lines"][i - 1].get("facts") or [])}
        # a story repair whose new lines only lean on pages already read here needs no web search
        web = not story or bool(cited & set(unread))
        ans = claude.ask_json(f"""You are a strict fact-checker for a science Shorts channel. A few lines of this script were just
reworded to fix problems an earlier check found; the other lines were already checked. Check ONLY {ask}, in the
context of the whole script. Each source below comes with text Jarvis just read from that page. Use it, and open the
page or search the web whenever that text doesn't clearly settle a claim. Do not trust the writer.

Sources:
{fact_list}

Script lines:
{lines}

For each of {ask} that states a fact or number, give a verdict: "supported" (a source says it, or it follows by
arithmetic from sourced numbers), "unsupported" (no source backs it) or "wrong" (a source contradicts it; give the
correct statement and the URL). A line that only states standard textbook science (a named law or formula, or a
well-known physical constant, stated correctly in its usual form) gets "standard"; any number specific to this topic
never counts as standard. A value the line plainly presents as the narrator's own chosen example ("say the car is
4.5 m long") is "standard" when realistic and only used to work the example; stated as a fact it is "unsupported". Leave opinion and transition lines out. For "supported" or "wrong" give "quote" (the
source's own words, at most 25 words, copied exactly), "confidence" 0.0-1.0 and "source_date" (YYYY-MM-DD or "").
Give "note" only for "unsupported", "wrong" and "standard". "problems": anything a careful editor would fix in
those lines (confusing step, misleading simplification, unit mistake).
{story_ask}

JSON only: {{"claims": [{{"line": 3, "verdict": "supported", "fact": "F1", "quote": "...", "confidence": 0.9,
"source_date": ""}}], "problems": ["..."]{story_shape}}}""", timeout=900, web=web,
                              purpose=f"fact-check (changed lines{', story' if story else ''}): {topic['title']}")
        if not isinstance(ans, dict) or not isinstance(ans.get("claims"), list):
            return None
    new = [dict(c) for c in ans["claims"] if isinstance(c, dict) and c.get("line") in plan["changed"]]
    old_by_line = {c.get("line"): c for c in prior.get("claims") or [] if isinstance(c, dict)}
    for new_i, old_i in plan["kept"].items():
        c = old_by_line.get(old_i)
        if c is not None and c.get("verdict") not in ("unsupported", "wrong"):
            new.append(dict(c, line=new_i))
    new.sort(key=lambda c: c.get("line") or 0)
    editor = [p[len("editor: "):] for p in prior.get("problems") or [] if p.startswith("editor: ")]
    return {"claims": new, "hooks": {str(k): v for k, v in (prior.get("hook_ratings") or {}).items()},
            "loop": prior.get("loop"), "rehook": prior.get("rehook"), "originality": prior.get("originality"),
            "question": ans.get("question") if story and ans.get("question") else prior.get("question"),
            "answered": ans.get("answered") if story and isinstance(ans.get("answered"), (int, float))
            else prior.get("answered"),
            "inauthentic_risk": prior.get("inauthentic_risk"),
            "story": ans.get("story") if story and isinstance(ans.get("story"), dict)
            else (prior.get("narrative") or {}).get("story"),
            "problems": (editor + [p for p in ans.get("problems") or [] if isinstance(p, str)])[:5]}


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
    if not draft and not feedback and not previous:
        from studio import topics
        try:
            topics.prepare(topic)                # more sourced numbers + the newcomer facts (free, once per topic)
        except Exception:
            pass                                 # the topic's own sources are always enough to go on
    if not draft:
        draft = write(topic, feedback, previous)
        if not feedback and not previous:
            draft = _pre_fix(draft, topic)
    v = db.one("SELECT MAX(version) v FROM scripts WHERE video_id IS ?", video_id)["v"] or 0
    hook = draft["hooks"][0] if draft["hooks"] else draft["lines"][0]["text"]
    return db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", video_id, v + 1, json.dumps(draft["hooks"]), hook, text_of(draft),
                     json.dumps({"lines": draft["lines"], "calcs": draft.get("calcs") or [], "titles": draft.get("titles") or [],
                                 "by": draft.get("by") or "claude",
                                 **({"model": draft["model"]} if isinstance(draft.get("model"), dict) else {}),
                                 **({"picked": draft["picked"]} if draft.get("picked") else {}),
                                 **({"recheck": draft["recheck"]} if draft.get("recheck") else {}),
                                 **({"number_check": draft["number_check"]} if draft.get("number_check") else {}),
                                 **({"story_fix": draft["story_fix"]} if draft.get("story_fix") else {}),
                                 **({k: draft[k] for k in ("newcomer_read", "editor", "pre_fix") if draft.get(k)}),
                                 **({"originality_fix": draft["originality_fix"]} if draft.get("originality_fix") else {})}),
                     "{}", "draft", time.time())


def _pre_fix(draft, topic):
    """Pipeline order review (2026-11-27): what the editor and the newcomer read found in the picked first draft
    is repaired ONCE here, before the paid web fact-check, instead of after it (a fail there used to cost the
    check, a repair and a second check). An originality miss gets the originality-only rewrite; story gaps get the
    targeted story repair. Never more than one Claude call; the draft goes on unchanged when nothing is found."""
    if not editor_on():
        return draft
    probs = editor_problems(draft.get("editor"), draft.get("lines")) + read_problems(draft.get("newcomer_read"))
    if not probs:
        return draft
    keep = {k: draft[k] for k in ("picked", "editor", "newcomer_read", "number_check") if draft.get(k)}
    try:
        if any(p.startswith("originality ") for p in probs):
            fixed = write(topic, probs, draft, only_originality=True)
            kind = "originality"
        else:
            fixed = _fix_story(draft, topic, probs)
            kind = "story"
    except ScriptRejected:
        fixed = None
    except Exception as e:
        if type(e).__name__ in ("QuotaWait", "Transient"):
            raise
        print(f"(repair before the fact-check skipped: {str(e)[:120]})")
        fixed = None
    if not fixed:
        return dict(draft, pre_fix={"kind": "none", "problems": [p[:200] for p in probs[:5]]})
    if isinstance(draft.get("model"), dict) and not isinstance(fixed.get("model"), dict):
        fixed["model"] = draft["model"]
    keep.pop("newcomer_read", None)               # the read was of the old wording
    sf = fixed.pop("story_fix", None)             # kept apart: the after-check story repair stays available
    fixed.pop("originality_fix", None)
    return dict(fixed, **keep, pre_fix={"kind": kind, "problems": [p[:200] for p in probs[:5]],
                                        **({"story_fix": sf} if sf else {})})


def partial_recheck_on():
    try:
        from studio import config
        return config.general().get("partial_recheck", True) is not False
    except Exception:
        return True


def _saved_recheck(st, video_id):
    """The partial re-check plan saved with a fixed draft, with the earlier check it builds on, or None."""
    r = st.get("recheck")
    if not r or not partial_recheck_on():
        return None
    prev = db.one("SELECT checks FROM scripts WHERE id=? AND video_id IS ?", r.get("from"), video_id)
    if not prev:
        return None
    return {"changed": set(r.get("changed") or []), "kept": {int(k): v for k, v in (r.get("kept") or {}).items()},
            "prior": json.loads(prev["checks"] or "{}"), **({"story": True} if r.get("story") else {})}


def _draft_of(row):
    st = json.loads(row["structure"] or "{}")
    d = {"hooks": json.loads(row["hooks"] or "[]"), "lines": st.get("lines") or [], "calcs": st.get("calcs") or [],
         "titles": st.get("titles") or []}
    if isinstance(st.get("model"), dict):
        d["model"] = st["model"]
    return d


def lookup_on():
    try:
        from studio import config
        return config.general().get("lookup_before_cut", True) is not False
    except Exception:
        return True


def _look_up_flagged(topic, video_id, row, draft, st, rev, checks):
    """Before cutting one or two unsupported lines: one search for a page that states each line's number. Lines it
    sources get that page as their fact and are checked again (only those lines). Returns the new draft row's id,
    or None when there was nothing to look up or nothing was found (then the usual cut or rewrite follows)."""
    hard = rev.get("hard_problems") or []
    if not hard or not lookup_on() or st.get("lookup_from"):
        return None
    flagged = {}
    for h in hard:
        m = LINE_PROBLEM.match(h)
        if not m or m.group(2) != "unsupported":
            return None
        flagged[int(m.group(1))] = h
    if len(flagged) > PRUNE_MAX:
        return None
    asked = {}
    for n in flagged:
        if 1 <= n <= len(draft["lines"]):
            text = " ".join(str(draft["lines"][n - 1].get("text", "")).split())
            nums = [x for x in _numbers(text) if not (x.isdigit() and int(x) <= SMALL)]
            if nums:
                asked[n] = {"what": text[:200], "value": nums[0]}
    found = look_up(asked, topic, PRUNE_MAX) if asked else {}
    if not found:
        return None
    lines = [dict(ln, facts=[f for f in ln.get("facts") or [] if str(f) not in ("needs", "none")] + [found[i]])
             if i in found else ln for i, ln in enumerate(draft["lines"], 1)]
    new = dict(draft, lines=lines)
    st2 = {"lines": lines, "calcs": st.get("calcs") or [], "titles": st.get("titles") or [], "by": st.get("by") or "claude",
           "lookup_from": row["id"], "looked_up": {str(i): f for i, f in found.items()},
           **({"model": st["model"]} if isinstance(st.get("model"), dict) else {})}
    plan = recheck_plan(draft, new, {i: i for i in range(1, len(lines) + 1)}, checks) if partial_recheck_on() else None
    if plan:
        st2["recheck"] = {"from": row["id"], "changed": sorted(plan["changed"]),
                          "kept": {str(k): v for k, v in plan["kept"].items()}}
    return db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", video_id, row["version"] + 1, row["hooks"], row["hook"], row["body"],
                     json.dumps(st2), "{}", "draft", time.time())


def check_step(topic, video_id=None, max_revisions=MAX_REVISIONS):
    """Fact-checks the newest draft. Returns ("passed", row) or ("revise", row); raises ScriptRejected
    when the last allowed revision still fails."""
    from studio import topics
    row = db.one("SELECT * FROM scripts WHERE video_id IS ? AND status='draft' ORDER BY version DESC LIMIT 1", video_id)
    if not row:
        raise ScriptRejected("there's no draft to check")
    draft = _draft_of(row)
    st = json.loads(row["structure"] or "{}")
    rev = review(draft, topic, video_id, recheck=_saved_recheck(st, video_id))
    checks = {k: rev[k] for k in ("ok", "problems", "hard_problems", "claims", "hook_ratings", "hook_index",
                                  "hook_score", "words", "loop", "rehook", "originality", "inauthentic_risk", "similar",
                                  "question", "answered", "narrative", "originality_hold")
              if k in rev}
    if rev.get("rechecked"):
        checks["rechecked"] = rev["rechecked"]
    if rev["ok"]:
        polish = polish_problems(rev, video_id, max_revisions)
        if polish:
            checks["polish"] = True
            rev["ok"] = False
            rev["hard_problems"] = list(rev.get("hard_problems") or []) + polish
            checks.update(ok=False, hard_problems=rev["hard_problems"])
    if rev["ok"]:
        back = explain_back(topic, draft, rev["hook"], video_id, max_revisions, read=st.get("newcomer_read"))
        if back:
            checks["explain_back"] = back
            if back.get("unclear"):
                rev["ok"] = False
                rev["hard_problems"] = list(rev.get("hard_problems") or []) + [back["problem"]]
                rev["problems"] = list(rev.get("problems") or []) + [back["problem"]]
                checks.update(ok=False, hard_problems=rev["hard_problems"], problems=rev["problems"])
    st["calcs"] = rev["calcs"]
    db.run("UPDATE scripts SET hook=?, body=?, structure=?, checks=?, status=? WHERE id=?", rev["hook"], rev["body"],
           json.dumps(st), json.dumps(checks), "passed" if rev["ok"] else "failed", row["id"])
    row = dict(db.one("SELECT * FROM scripts WHERE id=?", row["id"]))
    _story_memory(topic, video_id, row, rev)
    if rev["ok"]:
        _trial_outcome(topic, video_id, True)
        _record_claims(topic, video_id, rev, draft["lines"])
        topics.set_status(topic["id"], script_status="passed")
        guard.audit("script.passed", True, {"topic": topic["id"], "video": video_id, "version": row["version"]})
        return "passed", row
    from studio import lessons
    lessons.record("script", rev["hard_problems"], topic, video_id)
    tries_now = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    if tries_now < max_revisions or tries_now <= max_revisions and _prune(draft, rev):
        if _look_up_flagged(topic, video_id, row, draft, st, rev, checks):
            return check_step(topic, video_id, max_revisions)
    pruned = _prune(draft, rev)
    if pruned:                                    # one or two extra lines were the only problem: cut them, no new call
        draft2, gone, mapping = pruned
        st2 = dict(st, lines=draft2["lines"], pruned={"from_version": row["version"], "lines": gone,
                                                     "why": rev["hard_problems"][:4]})
        claims = [dict(c, line=mapping[c["line"]]) for c in rev["claims"]
                  if isinstance(c.get("line"), int) and c["line"] in mapping]
        body = text_of(draft2, rev["hook"])
        checks2 = dict(checks, ok=True, hard_problems=[], claims=claims, words=len(body.split()),
                       problems=[p for p in rev["problems"] if p.startswith("editor: ")]
                       + [f"cut line {n} (it was the only problem: {why[:120]})" for n, why in gone])
        back = explain_back(topic, draft2, rev["hook"], video_id, max_revisions)   # does it still make sense?
        if back:
            checks2["explain_back"] = back
        if back and back.get("unclear"):
            checks2.update(ok=False, hard_problems=[back["problem"]], problems=checks2["problems"] + [back["problem"]])
            db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", video_id, row["version"] + 1, json.dumps(draft2["hooks"]),
                      rev["hook"], body, json.dumps(st2), json.dumps(checks2), "failed", time.time())
            lessons.record("script", [back["problem"]], topic, video_id)
            return "revise", dict(db.one("SELECT * FROM scripts WHERE video_id IS ? ORDER BY version DESC LIMIT 1",
                                         video_id))
        rid = db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", video_id, row["version"] + 1, json.dumps(draft2["hooks"]),
                        rev["hook"], body, json.dumps(st2), json.dumps(checks2), "passed", time.time())
        prow = dict(db.one("SELECT * FROM scripts WHERE id=?", rid))
        _trial_outcome(topic, video_id, True)
        _record_claims(topic, video_id, dict(rev, claims=claims), draft2["lines"])
        topics.set_status(topic["id"], script_status="passed")
        guard.audit("script.passed", True, {"topic": topic["id"], "video": video_id, "version": prow["version"],
                                            "pruned": [n for n, _ in gone]})
        return "passed", prow
    tries = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    again = None if st.get("lookup_from") else _repeated(video_id, row["id"], rev["hard_problems"])
    if again and _story_repairable(rev["hard_problems"]) and not _story_fix_tried(video_id):
        again = None                              # the targeted story repair gets its one try before giving up
    if again == "originality" and _originality_due(video_id, rev["hard_problems"]):
        again = None                              # one originality-only rewrite with the checker's own idea first
    if again:                                     # the same kind of problem twice in a row: move on to the next idea
        _trial_outcome(topic, video_id, False)
        topics.set_status(topic["id"], script_status="failed")
        guard.audit("script.failed", True, {"topic": topic["id"], "video": video_id, "repeated": again,
                                            "problems": rev["hard_problems"][:5]})
        _story_outcome(video_id, False)
        raise ScriptRejected(f"the same problem came back after a revision ({again.replace('_', ' ')}): "
                             + "; ".join(rev["hard_problems"][:3]))
    if tries > max_revisions and not (_story_repairable(rev["hard_problems"]) and not _story_fix_tried(video_id)) \
            and not _originality_due(video_id, rev["hard_problems"]):
        _trial_outcome(topic, video_id, False)
        topics.set_status(topic["id"], script_status="failed")
        guard.audit("script.failed", True, {"topic": topic["id"], "video": video_id, "problems": rev["hard_problems"][:5]})
        _story_outcome(video_id, False)
        raise ScriptRejected("the script still failed the fact-check: " + "; ".join(rev["hard_problems"][:4]))
    return "revise", row


# ---------- explain it back: does a first-time viewer get the point? (local brain, free) ----------

EXPLAIN_MIN_MATCH = 2              # the local model's 1-5 "same point" rating at or under this sends it back once
EXPLAIN_SYSTEM = ("You are a curious 14-year-old who just watched a 45-second science Short once. You only know what "
                  "the narrator said. Answer in JSON.")


POLISH_WORDS = re.compile(r"\b(wrong|incorrect|misleading|confus\w*|hang\w*|dangl\w*|unclear|doesn'?t follow|"
                          r"never answer\w*|unanswered|no payoff|skips?|missing step)\b", re.I)


def polish_on():
    try:
        from studio import config
        return config.general().get("polish_rewrite", True) is not False
    except Exception:
        return True


def polish_problems(rev, video_id=None, max_revisions=MAX_REVISIONS):
    """Connor's choice (2026-11-25): a script that passed the facts but has a clear storytelling problem gets ONE
    rewrite. Clear means: a weak loop or re-hook (under 5/10), the payoff not quite answering the hook's question
    (under 6/10), surprising numbers with no comparison, or an editor note that says something is wrong, misleading,
    confusing or left hanging. Never twice for a Short, never on the last allowed try. Returns the problems ("polish: ...")
    or []."""
    if not polish_on():
        return []
    notes = [p[len("editor: "):] for p in rev.get("problems") or [] if str(p).startswith("editor: ")]
    clear = []
    for n in notes:
        if (re.search(r"\((loop|rehook) [0-4](\.\d)?/10\)", n) or re.search(r"\(answered [0-5](\.\d)?/10\)", n)
                or n.startswith("give these numbers a plain comparison") or POLISH_WORDS.search(n)):
            clear.append("polish: " + n)
    if not clear:
        return []
    tries = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    done = any(json.loads(r["checks"] or "{}").get("polish") for r in db.q("SELECT checks FROM scripts WHERE video_id IS ?",
                                                                           video_id))
    if done or tries >= max_revisions:
        return []
    return clear[:5]


def explain_on():
    try:
        from studio import config
        return config.general().get("explain_back", True) is not False
    except Exception:
        return True


def explain_back(topic, draft, hook, video_id=None, max_revisions=MAX_REVISIONS, read=None):
    """The local model retells the Short in one sentence and rates (1-5) how well that matches the point the
    Short was meant to make. A clear miss (EXPLAIN_MIN_MATCH or lower) sends the script back once, with what the
    viewer took away, so the rewrite makes the point plainer. It also reads it as a confused newcomer: lines it
    really couldn't follow (a skipped step, an unexplained word) send it back the same way. Never more than once per Short, never on the last
    allowed try (so it can't cost a Short), and skipped when the local brain isn't ready. None when skipped."""
    if read and read.get("body") == text_of(draft, hook):
        out = read                                # this exact wording was already read before the fact-check
    else:
        out = newcomer_read(topic, draft, hook)
    if out is None:
        return None
    out = {k: v for k, v in out.items() if k != "body"}
    lines = [hook] + [ln["text"] for ln in draft["lines"][1:]] if draft.get("lines") else [hook]
    topic = dict(topic or {})
    point = f"{topic.get('title') or ''}. {topic.get('angle') or ''}".strip(". ")
    match = out["match"]
    tries = db.one("SELECT COUNT(*) n FROM scripts WHERE video_id IS ? AND status='failed'", video_id)["n"]
    asked = any("explain_back" in (json.loads(r["checks"] or "{}"))
                and (json.loads(r["checks"] or "{}")["explain_back"] or {}).get("unclear")
                for r in db.q("SELECT checks FROM scripts WHERE video_id IS ?", video_id))
    missed = match <= EXPLAIN_MIN_MATCH and out["takeaway"]
    if (missed or out["confusing"]) and not asked and tries < max_revisions:
        out["unclear"] = True
        said = []
        if missed:
            said.append(f"would come away with \"{out['takeaway']}\" instead of the intended point ({point[:160]})")
        if out["confusing"]:
            said.append("couldn't follow " + "; ".join(f"line {c['line']} (\"{lines[c['line'] - 1][:80]}\": {c['why']})"
                                                       for c in out["confusing"]))
        out["problem"] = ("clarity: a first-time viewer " + " and ".join(said)
                          + ". Keep every checked fact, but add the missing step or plain word, and tie each line to the "
                            "main point.")
    return out


def newcomer_read(topic, draft, hook=None):
    """The local model reads the narration once as a newcomer: {"takeaway", "match" 1-5, "confusing": [{line, why}],
    "body"}. Free (this computer). None when it's switched off, not ready or gives no usable answer."""
    if not explain_on() or not draft.get("lines"):
        return None
    local = _local()
    if not local or not local.ready("studio_json")[0]:
        return None
    hook = hook or (draft.get("hooks") or [None])[0] or draft["lines"][0]["text"]
    lines = [hook] + [ln["text"] for ln in draft["lines"][1:]]
    topic = dict(topic or {})
    point = f"{topic.get('title') or ''}. {topic.get('angle') or ''}".strip(". ")
    ans = local.chat_json([{"role": "system", "content": EXPLAIN_SYSTEM},
                           {"role": "user", "content":
                            "The narration, line by line:\n"
                            + "\n".join(f"{i}. {x}" for i, x in enumerate(lines, 1)) + "\n\n"
                            f"The point it was meant to make: {point}\n\n"
                            "You are new to this topic. Point out only lines you really could not follow: a word "
                            "you didn't know that wasn't explained, a step that was skipped, or a line that doesn't "
                            "connect to the one before. Most good scripts have none.\n"
                            'Reply as {"takeaway": "the one thing you learned, in one sentence", '
                            '"match": 1-5 (5 = exactly the intended point, 1 = something else), '
                            '"confusing": [{"line": 3, "why": "what you didn\'t get"}]}'}],
                          max_tokens=300, timeout=60, first_timeout=30, temperature=0.2)
    if not ans:
        return None
    try:
        match = int(float(ans.get("match")))
    except (TypeError, ValueError):
        return None
    confusing = []
    for c in ans.get("confusing") or []:
        try:
            n, why = int(c.get("line")), " ".join(str(c.get("why") or "").split())[:160]
        except (AttributeError, TypeError, ValueError):
            continue
        if 1 <= n <= len(lines) and len(why) >= 8:
            confusing.append({"line": n, "why": why})
    return {"takeaway": str(ans.get("takeaway") or "")[:240], "match": match, "confusing": confusing[:3],
            "body": text_of(draft, hook)}


PRUNE_MAX = 2                      # at most this many flagged lines are simply cut
PRUNE_MIN_LINES = 5                # and at least this many lines must stay
PRUNE_KEEP_BEATS = ("hook", "payoff", "loop")
REFERS_BACK = ("that", "this", "these", "those", "which", "it", "it's", "its", "they", "there", "same")


def _prune(draft, rev):
    """When the ONLY hard problems are one or two lines the fact-check flagged, and those lines aren't the hook,
    the payoff, the loop or the last step of the math, the script works without them: (pruned draft, [(line,
    why)], {old line: new line}). Every line that stays was checked in this very review, word for word, so no
    new fact-check is needed. None when cutting isn't safe (then the usual revision runs)."""
    hard = rev.get("hard_problems") or []
    if not hard:
        return None
    flagged = {}
    for h in hard:
        m = LINE_PROBLEM.match(h)
        if not m:
            return None
        flagged[int(m.group(1))] = h
    lines = draft.get("lines") or []
    n = len(lines)
    if len(flagged) > PRUNE_MAX or n - len(flagged) < PRUNE_MIN_LINES:
        return None
    for k in flagged:
        if not 1 < k < n or str(lines[k - 1].get("beat") or "") in PRUNE_KEEP_BEATS:
            return None
    keep = [i for i in range(1, n + 1) if i not in flagged]
    if not any(str(lines[i - 1].get("beat")) == "math" for i in keep) and any(
            str(ln.get("beat")) == "math" for ln in lines):
        return None
    for k in flagged:                       # the line after a cut one mustn't lean on it ("That's why...")
        nxt = next((i for i in keep if i > k), None)
        if nxt:
            first = re.sub(r"[^a-z']", "", (str(lines[nxt - 1].get("text") or "").split() or [""])[0].lower())
            if first in REFERS_BACK:
                return None
        prev = max((i for i in keep if i < k), default=None)   # nor may the line before lead into it ("...is simple:")
        if prev and str(lines[prev - 1].get("text") or "").rstrip().endswith((":", "—", "-", "...", "…")):
            return None
    if any(str(ln.get("beat")) == "payoff" for ln in lines) and not any(
            str(lines[i - 1].get("beat")) == "payoff" for i in keep):
        return None
    if str(lines[keep[-1] - 1].get("text") or "").rstrip().endswith((":", "—", "...", "…")):
        return None                         # the Short can't end mid-thought
    out = dict(draft, lines=[lines[i - 1] for i in keep])
    words = len(text_of(out, rev.get("hook")).split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        return None
    if _narrative_on():                     # a cut line may have been a step of the explanation
        from studio import narrative
        try:
            if narrative.evaluate(dict(out, hook=rev.get("hook")))["hard"]:
                return None
        except Exception:
            return None
    return out, sorted(flagged.items()), {old: new for new, old in enumerate(keep, 1)}


REPEAT_STOPS = ("unsupported", "wrong_fact", "originality", "missing_value", "story_math")


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
When the flagged part is a comparison no fact backs ("slower than a snail", "as wide as your palm"), remove just
the comparison and keep the line's sourced number, said in a unit a viewer can picture; never swap in another
unbacked comparison. When the flagged part is a number no fact gives that the example needs as an input (a car's
length, a turn's radius), say it plainly as your own pick ("say the car is 4.5 m long") with a realistic round
value that isn't equal to any result; any other unbacked number is removed, or the line is deleted. Never add a new claim, caveat or framing line. Every other line stays exactly as it is, word for word.

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
    out, mapping = [], {}
    for i, ln in enumerate(draft["lines"], 1):
        e = edits.get(i)
        if e and (e.get("delete") or not str(e.get("text", "")).strip()):
            continue
        out.append(dict(ln, text=str(e["text"]).strip()) if e else ln)
        mapping[len(out)] = i
    if not out:
        return None
    fixed = dict(draft, lines=out)
    n = len(text_of(fixed, fixed["hooks"][0] if fixed.get("hooks") else None).split())
    if not WORDS_MIN <= n <= WORDS_MAX:
        fitted = _fit_length(fixed, n)
        if fitted:
            fixed, mapping = fitted, None          # the length fix may touch any line: check it all again
    fixed["_mapping"] = mapping
    return fixed


def revise_step(topic, video_id=None):
    row = _best_failed(video_id)
    checks = json.loads(row["checks"] or "{}")
    hard = checks.get("hard_problems") or []
    if hard and all(LINE_PROBLEM.match(h) for h in hard):     # a near-miss: repair just those lines
        old = _draft_of(row)
        fixed = _fix_lines(old, topic, hard)
        if fixed:
            mapping = fixed.pop("_mapping", None)
            plan = recheck_plan(old, fixed, mapping, checks) if partial_recheck_on() else None
            if plan:
                fixed["recheck"] = {"from": row["id"], "changed": sorted(plan["changed"]),
                                    "kept": {str(k): v for k, v in plan["kept"].items()}}
            return draft_step(topic, video_id, draft=fixed)
    if _originality_due(video_id, hard):
        fixed = write(topic, hard, _draft_of(row), only_originality=True)
        fixed["originality_fix"] = {"from_version": row["version"], "problems": [p[:300] for p in hard[:4]]}
        return draft_step(topic, video_id, draft=fixed)
    if _story_repairable(hard) and not _story_fix_tried(video_id):
        old = _draft_of(row)
        fixed = _fix_story(old, topic, hard)               # a story gap: add the missing set-up, keep the rest
        if fixed:
            plan = story_recheck_plan(old, fixed, checks) if partial_recheck_on() else None
            if plan:                                       # only the new lines and the story are checked again
                fixed["recheck"] = {"from": row["id"], "changed": sorted(plan["changed"]),
                                    "kept": {str(k): v for k, v in plan["kept"].items()}, "story": True}
            return draft_step(topic, video_id, draft=fixed)
    orig = checks.get("originality")
    if isinstance(orig, (int, float)) and orig >= 6:   # a full rewrite for other problems keeps the original image
        fixed = write(topic, checks.get("problems") or [], _draft_of(row), keep_original=True)
        return draft_step(topic, video_id, draft=fixed)
    return draft_step(topic, video_id, checks.get("problems") or [], _draft_of(row))


# story stops tied to one line (or one untold value), which a set-up line or a reworded line can fix
STORY_LINE = re.compile(r"^narrative: (line \d+ (leans on|relies on|says .* before)|the starting value|"
                        r"unexplained number: line \d+|the conclusion doesn't follow|"
                        # a demonstration or a named period said badly in one line (Short 35): reword that line
                        r"line \d+ (places the marker|says where the marker|turns |calls \S+ days|ties 29|"
                        r"says the marker is))")


LENGTH_PROBLEM = re.compile(r"^\d+ words; it must be")


def _story_repairable(hard):
    return bool(hard) and _narrative_on() and any(STORY_LINE.match(h) for h in hard) and \
        all(STORY_LINE.match(h) or LINE_PROBLEM.match(h) or LENGTH_PROBLEM.match(h) for h in hard)


def _originality_due(video_id, hard):
    """Connor's choice (2026-11-26): when "reads like the standard explainer" comes back after the normal rewrite,
    the writer gets ONE more rewrite aimed only at originality (the checker's own suggestion), before the repeat
    stop. Only when originality (plus at most a flagged line or the length) is what's left to fix."""
    if not any(str(p).startswith("originality ") for p in hard or []):
        return False
    if not all(str(p).startswith("originality ") or LINE_PROBLEM.match(p) or LENGTH_PROBLEM.match(p) for p in hard):
        return False
    rows = list(db.q("SELECT checks, structure FROM scripts WHERE video_id IS ?", video_id))
    if any(json.loads(r["structure"] or "{}").get("originality_fix") for r in rows):
        return False
    return sum(any(str(p).startswith("originality ") for p in json.loads(r["checks"] or "{}").get("hard_problems") or [])
               for r in rows) >= 2


HOLD_ORIGINALITY = 5           # the one originality score under the bar that may pass, held for Connor's review
HOLD_STORY_MIN = 80            # story grade (0-100) it needs
HOLD_SCORES_MIN = {"loop": 6, "rehook": 6, "answered": 7}


def originality_hold_on():
    try:
        from studio import config
        return config.general().get("originality_hold", True) is not False
    except Exception:
        return True


def originality_hold(orig, hard, loop, rehook, answered, nar, video_id=None):
    """Connor's middle ground (2026-11-26): an originality 5 passes only when it is the ONE thing wrong and
    everything else scores high (clean fact-check, story grade 80+, loop, re-hook and payoff strong), and then the
    Short is held for Connor's review and never goes out by itself. At most one Short is held at a time. 6 stays the
    normal bar. Returns {"originality", "story", "why"} or None."""
    if not originality_hold_on() or not isinstance(orig, (int, float)) or not HOLD_ORIGINALITY <= orig < 6:
        return None
    if [p for p in hard if not p.startswith("originality ")] or not nar or nar.get("hard"):
        return None
    story = nar.get("score")
    if not isinstance(story, (int, float)) or story < HOLD_STORY_MIN:
        return None
    for k, v in (("loop", loop), ("rehook", rehook), ("answered", answered)):
        if not isinstance(v, (int, float)) or v < HOLD_SCORES_MIN[k]:
            return None
    if any(held_for_review(r["id"]) for r in db.q("SELECT id FROM videos WHERE id IS NOT ? AND state NOT IN "
                                                   "('published','failed','rejected')", video_id)):
        return None                              # one waits for Connor already
    why = next((p for p in hard if p.startswith("originality ")), "")
    return {"originality": orig, "story": story, "why": why[:300]}


def held_for_review(video_id):
    """Why this Short is held for Connor (an originality-5 pass he hasn't approved yet), or None."""
    row = latest(video_id, "passed")
    hold = json.loads(row["checks"] or "{}").get("originality_hold") if row else None
    if not hold:
        return None
    v = db.video(video_id) or {}
    if json.loads(v.get("meta") or "{}").get("review_ok"):
        return None
    return (f"held for Connor's review: originality {hold.get('originality')}/10 (story {hold.get('story')}), "
            f"so it never goes out by itself; approve with: studio approve --video {video_id}")


def _story_fix_tried(video_id):
    return any(json.loads(r["structure"] or "{}").get("story_fix")
               for r in db.q("SELECT structure FROM scripts WHERE video_id IS ?", video_id))


PLAN_KEYS = ("learn", "uses", "tells", "needs")


def _carry_plan(new, old):
    """A rewrite often drops the per-line storyboard ("learn", "uses", "tells", "needs"; test 39). A new line
    without it takes it from the old line it most resembles (half or more of the old line's own words kept)."""
    olds = [ln for ln in (old or {}).get("lines") or [] if isinstance(ln, dict)]
    out = []
    for ln in new.get("lines") or []:
        missing = [k for k in PLAN_KEYS if ln.get(k) in (None, "")]
        if missing and olds:
            words = _content_words(ln.get("text"))
            best = max(olds, key=lambda o: len(_content_words(o.get("text")) & words) /
                       max(1, len(_content_words(o.get("text")))))
            if _keeps_image(best.get("text"), ln.get("text")):
                ln = dict(ln, **{k: best[k] for k in missing if best.get(k) not in (None, "")})
        out.append(ln)
    return dict(new, lines=out)


def _content_words(t):
    return {w for w in re.findall(r"[a-z']+", str(t or "").lower()) if len(w) > 3}


def _keeps_image(old, new):
    """A story-repair rewording must keep most of the line's own words (its picture): test 31's repair swapped
    "One more floor: the Sun's center" for a bare division and the hook's question for a statement, and the
    originality score fell. A rewording that keeps under half the old words is dropped (the line stays)."""
    a = _content_words(old)
    return not a or len(a & _content_words(new)) / len(a) >= 0.5


TOO_LATE = re.compile(r"^narrative: line (\d+) relies on .* only hears it in line (\d+)")
NO_MOVE_BEATS = ("hook", "payoff", "loop")


def _reorder(draft, problems):
    """Free first try for "a value is heard a line or two too late" (test 34b: "25% reflectivity" used two lines
    before the line that says it): move the line that introduces it up to just before the first line that needs
    it. Kept only when the code's own story checks then find fewer problems. None if nothing could be moved."""
    lines = [dict(ln) for ln in draft.get("lines") or []]
    moved = False
    for p in problems:
        m = TOO_LATE.match(p)
        if not m:
            continue
        need, said = int(m.group(1)), int(m.group(2))
        if not 1 < need < said <= len(lines) or said - need > 3:
            continue
        if str(lines[said - 1].get("beat")) in NO_MOVE_BEATS:
            continue
        lines.insert(need - 1, lines.pop(said - 1))
        moved = True
        break                                          # one move at a time: line numbers change after it
    if not moved:
        return None
    out = dict(draft, lines=lines)
    return out if _story_hard(out) < _story_hard(draft) else None


def _fix_story(draft, topic, problems):
    """Repairs story gaps the way an editor would: a short set-up line ADDED before the line that needs it (what
    a term means, a value with its unit, why an assumption is fair), or the line reworded in plain words, with
    every other line kept word for word. Rewriting the whole script fixed none of 25 such stops (tests 21-23):
    each rewrite made new gaps of its own. The fixed script then gets a full check. None if it can't be used."""
    moved, todo = draft, problems
    for _ in range(3):                                 # free: move introducing lines up before asking Claude
        nxt = _reorder(moved, todo)
        if not nxt:
            break
        moved, todo = nxt, narrative_hard(nxt)         # line numbers changed: re-read the code's own stops
    if moved is not draft:
        left = narrative_hard(moved)
        if not [h for h in problems if not TOO_LATE.match(h)] and not left:
            moved["story_fix"] = {"problems": [p[:200] for p in problems[:5]], "edits": 0, "moved": True}
            return moved
        draft = moved
    from studio import claude
    facts = _facts(topic)
    fact_list = "\n".join(f"F{i + 1}. {s['claim']} (source: {s['url']})" for i, s in enumerate(facts))
    lines = "\n".join(f"L{i + 1}. [{ln.get('beat')}] {ln['text']}  [uses: {', '.join(map(str, ln.get('facts') or []))}]"
                      for i, ln in enumerate(draft["lines"]))
    n = len(text_of(draft, draft["hooks"][0] if draft.get("hooks") else None).split())
    ans = claude.ask_json(f"""A story editor found places in this Short script where a first-time viewer gets lost. Fix ONLY those
places, every one of them: each problem below needs its own edit on (or just before) the line it names. Keep the Short's image: a reworded line keeps its picture words (the elevator, the floor,
the everyday object) and adds the missing value INTO that picture ("One more floor: the Sun's center, about 15.7
million kelvin"), never swaps the picture for a bare number. The hook (line 1) only ever gains a missing value
or unit; its question and picture stay. For each:
either add ONE short set-up line (under 14 words) just before the flagged line that gives what is
missing in plain words (what a term means, a value with its unit, why an assumption is fair, a step the viewer
would otherwise have to work out, like "two steps up means 32 times 32"; when the problem says what is missing,
the set-up line says exactly that), or reword the
flagged line so it no longer needs it (plain words instead of jargon like a band name or an abbreviation). A
line the fact-checker marked wrong is reworded to its correction. If the conclusion doesn't follow, reword the
payoff line (the line before the last) so it answers the opening question plainly from the lines before it. A
value you add must come from the facts below; never a new number. Every other line stays exactly as it is,
word for word. The script is {n} words; when you are done it must be {WORDS_MIN + 10}-{WORDS_MAX - 8} words
(count them, the hook included: a script over {WORDS_MAX} is stopped). If an added line would push it over,
shorten or delete a line that explains nothing (a teaser or a restatement), never a step of the explanation,
the payoff or the last line.

Facts:
{fact_list}

Lines:
{lines}

Problems:
""" + "\n".join(f"- {p}" for p in problems) + """

JSON only: {"edits": [{"op": "insert_before|replace|delete", "line": 5, "text": "...", "facts": ["F2"], "beat": "setup"}]}""",
                          timeout=300, web=False, purpose=f"fix story gaps: {topic['title']}")
    edits = [e for e in (ans.get("edits") if isinstance(ans, dict) else None) or []
             if isinstance(e, dict) and str(e.get("line", "")).isdigit() and e.get("op") in ("insert_before", "replace",
                                                                                           "delete")]
    if not edits:
        return None
    by = {}
    for e in edits:
        by.setdefault(int(e["line"]), []).append(e)
    wrong = {int(m.group(1)) for p in problems for m in [LINE_PROBLEM.match(p)] if m}
    out = []
    for i, ln in enumerate(draft["lines"], 1):
        keep = dict(ln)
        for e in by.get(i, []):
            text = " ".join(str(e.get("text") or "").split())
            if e["op"] == "insert_before" and text and i > 1:
                out.append({"text": text, "facts": [str(f) for f in e.get("facts") or []] or ["none"],
                            "beat": str(e.get("beat") or "setup"), "visual": "", "learn": "the missing set-up"})
            elif e["op"] == "replace" and text and (i in wrong or _keeps_image(ln.get("text"), text)):
                keep = dict(keep, text=text, **({"facts": [str(f) for f in e["facts"]]} if e.get("facts") else {}))
            elif e["op"] == "delete" and str(ln.get("beat")) not in PRUNE_KEEP_BEATS and i > 1:
                keep = None
        if keep is not None:
            out.append(keep)
    fixed = _clean(dict(draft, lines=out))
    words = len(text_of(fixed, fixed["hooks"][0] if fixed.get("hooks") else None).split())
    if not WORDS_MIN <= words <= WORDS_MAX:
        fixed = _kept_story(fixed, _fit_length(fixed, words))
    if _story_hard(fixed) > _story_hard(draft):
        return None                                    # the repair broke something the code can see
    named = {int(m) for p in problems for m in re.findall(r"^narrative: (?:unexplained number: )?line (\d+)", p)}
    touched = {int(e["line"]) for e in edits} | {int(e["line"]) + 1 for e in edits}
    fixed["story_fix"] = {"problems": [p[:200] for p in problems[:5]], "edits": len(edits),
                          "missed_lines": sorted(named - touched)}
    return fixed


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


def _story_outcome(video_id, worked):
    try:
        from studio import storymemory
        storymemory.outcome(video_id, worked)
    except Exception:
        pass


def _story_memory(topic, video_id, row, rev):
    """Story failures go to the narrative_failures table (exact line, what was missing, root cause, prevention
    rule); a pass marks this Short's earlier failures as fixed. Never stops a script."""
    try:
        from studio import storymemory
        nar = rev.get("narrative") or {}
        if rev["ok"]:
            storymemory.outcome(video_id, True)
        elif nar.get("issues"):
            storymemory.record(nar["issues"], topic, video_id, row.get("version") if hasattr(row, "get") else row["version"])
    except Exception as e:
        print(f"(couldn't save the story lesson: {str(e)[:120]})")


def _confidence(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


def _record_claims(topic, video_id, rev, lines=None):
    """One sources row per checked claim: the line, its verdict, the fact's URL, the source's own words that
    settle it, the checker's confidence and the source's date (as far as the checker could see them)."""
    facts = _facts(topic)
    now = time.time()
    for c in rev["claims"]:
        if c.get("verdict") not in ("supported", "unsupported", "wrong"):
            continue
        m = re.match(r"F(\d+)", str(c.get("fact") or ""))
        src = facts[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(facts) else {}
        n = c.get("line")
        text = ""
        if lines and isinstance(n, int) and 0 < n <= len(lines):
            text = str((lines[n - 1] or {}).get("text") or "")[:400]
        date = str(c.get("source_date") or "").strip()
        db.insert("INSERT INTO sources(video_id, topic_id, claim, url, title, retrieved, verdict, note, domain, tier, http_status, "
                  "line_text, passage, confidence, source_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  video_id, topic["id"], f"line {n}", src.get("url"), src.get("title"), now, c["verdict"],
                  str(c.get("note", ""))[:400], src.get("domain"), src.get("tier"), src.get("http_status"), text,
                  str(c.get("quote") or "")[:300] or None, _confidence(c.get("confidence")),
                  date if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", date) else None)


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
    for c in ck.get("claims") or []:
        extra = (f' Source says: "{c["quote"]}"' if c.get("quote") else "") + (
            f" (confidence {_confidence(c['confidence']):.0%})" if _confidence(c.get("confidence")) is not None else "")
        lines.append(f"- line {c.get('line')}: **{c.get('verdict')}** {c.get('note', '')}{extra}")
    lines += ["", "## Title ideas", ""] + [f"- {t}" for t in st.get("titles") or []]
    lines += ["", "## Other hooks", ""] + [f"- {h}" for h in json.loads(row["hooks"]) if h != row["hook"]]
    nar = ck.get("narrative")
    if nar:
        from studio import narrative
        lines += ["", "## Story check", "", narrative.summary(nar) + "."]
        m = st.get("model") or {}
        if m:
            lines += [f"- Question: {m.get('question', '')}", f"- What happens: {m.get('premise', '')}",
                      f"- Formula: `{(m.get('formula') or {}).get('expr', '') if isinstance(m.get('formula'), dict) else m.get('formula')}`",
                      f"- Answer: {m.get('answer', '')}"]
        lines += [f"- {a}" for a in nar.get("advice") or []]
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
