"""Moving the YouTube business to another computer (Mac -> PC): only one computer may ever run it, the
YouTube sign-in never travels, the protected channels do, and saved paths point at the new folders."""
import json
import zipfile

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def _mac_studio(tmp_path, monkeypatch):
    """A studio on 'the Mac': a locked channel, one Short on YouTube, one waiting, a sign-in token."""
    from studio import config, db, guard, handover
    monkeypatch.setattr(handover, "ROOT", tmp_path)
    monkeypatch.setattr(handover, "_desktop", lambda: tmp_path)
    config.set_values(enabled=True, autopilot=True, voice="am_michael", protected_channels=["UC_SAUCE"],
                      engines={"visuals": "manim"})
    home = str(db.path().parent)
    db.run("INSERT OR REPLACE INTO channel(id, youtube_channel_id, title) VALUES(1, 'UC_HUSS', 'HussUniversity')")
    for vid, state in ((1, "published"), (2, "ready")):
        folder = db.path().parent / "videos" / str(vid)
        folder.mkdir(parents=True)
        (folder / "short.mp4").write_bytes(b"video")
        (folder / "report.json").write_text(json.dumps({"path": f"{home}/videos/{vid}/short.mp4"}))
        db.run("INSERT INTO videos(id, state, path, meta, owner, heartbeat) VALUES(?,?,?,?,?,?)", vid, state,
               f"{home}/videos/{vid}/short.mp4", json.dumps({"voice": f"{home}/videos/{vid}/voice.wav"}),
               "old-worker", 1.0)
    sec = db.path().parent / "secrets"
    sec.mkdir(exist_ok=True)
    (sec / "youtube_business.json").write_text('{"refresh_token": "SECRET"}')
    client = tmp_path / "clipper_data" / "secrets"
    client.mkdir(parents=True)
    (client / "google_client.json").write_text('{"installed": {"client_id": "id", "client_secret": "s"}}')
    assert guard.locked_channel()["title"] == "HussUniversity"
    return home


def test_export_switches_this_computer_off_and_never_packs_the_sign_in(studio_home, monkeypatch):  # noqa: F811
    from studio import config, handover
    _mac_studio(studio_home, monkeypatch)
    assert config.enabled()
    r = handover.export(log=lambda *a: None)
    assert not config.enabled() and config.general()["autopilot"] is False and handover.handed_over()
    config.set_values(enabled=True)                      # even switched back on by hand, it stays off here
    assert not config.enabled()
    with zipfile.ZipFile(r["path"]) as z:
        names = set(z.namelist())
        blob = b"".join(z.read(n) for n in names)
        man = json.loads(z.read("manifest.json"))
    assert "studio_data/studio.db" in names and "google_client.json" in names
    assert b"SECRET" not in blob and not any(n.endswith("youtube_business.json") for n in names)
    assert "studio_data/videos/2/short.mp4" in names                  # waiting to go out: travels
    assert "studio_data/videos/1/short.mp4" not in names              # already on YouTube: stays behind
    assert "studio_data/videos/1/report.json" in names
    assert man["protected_channels"] == ["UC_SAUCE"] and man["channel"]["youtube_channel_id"] == "UC_HUSS"
    assert "enabled" not in man["settings"] and "autopilot" not in man["settings"]
    assert man["settings"]["voice"] == "am_michael"
    handover.undo()
    assert config.enabled() and config.general()["autopilot"] is False


def test_export_waits_for_a_running_stage(studio_home, monkeypatch):  # noqa: F811
    import time
    from studio import db, handover
    _mac_studio(studio_home, monkeypatch)
    db.run("UPDATE videos SET heartbeat=? WHERE id=2", time.time())
    monkeypatch.setattr(handover.time, "sleep", lambda s: None)
    with pytest.raises(handover.HandoverError, match="still ready"):
        handover.export(wait=0, log=lambda *a: None)
    assert not handover.config.enabled()                 # it stays off while it waits


def _to_pc(studio_home, monkeypatch, zpath):
    """Switches the test over to 'the PC': a fresh studio folder, Windows, an empty config."""
    from studio import config, db, handover
    pc = studio_home / "pc"
    pc.mkdir()
    monkeypatch.setenv("JARVIS_STUDIO_DATA", str(pc / "studio_data"))
    cfg = pc / "config.json"
    cfg.write_text(json.dumps({"studio": {"tick_seconds": 0}}))
    monkeypatch.setattr(config, "CONFIG", cfg)
    monkeypatch.setattr(config, "PLATFORM", "win32")
    monkeypatch.setattr(handover, "ROOT", pc)
    db.reset_connections()
    saved = []
    from clipper import youtube
    monkeypatch.setattr(youtube, "client", lambda: None)
    monkeypatch.setattr(youtube, "save_client", lambda text: saved.append(text))
    return pc, saved


def test_import_on_the_pc_repoints_paths_and_keeps_the_autopilot_off(studio_home, monkeypatch):  # noqa: F811
    from studio import config, db, guard, handover
    mac_home = _mac_studio(studio_home, monkeypatch)
    z = handover.export(log=lambda *a: None)["path"]
    pc, saved = _to_pc(studio_home, monkeypatch, z)
    assert not config.enabled()
    r = handover.import_(z, log=lambda *a: None)
    new_home = (pc / "studio_data").as_posix()
    assert r["channel"]["title"] == "HussUniversity" and r["protected"] == ["UC_SAUCE"]
    s = config.general()
    assert config.enabled() and s["allow_windows"] and s["autopilot"] is False and not s.get("handed_over")
    assert s["voice"] == "am_michael" and s["engines"] == {"visuals": "manim"}
    assert "UC_SAUCE" in guard.protected_channels() and guard.locked_channel()["youtube_channel_id"] == "UC_HUSS"
    v = db.video(2)
    assert v["path"] == f"{new_home}/videos/2/short.mp4" and v["owner"] is None and v["heartbeat"] == 0
    assert json.loads(v["meta"])["voice"] == f"{new_home}/videos/2/voice.wav"
    assert mac_home not in (pc / "studio_data" / "videos" / "2" / "report.json").read_text()
    assert (pc / "studio_data" / "videos" / "2" / "short.mp4").read_bytes() == b"video"
    assert not (pc / "studio_data" / "secrets" / "youtube_business.json").exists()   # sign in again on the PC
    assert saved and "client_id" in saved[0]


def test_import_wont_overwrite_a_studio_that_already_has_shorts(studio_home, monkeypatch):  # noqa: F811
    from studio import db, handover
    _mac_studio(studio_home, monkeypatch)
    z = handover.export(log=lambda *a: None)["path"]
    pc, _ = _to_pc(studio_home, monkeypatch, z)
    db.run("INSERT INTO videos(id, state) VALUES(9, 'ready')")
    db.reset_connections()
    with pytest.raises(handover.HandoverError, match="already has a studio"):
        handover.import_(z, log=lambda *a: None)
    handover.import_(z, replace=True, log=lambda *a: None)
    assert db.video(2) and not db.video(9)
    assert list(pc.glob("studio_data-before-handover-*"))


def test_import_refuses_a_file_with_escaping_paths(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import handover
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "from_home": "/x/studio_data", "from_root": "/x",
                                                "from_platform": "darwin", "made_at": 0}))
        z.writestr("studio_data/studio.db", b"")
        z.writestr("studio_data/../../evil.txt", b"nope")
    with pytest.raises(handover.HandoverError, match="bad path"):
        handover.import_(bad, log=lambda *a: None)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_gpu_installs_get_the_cuda_build(studio_home, monkeypatch):  # noqa: F811
    from studio import engines
    calls = []

    class P:
        returncode = 0
    monkeypatch.setattr(engines, "_uv", lambda: "uv")
    monkeypatch.setattr(engines.subprocess, "run", lambda cmd, **k: calls.append(cmd) or P())
    monkeypatch.setattr(engines, "nvidia", lambda: {"name": "NVIDIA GeForce RTX 2060", "vram_gb": 6.0})
    monkeypatch.setattr(engines.sys, "platform", "win32")
    engines.install("chatterbox", log=lambda *a: None)
    assert calls[-1][-2:] == ["--torch-backend", "cu126"] and "chatterbox-tts==0.1.7" in calls[-1]
    calls.clear()
    engines.install("manim", log=lambda *a: None)                 # Windows: from PyPI, no micromamba
    assert any("manim==0.22.1" in c for c in calls) and not any("--torch-backend" in c for c in calls)
    calls.clear()
    monkeypatch.setattr(engines, "nvidia", lambda: None)
    engines.install("whisperx", log=lambda *a: None)
    assert "--torch-backend" not in calls[-1]


def test_gpu_install_falls_back_to_the_processor_version(studio_home, monkeypatch):  # noqa: F811
    from studio import engines
    calls = []

    class P:
        def __init__(self, rc):
            self.returncode = rc

    def run(cmd, **k):
        calls.append(cmd)
        return P(1 if "--torch-backend" in cmd else 0)
    monkeypatch.setattr(engines, "_uv", lambda: "uv")
    monkeypatch.setattr(engines.subprocess, "run", run)
    monkeypatch.setattr(engines, "nvidia", lambda: {"name": "RTX", "vram_gb": 6.0})
    engines.install("whisperx", log=lambda *a: None)
    assert ["uv", "self", "update"] in calls and "--torch-backend" not in calls[-1]


def test_no_gpu_on_a_mac(monkeypatch):
    from studio import engines
    engines._GPU.clear()
    monkeypatch.setattr(engines.sys, "platform", "darwin")
    assert engines.nvidia() is None
    engines._GPU.clear()


def test_windows_node_download_is_checked_and_found(studio_home, monkeypatch, tmp_path):  # noqa: F811
    import hashlib
    import io
    from studio import engines
    name = f"node-{engines.NODE_VERSION}-win-x64"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/node.exe", b"exe")
        z.writestr(f"{name}/node_modules/npm/bin/npm-cli.js", b"")
        z.writestr(f"{name}/node_modules/npm/bin/npx-cli.js", b"")
    data = buf.getvalue()
    good = f"{hashlib.sha256(data).hexdigest()}  {name}.zip\n"
    answers = {}

    class R:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: R(data if url.endswith(".zip") else answers["sums"]))
    monkeypatch.setattr(engines, "TOOLS", tmp_path / "tools")
    monkeypatch.setattr(engines, "NODE_LOCAL", tmp_path / "tools" / "node")
    (tmp_path / "tools").mkdir()
    monkeypatch.setattr(engines.shutil, "which", lambda n: None)
    answers["sums"] = ("0" * 64 + f"  {name}.zip\n").encode()
    with pytest.raises(engines.EngineError, match="checksum"):
        engines._download_node(log=lambda *a: None)
    assert engines._node_exe() is None
    answers["sums"] = good.encode()
    node = engines._download_node(log=lambda *a: None)
    assert node == tmp_path / "tools" / "node" / "node.exe" and node.read_bytes() == b"exe"
