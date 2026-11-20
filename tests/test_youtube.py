"""YouTube integration against a fake Google server (no real credentials in tests): sign-in with PKCE,
token refresh, resumable upload with a server error and partial chunks, duplicate prevention, the
publishing permission gate, quota handling, status checks and analytics."""
import base64
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import make_video


class Fake:
    def __init__(self):
        self.challenge = None
        self.received = b""
        self.size = 0
        self.fail_next_put = True
        self.uploads = 0
        self.inserts = []
        self.quota_out = False
        self.refreshes = 0
        self.privacy_override = None


def server(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj, headers=None):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            if self.path.startswith("/token"):
                f = dict(urllib.parse.parse_qsl(data.decode()))
                if f["grant_type"] == "authorization_code":
                    want = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                    if f["code"] != "good-code" or want != fake.challenge:
                        return self._json(400, {"error": "invalid_grant"})
                    return self._json(200, {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
                fake.refreshes += 1
                return self._json(200, {"access_token": f"at{fake.refreshes + 1}", "expires_in": 3600})
            if self.path.startswith("/upload"):
                if fake.quota_out:
                    return self._json(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
                fake.inserts.append(json.loads(data))
                fake.size = int(self.headers["X-Upload-Content-Length"])
                fake.received = b""
                return self._json(200, {}, {"Location": f"http://127.0.0.1:{self.server.server_address[1]}/session/1"})
            self._json(404, {})

        def do_PUT(self):
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n)
            rng = self.headers.get("Content-Range", "")
            if rng.startswith("bytes */"):
                if len(fake.received) >= fake.size:
                    return self._json(200, self._video())
                self.send_response(308)
                if fake.received:
                    self.send_header("Range", f"bytes=0-{len(fake.received) - 1}")
                self.end_headers()
                return
            if fake.fail_next_put and len(fake.received) > 0:
                fake.fail_next_put = False
                return self._json(503, {"error": "busy"})
            start = int(rng.split(" ")[1].split("-")[0])
            assert start == len(fake.received), "client resent from the wrong place"
            keep = data[: max(1, len(data) // 2)] if start == 0 else data      # first piece: only half arrives
            fake.received += keep
            if len(fake.received) >= fake.size:
                fake.uploads += 1
                return self._json(200, self._video())
            self.send_response(308)
            self.send_header("Range", f"bytes=0-{len(fake.received) - 1}")
            self.end_headers()

        def _video(self):
            priv = fake.privacy_override or fake.inserts[-1]["status"]["privacyStatus"]
            return {"id": "vid12345678", "status": {"privacyStatus": priv, "uploadStatus": "uploaded"}}

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if self.headers.get("Authorization", "").split(" ")[-1] not in ("at1", "at2", "at3"):
                return self._json(401, {"error": {"errors": [{"reason": "authError"}]}})
            if u.path.endswith("/channels"):
                return self._json(200, {"items": [{"id": "UCconnor", "snippet": {"title": "Connor Clips"},
                                                   "contentDetails": {"relatedPlaylists": {"uploads": "UUconnor"}}}]})
            if u.path.endswith("/videos"):
                return self._json(200, {"items": [{"id": i, "status": {"privacyStatus": fake.privacy_override or "private",
                                                                     "uploadStatus": "processed"}} for i in q["id"].split(",")]})
            if u.path.startswith("/reports"):
                return self._json(200, {"columnHeaders": [{"name": n} for n in
                                        ["video", "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                                         "likes", "comments", "shares", "subscribersGained"]],
                                        "rows": [["vid12345678", 1200, 300.5, 25, 81.5, 90, 4, 12, 7]]})
            self._json(404, {})
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture
def yt(monkeypatch):
    from clipper import youtube
    fake = Fake()
    s = server(fake)
    base = f"http://127.0.0.1:{s.server_address[1]}"
    monkeypatch.setattr(youtube, "TOKEN_URL", base + "/token")
    monkeypatch.setattr(youtube, "API", base + "/youtube/v3")
    monkeypatch.setattr(youtube, "UPLOAD", base + "/upload")
    monkeypatch.setattr(youtube, "ANALYTICS", base + "/reports")
    monkeypatch.setattr(youtube, "CHUNK", 64 * 1024)
    monkeypatch.setattr(youtube.time, "sleep", lambda s: None)
    monkeypatch.setenv("JARVIS_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("JARVIS_GOOGLE_CLIENT_SECRET", "secret")

    def fake_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        fake.challenge = q["code_challenge"]
        assert q["code_challenge_method"] == "S256" and "youtube.upload" in q["scope"]

        def go():
            time.sleep(0.3)
            urllib.request.urlopen(f"{q['redirect_uri']}/?code=good-code&state={q['state']}", timeout=10).read()
        threading.Thread(target=go, daemon=True).start()
        return True
    monkeypatch.setattr(youtube.webbrowser, "open", fake_browser)
    yield fake
    s.shutdown()


def test_sign_in_identify_and_token_storage(yt):
    import os
    from clipper import settings, youtube
    settings.ensure_default_channel()
    assert youtube.sign_in("main", timeout=20) == "Connor Clips"
    assert settings.channel("main")["youtube_channel_id"] == "UCconnor"
    p = youtube._token_path("main")
    if os.name != "nt":
        assert oct(p.stat().st_mode)[-3:] == "600"
    tok = json.loads(p.read_text())
    tok["expires_at"] = 0
    youtube._write_secret(p, tok)
    assert youtube.access_token("main") == "at2" and yt.refreshes == 1


def _ready_clip(tmp_path, approved=True):
    from clipper import db, sources
    v, job, _ = sources.add(str(make_video(tmp_path / "s.mp4", 5)))
    path = make_video(tmp_path / "clip.mp4", 3)
    cid = db.add_clip(job_id=job["id"], video_id=v["id"], channel="main", rank=1, start=0, end=3, duration=3,
                      title="My clip", description="desc", tags=["a"], state="approved" if approved else "awaiting_approval",
                      path=str(path), qa={"passed": True}, approved=1 if approved else 0, transcript="x")
    return cid


def test_gate_blocks_until_allowed(yt, tmp_path):
    from clipper import db, publish, settings
    settings.ensure_default_channel()
    cid = _ready_clip(tmp_path)
    assert "switched off" in publish.queue(cid)                          # uploads off by default
    settings.update_channel("main", permissions={"upload_private": True})
    msg = publish.queue(cid, "public")
    assert "private" in msg and "public publishing isn't switched on" in msg
    assert db.one("SELECT privacy FROM uploads WHERE clip_id=?", cid)["privacy"] == "private"
    settings.update_channel("main", permissions={"publish_public": True})
    (tmp_path / "b").mkdir()
    unapproved = _ready_clip(tmp_path / "b", approved=False)
    assert publish.decide(db.clip(unapproved), settings.channel("main"), "public")[0] == "private"   # needs approval
    assert publish.decide(db.clip(cid), settings.channel("main"), "public")[0] == "public"
    settings.set_stopped(True)
    assert publish.decide(db.clip(cid), settings.channel("main"), "private")[0] is None


def test_resumable_upload_with_errors_then_no_duplicate(yt, tmp_path):
    from clipper import db, publish, settings, youtube
    settings.ensure_default_channel()
    youtube.sign_in("main", timeout=20)
    settings.update_channel("main", permissions={"upload_private": True})
    cid = _ready_clip(tmp_path)
    publish.queue(cid, "unlisted")
    assert publish.run_due() == 1
    c = db.clip(cid)
    assert c["youtube_id"] == "vid12345678" and c["state"] == "uploaded" and c["privacy"] == "unlisted"
    assert yt.received == open(c["path"], "rb").read()                 # every byte, in order, despite the 503
    assert yt.inserts[0]["status"]["selfDeclaredMadeForKids"] is False and yt.inserts[0]["snippet"]["title"] == "My clip"
    assert "already on YouTube" in publish.queue(cid)
    assert publish.run_due() == 0 and yt.uploads == 1


def test_quota_exceeded_defers(yt, tmp_path):
    from clipper import db, publish, settings, youtube
    settings.ensure_default_channel()
    youtube.sign_in("main", timeout=20)
    settings.update_channel("main", permissions={"upload_private": True})
    cid = _ready_clip(tmp_path)
    publish.queue(cid)
    yt.quota_out = True
    assert publish.run_due() == 0
    u = db.one("SELECT * FROM uploads WHERE clip_id=?", cid)
    assert u["status"] == "queued" and u["not_before"] > time.time() + 60
    assert db.clip(cid)["youtube_id"] is None


def test_local_quota_budget_blocks(yt, tmp_path):
    from clipper import settings, youtube
    settings.ensure_default_channel()
    youtube.sign_in("main", timeout=20)
    settings.set_global(youtube_daily_quota=youtube.quota_used() + 100)
    with pytest.raises(youtube.QuotaExceeded):
        youtube._spend("insert")


def test_public_lock_detected_and_analytics(yt, tmp_path):
    from clipper import db, learning, publish, settings, youtube
    settings.ensure_default_channel()
    youtube.sign_in("main", timeout=20)
    settings.update_channel("main", permissions={"upload_private": True, "publish_public": True})
    cid = _ready_clip(tmp_path)
    publish.queue(cid, "public")
    yt.privacy_override = "private"                                   # Google locks unaudited apps' uploads
    publish.run_due()
    c = db.clip(cid)
    assert c["privacy"] == "private" and "locked" in (c["note"] or "")
    publish.refresh_analytics()
    m = db.one("SELECT * FROM metrics WHERE clip_id=?", cid)
    assert m["views"] == 1200 and m["avg_view_pct"] == 81.5
    assert "1 published" in learning.report("main")


def test_not_connected_is_reported(tmp_path):
    from clipper import db, publish, settings
    settings.ensure_default_channel()
    settings.update_channel("main", permissions={"upload_private": True})
    cid = _ready_clip(tmp_path)
    publish.queue(cid)
    assert publish.run_due() == 0
    assert db.one("SELECT COUNT(*) n FROM events WHERE msg LIKE '%Connect YouTube%' AND told=0")["n"] == 1
