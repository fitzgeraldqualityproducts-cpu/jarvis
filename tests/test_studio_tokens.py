"""Fewer Claude tokens with the same checks: shorter fact-check answers, and after "fix lines" only the changed
lines are checked again, while everything else keeps its verdicts and ratings from the full check."""
import json

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import BODY, fakes  # noqa: F401

BEATS = ["hook", "setup", "setup", "math", "rehook", "payoff", "loop"]
LINES = list(BODY)
LINES[3] = "That means " + BODY[3][0].lower() + BODY[3][1:]          # line 4 leans on line 3, so it can't be cut


def _draft():
    return {"hooks": [BODY[0], "What if a rocket kept its empty tanks?"],
            "lines": [{"text": t, "facts": ["F1"], "visual": "diagram", "beat": b} for t, b in zip(LINES, BEATS)],
            "calcs": [{"expr": "2290/2970", "result": 0.77, "unit": "", "says": "share"}], "titles": ["Rockets"]}


class Checker:
    """A fake Claude for the fact-check, the line fix and the partial re-check."""

    def __init__(self, fix_text="Its first stage was about 2,290 tonnes.", fix_line=3, recheck_verdict="supported"):
        self.prompts, self.fix_text, self.fix_line, self.recheck_verdict = [], fix_text, fix_line, recheck_verdict

    def __call__(self, prompt, timeout, web):
        self.prompts.append(prompt)
        if prompt.startswith("A fact-checker flagged"):
            return json.dumps({"lines": [{"line": self.fix_line, "text": self.fix_text, "delete": False}]})
        if "were just\nreworded" in prompt:
            assert web and "Check ONLY L3" in prompt
            return json.dumps({"claims": [{"line": 3, "verdict": self.recheck_verdict, "fact": "F2",
                                           "quote": "fueled mass of about 2,290 metric tons", "confidence": 0.9,
                                           "source_date": ""}], "problems": []})
        assert prompt.startswith("You are a strict fact-checker")
        self.full = getattr(self, "full", 0) + 1
        assert '"n/a"' not in prompt and "leave it out for\n\"supported\"" in prompt
        return json.dumps({
            "claims": [{"line": 2, "verdict": "supported", "fact": "F1", "quote": "liftoff mass of 2,970 tonnes",
                        "confidence": 0.95, "source_date": ""},
                       {"line": 3, "verdict": "unsupported", "note": "no source says almost all was propellant"}
                       if self.full == 1 else {"line": 3, "verdict": "supported", "fact": "F2", "quote": "2,290"},
                       {"line": 4, "verdict": "supported", "fact": "F2", "quote": "2,290", "confidence": 0.9,
                        "source_date": ""}],
            "hooks": {"0": {"curiosity": 9, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8},
                      "1": {"curiosity": 6, "clarity": 6, "specificity": 6, "honesty": 6, "speed": 6}},
            "loop": 8, "rehook": 7, "originality": 8, "inauthentic_risk": "low", "problems": ["line 6 is a bit long"]})


def _run(fakes, monkeypatch, checker):  # noqa: F811
    from studio import claude, db, script, topics
    monkeypatch.setattr(claude, "RUNNER", checker)
    fc, tid = fakes
    topic = topics.topic(tid)
    vid = db.insert("INSERT INTO videos(title, state, topic_id) VALUES(?,?,?)", "t", "scripting", tid)
    script.draft_step(topic, vid, draft=_draft())
    assert script.check_step(topic, vid)[0] == "revise"
    script.revise_step(topic, vid)
    return script.check_step(topic, vid), topic, vid


def test_after_a_line_fix_only_that_line_is_checked_again(fakes, monkeypatch):  # noqa: F811
    checker = Checker()
    (result, row), topic, vid = _run(fakes, monkeypatch, checker)
    assert result == "passed"
    full = [p for p in checker.prompts if p.startswith("You are a strict fact-checker and editor")]
    assert len(full) == 1                                               # the second check was the short one
    checks = json.loads(row["checks"])
    assert checks["rechecked"] == [3]
    verdicts = {c["line"]: c["verdict"] for c in checks["claims"]}
    assert verdicts == {2: "supported", 3: "supported", 4: "supported"}  # kept lines keep their verdicts
    assert checks["loop"] == 8 and checks["originality"] == 8 and checks["hook_score"]   # ratings carry over
    assert "editor: line 6 is a bit long" in checks["problems"]
    from studio import db
    assert db.one("SELECT COUNT(*) n FROM sources WHERE video_id=?", vid)["n"] >= 3     # claims are still recorded


def test_a_fixed_line_that_is_still_unsupported_fails_again(fakes, monkeypatch):  # noqa: F811
    from studio import script
    checker = Checker(recheck_verdict="unsupported")
    with pytest.raises(script.ScriptRejected, match="line 3 is unsupported"):  # the same problem twice: next idea
        _run(fakes, monkeypatch, checker)


def test_a_fix_to_the_hook_payoff_or_loop_gets_a_full_check(fakes, monkeypatch):  # noqa: F811
    from studio import script
    d = _draft()
    prior = {"hard_problems": ["line 2 is unsupported: x"], "hook_ratings": {"0": {}}, "loop": 8, "originality": 8,
             "claims": []}
    for k in (1, 5, 6, 7):                                              # hook, rehook, payoff, loop lines
        new = json.loads(json.dumps(d))
        new["lines"][k - 1]["text"] = "Changed."
        assert script.recheck_plan(d, new, {i: i for i in range(1, 8)}, prior) is None
    new = json.loads(json.dumps(d))
    new["lines"][2]["text"] = "Changed."
    plan = script.recheck_plan(d, new, {i: i for i in range(1, 8)}, prior)
    assert plan["changed"] == {2, 3}                                    # the flagged line 2, left as it was, too
    new["hooks"] = ["Another hook?"]
    assert script.recheck_plan(d, new, {i: i for i in range(1, 8)}, prior) is None   # new hooks need rating


def test_no_partial_check_after_a_failed_rating_or_a_length_fix(fakes, monkeypatch):  # noqa: F811
    from studio import script
    d = _draft()
    new = json.loads(json.dumps(d))
    new["lines"][2]["text"] = "Changed."
    ident = {i: i for i in range(1, 8)}
    assert script.recheck_plan(d, new, ident, {"hard_problems": ["originality 5/10: needs its own explanation"],
                                               "hook_ratings": {"0": {}}, "loop": 8, "originality": 5}) is None
    assert script.recheck_plan(d, new, None, {"hard_problems": ["line 3 is unsupported: x"],
                                              "hook_ratings": {"0": {}}, "loop": 8, "originality": 8}) is None


def test_partial_checks_can_be_switched_off(fakes, monkeypatch):  # noqa: F811
    from studio import config
    config.set_values(partial_recheck=False)
    checker = Checker()
    (result, row), topic, vid = _run(fakes, monkeypatch, checker)
    assert result == "passed" and "rechecked" not in json.loads(row["checks"])
    assert len([p for p in checker.prompts if p.startswith("You are a strict fact-checker and editor")]) == 2


def test_an_unsupported_line_found_by_a_lookup_is_checked_again_without_a_rewrite(fakes, monkeypatch):  # noqa: F811
    from studio import claude, db, script, topics
    checker = Checker()
    monkeypatch.setattr(claude, "RUNNER", checker)
    monkeypatch.setattr(script, "lookup_on", lambda: True)
    asked = []
    monkeypatch.setattr(script, "look_up", lambda a, t, most=2: (asked.append(sorted(a)), {3: "F2"})[1])
    fc, tid = fakes
    topic = topics.topic(tid)
    vid = db.insert("INSERT INTO videos(title, state, topic_id) VALUES(?,?,?)", "t", "scripting", tid)
    script.draft_step(topic, vid, draft=_draft())
    result, row = script.check_step(topic, vid)
    assert result == "passed" and asked == [[3]]
    checks = json.loads(row["checks"])
    assert checks["rechecked"] == [3] and json.loads(row["structure"])["lookup_from"]
    assert not [p for p in checker.prompts if p.startswith("A fact-checker flagged")]     # no rewrite call
    assert len([p for p in checker.prompts if p.startswith("You are a strict fact-checker and editor")]) == 1
