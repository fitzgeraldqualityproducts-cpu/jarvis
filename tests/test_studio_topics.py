"""Phase 5: topic discovery, scoring, duplicates, source checks and picking (fake Claude, fake web)."""
import json
import re
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_niche import FakeYouTube

IDEAS = {
    "Run It Yourself": [
        ("How fast does a falling phone really hit the floor", "evergreen", ["https://www.nasa.gov/g", "https://en.wikipedia.org/wiki/Free_fall"]),
        ("Can you outrun a raindrop", "question", ["https://www.noaa.gov/rain", "https://phys.org/rain"]),
        ("New comet brightness this week, calculated", "trending", ["https://www.esa.int/comet", "https://www.space.com/comet"]),
        ("A pill that cures colds", "evergreen", ["https://www.nih.gov/x", "https://example.com/y"]),           # banned area
        ("Weight of every cloud", "evergreen", ["https://dead.example.org/a", "https://dead.example.org/b"]),  # sources broken
    ],
    "Real Space Data": [
        ("How far Voyager 1 travels while you watch this", "evergreen", ["https://voyager.jpl.nasa.gov/", "https://www.britannica.com/v"]),
        ("This week's asteroid flyby in football fields", "trending", ["https://cneos.jpl.nasa.gov/", "https://www.space.com/a"]),
    ],
}


def fake_claude(prompt, timeout, web):
    if prompt.startswith("You find topics"):
        assert web and "Already covered" in prompt
        pillar = re.search(r"Pillar: (.+?):", prompt).group(1)
        return json.dumps([{"title": t, "kind": k, "angle": f"the numbers behind {t}", "hook": "Watch this.",
                            "why": "people ask", "keywords": t.lower().split()[:4],
                            "facts": [{"claim": f"fact {i}", "url": u, "source": "pub"} for i, u in enumerate(urls)]}
                           for t, k, urls in IDEAS[pillar]])
    if prompt.startswith("Use web search. For each Shorts topic"):
        assert web
        out = {}
        for line in prompt.splitlines():
            m = re.match(r"(\d+)\. (.+?):", line)
            if m:
                dead = "cloud" in m.group(2).lower()
                out[m.group(1)] = [{"claim": "extra", "url": f"https://{'dead.example.org' if dead else 'www.nist.gov'}/{m.group(1)}-{abs(hash(m.group(2))) % 999}",
                                    "source": "x"}]
        return json.dumps(out)
    assert prompt.startswith("Rate these") and not web
    out = {}
    for line in prompt.splitlines():
        m = re.match(r"(\d+)\. \[(\w+)\] (.+?):", line)
        if m:
            i, title = m.group(1), m.group(3)
            out[i] = {"relevance": 8, "interest": 7, "novelty": 6, "curiosity": 8, "value": 7, "story": 6,
                      "feasibility": 8, "originality": 7, "copyright_risk": 1, "avoid": "pill" in title.lower(),
                      "note": "ok"}
    return json.dumps(out)


def web_status(url):
    return 0 if "dead." in url else 200


@pytest.fixture
def setup(monkeypatch):
    from studio import claude, db, topics
    monkeypatch.setattr(claude, "RUNNER", fake_claude)
    monkeypatch.setattr(topics, "FETCH", web_status)
    db.put("niche_decision", {"niche": "science", "name": "Science explained",
                              "plan": {"positioning": "shows the calculation", "avoid": ["health claims"]}})
    for p in IDEAS:
        db.insert("INSERT INTO pillars(name, description, active, created) VALUES(?,?,1,?)", p, "x", time.time())
    return topics


def test_discovery_keeps_good_ideas_with_checked_sources(setup):
    from studio import db
    topics = setup
    res = topics.discover(progress=lambda s: None)
    kept = {r["title"] for r in res["added"]}
    assert len(kept) == 5 and "A pill that cures colds" not in kept and "Weight of every cloud" not in kept
    why = {r["title"]: r["why"] for r in res["rejected"]}
    assert "banned" in why["A pill that cures colds"][0] and "working source" in why["Weight of every cloud"][0]
    s = db.q("SELECT * FROM sources WHERE url='https://www.nasa.gov/g'")[0]
    assert s["tier"] == 3 and s["http_status"] == 200 and s["verdict"] == "reachable"
    t = db.one("SELECT * FROM topics WHERE kind='trending' AND status='idea' LIMIT 1")
    assert t["fresh_until"] > time.time() + 9 * 86400
    run = db.q("SELECT * FROM discovery_runs")
    assert len(run) == 2 and sum(r["added"] for r in run) == 5
    # the same ideas again are all repeats, including the rejected ones
    again = topics.discover(progress=lambda s: None)
    assert again["added"] == [] and again["duplicates"] == 7


def test_scoring_is_deterministic():
    from studio import topics
    r = {"relevance": 10, "interest": 10, "curiosity": 10, "value": 10, "novelty": 10, "feasibility": 10,
         "story": 10, "originality": 10, "copyright_risk": 0}
    assert topics.score_one(r, 10.0)[0] == 100.0
    assert topics.score_one(dict(r, interest=4), 10.0, measured=8)[1]["interest"] == {"score": 6.0, "basis": "estimate + measured"}
    low, f = topics.score_one({"relevance": 5}, 0.0)
    assert f["interest"]["score"] is None and low == round(10 * (0.14 * 5) / (0.14 + 0.10), 1)
    assert sum(topics.WEIGHTS.values()) == pytest.approx(1.0)
    assert topics.reliability([{"url": "a", "tier": 3, "http_status": 200}, {"url": "b", "tier": 2, "http_status": 403},
                               {"url": "c", "tier": 3, "http_status": 404}]) == round(10 * 5 / 6, 1)
    same = [{"url": "https://x.gov/a", "tier": 3, "http_status": 200}, {"url": "https://x.gov/a/", "tier": 3, "http_status": 200}]
    assert len(topics._working(same)) == 1                     # the same page twice is one source
    assert topics.tier("https://www.cdc.gov/x") == 3 and topics.tier("https://en.wikipedia.org/x") == 2
    assert topics.tier("https://randomblog.net/x") == 1 and topics.tier("not a url") == 0


def test_duplicates_catch_rephrasing(setup):
    from studio import db
    topics = setup
    db.insert("INSERT INTO topics(title, fingerprint, angle, status) VALUES(?,?,?,?)", "Why is the sky blue?",
              topics.fingerprint("Why is the sky blue?"), "Rayleigh scattering numbers", "published")
    assert topics.is_duplicate("Why the sky is blue")[0]
    assert topics.is_duplicate("The real reason the sky looks blue")[0]
    assert not topics.is_duplicate("How deep is the Mariana Trench")[0]
    db.insert("INSERT INTO videos(title, state) VALUES(?,?)", "How tall could a mountain on Mars get", "published")
    assert topics.is_duplicate("How tall can a mountain on Mars get")[0]


def _idea(db, title, pillar, score, kind="evergreen", fresh=None):
    from studio import topics
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, status, fresh_until, discovered) "
                    "VALUES(?,?,?,?,?,?,?,?)", title, topics.fingerprint(title), pillar, kind, score, "idea", fresh, time.time())
    for n in (1, 2):
        db.insert("INSERT INTO sources(topic_id, claim, url, http_status) VALUES(?,?,?,?)", tid, "fact",
                  f"https://example.org/{tid}/{n}", 200)
    return tid


def test_pick_rotates_pillars_and_limits_trends(setup):
    from studio import db
    topics = setup
    _idea(db, "alpha one", "A", 80)
    _idea(db, "alpha two", "A", 79)
    _idea(db, "beta one", "B", 75)
    first = topics.pick_next()
    assert first["title"] == "alpha one" and db.one("SELECT status FROM topics WHERE id=?", first["id"])["status"] == "selected"
    assert topics.pick_next()["title"] == "beta one"                 # A just went, so B gets a turn
    assert db.one("SELECT COUNT(*) n FROM decisions WHERE area='topic'")["n"] == 2
    # trending: boosted while fresh, held back once trends are 30%+ of recent picks
    now = time.time()
    t = _idea(db, "comet tonight", "C", 74, "trending", now + 9 * 86400)
    _idea(db, "gamma one", "D", 76)
    assert topics.pick_next(mark=False)["id"] == t
    for i in range(3):
        db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, status, selected) VALUES(?,?,?,?,?,?)",
                  f"old trend {i}", f"old trend {i}", "Z", "trending", "made", now - 100 - i)
    assert topics.pick_next(mark=False)["title"] == "gamma one"
    # stale trending ideas expire and never come back
    assert topics.expire(now + 10 * 86400) == 1
    assert all(x["kind"] != "trending" for x in topics.pool(now + 10 * 86400))


def test_background_discovery_only_when_needed(setup, monkeypatch):
    from studio import config, db
    topics = setup
    calls = []
    monkeypatch.setattr(topics, "discover", lambda **k: calls.append(1) or {"added": []})
    assert topics.maybe_discover() is None                        # studio switched off
    config.set_values(enabled=True)
    topics.maybe_discover()
    assert calls == [1]
    topics.maybe_discover()                                       # too soon
    assert calls == [1]
    for i in range(topics.MIN_POOL):
        _idea(db, f"ready idea number {i}", "A", 70)
    assert topics.maybe_discover(time.time() + 7 * 3600) is None and calls == [1]    # pool is full


def test_measured_interest_uses_youtube(setup, monkeypatch):
    from clipper import youtube
    from studio import ytpublic
    topics = setup
    monkeypatch.setattr(ytpublic, "FETCH", FakeYouTube())
    ytpublic.save_key("AIza" + "x" * 35)
    score, median = topics.measured_interest(["comet", "brightness"])
    assert 0 <= score <= 10 and median > 0
    assert youtube.quota_used("searches") == 1


def test_idle_chores_run_and_never_crash_the_worker(setup):
    from studio import config, db, jobs
    config.set_values(enabled=True)
    ran = []

    def boom():
        ran.append(1)
        raise RuntimeError("web down")
    jobs.IDLE.append(boom)
    assert jobs.tick() is None and ran == [1]
    assert db.one("SELECT stage FROM errors ORDER BY id DESC LIMIT 1")["stage"] == "boom"


def test_offline_keeps_nothing_instead_of_rejecting(setup, monkeypatch):
    from studio import db, jobs
    topics = setup
    monkeypatch.setattr(topics, "FETCH", lambda url: 0)
    with pytest.raises(jobs.Transient):
        topics.discover(["Run It Yourself"], progress=lambda s: None)
    assert db.one("SELECT COUNT(*) n FROM topics")["n"] == 0


def test_thin_sources_get_a_second_search_and_old_rejects_come_back(setup, monkeypatch):
    from studio import db
    topics = setup
    IDEAS_ONE = {"Run It Yourself": [("Moon's same face, explained", "evergreen", ["https://science.nasa.gov/moon/facts/"])],
                 "Real Space Data": []}
    monkeypatch.setitem(IDEAS, "Run It Yourself", IDEAS_ONE["Run It Yourself"])
    res = topics.discover(["Run It Yourself"], progress=lambda s: None)
    assert [r["title"] for r in res["added"]] == ["Moon's same face, explained"]          # found a 2nd source
    assert db.one("SELECT COUNT(*) n FROM sources WHERE url LIKE 'https://www.nist.gov/%'")["n"] == 1
    # an idea rejected earlier only for sources gets one more try (and only one)
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, score, scores, status, reject_reason) VALUES(?,?,?,?,?,?,?,?)",
                    "Saturn gravity puzzle", "gravity puzzle saturn", "Real Space Data", "evergreen", 70,
                    json.dumps({"factors": {"relevance": {"score": 8}, "interest": {"score": 7}, "copyright_safety": {"score": 9}}}),
                    "rejected", "only 1 working source")
    db.insert("INSERT INTO sources(topic_id, url, tier, http_status) VALUES(?,?,?,?)", tid, "https://nssdc.gsfc.nasa.gov/x", 3, 200)
    revived = topics.retry_rejected(progress=lambda s: None)
    assert [r["id"] for r in revived] == [tid] and db.one("SELECT status FROM topics WHERE id=?", tid)["status"] == "idea"
    assert topics.retry_rejected(progress=lambda s: None) == []


def test_ordinary_angles_are_dropped_before_any_script(setup):
    """Phase 3 (2026-11-24): 10 of 30 script stops were "originality 5/10" after the full script and fact-check
    spend. An idea rated under 6 for originality is now turned down at the topic stage, for free."""
    from studio import db
    topics = setup
    r = {"relevance": 8, "interest": 7, "novelty": 6, "curiosity": 7, "value": 7, "story": 6, "feasibility": 8,
         "originality": 5, "copyright_risk": 0}
    _, f = topics.score_one(r, 8)
    c = {"_checked": [{"http_status": 200, "url": "https://a.org/1"}, {"http_status": 200, "url": "https://b.org/2"}]}
    assert any("too ordinary" in w for w in topics.gate(c, r, 8, f))
    _, f = topics.score_one(dict(r, originality=7), 8)
    assert not topics.gate(c, dict(r, originality=7), 8, f)
    # an ordinary idea already in the pool is skipped (and turned down) when picking
    old = _idea(db, "plain one", "A", 90)
    db.run("UPDATE topics SET scores=? WHERE id=?", json.dumps({"factors": {"originality": {"score": 5}}}), old)
    good = _idea(db, "fresh one", "B", 70)
    assert topics.pick_next()["id"] == good
    row = db.one("SELECT status, reject_reason FROM topics WHERE id=?", old)
    assert row["status"] == "rejected" and "too ordinary" in row["reject_reason"]
