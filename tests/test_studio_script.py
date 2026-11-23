"""Phase 6: scripts, hooks, fact-check, calculations, narration and the production stages."""
import json
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401

BODY = [
    "Why do rockets throw away 88 percent of themselves?",
    "A rocket's fuel is most of its weight. The Saturn V weighed about 2,970 tonnes on the pad.",
    "Its first stage alone was about 2,290 tonnes, and almost all of that was propellant.",
    "Divide one by the other: 2290 over 2970 is about 0.77, so three quarters of the rocket burns in under three minutes.",
    "An empty tank is dead weight.",
    "So engineers drop the empty stage and the next one starts lighter, going faster on less fuel.",
    "Every rocket is really a stack of smaller rockets.",
]


class FakeClaude:
    def __init__(self):
        self.writes, self.reviews = 0, 0
        self.bad_first = False      # first review finds a wrong claim
        self.always_bad = False
        self.wrong_calc = False
        self.too_long = False       # the writer returns about 140 words
        self.fits = 0
        self.line_fixes = 0
        self.orig_first = None       # originality of the first review only
        self.bad_reviews = 0        # this many reviews fail before one passes
        self.orig = 8
        self.standard_only = False

    def __call__(self, prompt, timeout, web):
        if prompt.startswith("This Short script is"):
            assert not web and "Keep every number" in prompt
            self.fits += 1
            return json.dumps({"hooks": ["Why do rockets throw away 88 percent of themselves?"],
                               "lines": [{"text": t, "facts": ["F1"], "visual": "diagram"} for t in BODY],
                               "calcs": [{"expr": "2290/2970", "result": 0.77, "unit": "", "says": "share"}]})
        if prompt.startswith("A fact-checker flagged"):
            assert not web and "word for word" in prompt
            self.line_fixes += 1
            return json.dumps({"lines": [{"line": 2, "text": BODY[1], "delete": False}]})
        if prompt.startswith("Write a YouTube Short script"):
            assert not web and "FACTS YOU MAY USE" in prompt and "F1." in prompt
            self.writes += 1
            if self.writes > 1:
                assert "failed review" in prompt
            return json.dumps({
                "hooks": ["Why do rockets throw away 88 percent of themselves?",
                          "You won't believe what rockets do!!!",
                          "Rockets are amazing and today we will talk about them in a lot of detail okay",
                          "What if a rocket kept its empty tanks?", "2,970 tonnes, gone in minutes."],
                "lines": [{"text": t, "facts": ["F1"], "visual": "diagram"} for t in (BODY + BODY[1:4] if self.too_long else BODY)],
                "calcs": [{"expr": "2290/2970", "result": 0.5 if self.wrong_calc else 0.77, "unit": "", "says": "share"}],
                "titles": ["Why rockets throw parts away"]})
        assert prompt.startswith("You are a strict fact-checker") and web
        self.reviews += 1
        bad = self.always_bad or (self.bad_first and self.reviews == 1) or self.reviews <= self.bad_reviews
        if self.standard_only:
            return json.dumps({"claims": [{"line": 2, "verdict": "standard", "note": "Kepler's third law"}],
                               "hooks": {str(i): {"curiosity": 9, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8}
                                         for i in range(5)}, "originality": 8, "inauthentic_risk": "low", "problems": []})
        return json.dumps({
            "claims": [{"line": 2, "verdict": "wrong" if bad else "supported", "fact": "F1", "note": "NASA says 2,950" if bad else ""},
                       {"line": 3, "verdict": "supported", "fact": "F2", "note": ""}, {"line": 7, "verdict": "n/a"}],
            "hooks": {str(i): {"curiosity": 9 - i, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8} for i in range(5)},
            "originality": self.orig_first if (self.orig_first is not None and self.reviews == 1) else self.orig,
            "originality_fix": "the rocket-as-a-bottle idea is the usual one; compare it to a "
            "hiker dropping empty water bottles", "inauthentic_risk": "low", "problems": []})


@pytest.fixture
def fakes(monkeypatch):
    from studio import claude, db, topics
    fc = FakeClaude()
    monkeypatch.setattr(claude, "RUNNER", fc)
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {"positioning": "shows the math"}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, angle) VALUES(?,?,?,?,?,?,?)",
                    "Why Rockets Throw Parts Away", "away part rocket throw", "Inside the Machine", "question", 77.6,
                    "idea", "stage mass fractions")
    for url, claim in (("https://www.nasa.gov/saturn-v", "Saturn V launch mass 2,970 t"),
                       ("https://en.wikipedia.org/wiki/S-IC", "S-IC stage mass 2,290 t")):
        db.insert("INSERT INTO sources(topic_id, claim, url, verdict, tier, http_status, domain) VALUES(?,?,?,?,?,?,?)",
                  tid, claim, url, "reachable", 3, 200, topics.domain(url))
    return fc, tid


def test_calculations_are_rerun_safely():
    from studio import script
    assert script.calc("384,400 / 3,474") == pytest.approx(110.65, rel=1e-3)
    assert script.calc("sqrt(2*g*1.5)") == pytest.approx(5.42, rel=1e-2)
    assert script.calc("max(1, 2) * 3^2") == 18
    for bad in ("__import__('os')", "open('x')", "2**10000", "a.b"):
        with pytest.raises(Exception):
            script.calc(bad)
    out = script.check_calcs([{"expr": "2290/2970", "result": 0.77}, {"expr": "2290/2970", "result": 0.5},
                              {"expr": "os.system(1)", "result": 1}])
    assert [c["ok"] for c in out] == [True, False, False] and "not 0.5" in out[1]["problem"]


def test_hook_rules():
    from studio import script
    assert script.hook_checks("Why do rockets throw away 88 percent of themselves?") == []
    assert any("clickbait" in p for p in script.hook_checks("You won't believe this rocket!"))
    assert any("words" in p for p in script.hook_checks("Rockets are amazing and today we will talk about them in a lot of detail okay sure"))
    assert any("concrete" in p for p in script.hook_checks("Rockets are cool."))
    i, s = script.best_hook(["You won't believe it", "Why is space cold?", "How hot is 5,500 C?"],
                            {0: {"curiosity": 10}, 1: {"curiosity": 6, "clarity": 6, "specificity": 6, "honesty": 6, "speed": 6},
                             2: {"curiosity": 9, "clarity": 9, "specificity": 9, "honesty": 9, "speed": 9}})
    assert (i, s) == (2, 90.0) or (i == 2 and s == 9.0)


def test_script_passes_and_records_claims(fakes):
    from studio import db, jobs, script, topics
    fc, tid = fakes
    vid = jobs.create(tid, title="x")
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    ck = json.loads(row["checks"])
    assert row["status"] == "passed" and row["hook"] == "Why do rockets throw away 88 percent of themselves?"
    assert ck["words"] >= script.WORDS_MIN and ck["claims"][0]["verdict"] == "supported"
    assert json.loads(row["structure"])["calcs"][0]["ok"]
    assert db.one("SELECT COUNT(*) n FROM sources WHERE video_id=? AND verdict='supported'", vid)["n"] == 2
    assert db.one("SELECT script_status FROM topics WHERE id=?", tid)["script_status"] == "passed"
    assert "## Fact-check" in script.report(vid) and "✓" in script.report(vid)


def test_wrong_claim_gets_one_revision(fakes):
    from studio import jobs, script, topics
    fc, tid = fakes
    fc.bad_first = True
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    assert fc.writes == 1 and fc.line_fixes == 1 and row["version"] == 2 and row["status"] == "passed"


def test_still_wrong_is_rejected_never_passed_on(fakes):
    from studio import db, jobs, script, topics
    fc, tid = fakes
    fc.always_bad = True
    vid = jobs.create(tid)
    with pytest.raises(script.ScriptRejected):
        script.make(topics.topic(tid), vid, progress=lambda s: None)
    assert script.latest(vid) is None and db.one("SELECT script_status FROM topics WHERE id=?", tid)["script_status"] == "failed"
    assert jobs.classify(script.ScriptRejected("x")) == "permanent"


def test_wrong_arithmetic_fails_even_if_claude_says_fine(fakes):
    from studio import jobs, script, topics
    fc, tid = fakes
    fc.wrong_calc = True
    with pytest.raises(script.ScriptRejected, match="not 0.5"):
        script.make(topics.topic(tid), jobs.create(tid), progress=lambda s: None)


def test_remake_of_an_earlier_script_is_refused(fakes):
    from studio import jobs, script, topics
    fc, tid = fakes
    script.make(topics.topic(tid), jobs.create(tid), progress=lambda s: None)
    fc.writes = 0
    rev = script.review(script.write(topics.topic(tid)), topics.topic(tid), video_id=999)
    assert not rev["ok"] and any("too close" in p for p in rev["problems"])


def test_topic_needs_two_working_sources(fakes):
    from studio import db, script, topics
    fc, tid = fakes
    db.run("UPDATE sources SET verdict='unreachable' WHERE url LIKE '%wikipedia%'")
    with pytest.raises(script.ScriptRejected, match="2 working"):
        script.make(topics.topic(tid), None, progress=lambda s: None)


def test_worker_runs_research_to_narration(fakes, fake_voice):
    from studio import config, db, jobs, pipeline
    fc, tid = fakes
    config.set_values(enabled=True)
    pipeline.register()
    vid = jobs.create()
    states = [jobs.tick() for _ in range(4)]
    assert states == ["scripting", "fact_checking", "generating_audio", "rendering"]
    v = db.video(vid)
    assert v["topic_id"] == tid and v["title"] == "Why Rockets Throw Parts Away"
    assert db.one("SELECT COUNT(*) n FROM voiceovers WHERE video_id=?", vid)["n"] == 1
    assert fake_voice["voice"] == "am_michael" and "88 percent" in fake_voice["text"]
    assert db.one("SELECT status, video_id FROM topics WHERE id=?", tid)["video_id"] == vid
    assert "rendering" in jobs.STAGES                            # Phase 7 picks it up from here


def test_too_long_draft_is_fitted_before_the_fact_check(fakes):
    from studio import script, topics
    fc, tid = fakes
    fc.too_long = True
    row = script.make(topics.topic(tid))
    assert fc.fits == 1 and fc.reviews == 1 and row["status"] == "passed"
    assert script.WORDS_MIN <= len(row["body"].split()) <= script.WORDS_MAX



def test_hooks_must_grab_in_the_first_words():
    from studio import script
    assert not script.hook_checks("88 percent of a rocket is thrown away. Here's why.")
    assert not script.hook_checks("Why do rockets throw away 88 percent?")
    assert any("too late" in p for p in script.hook_checks("Rockets are tall machines that carry 88 percent fuel"))
    assert any("slow start" in p for p in script.hook_checks("Did you know rockets drop 88 percent?"))
    assert any("words (max" in p for p in script.hook_checks("Why " + "very " * 12 + "long?"))


def test_hook_scene_shows_the_hooks_number():
    from studio import video
    sc = video.hook_scene("88 percent of every rocket is thrown away", {"type": "compare", "items": []})
    assert sc["type"] == "number" and sc["value"] == "88"
    keep = {"type": "number", "value": "88", "unit": "%", "label": "thrown away"}
    assert video.hook_scene("88 percent of every rocket is thrown away", keep) is keep
    assert video.hook_scene("Why do rockets fall apart?")["type"] == "headline"


def test_two_rewrites_before_giving_up(fakes):
    from studio import jobs, script, topics
    fc, tid = fakes
    fc.bad_reviews = 2
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    assert fc.writes + fc.line_fixes == 3 and row["version"] == 3 and row["status"] == "passed"


def test_textbook_law_is_fine_but_verifies_nothing_alone(fakes):
    from studio import script, topics
    fc, tid = fakes
    draft = script.write(topics.topic(tid))
    fc.standard_only = True
    rev = script.review(draft, topics.topic(tid))
    assert not any("is standard" in p for p in rev["problems"])
    assert any("no checkable claims" in p for p in rev["hard_problems"])


def test_originality_fail_says_how_to_fix_it(fakes):
    from studio import script, topics
    fc, tid = fakes
    fc.orig = 5
    rev = script.review(script.write(topics.topic(tid)), topics.topic(tid))
    p = [x for x in rev["hard_problems"] if x.startswith("originality 5/10")]
    assert p and "hiker" in p[0]


def test_writer_is_told_about_general_science_and_own_example(fakes, monkeypatch):
    from studio import claude, script, topics
    fc, tid = fakes
    seen = []
    real = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (seen.append(p), real(p, timeout, web))[1])
    script.write(topics.topic(tid))
    assert '["general"]' in seen[0] and "everyday example" in seen[0]


def test_a_rewrite_starts_from_the_best_version_and_uses_the_reviewers_idea(fakes, monkeypatch):
    from studio import claude, jobs, script, topics
    fc, tid = fakes
    fc.orig_first, fc.orig = 7, 5          # v1 original but one wrong line; later versions generic
    fc.bad_reviews = 3
    prompts = []
    real = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (prompts.append(p), real(p, timeout, web))[1])
    vid = jobs.create(tid)
    with pytest.raises(script.ScriptRejected):
        script.make(topics.topic(tid), vid, progress=lambda s: None)
    rewrites = [p for p in prompts if p.startswith("Write a YouTube Short script") and "failed review" in p]
    fixes = [p for p in prompts if p.startswith("A fact-checker flagged")]
    assert len(fixes) == 2                 # both revisions repaired the near-miss v1, not the generic v2
    assert not rewrites


def test_rewrite_is_told_the_reviewers_idea_and_no_new_claims(fakes, monkeypatch):
    from studio import claude, script, topics
    fc, tid = fakes
    seen = []
    real = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (seen.append(p), real(p, timeout, web))[1])
    script.write(topics.topic(tid), ["originality 5/10: needs its own explanation (use a hiker dropping bottles)"], {"lines": []})
    assert "Rebuild the Short around this idea" in seen[0] and "hiker dropping bottles" in seen[0]
    assert "Don't add any new claim" in seen[0] and "No made-up caveats" in seen[0]
