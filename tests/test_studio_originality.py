"""Connor's originality plan (2026-11-26): ideas whose answer is already a famous explainer are screened before
writing, the writer gets one originality-only rewrite with the checker's own idea before the repeat stop, and an
originality 5 that is otherwise excellent may pass, held for Connor's review and never sent out by itself."""
import json
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401


def _rating(**kw):
    r = {"relevance": 8, "interest": 8, "curiosity": 8, "value": 8, "novelty": 7, "story": 8, "feasibility": 8,
         "originality": 7, "copyright_risk": 0, "avoid": False, "note": ""}
    r.update(kw)
    return r


def test_a_famous_explainer_needs_a_twist_of_our_own():
    from studio import topics
    c = {"title": "How long to walk around the Moon?", "angle": "circumference over walking speed",
         "_checked": [{"http_status": 200, "url": "https://nasa.gov/a"}, {"http_status": 200, "url": "https://space.com/b"}]}
    plain = _rating()
    _, f = topics.score_one(plain, 8)
    assert not any("famous" in w for w in topics.gate(c, plain, 8, f))
    famous = _rating(famous="Space.com: how long to walk around the Moon", twist="")
    assert any("famous explainer" in w for w in topics.gate(c, famous, 8, f))
    twisted = _rating(famous="Space.com: how long to walk around the Moon",
                      twist="in school days of your own commute walk")
    assert not any("famous" in w for w in topics.gate(c, twisted, 8, f))
    angle = topics.with_twist(c["angle"], *topics.famous_of(twisted))
    assert angle.startswith("in school days") and "don't retell Space.com" in angle
    assert topics.famous_of({"famous": "none", "twist": "x"}) == ("", "")


def test_rating_and_writing_ask_about_famous_explainers(fakes, monkeypatch):  # noqa: F811
    from studio import claude, script, topics
    fc, tid = fakes
    seen = []
    real = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (seen.append(p), real(p, timeout, web))[1])
    script.write(topics.topic(tid))
    assert "famous explainer" in seen[0]
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (seen.append(p), "{}")[1])
    topics.rate([{"title": "x", "angle": "y", "facts": []}])
    assert '"famous"' in seen[-1] and '"twist"' in seen[-1]


def test_one_originality_only_rewrite_before_the_repeat_stop(fakes, monkeypatch):  # noqa: F811
    from studio import claude, db, jobs, script, topics
    fc, tid = fakes
    fc.orig, fc.orig_on = 5, {3: 8}        # v1 and v2 read like the standard explainer; the extra rewrite is our own
    prompts = []
    real = claude.RUNNER
    monkeypatch.setattr(claude, "RUNNER", lambda p, timeout, web: (prompts.append(p), real(p, timeout, web))[1])
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    assert row["status"] == "passed" and fc.reviews == 3
    only = [p for p in prompts if "ONLY about originality" in p]
    assert len(only) == 1 and "hiker dropping empty water bottles" in only[0]
    st = [json.loads(r["structure"]) for r in db.q("SELECT structure FROM scripts WHERE video_id=?", vid)]
    assert sum(1 for s in st if s.get("originality_fix")) == 1


def test_the_extra_rewrite_is_given_once(fakes):  # noqa: F811
    from studio import jobs, script, topics
    fc, tid = fakes
    fc.orig = 5
    vid = jobs.create(tid)
    with pytest.raises(script.ScriptRejected, match="same problem came back"):
        script.make(topics.topic(tid), vid, progress=lambda s: None)
    assert fc.reviews == 3                 # draft, normal rewrite, the one originality rewrite; then it moves on


def test_no_extra_rewrite_when_something_else_is_wrong_too():
    from studio import script
    assert not script._originality_due(None, ["originality 5/10: needs its own explanation",
                                              "no worked calculation, but this channel always shows the math"])


NAR = {"hard": [], "advice": [], "score": 86}
ORIG = ["originality 5/10: needs its own explanation (use a hiker)"]


def test_an_otherwise_excellent_originality_five_is_held_not_failed(studio_home):  # noqa: F811
    from studio import script
    hold = script.originality_hold(5, list(ORIG), 8, 7, 9, NAR)
    assert hold and hold["originality"] == 5 and hold["story"] == 86
    assert script.originality_hold(4, ["originality 4/10: x"], 8, 7, 9, NAR) is None      # 4 always fails
    assert script.originality_hold(6, [], 8, 7, 9, NAR) is None                            # 6 passes normally
    assert script.originality_hold(5, ORIG + ["line 2 is unsupported: x"], 8, 7, 9, NAR) is None
    assert script.originality_hold(5, list(ORIG), 8, 7, 9, dict(NAR, score=79)) is None    # story under 80
    assert script.originality_hold(5, list(ORIG), 5, 7, 9, NAR) is None                    # weak loop
    assert script.originality_hold(5, list(ORIG), 8, 7, 9, None) is None                   # no story check


def test_review_passes_a_held_five_and_says_why(fakes, monkeypatch):  # noqa: F811
    from studio import claude, narrative, script, topics
    fc, tid = fakes
    fc.orig = 5
    real = claude.RUNNER

    def runner(p, timeout, web):
        out = real(p, timeout, web)
        if p.startswith("You are a strict fact-checker"):
            out = json.dumps(dict(json.loads(out), loop=8, rehook=7, answered=9))
        return out
    monkeypatch.setattr(claude, "RUNNER", runner)
    monkeypatch.setattr(script, "_narrative_on", lambda: True)
    monkeypatch.setattr(narrative, "evaluate", lambda *a, **k: dict(NAR))
    rev = script.review(script.write(topics.topic(tid)), topics.topic(tid))
    assert rev["ok"] and rev["originality_hold"]["originality"] == 5
    assert any(p.startswith("editor: held for Connor's review: originality 5/10") for p in rev["problems"])


def _held_video(tid):
    from studio import db, jobs
    vid = jobs.create(tid)
    db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", vid, 1, "[]", "h", "b", "{}",
              json.dumps({"ok": True, "originality_hold": {"originality": 5, "story": 86, "why": "x"}}), "passed",
              time.time())
    db.set_video(vid, state="ready")
    return vid


def test_a_held_short_never_goes_out_until_connor_approves(fakes, capsys):  # noqa: F811
    from studio import __main__ as cli, autopilot, db, jobs, pipeline, script
    fc, tid = fakes
    vid = _held_video(tid)
    assert "held for Connor's review" in script.held_for_review(vid)
    with pytest.raises(jobs.Wait, match="held for Connor's review"):
        pipeline.ready(db.video(vid), None)
    assert autopilot._ready_count() == 0               # it doesn't stop the line making the next Short
    assert script.originality_hold(5, list(ORIG), 8, 7, 9, NAR, video_id=None) is None   # one held at a time
    cli._approve(["approve"])
    assert f"video {vid}" in capsys.readouterr().out
    cli._approve(["approve", "--video", str(vid)])
    assert script.held_for_review(vid) is None and autopilot._ready_count() == 1
    assert script.originality_hold(5, list(ORIG), 8, 7, 9, NAR) is not None
