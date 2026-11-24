"""Learning from real viewers: the retention curve laid over each Short's lines, leaks become lessons, the writer is
told only when 3+ Shorts agree, Shorts without a curve are listed honestly, and the quality score is checked
against real results."""
import json
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_analytics import DAY, _published, stats  # noqa: F401

BEATS = ["hook", "setup", "math", "rehook", "payoff", "loop"]
STARTS = [0.0, 3.0, 8.0, 14.0, 17.0, 23.0, 30.0]          # 6 lines, 30 s


def _timed(vid, quality=None):
    from studio import db
    meta = {"render": {"duration": 30.0, "line_starts": STARTS}}
    if quality is not None:
        meta["quality"] = {"score": quality, "parts": {"hook": quality / 100, "loop": 0.5}}
    db.set_video(vid, meta=json.dumps(meta))
    db.run("UPDATE scripts SET structure=?, hook=? WHERE video_id=?", json.dumps(
        {"lines": [{"text": f"line {i + 1}", "beat": b} for i, b in enumerate(BEATS)]}), "Why 88 percent?", vid)


def _curve_with_drop_in(span, fake, yid):
    """A curve that falls slowly everywhere and fast inside span (seconds)."""
    pts, w = [], 1.0
    for k in range(0, 101):
        x = k / 100
        t = 30 * x
        pts.append([round(x, 2), round(w, 4)])
        w -= 0.03 if span[0] <= t < span[1] else 0.004
    fake.videos[yid]["curve"] = pts


@pytest.fixture
def curves(stats, monkeypatch):  # noqa: F811
    orig = stats.__call__

    def call(method, url, headers, data):
        import urllib.parse
        p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1])) if "?" in url else {}
        if p.get("dimensions") == "elapsedVideoTimeRatio":
            assert "relativeRetentionPerformance" in p["metrics"] and "revenue" not in url.lower()
            v = stats.videos[p["filters"].split("==")[1]]
            rows = v.get("curve")
            if rows is None:
                return 200, {}, json.dumps({"columnHeaders": [], "rows": []}).encode()
            return 200, {}, json.dumps({"columnHeaders": [{"name": "elapsedVideoTimeRatio"},
                                                          {"name": "audienceWatchRatio"},
                                                          {"name": "relativeRetentionPerformance"}],
                                        "rows": [r + [0.5] for r in rows]}).encode()
        return orig(method, url, headers, data)
    from studio import publish
    monkeypatch.setattr(publish, "HTTP", call)
    return stats


def _short(fake, n, span=(8.0, 14.0), quality=None, curve=True):
    from studio import db
    tid, yid = _published(fake, n, "Space", 1000 * n, 60.0 + n, n)
    vid = db.one("SELECT video_id FROM published WHERE youtube_id=?", yid)["video_id"]
    _timed(vid, quality)
    if curve:
        _curve_with_drop_in(span, fake, yid)
    return vid, yid


def test_the_line_where_viewers_left_is_found_and_learned_once(curves):
    from studio import analytics, db, lessons, retention
    vid, yid = _short(curves, 1)
    analytics.fetch()
    assert analytics.latest(yid, "retention")["metrics"]["curve"][0][0] == 0.0
    r = analytics.learn()
    assert r["retention"] == 1
    rows = db.get(f"retention:{vid}")["lines"]
    leaks = [x for x in rows if x["leak"]]
    assert [x["beat"] for x in leaks] == ["math"] and leaks[0]["lost"] > 0.1
    assert rows[0]["text"] == "Why 88 percent?"                       # the hook as it was said
    n = db.one("SELECT COUNT(*) n FROM lessons WHERE stage='retention'")["n"]
    assert n == 1 and "math line" in db.one("SELECT detail FROM lessons WHERE stage='retention'")["detail"]
    analytics.learn()
    assert db.one("SELECT COUNT(*) n FROM lessons WHERE stage='retention'")["n"] == 1   # once per Short
    assert retention.advice() == ""                                    # one Short isn't enough to tell the writer
    assert "real-viewer" not in lessons.advice("script")
    assert "line 3 (calculation) lost" in retention.text()


def test_the_writer_is_told_once_three_shorts_agree(curves):
    from studio import analytics, lessons, retention
    for n in (1, 2, 3):
        _short(curves, n)
    analytics.fetch()
    analytics.learn()
    adv = retention.advice()
    assert "calculation lines (3 of 3 Shorts)" in adv
    assert adv in lessons.advice("script")
    assert "stopped work" not in lessons.summary() and "Real viewers left fastest" in lessons.summary()


def test_shorts_without_enough_views_or_timings_are_listed_not_guessed(curves):
    from studio import analytics, db, retention
    v1, _ = _short(curves, 1, curve=False)
    v2, y2 = _short(curves, 2)
    db.set_video(v2, meta=json.dumps({"render": {"duration": 30.0}}))        # made before line timings were saved
    analytics.fetch()
    r = analytics.learn()
    assert r["retention"] == 0
    t = retention.text()
    assert f"Not enough views yet for YouTube to show a retention curve: Short(s) #{v1}" in t
    assert f"before line timings were saved (can't be laid over the curve): Short(s) #{v2}" in t
    assert db.one("SELECT COUNT(*) n FROM lessons WHERE stage='retention'")["n"] == 0


def test_the_quality_score_is_checked_against_real_results(curves):
    from studio import analytics, retention
    for n in range(1, 4):
        _short(curves, n, quality=40 + 10 * n)
    analytics.fetch()
    analytics.learn()
    t = retention.score_text()
    assert "predicted 50, real index" in t and "needs 5+ measured Shorts (have 3)" in t
    for n in range(4, 7):
        _short(curves, n, quality=40 + 10 * n)
    analytics.fetch()
    s = analytics.learn()
    assert s["score_review"] == 6
    t = retention.score_text()
    assert "The score ranks real results well" in t and "Parts that predicted results: hook" in t


def test_rank_correlation():
    from studio import retention
    assert retention.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert retention.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert retention.spearman([1, 2], [1, 2]) is None
    assert retention.spearman([1, 1, 1], [1, 2, 3]) is None


def test_the_weekly_report_has_both_sections(curves):
    from studio import analytics
    _short(curves, 1)
    analytics.fetch()
    analytics.learn()
    rep = analytics.report(save=False)
    assert "## Where viewers left" in rep and "## Does the quality score predict real results?" in rep


def test_line_starts_are_saved_with_every_render():
    import inspect
    from studio import video
    assert '"line_starts"' in inspect.getsource(video.render)
