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


def test_a_link_you_add_starts_right_away(monkeypatch):
    from clipper import acquire, db, sources
    monkeypatch.setattr(acquire, "info", lambda url: {"title": "Someone's podcast", "creator": "Other", "creator_id": "UCother",
                                                      "license": "youtube", "duration": 3600})
    v, job, msg = sources.add("https://youtu.be/aaaaaaaaaaa")
    assert job["state"] == "queued" and v["auth_basis"] == "a link you added yourself" and "queued" in msg


def test_found_videos_still_wait_and_grant(monkeypatch):
    from clipper import acquire, db, sources
    monkeypatch.setattr(acquire, "info", lambda url: {"title": "Someone's podcast", "creator": "Other", "creator_id": "UCother",
                                                      "license": "youtube", "duration": 3600})
    v, job, msg = sources.add("https://youtu.be/aaaaaaaaaaa", added_by_owner=False)
    assert job["state"] == "awaiting_authorization" and "permission" in msg
    v2, job2, msg2 = sources.add("https://youtu.be/aaaaaaaaaaa")   # pasting it yourself approves it
    assert job2["id"] == job["id"] and job2["state"] == "queued"
    assert sources.authorize_check("main", "youtube", {"creator_id": "UCx"}, added_by_owner=True)[0] == "authorized"
    assert sources.authorize_check("main", "youtube", {"drm": True}, added_by_owner=True)[0] == "rejected"


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


def test_retrying_a_project_that_failed_for_permission(monkeypatch):
    """Projects made before links started right away failed with "not authorized"; Retry fixes them."""
    from clipper import acquire, db, jobs, sources
    monkeypatch.setattr(acquire, "info", lambda url: {"title": "I Flew Overseas", "creator": "Other", "creator_id": "UCother",
                                                      "license": "youtube", "duration": 6 * 3600})
    v, job, msg = sources.add("https://youtu.be/bbbbbbbbbbb", added_by_owner=False)
    db.set_job(job["id"], state="failed", error="not authorized: not yours, not Creative Commons, and no permission recorded")
    assert "back in the queue" in jobs.retry(job["id"])
    assert db.job(job["id"])["state"] == "queued"
    assert db.video(v["id"])["auth_status"] == "authorized"
    found = db.run("INSERT INTO videos(channel, platform, external_id, url, title, auth_status, auth_basis, source_id, "
                   "discovered) VALUES('main','youtube','x1','u','found','awaiting_authorization','not yours',1,0)")
    j2 = sources.make_job(db.video(found))
    db.set_job(j2["id"], state="failed")
    jobs.retry(j2["id"])
    assert db.video(found)["auth_status"] == "awaiting_authorization"    # found by a source: still needs your OK
