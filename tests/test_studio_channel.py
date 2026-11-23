"""Phase 4: channel branding and the business channel's sign-in (fake Google, fake Claude)."""
import json
import os
import stat
import time
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from studio import guard

UPLOAD, READ, ANALYTICS = guard.SCOPES


class FakeGoogle:
    def __init__(self, channel="UC_BIZ", title="Business", scopes=(UPLOAD, READ, ANALYTICS)):
        self.channel, self.title, self.scopes, self.calls = channel, title, list(scopes), []

    def __call__(self, url, headers):
        self.calls.append(url)
        if "tokeninfo" in url:
            return {"scope": " ".join(self.scopes)}
        assert headers["Authorization"] == "Bearer AT"
        p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        assert p.get("mine") == "true"
        if not self.channel:
            return {"items": []}
        return {"items": [{"id": self.channel, "snippet": {"title": self.title, "customUrl": "@biz"}, "status": {}}]}


@pytest.fixture
def google(monkeypatch):
    from clipper import youtube
    from studio import ytauth
    g = FakeGoogle()
    monkeypatch.setattr(ytauth, "OPEN", g)
    monkeypatch.setattr(youtube, "browser_code", lambda *a, **k: {"code": "c", "redirect": "r", "verifier": "v"})
    monkeypatch.setattr(youtube, "exchange", lambda ans: {"access_token": "AT", "refresh_token": "RT",
                                                          "expires_at": time.time() + 3000})
    return g


def _protect_existing():
    from clipper import db as clip_db, settings as clip_settings
    clip_settings.ensure_default_channel()
    clip_db.run("UPDATE channels SET youtube_channel_id='UC_EXISTING' WHERE id='main'")


def test_connect_locks_the_new_channel_and_keeps_tokens_private(google):
    from studio import ytauth
    _protect_existing()
    me = ytauth.connect(open_browser=False)
    assert me["id"] == "UC_BIZ" and guard.locked_channel()["youtube_channel_id"] == "UC_BIZ"
    assert stat.S_IMODE(os.stat(ytauth._path()).st_mode) == 0o600
    rep = ytauth.verify()
    assert rep["ok"] and rep["destination_ok"] and rep["can_upload"] and rep["can_read_analytics"] and not rep["extra_scopes"]


def test_connect_refuses_the_existing_channel_and_forgets_the_sign_in(google):
    from studio import ytauth
    _protect_existing()
    google.channel = "UC_EXISTING"
    with pytest.raises(guard.WrongChannel):
        ytauth.connect(open_browser=False)
    assert not ytauth.connected() and guard.locked_channel() is None


def test_account_without_a_channel_is_refused(google):
    from studio import ytauth
    google.channel = None
    with pytest.raises(guard.WrongChannel):
        ytauth.connect(open_browser=False)
    assert not ytauth.connected()


def test_verify_flags_a_changed_channel_and_extra_permissions(google):
    from studio import ytauth
    ytauth.connect(open_browser=False)
    google.channel, google.scopes = "UC_OTHER", [READ, "https://www.googleapis.com/auth/youtube.force-ssl"]
    rep = ytauth.verify()
    assert not rep["ok"] and not rep["destination_ok"] and not rep["can_upload"]
    assert rep["extra_scopes"] == ["https://www.googleapis.com/auth/youtube.force-ssl"]


def test_not_connected_is_an_auth_problem():
    from studio import jobs, ytauth
    with pytest.raises(jobs.AuthProblem):
        ytauth.access_token()
    assert ytauth.verify() == {"connected": False, "locked": None}


def test_protect_existing_by_handle(monkeypatch):
    from studio import ytauth, ytpublic
    monkeypatch.setattr(ytpublic, "get", lambda res, p: {"items": [{"id": "UC_MINE", "snippet": {"title": "Connor"}}]}
                        if p.get("forHandle") == "@connor" else {"items": []})
    assert ytauth.protect_existing("connor")["id"] == "UC_MINE"
    assert "UC_MINE" in guard.protected_channels()
    with pytest.raises(guard.WrongChannel):
        guard.lock_channel("UC_MINE", "Connor")
    with pytest.raises(ValueError):
        ytauth.protect_existing("@nobody")


@pytest.fixture
def niche_chosen():
    from studio import db
    db.put("niche_decision", {"niche": "science", "name": "Science explained", "plan": {
        "positioning": "Science that shows the actual calculation", "value": "Real numbers you can check.",
        "pillars": [{"name": "Run It Yourself", "description": "Do the maths. More text."},
                    {"name": "Real Space Data", "description": "NASA numbers."}],
        "style": "calm, clear", "avoid": ["health claims"]}})


def test_names_check_each_handle(monkeypatch, niche_chosen):
    from studio import branding, claude, ytpublic
    monkeypatch.setattr(claude, "RUNNER", lambda p, t, w: json.dumps(
        [{"name": "Show The Math", "handle": "@ShowTheMath!", "why": "says what it does"},
         {"name": "Dup", "handle": "showthemath", "why": "duplicate"},
         {"name": "Orbit Math", "handle": "orbitmath", "why": "short"}]))
    monkeypatch.setattr(ytpublic, "get", lambda res, p: {"items": [{"id": "UCx"}]} if p["forHandle"] == "@orbitmath" else {})
    out = branding.names()
    assert [(n["handle"], n["handle_status"]) for n in out] == [("ShowTheMath", "not found"), ("orbitmath", "taken")]
    assert (branding.data_dir("brand") / "names.json").exists()


def test_handle_check_without_youtube_access_is_unknown(monkeypatch):
    from studio import branding, ytpublic

    def boom(*a):
        raise RuntimeError("no key")
    monkeypatch.setattr(ytpublic, "get", boom)
    assert branding.check_handle("abc") == "unknown"


def test_branding_files(niche_chosen):
    from PIL import Image
    from studio import branding, db
    folder = branding.make("Show The Math With A Very Long Channel Name", "ShowTheMath")
    assert Image.open(folder / "logo.png").size == (800, 800)
    assert Image.open(folder / "banner.png").size == (2048, 1152)
    desc = (folder / "description.txt").read_text()
    assert "AI-generated" in desc and len(desc) <= 1000 and "Run It Yourself" in desc
    assert "#36D1FF" in (folder / "brand.md").read_text() and "health claims" in (folder / "brand.md").read_text()
    assert db.one("SELECT handle FROM channel WHERE id=1")["handle"] == "ShowTheMath"
    # everything readable sits inside YouTube's safe area: outside it there is only background and grid
    img = Image.open(folder / "banner.png").convert("RGB")
    sx, sy = (2048 - 1235) // 2, (1152 - 338) // 2
    bright = [(x, y) for x in range(0, 2048, 4) for y in range(0, 1152, 4)
              if sum(img.getpixel((x, y))) > 300 and not (sx <= x < sx + 1235 and sy <= y < sy + 338)]
    assert not bright


def test_a_wrong_reconnect_keeps_the_working_sign_in(google, monkeypatch):
    """Signing in again with the wrong account must not wipe the business channel's saved sign-in."""
    import json as _json
    from clipper import youtube
    from studio import ytauth
    ytauth.connect(open_browser=False)
    good = _json.loads(ytauth._path().read_text())["refresh_token"]
    monkeypatch.setattr(youtube, "exchange", lambda ans: {"access_token": "AT", "refresh_token": "RT2",
                                                          "expires_at": time.time() + 3000})
    google.channel = "UC_OTHER"                                   # Connor picked another account
    with pytest.raises(guard.WrongChannel):
        ytauth.connect(open_browser=False)
    assert ytauth.connected() and _json.loads(ytauth._path().read_text())["refresh_token"] == good
    assert guard.locked_channel()["youtube_channel_id"] == "UC_BIZ"
    assert not ytauth._path().with_name(ytauth._path().name + ".previous").exists()
