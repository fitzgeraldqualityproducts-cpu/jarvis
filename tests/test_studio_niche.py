"""Phase 3: niche research, scoring, report and selection (fake YouTube API and fake Claude)."""
import hashlib
import json
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def _h(s, mod):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % mod


class FakeYouTube:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        res = url.split("/v3/")[1].split("?")[0]
        p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        self.calls.append(res)
        assert "key" in p                                   # the research key is used, never a channel token
        if res == "search":
            q = p["q"]
            return {"items": [{"id": {"videoId": f"v{_h(q, 10**6)}-{i}"},
                               "snippet": {"channelId": f"UC{_h(q, 7)}{i % 4}", "title": f"{q} {i}",
                                           "publishedAt": "2026-11-10T00:00:00Z"}} for i in range(12)]}
        if res == "videos":
            out = []
            for vid in p["id"].split(","):
                base = vid.split("-")[0]
                out.append({"id": vid, "statistics": {"viewCount": str(1000 * (1 + _h(vid, 3000)) * (1 + _h(base, 5)))},
                            "contentDetails": {"duration": f"PT{20 + _h(vid, 50)}S"},
                            "snippet": {"channelId": f"UC{_h(base, 7)}{_h(vid, 4)}", "title": vid,
                                        "publishedAt": f"2026-11-{10 + _h(vid, 9):02d}T00:00:00Z"}})
            return {"items": out}
        if res == "channels":
            return {"items": [{"id": c, "snippet": {"title": f"Channel {c}", "publishedAt": "2025-12-01T00:00:00Z" if _h(c, 2) else "2019-01-01T00:00:00Z"},
                               "statistics": {"subscriberCount": str(10_000 * (1 + _h(c, 300))), "viewCount": "1", "videoCount": "100"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c}}} for c in p["id"].split(",")]}
        if res == "playlistItems":
            return {"items": [{"contentDetails": {"videoId": f"{p['playlistId']}-{i}"}} for i in range(10)]}
        raise AssertionError(res)


def fake_claude(prompt, timeout, web):
    if "Define the channel" in prompt:
        assert not web
        return json.dumps({"pillars": [{"name": "How it works", "description": "x"}, {"name": "Myths", "description": "y"},
                                       {"name": "Numbers", "description": "z"}],
                           "audience": "Curious US adults", "positioning": "Big ideas in 45 seconds",
                           "style": "calm", "value": "learn", "differentiation": "data", "avoid": ["templates"]})
    assert web
    ids = [line[2:].split(":")[0] for line in prompt.splitlines() if line.startswith("- ") and ":" in line]
    return "Here you go:\n```json\n" + json.dumps([{
        "id": i, "monetization": {"score": 40 + _h(i, 50), "shorts": "pool share", "long_form": "ok",
                                  "advertiser_friendly": "yes", "sources": ["https://support.google.com/youtube/answer/72851"]},
        "sustainability": {"score": 50 + _h(i, 40), "evergreen_share": "80%", "topic_depth": "hundreds"},
        "feasibility": {"score": 30 + _h(i, 60), "visuals": "charts", "blockers": "none", "inauthentic_risk": "medium"},
        "saturation": "busy", "original_angles": ["a", "b", "c"], "risks": ["r"], "publishing": "4 a week",
        "audience": "US adults", "confidence": "medium"} for i in ids]) + "\n```"


@pytest.fixture
def fakes(monkeypatch):
    from studio import claude, ytpublic
    yt = FakeYouTube()
    monkeypatch.setattr(ytpublic, "FETCH", yt)
    monkeypatch.setattr(claude, "RUNNER", fake_claude)
    ytpublic.save_key("AIza" + "x" * 35)
    return yt


def test_full_run_reports_and_chooses_one_niche(fakes):
    from clipper import youtube
    from studio import db, niche
    lines = []
    run = niche.research("r1", progress=lines.append)
    ev = niche.load("r1")
    assert len(ev["niches"]) == len(niche.CANDIDATES) and all(n.get("measured") and n.get("qualitative") for n in ev["niches"].values())
    assert youtube.quota_used("searches") == 2 * len(niche.CANDIDATES)          # counted in Google's search bucket
    assert niche.score(ev) == niche.score(niche.load("r1"))                   # reproducible from saved evidence
    d = niche.choose(run)
    ranked = niche.score(ev)
    assert d["niche"] == niche.pick(ranked)["id"] and d["niche"] in [r["id"] for r in ranked[:3]]
    assert db.one("SELECT niche FROM channel")["niche"] == d["name"]
    assert len(db.q("SELECT * FROM pillars WHERE active=1")) == 3
    assert db.one("SELECT COUNT(*) n FROM niche_research WHERE run_id='r1' AND chosen=1")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM competitors")["n"] > 0
    md = (niche.run_dir("r1") / "report.md").read_text()
    assert "## Decision" in md and "## Top 10" in md and md.count("\n## ") >= 12
    assert "(measured)" in md and "(estimate)" in md and "https://support.google.com/youtube/answer/72851" in md
    assert "<table>" in (niche.run_dir("r1") / "report.html").read_text()
    for factor in ranked[0]["factors"].values():
        assert factor["basis"] in ("measured", "estimate", "estimate + measured")


def test_niche_stays_unless_forced_with_a_reason(fakes):
    from studio import db, niche
    niche.research("r1", progress=lambda s: None)
    first = niche.choose("r1")
    assert niche.choose("r1")["niche"] == first["niche"]                     # stable
    with pytest.raises(ValueError):
        niche.choose("r1", force=True)
    niche.choose("r1", force=True, reason="test")
    assert db.one("SELECT COUNT(*) n FROM decisions WHERE area='niche'")["n"] == 2


def test_run_is_resumable_without_refetching(fakes):
    from studio import niche
    niche.research("r2", progress=lambda s: None)
    n = len(fakes.calls)
    niche.research("r2", progress=lambda s: None)
    assert len(fakes.calls) == n


def test_without_youtube_data_scores_are_marked_missing(monkeypatch):
    from studio import claude, niche
    monkeypatch.setattr(claude, "RUNNER", fake_claude)
    niche.research("r3", progress=lambda s: None)                             # no key, no connected channel
    ev = niche.load("r3")
    assert any("No YouTube research key" in x for x in ev["limits"])
    r = niche.score(ev)[0]
    assert r["factors"]["demand"]["basis"] == "missing" and r["coverage"] == pytest.approx(0.5)
    assert "Limitation" in (niche.run_dir("r3") / "report.md").read_text()


def test_scoring_rules():
    from studio import niche
    mm = {"sample": 50, "median_views": 1_000_000, "big_channel_share": 0.0, "small_channel_hit_share": 0.5,
          "young_channel_share": 0.5, "share_over_1m": 0.5, "outlier_share": 0.5, "competitor_consistency": 0.6}
    q = {"monetization": {"score": 80}, "sustainability": {"score": 70}, "feasibility": {"score": 90}, "confidence": "high"}
    s = niche.score_one("x", mm, q)
    assert s["factors"]["demand"]["score"] == 75.0 and s["factors"]["competition"]["score"] == 100.0
    assert s["factors"]["viral"]["score"] == 100.0 and s["factors"]["sustainability"]["score"] == 79.0
    assert s["overall"] == round(0.25 * 80 + 0.2 * 75 + 0.15 * 100 + 0.15 * 100 + 0.15 * 79 + 0.1 * 90, 1)
    assert sum(niche.WEIGHTS.values()) == pytest.approx(1.0)


def test_claude_daily_cap(monkeypatch):
    from studio import claude, config, jobs
    config.set_values(claude_calls_per_day=1)
    monkeypatch.setattr(claude, "RUNNER", lambda p, t, w: "ok")
    assert claude.ask("hi") == "ok"
    with pytest.raises(jobs.QuotaWait):
        claude.ask("again")


def test_api_key_is_private(tmp_path):
    import os
    import stat
    from studio import ytpublic
    with pytest.raises(ValueError):
        ytpublic.save_key("not a key")
    ytpublic.save_key("AIza" + "k" * 35)
    mode = stat.S_IMODE(os.stat(ytpublic._key_path()).st_mode)
    assert mode == 0o600 and ytpublic.available()
