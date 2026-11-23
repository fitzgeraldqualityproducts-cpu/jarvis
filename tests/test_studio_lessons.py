"""Learning from mistakes: rejections are recorded, sorted, fed back, and sure failures skipped."""
import json
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def test_problems_are_sorted_into_categories():
    from studio import lessons
    c = lessons.categorize
    assert c("111 words; it must be 60-105 (25-45 seconds): cut about 14 words") == "too_long"
    assert c("52 words; it must be 60-105 (25-45 seconds): add about 16 words") == "too_short"
    assert c("originality 5/10: needs its own explanation") == "originality"
    assert c("line 3 is wrong: NASA says 2,950") == "wrong_fact"
    assert c("line 2 is unsupported: no source") == "unsupported"
    assert c("2290/2970 is 0.771, not 0.5") == "calc"
    assert c("the topic has fewer than 2 working sources") == "thin_sources"
    assert c("qa: safe_area (text outside)") == "qa"
    assert c("something new") == "other"


def test_advice_appears_after_mistakes_and_gets_firmer():
    from studio import lessons
    assert lessons.advice("script") == ""
    t = {"id": 1, "pillar": "Scale It Right", "kind": "evergreen"}
    lessons.record("script", ["111 words; it must be 60-105 (25-45 seconds): cut about 14 words",
                              "editor: tighten line 2"], t)                     # editor notes aren't lessons
    a = lessons.advice("script")
    assert "too long 1 time" in a and "MUST" not in a and "editor" not in a
    for _ in range(2):
        lessons.record("script", ["112 words; it must be 60-105 (25-45 seconds): cut about 15 words"], t)
    assert "MUST: Scripts ran too long 3 time" in lessons.advice("script")
    assert lessons.advice("ideas") == ""                                         # areas stay separate


def test_old_lessons_fade():
    from studio import db, lessons
    lessons.record("script", ["originality 4/10: needs its own explanation"], {"id": 1})
    db.run("UPDATE lessons SET t=?", time.time() - 40 * 86400)
    assert lessons.advice("script") == "" and "no lessons yet" in lessons.summary()


def test_risk_for_a_pillar_that_keeps_failing_is_capped():
    from studio import db, lessons
    for tid in range(1, 8):
        lessons.record("script", ["originality 4/10: x"], {"id": tid, "pillar": "P", "kind": "evergreen"})
    assert lessons.risk({"pillar": "P", "kind": "evergreen"}) == lessons.MAX_RISK
    assert lessons.risk({"pillar": "Q", "kind": "evergreen"}) == 0


def test_writer_and_idea_finder_are_told(monkeypatch):
    from studio import claude, db, lessons, script, topics
    seen = []

    def runner(prompt, timeout, web):
        seen.append(prompt)
        if prompt.startswith("You find topics"):
            return "[]"
        return json.dumps({"hooks": ["Why 2?"], "lines": [{"text": "w " * 80, "facts": ["F1"]}], "calcs": [], "titles": []})
    monkeypatch.setattr(claude, "RUNNER", runner)
    lessons.record("script", ["111 words; it must be 60-105 (25-45 seconds): cut about 14 words"], {"id": 1})
    lessons.record("ideas", ["the topic has fewer than 2 working sources"], {"id": 2})
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    script.write({"id": 5, "title": "T", "sources": []})
    assert any(p.startswith("Write a YouTube Short script") and "Scripts ran too long 1 time" in p for p in seen)
    topics.propose({"name": "Scale It Right"})
    assert "thin sources 1 time" in seen[-1]


def test_sure_failures_are_skipped_before_writing():
    from studio import db, lessons, topics
    thin = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status) VALUES(?,?,?,?,?,?)",
                     "thin idea", "thin", "A", "evergreen", 90, "idea")
    db.insert("INSERT INTO sources(topic_id, claim, url, http_status) VALUES(?,?,?,?)", thin, "f", "https://a.org/1", 200)
    good = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status) VALUES(?,?,?,?,?,?)",
                     "good idea", "good", "B", "evergreen", 70, "idea")
    for n in (1, 2):
        db.insert("INSERT INTO sources(topic_id, claim, url, http_status) VALUES(?,?,?,?)", good, "f", f"https://b.org/{n}", 200)
    assert topics.pick_next()["id"] == good
    assert db.one("SELECT status FROM topics WHERE id=?", thin)["status"] == "rejected"
    assert lessons.counts()["thin_sources"] == 1


def test_failed_review_is_recorded(monkeypatch):
    """A real fact-check rejection goes into the lessons table."""
    from studio import lessons
    from test_studio_script import FakeClaude  # noqa: F401
    import test_studio_script as ts
    from studio import claude, db, script, topics
    fc = ts.FakeClaude()
    fc.always_bad = True
    monkeypatch.setattr(claude, "RUNNER", fc)
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {}})
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status) VALUES(?,?,?,?,?,?)",
                    "Rockets", "rockets", "Inside the Machine", "question", 70, "idea")
    for url in ("https://www.nasa.gov/a", "https://en.wikipedia.org/wiki/b"):
        db.insert("INSERT INTO sources(topic_id, claim, url, verdict, tier, http_status, domain) VALUES(?,?,?,?,?,?,?)",
                  tid, "c", url, "reachable", 3, 200, topics.domain(url))
    with pytest.raises(script.ScriptRejected):
        script.make(topics.topic(tid))
    assert lessons.counts().get("wrong_fact") == 3                     # first draft + two rewrites
