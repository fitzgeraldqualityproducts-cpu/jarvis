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
    got = hard(FIXED, dict(GOOD_STORY, continuity=[{"line": 8, "missing": "what 'Gallivan number' means"}],
                           model_valid=False, model_problem="uses a full circle where the bend is a half circle"))
    assert any("line 8 leans on something never established" in h for h in got)      # payoff gap stops
    assert any("physics model is wrong" in h and "half circle" in h for h in got)


def test_small_gaps_warn_big_gaps_stop():
    """Connor's rule: 1-2 small gaps warn; 3+ gaps, or a gap in the opening or payoff, stop."""
    from studio import narrative

    def ev(*lines):
        return narrative.evaluate(FIXED, dict(GOOD_STORY, continuity=[{"line": n, "missing": f"thing {n}"}
                                                                      for n in lines]))
    r = ev(6, 7)                                     # two gaps in the maths steps: warnings only
    assert r["hard"] == [] and sum("leans on" in a for a in r["advice"]) == 2
    assert len([h for h in ev(3, 6, 7)["hard"] if "leans on" in h]) == 3     # three gaps: all stop
    assert any("line 2" in h for h in ev(2)["hard"])                         # the opening
    assert any("line 8" in h for h in ev(8)["hard"])                         # the payoff
    assert ev(9)["hard"] == []                                               # the loop line may end open
    assert ev(4, 6, 9)["hard"] == []                 # the loop line doesn't count toward three


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


def test_derived_si_units_known():
    from studio import narrative
    m = {"question": "q", "premise": "p", "answer": "a",
         "quantities": [{"name": "F", "means": "thrust", "value": 2279, "unit": "kN", "from": "F1"},
                        {"name": "v", "means": "exhaust speed", "value": 4436, "unit": "m/s", "from": "F2"}],
         "formula": {"expr": "F/v"}, "result": {"value": 514, "unit": "kg/s"}}
    hard = [p for p in narrative.model_check(m, [{"text": "2,279 kN of thrust at 4,436 m/s"}]) if p["hard"]]
    assert hard == []


def test_story_repair_allowed_with_length_problem():
    from studio import script
    hard = ["107 words; it must be 60-105", "narrative: unexplained number: line 5 says 452.3 (fix: say it first)"]
    assert script.STORY_LINE.match(hard[1])
    assert script._story_repairable(hard)
    assert not script._story_repairable(["107 words; it must be 60-105"])


def test_stopped_story_keeps_real_grade():
    from studio import narrative
    nar = {"hard": ["narrative: x"], "score": 49.0, "grade": 82.5}
    assert "graded 82.5" in narrative.summary(nar)


def test_moon_walk_false_alarms():
    """Test 27: pi typed in as 3.14159, '7' as days in a week, and a sourced circumference."""
    d = {"hooks": ["Could you walk around the Moon?"],
         "lines": [L("Could you walk around the Moon?", "hook"),
                   L("The Moon's radius is 1,737.4 km, and a circle's circumference is 2 times pi times the radius.",
                     "setup"),
                   L("So the Moon is 10,916 km around.", "math"),
                   L("At 5 km an hour for 4 hours a day, that's 546 days, or 78 weeks.", "math"),
                   L("So yes: about a year and a half of walking.", "payoff"), L("Which way would you go?", "loop")],
         "calcs": [{"expr": "2*3.14159265*1737.4", "result": 10916, "unit": "km"},
                   {"expr": "10916/(5*4)", "result": 546, "unit": "days"},
                   {"expr": "546/7", "result": 78, "unit": "weeks"}]}
    assert not [h for h in hard(d) if "unexplained" in h or "before the viewer" in h]
    d["lines"][1] = L("The Moon's radius is 1,737.4 km.", "setup")          # pi never said: still stopped
    assert any("pi" in h for h in hard(d))
    # a circumference the sources give needn't be derived on screen
    assert not [h for h in hard(d, facts=[{"claim": "The Moon's circumference is 10,916 km."}]) if "line 3" in h]


def test_astronomy_units_and_formula_with_left_side():
    """Test 29 (supernova brightness): light-years/parsecs unknown, 'm = ...' formula couldn't re-run,
    and a negative magnitude said aloud counted as never told."""
    from studio import narrative
    assert narrative.parse_unit("light-years")[0] == {"L": 1}
    assert narrative.parse_unit("light-years per parsec")[0] == {}
    assert narrative.parse_unit("AU")[0] == {"L": 1}
    m = {"question": "q", "premise": "p", "answer": "a",
         "quantities": [{"name": "M", "means": "absolute magnitude", "value": -19.4, "unit": "", "from": "F1"},
                        {"name": "d_pc", "means": "distance", "value": 15e6, "unit": "pc", "from": "F2"},
                        {"name": "d0", "means": "reference distance", "value": 10, "unit": "pc", "from": "F3"}],
         "formula": {"expr": "m = M + 5*log10(d_pc/d0)"}, "result": {"value": 11.5, "unit": ""}}
    lines = [{"text": "Its absolute magnitude is minus 19.4, seen from 15 million parsecs instead of 10."}]
    assert [p for p in narrative.model_check(m, lines) if p["hard"] or "re-run" in p["text"]] == []
    m["result"]["value"] = 16.5
    assert any(p["kind"] == "model_result" for p in narrative.model_check(m, lines))


def test_short_symbol_in_formula_is_bound_or_a_note():
    """Test 30 (noise cancelling): 'f' in lag/(1000/f) meant the sound frequency quantity; an unknown
    symbol is never a stop on its own (the calculations are re-run separately)."""
    from studio import narrative
    m = {"question": "q", "premise": "p", "answer": "a",
         "quantities": [{"name": "sound_frequency", "means": "pitch of the hum", "value": 1000, "unit": "Hz",
                         "from": "F1"},
                        {"name": "lag", "means": "processing delay", "value": 0.5, "unit": "ms", "from": "F2"}],
         "formula": {"expr": "lag*f"}, "result": {"value": 0.5, "unit": ""}}
    lines = [{"text": "A 1,000 hertz hum, and a delay of 0.5 milliseconds."}]
    probs = narrative.model_check(m, lines)
    assert not [p for p in probs if p["hard"]], probs
    m["formula"] = {"expr": "lag*zeta"}
    probs = narrative.model_check(m, lines)
    assert not [p for p in probs if p["hard"]] and any("re-run" in p["text"] for p in probs)


def test_story_repair_keeps_the_image(studio_home, monkeypatch):  # noqa: F811
    """Test 31: the repair swapped the image line for a bare number and originality fell 6 -> 5. A rewording
    that drops most of a line's own words is ignored; one that adds the value into the picture is kept."""
    from studio import claude, db, script, topics
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Paper folding", "fold paper", "Run It Yourself", "question", 70, "idea", "fold limit")
    old4, old6 = FIXED["lines"][3]["text"], FIXED["lines"][5]["text"]
    monkeypatch.setattr(claude, "RUNNER", lambda prompt, timeout, web: json.dumps({"edits": [
        {"op": "replace", "line": 4, "text": "Divide 2,798,250 by 6."},
        {"op": "replace", "line": 6, "text": old6.rstrip(".") + ", about 12 folds."}]}))
    probs = ["narrative: line 4 leans on something never established (x)",
             "narrative: line 6 leans on something never established (y)"]
    fixed = script._fix_story(dict(FIXED), topics.topic(tid), probs)
    texts = [ln["text"] for ln in fixed["lines"]]
    assert texts[3] == old4 and texts[5].endswith("about 12 folds.")
    assert script._keeps_image("One more floor: the Sun's center.",
                               "One more floor: the Sun's center, about 15.7 million kelvin.")
    assert not script._keeps_image("One more floor: the Sun's center.",
                                   "The Sun's core is about 15,710,000 kelvin; divide by 5,772.")


def test_formula_for_another_quantity_than_the_answer_is_not_a_unit_stop():
    """Test 32: 'mu = g*r/v**2' gives a grip number, while the model's answer is a speed."""
    from studio import narrative
    m = {"question": "q", "premise": "p", "answer": "a",
         "quantities": [{"name": "g", "means": "Moon gravity", "value": 1.62, "unit": "m/s^2", "from": "F1"},
                        {"name": "r", "means": "radius", "value": 4.7, "unit": "m", "from": "F2"},
                        {"name": "v", "means": "running speed", "value": 5.4, "unit": "m/s", "from": "F3"}],
         "formula": {"expr": "mu = g*r/v**2"}, "result": {"value": 2.8, "unit": "m/s"}}
    lines = [{"text": "Gravity 1.62 meters per second squared, a 4.7-meter radius, running at 5.4 meters per second."}]
    probs = narrative.model_check(m, lines)
    assert not [p for p in probs if p["hard"]] and any(p["kind"] == "units" for p in probs)
    m["formula"] = {"expr": "g*r/v**2"}                    # no left side: the answer is what it gives, so it stops
    assert any(p["hard"] and p["kind"] == "units" for p in narrative.model_check(m, lines))


def test_hook_may_tease_a_number_and_open_rows_are_counted(studio_home):  # noqa: F811
    """Test 33: the hook teased 23,000 km, said in line 2 (a note, not a stop); the story counter left 11 rows
    unexplained (still being worked on)."""
    from studio import db, narrative, storymemory
    model = {"quantities": [{"name": "gap", "means": "Moon's extra distance", "value": 23000, "unit": "km",
                             "from": "F1"}]}
    lines = [L("When does the last total eclipse happen?", "hook", uses=["gap"]),
             L("The Moon must drift 23,000 km further out.", "setup"), L("Then never again.", "loop")]
    probs = narrative.storyboard_check(lines, model)
    assert probs and not [p for p in probs if p["hard"]]
    lines[0] = L("When does the last total eclipse happen?", "setup", uses=["gap"])
    lines[1], lines[2] = L("Some other line.", "setup"), L("Not yet.", "setup")
    lines.append(L("The Moon must drift 23,000 km further out.", "math"))
    assert [p for p in narrative.storyboard_check(lines, model) if p["hard"]]      # too late beyond line 3
    db.conn()
    storymemory.record([{"hard": True, "kind": "continuity", "line": 2, "text": "x"}], {"id": 1, "title": "t"}, 9, 1)
    assert "1 still being worked on" in storymemory.summary()


def test_out_of_claude_during_a_script_puts_the_idea_back(studio_home, monkeypatch):  # noqa: F811
    from studio import __main__ as cli, db, jobs, script, topics
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Eclipses", "eclipse", "Run It Yourself", "question", 70, "idea", "last eclipse")

    def out(*a, **k):
        raise jobs.QuotaWait("the Claude plan's usage limit")
    monkeypatch.setattr(script, "make", out)
    assert cli._script(["--topic", str(tid), "--no-voice"]) == 1
    assert topics.topic(tid)["status"] == "idea"
    v = db.one("SELECT * FROM videos ORDER BY id DESC LIMIT 1")
    assert v["state"] == "failed" and v["error_kind"] == "quota"


def test_a_value_heard_too_late_is_fixed_by_moving_its_line_up(studio_home, monkeypatch):  # noqa: F811
    """Test 34b: '25% reflectivity' was used two lines before the line that says it; the repair now moves that
    line up for free (no Claude call) when that clears the code's own stops."""
    from studio import claude, script
    model = {"question": "q", "premise": "p", "answer": "a",
             "quantities": [{"name": "albedo", "means": "reflectivity", "value": 25, "unit": "%", "from": "F1"}]}
    d = {"hooks": ["How big is it?"], "hook": "How big is it?", "model": model, "calcs": [],
         "lines": [L("How big is it?", "hook"), L("We only see its glow.", "setup"),
                   L("Bright means big, if it's dark rock.", "setup", uses=["albedo"]),
                   L("A dull surface hides a giant.", "setup", uses=["albedo"]),
                   L("Assume it reflects 25 percent of sunlight.", "setup"),
                   L("So it's somewhere between 500 and 1,000 km.", "payoff"), L("Which is it?", "loop")]}
    probs = script.narrative_hard(d)
    assert probs and all(script.TOO_LATE.match(p) for p in probs)
    monkeypatch.setattr(claude, "RUNNER", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    fixed = script._fix_story(d, {"id": 1, "title": "t"}, probs)
    assert fixed["story_fix"]["moved"] and script.narrative_hard(fixed) == []
    assert [ln["text"] for ln in fixed["lines"]][2] == "Assume it reflects 25 percent of sunlight."


MOON_MODEL = {"question": "q", "premise": "p", "answer": "a", "quantities": [],
              "background": [{"key": "one_face", "fact": "the Moon always shows Earth the same face"},
                             {"key": "half_lit", "fact": "the Sun always lights half the Moon"},
                             {"key": "spare", "fact": "an unused fact"}]}


def test_background_facts_must_be_told_before_they_are_needed():
    """Connor (2026-11-27): fix the writer so future Shorts don't leave out background facts (test 37: 'the Moon
    always shows one face' and 'the Sun lights half the Moon' were never said)."""
    from studio import narrative
    lines = [L("Does the far side ever get sun?", "hook"),
             L("The Moon always shows us the same face.", "setup", tells=["one_face"]),
             L("So the far side is the half we never see.", "setup", needs=["one_face"]),
             L("At New Moon that hidden half faces the Sun.", "math", needs=["half_lit"]),
             L("The Sun always lights half the Moon.", "setup", tells=["half_lit"]),
             L("So yes: two weeks of sun.", "payoff", needs=["one_face", "half_lit"]), L("Which half?", "loop")]
    got = narrative.background_check(lines, MOON_MODEL)
    hard = [p for p in got if p["hard"]]
    assert len(hard) == 1 and "line 4" in hard[0]["text"] and "only hears it in line 5" in hard[0]["text"]
    assert any(not p["hard"] and "unused fact" in p["text"] for p in got)
    lines[3], lines[4] = lines[4], lines[3]                  # told first: no stop
    assert not [p for p in narrative.background_check(lines, MOON_MODEL) if p["hard"]]
    lines[3] = L("Sunlight matters.", "setup")                 # never told at all
    assert any("no line says it" in p["text"] for p in narrative.background_check(lines, MOON_MODEL) if p["hard"])


def test_background_told_late_is_moved_up_for_free(studio_home, monkeypatch):  # noqa: F811
    from studio import claude, script
    lines = [L("Does the far side ever get sun?", "hook"),
             L("The Moon always shows us the same face.", "setup", tells=["one_face"]),
             L("At New Moon the far side faces the Sun.", "math", needs=["one_face", "half_lit"]),
             L("The Sun always lights half the Moon.", "setup", tells=["half_lit"]),
             L("So yes: two weeks of sun.", "payoff"), L("Which half?", "loop")]
    d = {"hooks": ["Does the far side ever get sun?"], "hook": "Does the far side ever get sun?",
         "model": MOON_MODEL, "calcs": [], "lines": lines}
    probs = script.narrative_hard(d)
    assert probs and all(script.TOO_LATE.match(p) for p in probs)
    monkeypatch.setattr(claude, "RUNNER", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    fixed = script._fix_story(d, {"id": 1, "title": "t"}, probs)
    assert fixed and script.narrative_hard(fixed) == []


def test_writer_is_asked_for_background_and_pick_prefers_fewest_gaps():
    from studio import script
    assert "background" in script.STORY_FIRST and '"needs"' in script.STORY_FIRST
    assert '"background"' in script.MODEL_SHAPE and "as the calculation" in script.STORY_FIRST


def test_pick_prefers_the_draft_with_fewest_problems_when_none_is_clean(monkeypatch):
    from studio import script
    a = {"hooks": ["A?"], "lines": [L("A?", "hook"), L("a", "setup")], "angle": "a"}
    b = {"hooks": ["B?"], "lines": [L("B?", "hook"), L("b", "setup")], "angle": "b"}
    monkeypatch.setattr(script, "_screen", lambda d: ["x", "y"] if d["hooks"] == ["A?"] else ["x"])
    monkeypatch.setattr(script, "_learn_from_drafts", lambda *a, **k: None)
    got = script.pick_draft({"drafts": [a, b]}, {"id": 1, "title": "t"})
    assert got["hooks"] == ["B?"] and got["picked"]["passed_screen"] == 0


def test_named_physical_constants_count_as_told():
    """Test 38 (atom-sized black hole): G and c were in the formula; saying 'the gravitational constant' and
    'light's speed' is enough, the viewer doesn't need their digits. Never naming them still stops."""
    from studio import narrative
    m = {"question": "q", "premise": "p", "answer": "a",
         "quantities": [{"name": "G", "means": "gravitational constant", "value": 6.674e-11, "unit": "m^3/(kg*s^2)",
                         "from": "standard"},
                        {"name": "c", "means": "speed of light", "value": 3e8, "unit": "m/s", "from": "standard"},
                        {"name": "M", "means": "mass", "value": 1e12, "unit": "kg", "from": "F1"}],
         "formula": {"expr": "2*G*M/c**2"}, "result": {"value": 1.48e-15, "unit": "m"}}
    said = [{"text": "Squeeze a trillion kilograms, 1e12, using the gravitational constant and light's speed."}]
    assert not [p for p in narrative.model_check(m, said) if p["kind"] == "assumption"]
    quiet = [{"text": "Squeeze a trillion kilograms, 1e12."}]
    assert len([p for p in narrative.model_check(m, quiet) if p["kind"] == "assumption" and p["hard"]]) == 2
    d = {"hooks": ["How small?"], "lines": [L("How small?", "hook"),
                                            L("Take 1e12 kg and the gravitational constant and light's speed.", "setup"),
                                            L("Two times G times mass over c squared: 1.48e-15 meters.", "math"),
                                            L("Smaller than an atom.", "payoff"), L("Smaller?", "loop")],
         "calcs": [{"expr": "2*6.674e-11*1e12/299792458**2", "result": 1.48e-15, "unit": "m"}]}
    assert not [h for h in hard(d) if "unexplained" in h]


def test_rewrite_keeps_the_line_plan_and_no_plan_means_no_background_notes():
    """Test 39: the passed rewrite had tells/needs missing on every line, so 'no line says it' notes were false."""
    from studio import narrative, script
    old = {"lines": [L("Lie back. Every star is a dot on a ball around you.", "setup", tells=["all_around"]),
                     L("The ground hides the lower half, so divide 2,800 by 2.", "math", tells=["half_sky"],
                       needs=["all_around"])]}
    new = {"lines": [{"text": "Lie back: every star is a dot on a big ball around you.", "beat": "setup"},
                     {"text": "Something completely different here now.", "beat": "math"}]}
    got = script._carry_plan(new, old)
    assert got["lines"][0]["tells"] == ["all_around"] and "tells" not in got["lines"][1]
    model = {"background": [{"key": "all_around", "fact": "stars surround you"}]}
    assert narrative.background_check(new["lines"], model) == []
