"""Link recognition and authorization rules."""
import pytest


def test_classify_links(tmp_path):
    from clipper import sources
    assert sources.classify("https://youtu.be/dQw4w9WgXcQ")[:2] == ("youtube", "dQw4w9WgXcQ")
    assert sources.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10")[1] == "dQw4w9WgXcQ"
    assert sources.classify("youtube.com/shorts/dQw4w9WgXcQ")[1] == "dQw4w9WgXcQ"
    assert sources.classify("https://www.twitch.tv/videos/2233445566")[:2] == ("twitch", "v2233445566")
    with pytest.raises(ValueError):
        sources.classify("https://www.youtube.com/@somechannel")
    with pytest.raises(ValueError):
        sources.classify("not a link")


def test_authorization_rules():
    from clipper import settings, sources
    settings.ensure_default_channel()
    own = {"creator": "ConnorTV", "creator_id": "connortv"}
    assert sources.authorize_check("main", "twitch", own)[0] == "authorized"
    assert sources.authorize_check("main", "twitch", {"creator_id": "someoneelse"})[0] == "awaiting_authorization"
    assert sources.authorize_check("main", "youtube", {"license": "creativeCommon", "creator_id": "UCx"}) == \
        ("authorized", "Creative Commons (CC BY): credit added")
    assert sources.authorize_check("main", "youtube", {"license": "youtube", "creator_id": "UCx"})[0] == "awaiting_authorization"
    assert sources.authorize_check("main", "youtube", {"availability": "subscriber_only"})[0] == "rejected"
    assert sources.authorize_check("main", "youtube", {"drm": True})[0] == "rejected"
    sources.add_source("creator", "ucfriend", authorized=True, basis="written permission from the creator")
    assert sources.authorize_check("main", "youtube", {"creator_id": "UCfriend"})[0] == "authorized"


def test_twitch_channel_is_an_approved_source():
    from clipper import db, settings
    settings.ensure_default_channel()
    s = db.one("SELECT * FROM sources WHERE kind='twitch_channel'")
    assert s["value"] == "connortv" and s["authorized"] == 1


def test_unauthorized_link_waits_and_grant(monkeypatch):
    from clipper import acquire, db, sources
    monkeypatch.setattr(acquire, "info", lambda url: {"title": "Someone's podcast", "creator": "Other", "creator_id": "UCother",
                                                      "license": "youtube", "duration": 3600})
    v, job, msg = sources.add("https://youtu.be/aaaaaaaaaaa")
    assert job["state"] == "awaiting_authorization" and "permission" in msg
    sources.grant(v["id"])
    assert db.job(job["id"])["state"] == "queued"


def test_unauthorized_never_downloads(tmp_path):
    from clipper import acquire
    with pytest.raises(acquire.NotPermitted):
        acquire.acquire({"auth_status": "awaiting_authorization", "platform": "youtube", "url": "x"}, tmp_path / "j")


def test_own_twitch_vod_authorized(monkeypatch):
    from clipper import acquire, sources
    monkeypatch.setattr(acquire, "info", lambda url: {"title": "Friday stream", "creator": "ConnorTV", "creator_id": "connortv",
                                                      "duration": 4 * 3600})
    v, job, msg = sources.add("https://www.twitch.tv/videos/123456789")
    assert v["auth_basis"] == "your own Twitch VOD" and job["state"] == "queued"
