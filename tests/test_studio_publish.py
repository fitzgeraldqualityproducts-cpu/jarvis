"""Phase 8: idempotent, resumable uploads to the locked channel; verification; queue; pace."""
import json
import os
import time
import urllib.error
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401


class FakeYouTube:
    """Just enough of YouTube's upload and Data API to test the publishing logic."""

    def __init__(self, channel="UC_BIZ"):
        self.channel = channel
        self.sessions, self.videos, self.posts = {}, {}, []
        self.drop_final = False          # the last piece goes through but the answer is lost
        self.expire_after_drop = False
        self.status = "processed"
        self.privacy = None
        self.thumbs, self.thumb_status = [], 200

    # ytauth.OPEN (whoami)
    def open(self, url, headers):
        return {"items": [{"id": self.channel, "snippet": {"title": "HussUniversity", "customUrl": "@hussuniversityfr"}}]}

    def __call__(self, method, url, headers, data):
        assert headers["Authorization"] == "Bearer AT"
        if method == "POST" and "thumbnails/set" in url:
            self.thumbs.append((url, data))
            return (self.thumb_status, {}, b"{}" if self.thumb_status == 200 else
                    json.dumps({"error": {"errors": [{"reason": "forbidden"}]}}).encode())
        if method == "POST":
            meta = json.loads(data)
            self.posts.append(meta)
            sid = f"https://upload.example/s{len(self.posts)}"
            self.sessions[sid] = {"size": int(headers["X-Upload-Content-Length"]), "got": b"", "meta": meta, "alive": True}
            return 200, {"Location": sid}, b""
        if method == "PUT":
            s = self.sessions[url]
            if not s["alive"]:
                return 404, {}, b""
            rng = headers["Content-Range"]
            if rng.startswith("bytes */"):
                if s.get("done"):
                    return 200, {}, json.dumps({"id": s["done"]}).encode()
                return 308, ({"Range": f"bytes=0-{len(s['got']) - 1}"} if s["got"] else {}), b""
            start = int(rng.split(" ")[1].split("-")[0])
            assert start == len(s["got"])                      # never sends a byte twice or skips one
            s["got"] += data
            if len(s["got"]) < s["size"]:
                return 308, {"Range": f"bytes=0-{len(s['got']) - 1}"}, b""
            vid = f"yt{len(self.videos) + 1}"
            s["done"] = vid
            self.videos[vid] = {"meta": s["meta"], "channel": self.channel}
            if self.drop_final:
                self.drop_final = False
                if self.expire_after_drop:
                    s["alive"] = False
                raise urllib.error.URLError("connection reset")
            return 200, {}, json.dumps({"id": vid}).encode()
        p = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        res = url.split("/v3/")[1].split("?")[0]
        if res == "channels":
            return 200, {}, json.dumps({"items": [{"id": self.channel, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}).encode()
        if res == "playlistItems":
            return 200, {}, json.dumps({"items": [{"contentDetails": {"videoId": v}} for v in self.videos]}).encode()
        if res == "videos":
            out = []
            for vid in p["id"].split(","):
                v = self.videos.get(vid)
                if not v:
                    continue
                st = v["meta"]["status"]
                out.append({"id": vid, "snippet": {"channelId": v["channel"], "title": v["meta"]["snippet"]["title"],
                                                   "tags": v["meta"]["snippet"]["tags"], "publishedAt": "2026-11-23T00:00:00Z"},
                            "status": {"uploadStatus": "processed" if self.status == "processed" else "uploaded",
                                       "privacyStatus": self.privacy or st["privacyStatus"], "publishAt": st.get("publishAt")},
                            "processingDetails": {"processingStatus": "succeeded" if self.status == "processed" else "processing"}})
            return 200, {}, json.dumps({"items": out}).encode()
        raise AssertionError(url)


@pytest.fixture
def yt(monkeypatch, tmp_path):
    from clipper import youtube
    from studio import data_dir, db, guard, publish, ytauth
    fake = FakeYouTube()
    monkeypatch.setattr(publish, "HTTP", fake)
    monkeypatch.setattr(publish, "CHUNK", 256 * 1024)
    monkeypatch.setattr(ytauth, "OPEN", fake.open)
    youtube._write_secret(ytauth._path(), {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3000})
    guard.lock_channel("UC_BIZ", "HussUniversity")
    return fake


def _finished_video(db, passed=True):
    from studio import data_dir, jobs
    vid = jobs.create(idem_key=f"k{time.time()}", title="Why rockets throw parts away")
    path = data_dir("videos", str(vid)) / "short.mp4"
    path.write_bytes(os.urandom(700 * 1024))
    up = {"title": "Why rockets throw parts away", "description": "Sources:\n- https://nasa.gov\n\nAI-generated narration.",
          "tags": ["rocket"], "categoryId": "27", "madeForKids": False, "containsSyntheticMedia": False}
    db.set_video(vid, path=str(path), state="ready", meta=json.dumps({"upload": up}))
    db.insert("INSERT INTO qa_checks(video_id, check_name, passed, detail, t) VALUES(?,?,?,?,?)", vid, "all", int(passed), "", 0)
    return vid


def test_upload_is_private_resumable_and_tagged(yt):
    from clipper import youtube
    from studio import db, publish
    vid = _finished_video(db)
    yid = publish.upload(vid, "private")
    assert yid == "yt1" and len(yt.posts) == 1
    meta = yt.posts[0]
    assert meta["status"]["privacyStatus"] == "private" and publish.tag(vid) in meta["snippet"]["tags"]
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    j = publish.job(vid)
    assert j["youtube_id"] == "yt1" and j["channel_id"] == "UC_BIZ" and j["session_url"]
    assert youtube.quota_used("uploads") == 1
    assert publish.upload(vid, "private") == "yt1" and len(yt.posts) == 1          # never twice


def test_interrupted_upload_resumes_the_same_session(yt):
    from studio import db, jobs, publish
    vid = _finished_video(db)
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1
    with pytest.raises(jobs.Interrupted):
        publish.upload(vid, "private", stop=stop)
    got = len(next(iter(yt.sessions.values()))["got"])
    assert 0 < got < 700 * 1024
    assert publish.upload(vid, "private") == "yt1" and len(yt.posts) == 1


def test_lost_answer_is_reconciled_not_repeated(yt):
    from studio import db, publish
    vid = _finished_video(db)
    yt.drop_final, yt.expire_after_drop = True, True
    yid = publish.upload(vid, "private")
    assert yid == "yt1" and len(yt.posts) == 1 and len(yt.videos) == 1


def test_wrong_channel_or_unchecked_video_never_uploads(yt):
    from studio import db, guard, jobs, publish
    bad = _finished_video(db, passed=False)
    with pytest.raises(jobs.Permanent):
        publish.upload(bad, "private")
    vid = _finished_video(db)
    yt.channel = "UC_CONNORS_OWN"
    with pytest.raises(guard.WrongChannel):
        publish.upload(vid, "private")
    assert yt.posts == []


def test_verify_states(yt):
    from studio import db, publish
    vid = _finished_video(db)
    publish.upload(vid, "private")
    yt.status = "processing"
    assert publish.verify(vid)["state"] == "processing"
    yt.status = "processed"
    assert publish.verify(vid)["state"] == "private"
    yt.privacy = "public"
    assert publish.verify(vid)["state"] == "published"
    assert db.one("SELECT privacy FROM published WHERE video_id=?", vid)["privacy"] == "public"


def test_worker_waits_in_manual_and_uploads_in_private_mode(yt):
    from studio import config, db, jobs, pipeline
    config.set_values(enabled=True)
    pipeline.register()
    vid = _finished_video(db)
    assert jobs.tick() == "ready"                                   # manual: waits, not an error
    v = db.video(vid)
    assert v["not_before"] > time.time() + 5 * 3600 and v["error"] is None
    assert db.one("SELECT COUNT(*) n FROM errors")["n"] == 0
    config.set_values(publish_mode="private")
    db.set_video(vid, not_before=0)
    assert [jobs.tick() for _ in range(3)] == ["uploading", "processing", "published"]
    assert yt.posts[0]["status"]["privacyStatus"] == "private" and "publishAt" not in yt.posts[0]["status"]


def test_public_mode_schedules_with_publish_at(yt):
    from studio import config, db, jobs, pipeline, publish
    config.set_values(enabled=True, publish_mode="public")
    pipeline.register()
    vid = _finished_video(db)
    states = [jobs.tick() for _ in range(3)]
    assert states == ["uploading", "processing", "scheduled"]
    st = yt.posts[0]["status"]
    assert st["privacyStatus"] == "private" and st["publishAt"].endswith("Z")
    yt.privacy = "public"
    db.set_video(vid, not_before=0)
    assert jobs.tick() == "published"


def test_pace_spacing_and_weekly_cap():
    from studio import db, publish
    now = 1_800_000_000.0
    slots = []
    for i in range(5):
        s = publish.next_slot(now)
        slots.append(s)
        db.insert("INSERT INTO publish_jobs(video_id, publish_at) VALUES(?,?)", 100 + i, str(s))
    gaps = [b - a for a, b in zip(slots, slots[1:])]
    assert all(g >= 40 * 3600 for g in gaps)
    assert all(sum(1 for x in slots if s - 7 * 86400 < x <= s) <= 3 for s in slots)


def test_public_mode_needs_typed_confirmation(monkeypatch):
    from studio import __main__ as cli, publish
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    cli._publish_mode(["publish-mode", "public"])
    assert publish.mode() == "manual"
    monkeypatch.setattr("builtins.input", lambda *a: "PUBLIC")
    cli._publish_mode(["publish-mode", "public"])
    assert publish.mode() == "public"



def _with_cover(db, vid):
    from studio import data_dir
    p = data_dir("videos", str(vid)) / "cover.jpg"
    p.write_bytes(b"\xff\xd8jpeg")
    meta = json.loads(db.video(vid)["meta"])
    meta["cover"] = {"path": str(p)}
    db.set_video(vid, meta=json.dumps(meta))


def test_cover_goes_up_once_after_the_upload(yt):
    from studio import db, publish
    vid = _finished_video(db)
    _with_cover(db, vid)
    yid = publish.upload(vid, "private")
    assert len(yt.thumbs) == 1 and f"videoId={yid}" in yt.thumbs[0][0] and yt.thumbs[0][1] == b"\xff\xd8jpeg"
    assert json.loads(db.video(vid)["meta"])["cover"]["youtube"]["ok"] is True
    publish.upload(vid, "private")
    assert len(yt.thumbs) == 1                                   # never sent twice


def test_cover_refusal_never_fails_the_upload(yt):
    from studio import db, publish
    vid = _finished_video(db)
    _with_cover(db, vid)
    yt.thumb_status = 403
    assert publish.upload(vid, "private") == "yt1"
    res = json.loads(db.video(vid)["meta"])["cover"]["youtube"]
    assert res["ok"] is False and "verif" in res["hint"]
    for _ in range(5):
        publish.upload(vid, "private")
    assert len(yt.thumbs) == 3                                   # gives up after three tries


def test_no_cover_no_thumbnail_call(yt):
    from studio import db, publish
    vid = _finished_video(db)
    publish.upload(vid, "private")
    assert yt.thumbs == []
