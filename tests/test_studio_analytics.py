"""Phase 9: read-only analytics, honest missing numbers, learning with caps, the weekly report."""
import datetime
import json
import time
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401

DAY = 86400


class FakeStats:
    def __init__(self):
        self.videos = {}            # yid -> {"views", "privacy", "channel", "analytics": dict|None, "hold": float|None}
        self.calls = []

    def __call__(self, method, url, headers, data):
        assert method == "GET" and headers["Authorization"] == "Bearer AT"
        self.calls.append(url)
        p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        assert "revenue" not in url.lower() and "estimatedRevenue" not in url
        if url.startswith("https://youtubeanalytics.googleapis.com/v2/reports"):
            assert p["ids"] == "channel==MINE"
            v = self.videos[p["filters"].split("==")[1]]
            if p.get("dimensions") == "elapsedVideoTimeRatio":
                if v.get("hold") is None:
                    return 200, {}, json.dumps({"columnHeaders": [], "rows": []}).encode()
                rows = [[0.01, 1.0], [0.1, v["hold"]], [0.5, 0.4]]
                return 200, {}, json.dumps({"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
                                            "rows": rows}).encode()
            a = v.get("analytics")
            names = p["metrics"].split(",")
            if not a:
                return 200, {}, json.dumps({"columnHeaders": [{"name": n} for n in names], "rows": []}).encode()
            return 200, {}, json.dumps({"columnHeaders": [{"name": n} for n in names],
                                        "rows": [[a.get(n) for n in names]]}).encode()
        res = url.split("/v3/")[1].split("?")[0]
        if res == "channels":
            return 200, {}, json.dumps({"items": [{"id": "UC_BIZ", "statistics": {"subscriberCount": "12", "viewCount": "3400",
                                                                                 "videoCount": "3", "hiddenSubscriberCount": False}}]}).encode()
        if res == "videos":
            out = [{"id": y, "snippet": {"channelId": self.videos[y].get("channel", "UC_BIZ"), "publishedAt": "x"},
                    "status": {"privacyStatus": self.videos[y]["privacy"]},
                    "statistics": {"viewCount": str(self.videos[y]["views"]), "likeCount": "3"}}
                   for y in p["id"].split(",") if y in self.videos]
            return 200, {}, json.dumps({"items": out}).encode()
        raise AssertionError(url)


@pytest.fixture
def stats(monkeypatch):
    from clipper import youtube
    from studio import guard, publish, ytauth
    fake = FakeStats()
    monkeypatch.setattr(publish, "HTTP", fake)
    youtube._write_secret(ytauth._path(), {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3000})
    guard.lock_channel("UC_BIZ", "HussUniversity")
    return fake


def _iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _published(fake, n, pillar, views, pct, subs, days_ago=5, privacy="public", hold=0.8):
    from studio import db
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, status) VALUES(?,?,?,?,?)",
                    f"T{n}", f"fp{n}", pillar, "evergreen", "published")
    vid = db.insert("INSERT INTO videos(topic_id, idem_key, state, title) VALUES(?,?,?,?)", tid, f"k{n}", "published", f"Short {n}")
    db.insert("INSERT INTO scripts(video_id, version, hook, body, status) VALUES(?,?,?,?,?)", vid, 1,
              "Why 88 percent?" if n % 2 else "88 percent of a rocket.", "b", "passed")
    yid = f"yt{n}"
    db.insert("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy, published_at, verified) "
              "VALUES(?,?,?,?,?,?,?)", vid, yid, "UC_BIZ", f"Short {n}", privacy, _iso(time.time() - days_ago * DAY), time.time())
    fake.videos[yid] = {"views": views, "privacy": privacy, "hold": hold,
                        "analytics": {"views": views, "estimatedMinutesWatched": views * 0.4, "averageViewDuration": 30,
                                      "averageViewPercentage": pct, "likes": 3, "shares": 1,
                                      "subscribersGained": subs, "subscribersLost": 0}}
    return tid, yid


def test_fetch_reads_real_numbers_and_never_revenue(stats):
    from studio import analytics
    _published(stats, 1, "Scale It Right", 1000, 80.0, 5)
    assert analytics.fetch() == 1
    a = analytics.latest("yt1", "analytics")
    assert a["metrics"]["views"] == 1000 and a["metrics"]["hook_hold"] == 0.8 and a["unavailable"] == []
    assert analytics.latest("yt1", "data_api")["metrics"]["viewCount"] == 1000
    assert analytics.channel()["subscriberCount"] == 12


def test_lagging_analytics_are_recorded_as_missing_not_zero(stats):
    from studio import analytics
    _published(stats, 1, "Scale It Right", 40, 0, 0, days_ago=0)
    stats.videos["yt1"]["analytics"] = None
    analytics.fetch()
    a = analytics.latest("yt1", "analytics")
    assert a["metrics"] == {} and "views" in a["unavailable"] and "averageViewPercentage" in a["unavailable"]
    text = analytics.report(save=False)
    assert "not available yet" in text and "Revenue is never read" in text


def test_private_videos_get_no_analytics_and_wrong_channel_stops(stats):
    from studio import analytics, guard
    _published(stats, 1, "Scale It Right", 0, 0, 0, privacy="private")
    analytics.fetch()
    assert analytics.latest("yt1", "analytics") is None
    assert not any("youtubeanalytics" in u for u in stats.calls)
    stats.videos["yt1"]["channel"] = "UC_OTHER"
    with pytest.raises(guard.WrongChannel):
        analytics.fetch()


def test_privacy_change_is_picked_up(stats):
    from studio import analytics, db
    _published(stats, 1, "Scale It Right", 10, 50, 0, privacy="private")
    stats.videos["yt1"]["privacy"] = "public"
    analytics.fetch()
    assert db.one("SELECT privacy FROM published WHERE youtube_id='yt1'")["privacy"] == "public"


def test_index_is_relative_and_needs_three_days(stats):
    from studio import analytics
    _published(stats, 1, "A", 1000, 80.0, 5)
    _published(stats, 2, "A", 1000, 80.0, 5)
    _published(stats, 3, "A", 4000, 90.0, 20)
    _published(stats, 4, "B", 9999, 99.0, 99, days_ago=1)        # too new to judge
    analytics.fetch()
    idx = analytics.index_all()
    assert set(idx) == {"yt1", "yt2", "yt3"}
    assert idx["yt1"]["index"] == 50.0 and idx["yt3"]["index"] > 50


def test_learning_nudges_are_capped_and_need_enough_videos(stats):
    from studio import analytics, db, topics
    for n in range(1, 4):
        _published(stats, n, "Winners", 5000, 90.0, 30)
    for n in range(4, 7):
        _published(stats, n, "Losers", 100, 30.0, 0)
    _published(stats, 7, "Lonely", 99999, 99.0, 500)                 # one video: no nudge yet
    analytics.fetch()
    res = analytics.learn()
    nud = res["nudges"]
    assert set(nud) == {"Winners", "Losers"}
    assert 0 < nud["Winners"] <= analytics.MAX_NUDGE and -analytics.MAX_NUDGE <= nud["Losers"] < 0
    assert analytics.nudge("Lonely") == 0.0
    perf = json.loads(topics.topic(1)["performance"])
    assert perf["index"] and perf["youtube_id"] == "yt1"
    assert db.one("SELECT decision FROM decisions WHERE area='learning'")
    assert "Winners" in analytics.hint() and "Lonely" not in analytics.hint()


def test_pick_next_uses_the_learned_nudge(stats, monkeypatch):
    from studio import analytics, db, topics
    db.put("studio_pillar_nudges", {"Good": 6.0, "Bad": -6.0})
    monkeypatch.setattr(topics, "pool", lambda now=None: [
        {"id": 1, "title": "x", "pillar": "Bad", "kind": "evergreen", "score": 72.0, "fresh_until": None},
        {"id": 2, "title": "y", "pillar": "Good", "kind": "evergreen", "score": 64.0, "fresh_until": None}])
    assert topics.pick_next(mark=False)["id"] == 2
    db.put("studio_pillar_nudges", {})
    assert topics.pick_next(mark=False)["id"] == 1


def test_background_chore_is_cheap_to_skip_and_reports_weekly(stats, monkeypatch):
    from studio import analytics, config, db, health
    assert analytics.maybe_run() is None                               # studio off
    monkeypatch.setattr(config, "enabled", lambda: True)
    assert analytics.maybe_run() is None                               # nothing published yet
    _published(stats, 1, "A", 500, 70.0, 2)
    r = analytics.maybe_run()
    assert r["videos"] == 1
    assert analytics.maybe_run() is None                               # not again for a few hours
    news = health.peek_news()
    assert news and "weekly YouTube business report" in news[0][1]
    health.mark_told([i for i, _ in news])
    assert health.peek_news() == []
    from studio import data_dir
    assert list(data_dir("reports").glob("week-*.md"))
