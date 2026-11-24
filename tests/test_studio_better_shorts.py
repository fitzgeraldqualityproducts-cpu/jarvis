"""Better Shorts: side-by-side drafts with a judge, a re-hook and beats in every script, delivery by beat."""
import json

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import BODY, FakeClaude, fakes  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401

BEATS = ["hook", "setup", "setup", "math", "rehook", "payoff", "loop"]


def _draft(angle, hook, calc_result=0.77, lines=None):
    return {"angle": angle,
            "hooks": [hook, "What if a rocket kept its empty tanks?"],
            "lines": [{"text": t, "facts": ["F1"], "visual": "diagram", "beat": b} for t, b in zip(lines or BODY, BEATS + ["setup"] * 40)],
            "calcs": [{"expr": "2290/2970", "result": calc_result, "unit": "", "says": "share"}],
            "titles": ["Why rockets throw parts away"]}


class ThreeDrafts(FakeClaude):
    """The writer returns three drafts at once; the judge prefers the last good one."""

    def __init__(self):
        super().__init__()
        self.judged = 0
        self.prompts = []

    def __call__(self, prompt, timeout, web):
        if prompt.startswith("Write a YouTube Short script") and '"drafts"' in prompt:
            self.prompts.append(prompt)
            self.writes += 1
            return json.dumps({"drafts": [
                _draft("a kitchen scale", "Why do rockets throw away 88 percent of themselves?"),
                _draft("broken maths", "Why do rockets throw away 88 percent?", calc_result=0.5),
                _draft("a hiker's water bottles", "What does a rocket do with 2,290 tonnes?")]})
        if prompt.startswith("Judge these drafts"):
            assert not web and "Draft A" in prompt and "Draft B" in prompt and "Draft C" not in prompt
            self.judged += 1
            return json.dumps({"scores": {"A": {"hook": 6, "payoff": 6, "clarity": 7, "momentum": 5, "you": 4, "original": 5},
                                          "B": {"hook": 9, "payoff": 8, "clarity": 8, "momentum": 8, "you": 8, "original": 8}},
                               "best": "B", "why": "the water bottles make the drop easy to picture"})
        return super().__call__(prompt, timeout, web)


@pytest.fixture
def three(fakes, monkeypatch):
    from studio import claude
    fc = ThreeDrafts()
    monkeypatch.setattr(claude, "RUNNER", fc)
    return fc, fakes[1]


def test_three_drafts_are_screened_and_judged(three):
    from studio import jobs, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    st = json.loads(row["structure"])
    assert fc.writes == 1 and fc.judged == 1 and row["status"] == "passed"
    p = fc.prompts[0]
    assert "RE-HOOK" in p and '"beat"' in p and "you" in p and "Write 3 COMPLETE drafts" in p
    picked = st["picked"]
    assert picked["of"] == 3 and picked["passed_screen"] == 2 and picked["angle"] == "a hiker's water bottles"
    assert "1" in picked["dropped"] and "not 0.5" in picked["dropped"]["1"][0]
    assert [ln["beat"] for ln in st["lines"]] == BEATS


def test_revision_and_drafts_setting_write_one(fakes):
    from studio import config, script, topics
    fc, tid = fakes
    config.set_values(drafts=1)
    d = script.write(topics.topic(tid))
    assert fc.writes == 1 and "picked" not in d
    assert d["lines"][0]["beat"] == "hook" and d["lines"][-1]["beat"] == "loop"   # beats filled in when missing
    assert script._drafts_wanted(["line 2 is wrong"], {"lines": []}) == 1


def test_judge_failure_never_stops_the_script(three, monkeypatch):
    from studio import script, topics
    fc, tid = three
    real = fc.__call__

    def broken(prompt, timeout, web):
        if prompt.startswith("Judge these drafts"):
            return "not json at all"
        return real(prompt, timeout, web)
    from studio import claude
    monkeypatch.setattr(claude, "RUNNER", broken)
    d = script.write(topics.topic(tid))
    assert d["picked"]["angle"] == "a kitchen scale"          # the first good draft


def test_screen_catches_stock_rehooks_and_far_off_length():
    from studio import script
    d = script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?"))
    assert script._screen(d) == []
    d["lines"][4]["text"] = "But wait, it gets stranger."
    assert any("stock phrase" in p for p in script._screen(d))
    long = script._clean(_draft("x", "Why do rockets throw away 88 percent?", lines=BODY * 3))
    assert any("words" in p for p in script._screen(long))
    assert script.judge_score({"hook": 10, "payoff": 10, "clarity": 10, "momentum": 10, "you": 10, "original": 10}) == 10


def test_weak_rehook_is_an_editor_note_not_a_failure(fakes, monkeypatch):
    from studio import claude, jobs, script, topics
    fc, tid = fakes
    real = fc.__call__

    def no_rehook(prompt, timeout, web):
        out = real(prompt, timeout, web)
        if prompt.startswith("You are a strict fact-checker"):
            assert '"rehook"' in prompt
            d = json.loads(out)
            d["rehook"] = 2
            return json.dumps(d)
        return out
    monkeypatch.setattr(claude, "RUNNER", no_rehook)
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    ck = json.loads(row["checks"])
    assert row["status"] == "passed" and ck["rehook"] == 2 and any("re-hook" in p for p in ck["problems"])


def test_delivery_follows_the_beats(three):
    from studio import jobs, narration, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    segs = narration.segments(row["body"], vid)
    assert [s["beat"] for s in segs] == BEATS
    pay = segs[BEATS.index("payoff")]
    plain = segs[1]
    assert pay["exaggeration"] > plain["exaggeration"] and pay["pause_before"] > 0.3
    assert pay["cfg_weight"] < plain["cfg_weight"]
    assert narration.segments(row["body"] + " extra words", vid) is None     # text changed: read it evenly
    assert narration.segments(row["body"], None) is None


def test_chatterbox_gets_the_segments(three, fake_voice, monkeypatch, tmp_path):
    from studio import engines, jobs, narration, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    seen = {}

    def runner(tool, worker, payload, timeout):
        seen.update(payload)
        raise engines.EngineError("not installed here")
    monkeypatch.setattr(engines, "RUNNER", runner)
    monkeypatch.setattr(engines, "heavy", lambda what, wait=900: __import__("contextlib").nullcontext())
    with pytest.raises(engines.EngineError):
        narration._chatterbox(row["body"], narration.settings(), tmp_path / "x.wav", vid)
    assert len(seen["segments"]) == 7 and seen["gap"] == narration.GAP
