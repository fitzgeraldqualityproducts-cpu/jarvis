"""Best posting times (learned from the channel's own 24-hour views) and series playlists."""
import datetime
import json
import time
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401

CENTRAL = datetime.timezone(datetime.timedelta(hours=-6))


def _public(db, n, hour, views, pillar="Scale It Right"):
    day = datetime.datetime(2026, 11, 1 + n, hour, 0, tzinfo=CENTRAL)
    pub = day.timestamp()
    tid = db.insert("INSERT INTO topics(title, fingerprint, pillar, kind, status) VALUES(?,?,?,?,?)",
                    f"T{n}", f"fp{n}", pillar, "evergreen", "published")
    vid = db.insert("INSERT INTO videos(topic_id, idem_key, state, title) VALUES(?,?,?,?)", tid, f"k{n}", "published", f"S{n}")
    db.insert("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy, published_at, verified) VALUES(?,?,?,?,?,?,?)",
              vid, f"yt{n}", "UC_BIZ", f"S{n}", "public", day.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), pub)
    if views is not None:
        db.insert("INSERT INTO analytics(youtube_id, video_id, fetched, period, source, metrics, unavailable) VALUES(?,?,?,?,?,?,?)",
                  f"yt{n}", vid, pub + 24 * 3600, "lifetime", "data_api", json.dumps({"viewCount": views}), "[]")
    return tid


@pytest.fixture
def chan():
    from studio import db, guard
    guard.lock_channel("UC_BIZ", "HussUniversity")
    return db


def test_posting_time_explores_then_picks_the_best(chan):
    from studio import timing
    assert timing.hour() in timing.SLOTS                               # nothing measured: testing
    n = 0
    for h, views in ((12, 100), (17, 900), (20, 300)):
        for _ in range(3):
            n += 1
            _public(chan, n, h, views)
    res = timing.results()
    assert res[17]["measured"] == 3 and res[17]["avg_views_24h"] == 900
    picks = []
    for i in range(8):
        chan.insert("INSERT INTO publish_jobs(video_id, publish_at) VALUES(?,?)", 1000 + i, time.time() + i)
        picks.append(timing.hour())
    assert picks.count(17) >= 5 and set(picks) - {17}                   # mostly the best, still testing others
    assert "5 PM: 900 views" in timing.text()


def test_views_without_a_24h_snapshot_are_not_guessed(chan):
    from studio import timing
    _public(chan, 1, 12, None)
    assert timing.results()[12]["measured"] == 0


def test_next_slot_uses_the_learned_hour(chan, monkeypatch):
    from studio import publish, timing
    monkeypatch.setattr(timing, "hour", lambda now=None: 20)
    slot = publish.next_slot(now=datetime.datetime(2026, 12, 1, 8, tzinfo=CENTRAL).timestamp())
    assert datetime.datetime.fromtimestamp(slot, CENTRAL).hour == 20


class FakePlaylists:
    def __init__(self):
        self.posts = []

    def __call__(self, method, url, headers, data):
        assert headers["Authorization"] == "Bearer AT"
        path = url.split("/v3/")[1].split("?")[0]
        if method == "POST":
            body = json.loads(data)
            self.posts.append((path, body))
            if path == "playlists":
                return 200, {}, json.dumps({"id": f"PL{len(self.posts)}"}).encode()
            return 200, {}, json.dumps({"id": "item"}).encode()
        raise AssertionError(url)


@pytest.fixture
def lists(chan, monkeypatch):
    from clipper import youtube
    from studio import playlists, publish, ytauth
    fake = FakePlaylists()
    monkeypatch.setattr(publish, "HTTP", fake)
    monkeypatch.setattr(publish, "_destination", lambda: "UC_BIZ")
    monkeypatch.setattr(ytauth, "granted_scopes", lambda: [playlists.PLAYLIST_SCOPE])
    youtube._write_secret(ytauth._path(), {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3000})
    return fake


def test_public_shorts_go_into_their_series_once(lists):
    from studio import db, playlists
    _public(db, 1, 17, 10, "Scale It Right")
    _public(db, 2, 17, 10, "Scale It Right")
    _public(db, 3, 17, 10, "Real Space Data")
    assert playlists.sync() == 3
    made = [b for p, b in lists.posts if p == "playlists"]
    assert sorted(b["snippet"]["title"] for b in made) == ["Real Space Data", "Scale It Right"]
    assert all(b["status"]["privacyStatus"] == "public" and "AI-generated" in b["snippet"]["description"] for b in made)
    assert playlists.sync() == 0 and len(lists.posts) == 5               # nothing twice


def test_private_shorts_and_missing_permission_are_skipped(lists, monkeypatch):
    from studio import db, playlists, ytauth
    _public(db, 1, 17, 10)
    db.run("UPDATE published SET privacy='private'")
    assert playlists.sync() == 0 and not lists.posts
    db.run("UPDATE published SET privacy='public'")
    monkeypatch.setattr(ytauth, "granted_scopes", lambda: [])
    assert playlists.sync() == 0 and not lists.posts


def test_wrong_channel_stops_playlists(lists, monkeypatch):
    from studio import db, guard, playlists, publish
    _public(db, 1, 17, 10)

    def wrong():
        raise guard.WrongChannel("signed in to another channel")
    monkeypatch.setattr(publish, "_destination", wrong)
    with pytest.raises(guard.WrongChannel):
        playlists.sync()
    assert not lists.posts
