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


def test_rules_are_dated():
    from studio import eligibility
    assert eligibility.rules(_ts("2026-11-23"))["ad revenue"]["shorts_views_90d"] == 10_000_000
    assert eligibility.rules(_ts("2027-02-01"))["ad revenue"]["shorts_views_90d"] == 20_000_000
    assert eligibility.upcoming(_ts("2026-11-23"))


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
    t = eligibility.text()
    assert "Not yet" in t or "not yet" in t
    assert "ESTIMATE" in t and "No revenue or AdSense data" in t
    assert eligibility.latest()["values"]["subscribers"] == 12


def test_falls_back_to_plain_views_and_says_unknown_when_offline(stats, shorts):
    from studio import eligibility
    shorts["engaged"] = False
    rep = eligibility.check()
    assert "YouTube Analytics views" in rep["basis"]["shorts_views_90d"]
    rep = eligibility.check(online=False)
    assert "shorts_views_90d" not in rep["values"]
    assert rep["tiers"]["ad revenue"]["met"] is None
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
