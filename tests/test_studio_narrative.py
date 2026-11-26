"""Video 20 audit: the Narrative Integrity Engine catches stories that don't make sense (deliberately broken
scripts), lets good creative ones through, remembers specific failures, and is wired into the script and QA gates."""
import json

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def L(text, beat="setup", **more):
    return dict(text=text, beat=beat, **more)


# Short 20 as it was rendered (reconstructed from its captions): the numbers re-run fine, the story doesn't.
VIDEO_20 = {
    "hooks": ["1,219 meters of tissue, folded twelve times. Where does it go?"],
    "lines": [L("1,219 meters of tissue, folded twelve times. Where does it go?", "hook"),
              L("Lay it along your street: 1,219 meters."),
              L("Every fold bends the paper around a rounded edge,"),
              L("and those curves eat paper. How far?", "rehook"),
              L("Say your tissue is 0.1 millimeters thick, my pick.", facts=["example"]),
              L("Fold one loses 0.3 millimeters.", "math"),
              L("Fold twelve loses 2,798,250 times that:", "math"),
              L("pi times 0.1 millimeters times 2,798,250.", "math"),
              L("That's 879 meters, 72 percent of your street, leaving 340 meters.", "payoff"),
              L("Real thickness changes the number.", "loop")],
    "calcs": [{"expr": "pi*0.1", "result": 0.314, "unit": "mm"},
              {"expr": "(2**12+4)*(2**12-1)/6", "result": 2798250, "unit": ""},
              {"expr": "pi*0.0001*2798250", "result": 879, "unit": "m"},
              {"expr": "879/1219*100", "result": 72, "unit": "%"},
              {"expr": "1219-879", "result": 340, "unit": "m"}]}

# The same idea told so it makes sense: what is folded, why each fold costs more, where the multiplier comes
# from, and an answer to the opening question.
PAPER_FOLD_MODEL = {
    "question": "Why did folding one strip of tissue twelve times take 1,219 meters of it?",
    "premise": "a long strip is folded in half again and again in one direction; every fold bends all the layers "
               "around a half circle, and the outer layers go round a wider curve",
    "assumptions": [{"what": "tissue 0.1 mm thick", "why": "a round example value, said as our pick"},
                    {"what": "each fold is a half circle", "why": "the paper wraps around itself"}],
    "quantities": [{"name": "n", "means": "number of folds", "value": 12, "unit": "count", "from": "F1"},
                   {"name": "t", "means": "tissue thickness", "value": 0.1, "unit": "mm", "from": "example"},
                   {"name": "roll", "means": "length of the roll she used", "value": 1219, "unit": "m", "from": "F1"}],
    "formula": {"expr": "pi*t*(2**n+4)*(2**n-1)/6", "means": "the least length that can be folded n times",
                "applies_because": "it adds up every half-circle bend of every layer"},
    "result": {"value": 879, "unit": "m"},
    "answer": "twelve folds need at least 879 m of 0.1 mm tissue, so her 1,219 m roll was just enough"}
FIXED = {
    "hooks": ["Why did twelve folds need 1,219 meters of tissue?"],
    "model": PAPER_FOLD_MODEL,
    "lines": [L("Why did twelve folds need 1,219 meters of tissue?", "hook", uses=["roll", "n"],
                learn="the question"),
              L("In 2002 a student folded one strip of tissue in half, twelve times over.", facts=["F1"],
                learn="what is folded and what a fold means", uses=["n"]),
              L("Each fold doubles the layers: twelve folds make 4,096.", "math", learn="layers double each fold",
                uses=["n"]),
              L("Every fold bends all of them around a half circle, outer layers on wider curves.",
                learn="why each fold costs more paper"),
              L("So each fold eats more tissue than the last.", "rehook", learn="the stakes rise"),
              L("Say the tissue is 0.1 millimeters thick, my pick.", "math", facts=["example"],
                learn="the thickness we assume", uses=["t"]),
              L("Add every curve: 4,096 plus 4, times 4,096 minus 1, over 6.", "math",
                learn="where the multiplier comes from", uses=["n"]),
              L("That's 2,798,250, times pi and 0.1 millimeters: at least 879 meters.", "payoff",
                learn="the least tissue twelve folds need", uses=["t"]),
              L("Her 1,219-meter roll was just enough. So why", "loop", facts=["F1"],
                learn="the roll had enough to spare", uses=["roll"])],
    "calcs": [{"expr": "2**12", "result": 4096, "unit": "layers"},
              {"expr": "(2**12+4)*(2**12-1)/6", "result": 2798250, "unit": ""},
              {"expr": "pi*0.0001*2798250", "result": 879, "unit": "m"}]}
GOOD_STORY = {"premise_clear": 9, "model_valid": True, "continuity": [], "conclusion_follows": True,
              "stranger": {"1": "why twelve folds needed so much tissue", "2": "what a fold does to the layers",
                           "3": "add up every half-circle bend with the formula", "4": "the curves grow so fast",
                           "5": "folding costs paper that grows four times each fold"}}


def hard(d, story=None, facts=None):
    from studio import narrative
    return narrative.evaluate(d, story, facts)["hard"]


def test_video_20_is_rejected_for_its_unexplained_multiplier():
    from studio import narrative
    r = narrative.evaluate(VIDEO_20)
    assert any("unexplained number: line 7 says 2,798,250" in h and "4, 6" in h for h in r["hard"])
    assert r["score"] < 50
    # what the checker said about Short 20's story adds the physics and logic failures on top
    r = narrative.evaluate(VIDEO_20, {"premise_clear": 3, "model_valid": False,
                                      "model_problem": "the formula is the least length needed to fold 12 times, "
                                                       "not paper lost by fold twelve",
                                      "continuity": [{"line": 9, "missing": "what the 340 meters is left of"}],
                                      "conclusion_follows": False,
                                      "stranger": {"1": "where tissue goes", "2": "", "3": "", "4": "", "5": ""}})
    kinds = {i["kind"] for i in r["issues"] if i["hard"]}
    assert {"unexplained", "premise", "model", "continuity", "conclusion", "stranger"} <= kinds


def test_the_fixed_paper_fold_story_passes():
    from studio import narrative
    r = narrative.evaluate(FIXED, GOOD_STORY,
                           facts=[{"claim": "In 2002 Britney Gallivan folded a 1,219 m (4,000 ft) strip of tissue "
                                            "12 times"}])
    assert r["hard"] == [] and r["score"] >= 90, r


def test_deliberate_failure_unexplained_multiplier():
    d = {"hooks": ["How far can sound go in 3 seconds?"],
         "lines": [L("How far can sound go in 3 seconds?", "hook"), L("Sound moves fast through air.", "setup"),
                   L("Multiply 3 by 343 times 1.2.", "math"), L("That's about 1,235 meters.", "payoff"),
                   L("So, how far in 3 seconds?", "loop")],
         "calcs": [{"expr": "3*343*1.2", "result": 1234.8, "unit": "m"}]}
    assert hard(d) == []                       # every input is said before the result
    d["lines"][2] = L("Sound moves at 343 meters a second.", "math")
    assert any("1.2" in h and "unexplained" in h for h in hard(d))


def test_deliberate_failure_number_used_before_it_is_introduced():
    d = {"hooks": ["How deep is a 4 second drop?"],
         "lines": [L("How deep is a 4 second drop?", "hook"), L("That's about 78 meters deep.", "math"),
                   L("Gravity adds 9.8 meters a second, every second.", "math"),
                   L("Half of 9.8, times 4 squared, is 78.", "payoff"), L("Count to 4 next time.", "loop")],
         "calcs": [{"expr": "0.5*9.8*4**2", "result": 78.4, "unit": "m"}]}
    got = hard(d)
    assert any("line 2 says 78.4 before the viewer has heard 9.8" in h for h in got)


def test_deliberate_failure_unit_conversion():
    d = {"hooks": ["How thin is tissue?"],
         "lines": [L("How thin is tissue?", "hook"), L("Say it's 0.1 millimeters, or 0.001 meters.", "math"),
                   L("Stack ten sheets.", "setup"), L("That's a millimeter.", "payoff"), L("Thin.", "loop")],
         "calcs": [{"expr": "10*0.1", "result": 1, "unit": "mm"}]}
    assert any("unit slip in line 2" in h and "0.0001 meters" in h for h in hard(d))
    d["lines"][1] = L("Say it's 0.1 millimeters, or 0.0001 meters.", "math")
    assert not any("unit slip" in h for h in hard(d))


def test_deliberate_failure_formula_with_wrong_units_or_result():
    from studio import narrative
    model = {"question": "How fast?", "premise": "a car drives", "answer": "fast",
             "quantities": [{"name": "d", "means": "distance", "value": 100, "unit": "m", "from": "F1"},
                            {"name": "t", "means": "time", "value": 20, "unit": "s", "from": "F1"}],
             "formula": {"expr": "d*t"}, "result": {"value": 5, "unit": "m/s"}}
    lines = [L("A car covers 100 meters in 20 seconds.")]
    probs = narrative.model_check(model, lines)
    assert any(p["hard"] and p["kind"] == "units" for p in probs)          # distance x time isn't a speed
    model["formula"] = {"expr": "d/t"}
    assert not [p for p in narrative.model_check(model, lines) if p["hard"]]
    model["result"] = {"value": 50, "unit": "m/s"}
    assert any(p["kind"] == "model_result" for p in narrative.model_check(model, lines))
    model["result"] = {"value": 5, "unit": "m/s"}
    model["formula"] = {"expr": "d + t"}
    assert any("unlike things" in p["text"] for p in narrative.model_check(model, lines))


def test_deliberate_failure_assumption_never_told_and_one_number_two_meanings():
    from studio import narrative
    model = dict(PAPER_FOLD_MODEL, quantities=PAPER_FOLD_MODEL["quantities"] + [
        {"name": "street", "means": "length of your street", "value": 1219, "unit": "m", "from": "example"}])
    lines = [L(x["text"]) for x in VIDEO_20["lines"] if "0.1" not in x["text"]]
    probs = narrative.model_check(model, lines)
    assert any(p["kind"] == "assumption" and "tissue thickness" in p["text"] and p["hard"] for p in probs)
    assert any(p["kind"] == "two_meanings" and not p["hard"] for p in probs)


def test_deliberate_failure_conclusion_contradicts_premise_or_misses_the_question():
    got = hard(FIXED, dict(GOOD_STORY, conclusion_follows=False))
    assert any("conclusion doesn't follow" in h for h in got)


def test_deliberate_failure_undefined_term_and_correct_equation_wrong_model():
    got = hard(FIXED, dict(GOOD_STORY, continuity=[{"line": 7, "missing": "what 'Gallivan number' means"}],
                           model_valid=False, model_problem="uses a full circle where the bend is a half circle"))
    assert any("line 7 leans on something never established" in h for h in got)
    assert any("physics model is wrong" in h and "half circle" in h for h in got)
    # a gap on an ordinary setup line is a note, not a stop
    from studio import narrative
    r = narrative.evaluate(FIXED, dict(GOOD_STORY, continuity=[{"line": 2, "missing": "who the student was"}]))
    assert r["hard"] == [] and any("line 2" in a for a in r["advice"])


def test_pi_needs_a_circle_first_and_storyboard_order():
    d = {"hooks": ["How much paper does one fold use?"],
         "lines": [L("How much paper does one fold use?", "hook"), L("Paper is 0.1 millimeters thick.", "setup"),
                   L("Pi times 0.1 is about 0.31 millimeters.", "math"), L("Tiny.", "payoff"), L("Or is it?", "loop")],
         "calcs": [{"expr": "pi*0.1", "result": 0.314, "unit": "mm"}]}
    assert any("no circle or curve" in h for h in hard(d))
    d["lines"][1] = L("Paper 0.1 millimeters thick bends around a half circle.", "setup")
    assert hard(d) == []
    from studio import narrative
    bad = dict(FIXED, lines=[dict(ln) for ln in FIXED["lines"]])
    bad["lines"][1]["uses"] = ["t"]                   # line 2 leans on the thickness, said only in line 6
    probs = narrative.storyboard_check(bad["lines"], FIXED["model"])
    assert any(p["kind"] == "too_late" and "line 6" in p["text"] for p in probs)


def test_origins_and_confusion_rules():
    from studio import narrative
    d = {"hooks": ["How hot?"], "lines": [L("How hot?", "hook"), L("The core is 5,500 degrees.", "setup", facts=["F1"]),
                                           L("That's 37 times your oven.", "payoff")],
         "calcs": []}
    assert any("says 37" in h for h in hard(d, facts=[{"claim": "Earth's core is about 5,500 C"}]))
    c = narrative.confusion([L("It is hot."), L("A 2,000 km and 6,371 km sphere."), L("Times that, again.")])
    kinds = {x["kind"] for x in c}
    assert {"pronoun", "ambiguous"} <= kinds


def test_visual_purpose_score():
    from studio import narrative
    lines = [L("Hook", "hook"), L("4,096 plus 4, times 4,095, over 6", "math"), L("That's 879 meters, leaving 340.", "payoff")]
    v = narrative.visual_purpose([{"type": "headline"}, {"type": "headline"}, {"type": "number"}], lines)
    assert v["score"] == 0.5 and any("scene 2" in p for p in v["problems"]) and any("leftover" in p for p in v["problems"])
    v = narrative.visual_purpose([{"type": "headline"}, {"type": "equation"}, {"type": "compare"}], lines)
    assert v["score"] == 1.0 and not v["problems"]


def test_narrative_failures_are_remembered_specifically(studio_home):  # noqa: F811
    from studio import db, lessons, narrative, storymemory
    db.conn()
    r = narrative.evaluate(VIDEO_20)
    topic = {"id": 5, "title": "Paper folding"}
    assert storymemory.record(r["issues"], topic, 22, 1) == 1
    row = db.one("SELECT * FROM narrative_failures")
    assert row["video_id"] == 22 and row["line_no"] == 7 and "2,798,250" in row["sentence"]
    assert row["math_issue"].startswith("(2**12+4)") and "4, 6" in row["missing"]
    assert "input" in row["fix"] and row["worked"] is None
    adv = lessons.advice("script")
    assert "Story mistakes this channel made before" in adv and "Fold twelve loses 2,798,250" in adv
    storymemory.outcome(22, False)
    assert "1 rewrite(s) still failed this" in storymemory.advice() and "MUST:" in storymemory.advice()
    assert lessons.categorize(r["hard"][0]) == "story_math"
    lessons.record("script", r["hard"], topic, 22)
    assert "couldn't trace" in lessons.advice("script")


def test_off_and_advise_modes(studio_home):  # noqa: F811
    from studio import config, narrative
    assert narrative.mode() == "block"
    config.set_values(narrative_check="advise")
    assert narrative.mode() == "advise"
    config.set_values(narrative_check=False)
    assert narrative.mode() == "off"


def test_screen_drops_a_draft_with_an_unexplained_multiplier():
    from studio import script
    assert any(p.startswith("narrative: unexplained number") for p in script._screen(script._clean(VIDEO_20)))


def test_review_blocks_on_the_checkers_story_answer_and_saves_it(studio_home, monkeypatch):  # noqa: F811
    from studio import claude, db, script, storymemory, topics
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Paper folding", "fold paper", "Run It Yourself", "question", 70, "idea", "fold limit")
    for url, claim in (("https://www.example.edu/fold", "In 2002 Britney Gallivan folded a 1,219 m strip of tissue 12 "
                                                        "times"), ("https://www.example.org/f", "Gallivan's formula")):
        db.insert("INSERT INTO sources(topic_id, claim, url, verdict, tier, http_status, domain) VALUES(?,?,?,?,?,?,?)",
                  tid, claim, url, "reachable", 3, 200, topics.domain(url))
    asked = []

    def runner(prompt, timeout, web):
        asked.append(prompt)
        bad = len(asked) == 1
        return json.dumps({"claims": [{"line": 2, "verdict": "supported", "fact": "F1", "quote": "folded", "confidence": 0.9}],
                           "hooks": {"0": {"curiosity": 9, "clarity": 9, "specificity": 9, "honesty": 9, "speed": 9}},
                           "loop": 8, "rehook": 8, "question": "why", "answered": 8, "originality": 8,
                           "inauthentic_risk": "low", "problems": [],
                           "story": dict(GOOD_STORY, model_valid=not bad, model_problem="minimum length, not loss")})
    monkeypatch.setattr(claude, "RUNNER", runner)
    t = topics.topic(tid)
    rev = script.review(FIXED, t, 9)
    assert "STORY CHECK" in asked[0] and '"formula"' in asked[0]          # the model went to the checker
    assert not rev["ok"] and any("physics model is wrong" in p for p in rev["hard_problems"])
    rev = script.review(FIXED, t, 9)
    assert rev["ok"], rev["hard_problems"]
    assert rev["ok"] and rev["narrative"]["score"] >= 90
    vid = db.insert("INSERT INTO videos(topic_id, state, created) VALUES(?,?,?)", tid, "script", 0) \
        if "created" in [r["name"] for r in db.q("PRAGMA table_info(videos)")] else None
    assert storymemory.summary()                                          # table exists after migrations
    assert vid is None or vid > 0


def test_short_21_false_alarms_are_gone_and_fair_ones_stay():
    """Test Short 21 (transistors) was stopped partly on false alarms: count words read as unknown units, a
    formula of typed-in numbers judged as "a plain number, not nm", and a unit conversion treated as a value the
    viewer must hear."""
    from studio import narrative
    assert narrative.parse_unit("switches per mm^2") == ({"L": -2}, pytest.approx(1e6))
    assert narrative.parse_unit("squares") == ({}, 1.0)
    model = {"question": "How small is one transistor?", "premise": "208 billion switches share one chip",
             "answer": "about 88 nm on a side",
             "quantities": [{"name": "conv", "means": "square nanometres in a square millimetre", "value": 1e12,
                             "unit": "nm^2/mm^2", "from": "standard"},
                            {"name": "N", "means": "transistors on the chip", "value": 1.04e11, "unit": "switches",
                             "from": "F1"},
                            {"name": "A", "means": "chip area", "value": 800, "unit": "mm^2", "from": "F2"}],
             "formula": {"expr": "sqrt(1e12/(1.04e11/800))"}, "result": {"value": 88, "unit": "nm"}}
    lines = [L("104 billion switches on a chip of 800 square millimeters.")]
    probs = narrative.model_check(model, lines)
    assert not [p for p in probs if p["hard"]], probs
    assert not any("can't read" in p["text"] for p in probs)
    # the same formula from named quantities: units carried through, sqrt(mm^2 / count) is a length
    model["formula"] = {"expr": "sqrt(A/N)"}
    assert not [p for p in narrative.model_check(model, lines) if p["hard"]]
    model["result"] = {"value": 88, "unit": "s"}
    assert any(p["hard"] and p["kind"] == "units" for p in narrative.model_check(model, lines))
    # a real starting value the viewer never hears still stops it
    model["result"] = {"value": 88, "unit": "nm"}
    assert any(p["kind"] == "assumption" and "chip area" in p["text"]
               for p in narrative.model_check(model, [L("104 billion switches on one chip.")]))


def test_a_rewrite_is_told_how_to_fix_story_problems(studio_home, monkeypatch):  # noqa: F811
    from studio import claude, db, script, topics
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Paper folding", "fold paper", "Run It Yourself", "question", 70, "idea", "fold limit")
    db.insert("INSERT INTO sources(topic_id, claim, url, verdict, tier, http_status, domain) VALUES(?,?,?,?,?,?,?)",
              tid, "Gallivan folded 1,219 m 12 times", "https://www.example.edu/f", "reachable", 3, 200, "example.edu")
    asked = []
    monkeypatch.setattr(claude, "RUNNER", lambda prompt, timeout, web: asked.append(prompt) or json.dumps(FIXED))
    script.write(topics.topic(tid), ["narrative: unexplained number: line 7 says 2,798,250"], dict(FIXED))
    assert "For each story problem" in asked[0] and "named quantity with its unit" in asked[0]


def test_the_loop_line_may_end_mid_thought_and_stops_say_how_to_fix():
    """Test 22: the last line was left unfinished for the seamless loop and the story check stopped it. The answer
    belongs in the payoff line; the loop line is free."""
    from studio import narrative
    r = narrative.evaluate(FIXED, dict(GOOD_STORY, continuity=[{"line": 9, "missing": "the sentence is unfinished"}]))
    assert r["hard"] == [] and any("line 9" in a for a in r["advice"])
    assert "PAYOFF line" in narrative.REVIEW_ASK and "unfinished on purpose" in narrative.REVIEW_ASK
    h = narrative.evaluate(VIDEO_20)["hard"][0]
    assert "(fix: before saying a calculated number" in h


def test_a_length_fix_that_breaks_the_story_is_not_used():
    from studio import script
    broken = dict(FIXED, lines=[ln for i, ln in enumerate(FIXED["lines"]) if i != 6])   # the derivation line cut
    assert script._kept_story(FIXED, broken) is FIXED
    assert script._kept_story(FIXED, None) is FIXED
    assert script._kept_story(VIDEO_20, FIXED) is FIXED


def test_a_question_line_is_never_unsupported():
    """Test 23 was stopped twice on "line 5 is unsupported" for a line that only asked a question."""
    from studio import script
    assert script.plain_question("So what does infrared actually do to your skin?")
    assert not script.plain_question("Why is the Sun's surface 5,500 C?")
    assert not script.plain_question("Infrared is heat. So why can't you see it?")
    assert not script.plain_question("Infrared carries half the Sun's energy.")
    d = dict(FIXED, lines=[dict(ln) for ln in FIXED["lines"]])
    d["lines"][4] = L("So why does each fold cost more?", "rehook")
    ans = {"claims": [{"line": 2, "verdict": "supported", "fact": "F1"},
                      {"line": 5, "verdict": "unsupported", "note": "a question, closer to a transition"}],
           "hooks": {"0": {"curiosity": 9, "clarity": 9, "specificity": 9, "honesty": 9, "speed": 9}},
           "loop": 8, "rehook": 8, "answered": 8, "originality": 8, "inauthentic_risk": "low", "problems": []}
    rev = script._judged(d, ans, None)
    assert not any("line 5" in p for p in rev["hard_problems"])
    d["lines"][4] = L("Infrared is half of sunlight.", "rehook")
    assert any("line 5 is unsupported" in p for p in script._judged(d, ans, None)["hard_problems"])


def test_a_story_gap_is_repaired_by_adding_a_set_up_line(studio_home, monkeypatch):  # noqa: F811
    """Tests 21-23: whole rewrites fixed none of 25 story stops. A gap tied to one line now gets a set-up line
    added before it, with every other line kept word for word."""
    from studio import claude, db, script, topics
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Paper folding", "fold paper", "Run It Yourself", "question", 70, "idea", "fold limit")
    db.insert("INSERT INTO sources(topic_id, claim, url, verdict, tier, http_status, domain) VALUES(?,?,?,?,?,?,?)",
              tid, "Gallivan folded 1,219 m 12 times", "https://www.example.edu/f", "reachable", 3, 200, "example.edu")
    asked = []

    def runner(prompt, timeout, web):
        asked.append(prompt)
        return json.dumps({"edits": [{"op": "insert_before", "line": 4, "text": "Picture each fold as a hairpin bend.",
                                      "facts": ["general"]},
                                     {"op": "delete", "line": 5}]})
    monkeypatch.setattr(claude, "RUNNER", runner)
    probs = ["narrative: line 4 leans on something never established (what a bend looks like)"]
    assert script.STORY_LINE.match(probs[0])
    fixed = script._fix_story(dict(FIXED), topics.topic(tid), probs)
    texts = [ln["text"] for ln in fixed["lines"]]
    assert texts[3] == "Picture each fold as a hairpin bend." and texts[4] == FIXED["lines"][3]["text"]
    assert FIXED["lines"][4]["text"] not in texts                       # the teaser line made room
    assert texts[:3] == [ln["text"] for ln in FIXED["lines"][:3]] and texts[-1] == FIXED["lines"][-1]["text"]
    assert "word for word" in asked[0] and "never a new number" in asked[0]


def test_loop_line_is_never_unsupported_and_repair_runs_before_the_repeat_stop(studio_home):  # noqa: F811
    """Test 24: the loop line ("...") was called unsupported, and the repeat stop fired before the story repair
    ever ran."""
    from studio import db, script
    d = dict(FIXED, lines=[dict(ln) for ln in FIXED["lines"]])
    ans = {"claims": [{"line": 2, "verdict": "supported", "fact": "F1"},
                      {"line": 9, "verdict": "unsupported", "note": "unfinished by design as a loop line"}],
           "hooks": {"0": {"curiosity": 9, "clarity": 9, "specificity": 9, "honesty": 9, "speed": 9}},
           "loop": 8, "rehook": 8, "answered": 8, "originality": 8, "inauthentic_risk": "low", "problems": []}
    d["lines"][8] = L("And that's why every fold", "loop")
    assert not any("line 9" in p for p in script._judged(d, ans, None)["hard_problems"])
    hard = ["line 8 is wrong: the parcel floats because it's warmer",
            "narrative: line 8 leans on something never established (why moist air is lighter)",
            "narrative: the conclusion doesn't follow from the explanation"]
    assert script._story_repairable(hard)
    assert not script._story_repairable(hard[:1])                        # nothing story-shaped: the usual paths
    assert not script._story_repairable(hard + ["originality 4/10: generic"])
    db.conn()
    assert not script._story_fix_tried(7)
    db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", 7, 1, "[]", "", "", json.dumps({"story_fix": {"edits": 1}}), "{}", "draft", 0)
    assert script._story_fix_tried(7)
