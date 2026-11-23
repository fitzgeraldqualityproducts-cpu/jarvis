"""Google Drive backup: copies are checked, Mac copies are only removed when safe, restore works."""
import hashlib
import json
import time
import urllib.parse

import pytest

from test_studio_foundation import studio_home  # noqa: F401


class FakeDrive:
    """Just enough of the Drive v3 API (files, about, resumable upload) for the backup logic."""

    def __init__(self):
        self.files, self.n, self.limit, self.usage = {}, 0, 15 << 30, 0
        self.corrupt_next = False
        self.off = False
        self.uploads = 0

    def _new(self, meta, data=None):
        self.n += 1
        fid = f"f{self.n}"
        self.files[fid] = {"id": fid, "name": meta["name"], "appProperties": meta.get("appProperties", {}),
                           "parents": meta.get("parents", []), "trashed": False, "data": data,
                           "folder": meta.get("mimeType", "").endswith("folder")}
        return fid

    def _meta(self, f):
        out = {"id": f["id"], "name": f["name"], "trashed": f["trashed"], "parents": f["parents"]}
        if f["data"] is not None:
            out["size"] = str(len(f["data"]))
            out["md5Checksum"] = hashlib.md5(f["data"]).hexdigest()
        return out

    def __call__(self, method, url, headers, data):
        if self.off:
            return 403, {}, b'{"error": {"errors": [{"reason": "accessNotConfigured"}]}}'
        base, _, qs = url.partition("?")
        p = dict(urllib.parse.parse_qsl(qs))
        if url.startswith("https://upload.example/"):
            meta = self.sessions.pop(url)
            if self.corrupt_next:
                data, self.corrupt_next = data[:-1] + b"X", False
            self.uploads += 1
            self.usage += len(data)
            return 200, {}, json.dumps({"id": self._new(meta, data)}).encode()
        assert headers["Authorization"] == "Bearer AT"
        if base.endswith("/about"):
            return 200, {}, json.dumps({"user": {"emailAddress": "connor@example.com"},
                                        "storageQuota": {"limit": str(self.limit), "usage": str(self.usage)}}).encode()
        if base.startswith("https://www.googleapis.com/upload/drive/v3/files"):
            self.sessions = getattr(self, "sessions", {})
            sid = f"https://upload.example/s{len(self.sessions) + self.n}"
            self.sessions[sid] = json.loads(data)
            return 200, {"Location": sid}, b""
        if base.endswith("/files") and method == "POST":
            return 200, {}, json.dumps({"id": self._new(json.loads(data))}).encode()
        if base.endswith("/files"):
            key = p["q"].split("value='")[1].split("'")[0]
            hits = [self._meta(f) for f in self.files.values()
                    if f["appProperties"].get("jarvis") == key and not f["trashed"]]
            return 200, {}, json.dumps({"files": hits}).encode()
        fid = base.rsplit("/", 1)[1]
        f = self.files.get(fid)
        if not f:
            return 404, {}, b"{}"
        if method == "PATCH":
            f.update(json.loads(data))
            return 200, {}, b"{}"
        if p.get("alt") == "media":
            return 200, {}, f["data"]
        return 200, {}, json.dumps(self._meta(f)).encode()


@pytest.fixture
def drive_env(studio_home, monkeypatch):     # noqa: F811
    from studio import config, data_dir, db, drive
    config.set_values(enabled=True)
    fake = FakeDrive()
    monkeypatch.setattr(drive, "HTTP", fake)
    drive._save({"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 3600})
    yield fake, drive, db, data_dir
    drive.HTTP = None


def _short(db, data_dir, state="ready", big=3 << 20):
    from studio import jobs
    vid = jobs.create(idem_key=f"k{time.time()}")
    db.set_video(vid, state=state, title="Why rockets stage")
    d = data_dir("videos", str(vid))
    (d / "short.mp4").write_bytes(b"v" * big)
    (d / "voice.wav").write_bytes(b"a" * (big // 2))
    (d / "script.md").write_text("# script")
    (d / "upload.json").write_text("{}")
    (d / "scene_01.png").write_bytes(b"p" * 1000)        # not backed up, never removed
    return vid, d


def _on_youtube(db, vid, days_ago=10):
    db.set_video(vid, state="published")
    db.run("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy, published_at, verified) "
           "VALUES(?,?,?,?,?,?,?)", vid, f"yt{vid}", "UC_BIZ", "t", "public", "x", time.time() - days_ago * 86400)


def test_backs_up_finished_shorts_and_verifies(drive_env):
    fake, drive, db, data_dir = drive_env
    vid, d = _short(db, data_dir)
    _short(db, data_dir, state="rejected")                     # not backed up
    res = drive.back_up()
    assert res["copied"] == 4 and res["left"] == 0
    names = sorted(f["name"] for f in fake.files.values() if not f["folder"])
    assert names == ["script.md", "short.mp4", "upload.json", "voice.wav"]
    folders = {f["name"] for f in fake.files.values() if f["folder"]}
    assert {"Jarvis YouTube Backups", "Shorts", f"{vid} Why rockets stage"} <= folders
    assert drive.pending() == []
    assert drive.back_up()["copied"] == 0 and fake.uploads == 4          # nothing uploaded twice
    assert (d / "short.mp4").exists()                                     # ready Shorts stay on the Mac


def test_bad_copy_is_not_trusted(drive_env):
    fake, drive, db, data_dir = drive_env
    _short(db, data_dir)
    fake.corrupt_next = True
    with pytest.raises(drive.DriveError):
        drive.back_up()
    row = db.one("SELECT * FROM drive_backups WHERE name='short.mp4'")
    assert row["verified"] is None
    drive.back_up()                                                       # next run uploads it again
    assert db.one("SELECT verified FROM drive_backups WHERE name='short.mp4'")["verified"]


def test_frees_only_published_after_wait_and_recheck(drive_env):
    fake, drive, db, data_dir = drive_env
    waiting, dw = _short(db, data_dir)
    recent, dr = _short(db, data_dir)
    old, do = _short(db, data_dir)
    drive.back_up()
    _on_youtube(db, recent, days_ago=1)
    _on_youtube(db, old, days_ago=10)
    freed = drive.free_local()
    assert freed == (3 << 20) + (3 << 20) // 2
    assert not (do / "short.mp4").exists() and not (do / "voice.wav").exists()
    assert (do / "script.md").exists() and (do / "scene_01.png").exists()  # small and un-backed-up files stay
    assert (dr / "short.mp4").exists() and (dw / "short.mp4").exists()
    assert drive.free_local(urgent=True) > 0                              # low disk: recent published goes too
    assert not (dr / "short.mp4").exists()
    assert (dw / "short.mp4").exists()                                    # never one waiting for upload


def test_never_frees_when_drive_copy_is_gone(drive_env):
    fake, drive, db, data_dir = drive_env
    vid, d = _short(db, data_dir)
    drive.back_up()
    _on_youtube(db, vid)
    for f in fake.files.values():
        if f["name"] == "short.mp4":
            f["trashed"] = True                                           # Connor binned it on Drive
    drive.free_local()
    assert (d / "short.mp4").exists()
    assert ("%d/short.mp4" % vid, vid, d / "short.mp4") in drive.pending()  # it will be copied again


def test_restore_brings_files_back(drive_env):
    fake, drive, db, data_dir = drive_env
    vid, d = _short(db, data_dir)
    drive.back_up()
    _on_youtube(db, vid)
    drive.free_local()
    assert not (d / "short.mp4").exists()
    assert drive.restore(vid, say=lambda m: None) == 2
    assert (d / "short.mp4").read_bytes() == b"v" * (3 << 20)


def test_stops_before_drive_is_full(drive_env):
    fake, drive, db, data_dir = drive_env
    fake.limit, fake.usage = 15 << 30, (15 << 30) - (1 << 30)            # 1 GB left = the reserve
    _short(db, data_dir)
    res = drive.back_up()
    assert res["problem"] == "full" and fake.uploads == 0
    assert db.one("SELECT key FROM notices WHERE key='drive_full'")


def test_api_switched_off_is_explained(drive_env):
    fake, drive, db, data_dir = drive_env
    fake.off = True
    _short(db, data_dir)
    with pytest.raises(drive.DriveOff) as e:
        drive.back_up()
    assert "drive.googleapis.com" in e.value.link


def test_chore_runs_on_schedule_and_reports_and_only_one_scope(drive_env):
    fake, drive, db, data_dir = drive_env
    (data_dir("reports") / "week-1.md").write_text("report")
    now = time.time()
    assert drive.maybe_run(now)["copied"] == 1
    assert drive.maybe_run(now + 60) is None                              # not again within the half hour
    assert drive.SCOPE == "https://www.googleapis.com/auth/drive.file"
    assert "Short" in drive.say()
