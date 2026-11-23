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

    def __call__(self, prompt, timeout, web):
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
                "lines": [{"text": t, "facts": ["F1"], "visual": "diagram"} for t in BODY],
                "calcs": [{"expr": "2290/2970", "result": 0.5 if self.wrong_calc else 0.77, "unit": "", "says": "share"}],
                "titles": ["Why rockets throw parts away"]})
        assert prompt.startswith("You are a strict fact-checker") and web
        self.reviews += 1
        bad = self.always_bad or (self.bad_first and self.reviews == 1)
        return json.dumps({
            "claims": [{"line": 2, "verdict": "wrong" if bad else "supported", "fact": "F1", "note": "NASA says 2,950" if bad else ""},
                       {"line": 3, "verdict": "supported", "fact": "F2", "note": ""}, {"line": 7, "verdict": "n/a"}],
            "hooks": {str(i): {"curiosity": 9 - i, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8} for i in range(5)},
            "originality": 8, "inauthentic_risk": "low", "problems": []})


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
    assert fc.writes == 2 and row["version"] == 2 and row["status"] == "passed"


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
