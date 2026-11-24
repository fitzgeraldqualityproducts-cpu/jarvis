"""Phase 10: Partner Program tracker: real numbers, labelled estimates, dated rules, no money."""
import datetime
import json
import time
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_analytics import FakeStats, _published, stats  # noqa: F401


def _ts(day):
    return datetime.datetime.fromisoformat(day + "T12:00:00+00:00").timestamp()


@pytest.fixture
def shorts(stats, monkeypatch):
    from studio import publish
    totals = {"90": 2_500_000, "28": 1_000_000, "engaged": True}
    inner = publish.HTTP

    def http(method, url, headers, data):
        if "creatorContentType" in url:
            p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
            assert "revenue" not in url.lower()
            if "VIDEO_ON_DEMAND" in p["filters"]:              # no long videos: 0 watch minutes
                return 200, {}, json.dumps({"columnHeaders": [{"name": "creatorContentType"}, {"name": p["metrics"]}],
                                            "rows": [["VIDEO_ON_DEMAND", totals.get("minutes", 0)]]}).encode()
            if p["metrics"] == "engagedViews" and not totals["engaged"]:
                return 400, {}, json.dumps({"error": {"errors": [{"reason": "badRequest"}]}}).encode()
            start = datetime.date.fromisoformat(p["startDate"])
            end = datetime.date.fromisoformat(p["endDate"])
            n = totals["90"] if (end - start).days > 40 else totals["28"]
            return 200, {}, json.dumps({"columnHeaders": [{"name": "creatorContentType"}, {"name": p["metrics"]}],
                                        "rows": [["SHORTS", n]]}).encode()
        return inner(method, url, headers, data)
    monkeypatch.setattr(publish, "HTTP", http)
    return totals


def test_rules_are_dated_and_match_youtubes_pages():
    """Checked 2026-11-24 against YouTube Help 13429240, 72851, 12843009 and the YouTube blog of 2026-08-10."""
    from studio import eligibility
    now = eligibility.rules(_ts("2026-11-24"))
    assert now["fan funding"] == {"all": {"subscribers": 500, "public_uploads_90d": 3},
                                  "any": [{"watch_hours_365d": 3000}, {"shorts_views_90d": 3_000_000}]}
    assert now["ad revenue"] == {"all": {"subscribers": 1000},
                                 "any": [{"watch_hours_365d": 4000}, {"shorts_views_90d": 10_000_000}]}
    later = eligibility.rules(_ts("2027-02-01"))
    assert later["ad revenue"]["any"] == [{"watch_hours_365d": 8000}, {"shorts_views_90d": 20_000_000}]
    assert later["fan funding"] == now["fan funding"]              # unchanged in 2027
    up = eligibility.upcoming(_ts("2026-11-24"))
    assert up and up[0][0] == "2027-02-01"
    assert any("already in the" in n for n in eligibility.NOTES["2027-02-01"])


def test_any_one_route_is_enough():
    from studio import eligibility
    need = eligibility.rules(_ts("2026-11-24"))["fan funding"]
    shorts = eligibility.tier_status(need, {"subscribers": 600, "public_uploads_90d": 3, "watch_hours_365d": 0,
                                            "shorts_views_90d": 3_100_000})
    assert shorts["met"] is True and shorts["share"] == 1.0
    hours = eligibility.tier_status(need, {"subscribers": 600, "public_uploads_90d": 3, "watch_hours_365d": 3200})
    assert hours["met"] is True                                    # Shorts views unknown, hours route met
    few = eligibility.tier_status(need, {"subscribers": 600, "public_uploads_90d": 2, "watch_hours_365d": 0,
                                         "shorts_views_90d": 5_000_000})
    assert few["met"] is False                                     # the 3 uploads are required whatever the route
    half = eligibility.tier_status(need, {"subscribers": 250, "public_uploads_90d": 3, "watch_hours_365d": 0,
                                          "shorts_views_90d": 1_500_000})
    assert half["met"] is False and half["share"] == 0.5


def test_progress_is_measured_and_estimates_are_labelled(stats, shorts):
    from studio import eligibility
    for n in range(1, 4):
        _published(stats, n, "A", 100, 50, 1, days_ago=10)
    rep = eligibility.check()
    assert rep["values"]["subscribers"] == 12 and rep["values"]["public_uploads_90d"] == 3
    assert rep["values"]["shorts_views_90d"] == 2_500_000
    assert "ESTIMATE" in rep["basis"]["shorts_views_90d"] and "engagedViews" in rep["basis"]["shorts_views_90d"]
    ff = rep["tiers"]["fan funding"]
    assert ff["met"] is False and ff["rules"]["public_uploads_90d"]["met"] is True
    assert "PROJECTION" in rep["projection"]["ad revenue"]["label"]
    assert rep["values"]["watch_hours_365d"] == 0 and "ESTIMATE" in rep["basis"]["watch_hours_365d"]
    t = eligibility.text()
    assert "Not yet" in t or "not yet" in t
    assert "ONE of" in t and "watch hours" in t
    assert "ESTIMATE" in t and "No revenue or AdSense data" in t
    assert eligibility.latest()["values"]["subscribers"] == 12


def test_falls_back_to_plain_views_and_says_unknown_when_offline(stats, shorts):
    from studio import eligibility
    shorts["engaged"] = False
    rep = eligibility.check()
    assert "YouTube Analytics views" in rep["basis"]["shorts_views_90d"]
    rep = eligibility.check(online=False)
    assert "shorts_views_90d" not in rep["values"]
    # 12 subscribers is already short of 1,000, so "not yet" is known even with the views unknown
    assert rep["tiers"]["ad revenue"]["met"] is False
    assert rep["tiers"]["ad revenue"]["routes"][1]["met"] is None
    assert "unknown" in eligibility.text(rep)


def test_meeting_a_tier_is_announced_once(stats, shorts, monkeypatch):
    from studio import config, eligibility, health
    monkeypatch.setattr(config, "enabled", lambda: True)
    for n in range(1, 4):
        _published(stats, n, "A", 100, 50, 1, days_ago=10)
    shorts["90"] = 3_100_000
    monkeypatch.setattr("studio.analytics.channel", lambda: {"subscriberCount": 600})
    rep = eligibility.maybe_check()
    assert rep["tiers"]["fan funding"]["met"] is True and rep["tiers"]["ad revenue"]["met"] is False
    msgs = [m for _, m in health.peek_news(10)]
    assert sum("fan funding" in m for m in msgs) == 1 and "AdSense" in msgs[0]
    assert eligibility.maybe_check() is None                    # once a day
