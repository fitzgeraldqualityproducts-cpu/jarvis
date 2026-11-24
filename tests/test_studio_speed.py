"""Speed: the graphics card's encoder (NVENC) with the processor as the fallback, and saved source pages."""
import gzip
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from test_studio_foundation import studio_home  # noqa: F401


# ---------- NVENC ----------

def test_encoder_is_the_card_when_it_works_and_the_processor_otherwise(monkeypatch):
    from clipper import media
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)
    a = [str(x) for x in media.h264_args(18)]
    assert a[a.index("-c:v") + 1] == "h264_nvenc" and a[a.index("-cq") + 1] == "19"
    assert "-crf" not in a
    monkeypatch.setattr(media, "_NVENC", [])
    monkeypatch.setattr(media, "NVENC_TEST", lambda: False)
    a = [str(x) for x in media.h264_args(18)]
    assert a[a.index("-c:v") + 1] == "libx264" and a[a.index("-crf") + 1] == "18"
    assert media.encoder_name(media.h264_args(18, gpu=False)) == "libx264"


def test_gpu_encoding_can_be_switched_off(monkeypatch):
    from clipper import media
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)
    monkeypatch.setenv("JARVIS_GPU_ENCODE", "0")
    assert not media.nvenc_ok()
    assert media.encoder_name(media.h264_args(20)) == "libx264"


def test_the_probe_runs_once(monkeypatch):
    from clipper import media
    calls = []
    monkeypatch.setattr(media, "NVENC_TEST", lambda: calls.append(1) or True)
    assert media.nvenc_ok() and media.nvenc_ok() and len(calls) == 1


def test_a_failed_gpu_encode_is_redone_on_the_processor(monkeypatch):
    from clipper import media
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)
    seen = []

    def fake_run(args, timeout=None, check=True):
        seen.append([str(a) for a in args])
        if "h264_nvenc" in seen[-1]:
            raise media.MediaError("ffmpeg failed: No NVENC capable devices found")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(media, "run", fake_run)
    assert media.run_h264(["-i", "a.mp4"], ["out.mp4"], 18) == "libx264"
    assert "h264_nvenc" in seen[0] and "libx264" in seen[1]
    assert not media.nvenc_ok()                                     # later encodes skip the card
    assert media.run_h264(["-i", "a.mp4"], ["out.mp4"], 18) == "libx264" and len(seen) == 3


def test_frame_writer_encodes_on_the_processor(tmp_path):
    from clipper import media
    wav = tmp_path / "a.wav"
    media.run(["-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.5", str(wav)])
    wr = media.FrameWriter(tmp_path / "o.mp4", 64, 64, 10, wav)
    assert wr.encoder == "libx264"
    for _ in range(5):
        wr.write(np.zeros((64, 64, 3), np.uint8))
    wr.close()
    assert media.probe(tmp_path / "o.mp4")["vcodec"] == "h264"


def test_a_frame_writer_on_a_missing_card_turns_the_card_off(tmp_path, monkeypatch):
    from clipper import media
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)          # says yes, but this machine has no card
    r = media.run(["-hide_banner", "-encoders"], check=False)
    if "h264_nvenc" not in (r.stdout or ""):
        pytest.skip("this ffmpeg has no NVENC encoder at all")
    wav = tmp_path / "a.wav"
    media.run(["-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "0.5", str(wav)])
    wr = media.FrameWriter(tmp_path / "o.mp4", 64, 64, 10, wav)
    assert wr.encoder == "nvenc"
    with pytest.raises(media.MediaError):
        for _ in range(5):
            wr.write(np.zeros((64, 64, 3), np.uint8))
        wr.close()
    assert not media.nvenc_ok()                                     # the job's retry uses the processor


def test_remotion_tries_the_card_then_the_processor(studio_home, monkeypatch, tmp_path):
    from clipper import media
    from studio import engines
    monkeypatch.setattr(engines, "installed", lambda t: True)
    monkeypatch.setattr(engines, "enough_memory", lambda t: (True, 8.0, 1.0))
    monkeypatch.setattr(engines, "nvidia", lambda: {"name": "RTX 2060", "vram_gb": 6})
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)
    monkeypatch.setattr(engines, "remotion_bundle", lambda env=None: None)
    out = tmp_path / "s.mp4"
    cmds = []

    def fake(cmd, **kw):
        cmds.append(cmd)
        if "--hardware-acceleration=if-possible" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "Error: NVENC session failed")
        out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(engines.subprocess, "run", fake)
    r = engines.remotion_render({"a": 1}, tmp_path, out)
    assert r["encoder"] == "libx264" and len(cmds) == 2
    assert "--video-bitrate=12M" in cmds[0] and not any("hardware" in c for c in cmds[1])
    assert not any(str(c).startswith("--crf") for c in cmds[0])     # Remotion refuses crf with the card


def test_remotion_uses_the_card_when_it_works(studio_home, monkeypatch, tmp_path):
    from clipper import media
    from studio import engines
    monkeypatch.setattr(engines, "installed", lambda t: True)
    monkeypatch.setattr(engines, "enough_memory", lambda t: (True, 8.0, 1.0))
    monkeypatch.setattr(engines, "nvidia", lambda: {"name": "RTX 2060", "vram_gb": 6})
    monkeypatch.setattr(media, "NVENC_TEST", lambda: True)
    monkeypatch.setattr(engines, "remotion_bundle", lambda env=None: None)
    out = tmp_path / "s.mp4"

    def fake(cmd, **kw):
        out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(engines.subprocess, "run", fake)
    assert engines.remotion_render({}, tmp_path, out)["encoder"] == "nvenc"
    monkeypatch.setattr(engines, "nvidia", lambda: None)            # a Mac or a PC without the card
    assert engines.remotion_render({}, tmp_path, out)["encoder"] == "libx264"


# ---------- Remotion prepared once per update ----------

@pytest.fixture
def prepared(studio_home, monkeypatch, tmp_path):
    from studio import engines
    monkeypatch.setattr(engines, "installed", lambda t: True)
    monkeypatch.setattr(engines, "enough_memory", lambda t: (True, 8.0, 1.0))
    monkeypatch.setattr(engines, "nvidia", lambda: None)
    monkeypatch.setattr(engines, "TOOLS", tmp_path / "tools")
    monkeypatch.setattr(engines, "_BUNDLE_BAD", set())
    short = tmp_path / "short"
    short.mkdir()
    (short / "scene_01.png").write_bytes(b"picture")
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        if "bundle" in cmd:
            outd = Path(next(c for c in cmd if c.startswith("--out-dir=")).split("=", 1)[1])
            (outd / "public").mkdir(parents=True)
            (outd / "index.html").write_text("<html></html>")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        entry = cmd[cmd.index("render") + 1]
        if entry != "src/index.ts":                     # the prepared copy sees the Short's pictures
            assert (Path(entry) / "public" / "scene_01.png").read_bytes() == b"picture"
        if getattr(fake, "fail_prepared", False) and entry != "src/index.ts":
            return subprocess.CompletedProcess(cmd, 1, "", "Error: could not load")
        Path(cmd[cmd.index("Short") + 1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(engines.subprocess, "run", fake)
    return engines, short, calls, fake


def test_remotion_is_built_once_and_each_render_uses_the_build(prepared, tmp_path):
    engines, short, calls, fake = prepared
    for k in range(2):
        r = engines.remotion_render({"a": k}, short, tmp_path / f"o{k}.mp4")
        assert r["prepared"] is True
    assert sum("bundle" in c for c in calls) == 1                      # built once
    renders = [c for c in calls if "render" in c]
    assert len(renders) == 2 and all(c[c.index("render") + 1] != "src/index.ts" for c in renders)
    b = engines.remotion_bundle()
    assert not (b / "public").exists() and not engines._is_link(b / "public")   # the link is taken away after
    assert (short / "scene_01.png").read_bytes() == b"picture"         # and the Short's files are untouched


def test_a_failed_prepared_render_falls_back_to_the_old_way(prepared, tmp_path):
    engines, short, calls, fake = prepared
    fake.fail_prepared = True
    r = engines.remotion_render({}, short, tmp_path / "o.mp4")
    assert r["prepared"] is False and calls[-1][calls[-1].index("render") + 1] == "src/index.ts"
    n = len(calls)
    engines.remotion_render({}, short, tmp_path / "o2.mp4")            # straight to the old way from then on
    assert len(calls) == n + 1 and calls[-1][calls[-1].index("render") + 1] == "src/index.ts"
    assert (short / "scene_01.png").exists()


def test_new_composition_code_means_a_new_build(prepared, monkeypatch, tmp_path):
    engines, short, calls, fake = prepared
    a = engines._remotion_code_hash()
    src = tmp_path / "rem"
    shutil.copytree(engines.REMOTION / "src", src / "src")
    monkeypatch.setattr(engines, "REMOTION", src)
    (src / "src" / "extra.ts").write_text("export const x = 1;")
    assert engines._remotion_code_hash() != a


def test_the_old_way_can_be_chosen(prepared, tmp_path):
    engines, short, calls, fake = prepared
    from studio import config
    config.set_values(remotion_prepared=False)
    r = engines.remotion_render({}, short, tmp_path / "o.mp4")
    assert r["prepared"] is False and not any("bundle" in c for c in calls)


# ---------- saved source pages ----------

@pytest.fixture
def pages(studio_home, monkeypatch):
    from studio import topics
    monkeypatch.setattr(topics, "PAGE", None)
    monkeypatch.setattr(topics, "_SWEPT", [])
    got = []

    def download(url, limit):
        got.append(url)
        return f"The page at {url} says the value is 42."
    monkeypatch.setattr(topics, "_download", download)
    return topics, got


def test_a_page_is_downloaded_once_and_then_read_from_disk(pages):
    topics, got = pages
    url = "https://example.org/a"
    first = topics.page_text(url)
    assert topics.page_text(url) == first and got == [url]
    f = topics._page_file(url)
    assert f.exists() and f.suffixes[-2:] == [".json", ".gz"]
    assert "pages" in str(f.parent)
    topics.page_text("https://example.org/b")
    assert got == [url, "https://example.org/b"]


def test_an_old_saved_page_is_downloaded_again(pages):
    topics, got = pages
    url = "https://example.org/old"
    topics.page_text(url)
    old = time.time() - (topics.PAGE_DAYS + 1) * 86400
    os.utime(topics._page_file(url), (old, old))
    topics.page_text(url)
    assert got == [url, url]


def test_an_empty_or_failed_page_is_not_saved(pages, monkeypatch):
    topics, got = pages
    monkeypatch.setattr(topics, "_download", lambda url, limit: got.append(url) or "")
    url = "https://example.org/blocked"
    assert topics.page_text(url) == "" and topics.page_text(url) == ""
    assert got == [url, url] and not topics._page_file(url).exists()


def test_a_damaged_saved_page_is_ignored(pages):
    topics, got = pages
    url = "https://example.org/c"
    f = topics._page_file(url)
    f.write_bytes(b"not gzip")
    assert "42" in topics.page_text(url) and got == [url]
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["url"] == url


def test_saved_pages_over_a_month_old_are_deleted(pages):
    topics, got = pages
    stale = topics._page_dir() / ("0" * 40 + ".json.gz")
    stale.write_bytes(b"x")
    old = time.time() - (topics.PAGE_KEEP_DAYS + 1) * 86400
    os.utime(stale, (old, old))
    topics.page_text("https://example.org/d")
    assert not stale.exists()


def test_the_test_hook_and_the_web_permission_still_come_first(pages, monkeypatch):
    topics, got = pages
    monkeypatch.setattr(topics, "PAGE", lambda url: "hooked")
    assert topics.page_text("https://example.org/e") == "hooked" and got == []
    assert topics.page_text("file:///etc/passwd") == ""


# ---------- where the minutes go ----------

def test_each_stage_heavy_job_and_claude_call_is_timed(studio_home, monkeypatch):
    from studio import claude, db, engines, jobs, timings
    db.conn().execute("INSERT INTO videos(title, state) VALUES('t', 'scripting')")
    vid = db.one("SELECT MAX(id) id FROM videos")["id"]
    monkeypatch.setattr(claude, "RUNNER", lambda prompt, timeout, web: "ok")

    def handler(v, ctx):
        claude.ask("hi", purpose="write: The Moon")
        with engines.heavy("AI pictures (SDXL-Turbo)"):
            time.sleep(0.05)
        return "fact_checking"
    monkeypatch.setitem(jobs.STAGES, "scripting", handler)
    assert jobs.run_one(dict(db.video(vid))) == "fact_checking"
    names = {(s["kind"], s["name"]) for s in timings.steps(vid)}
    assert {("stage", "writing"), ("claude", "Claude: write"), ("heavy", "AI pictures (SDXL-Turbo)")} <= names
    r = timings.report(vid)
    assert r["stages"][0][0] == "writing" and r["total"] >= 0.05
    assert dict(r["parts"])["AI pictures (SDXL-Turbo)"] >= 0.05
    assert timings.current() is None                                    # nothing leaks to the next job


def test_a_failed_stage_is_still_timed_and_steps_outside_a_short_are_ignored(studio_home, monkeypatch):
    from studio import db, jobs, timings
    db.conn().execute("INSERT INTO videos(title, state) VALUES('t', 'rendering')")
    vid = db.one("SELECT MAX(id) id FROM videos")["id"]

    def boom(v, ctx):
        raise RuntimeError("render broke")
    monkeypatch.setitem(jobs.STAGES, "rendering", boom)
    jobs.run_one(dict(db.video(vid)))
    st = timings.steps(vid)
    assert st and st[0]["name"] == "pictures, music and render" and st[0]["ok"] is False
    with timings.step("loose"):
        pass
    assert len(timings.steps(vid)) == 1


def test_the_summary_averages_the_last_shorts_slowest_first(studio_home):
    from studio import dashboard, timings
    assert timings.summary()["shorts"] == 0 and "no timings yet" in timings.text()
    assert dashboard._timings() is None
    for vid, (w, r) in {1: (60, 300), 2: (120, 500)}.items():
        timings.add(vid, "writing", w, "stage")
        timings.add(vid, "pictures, music and render", r, "stage")
        timings.add(vid, "Remotion render", r - 100, "heavy")
    s = timings.summary()
    assert s["shorts"] == 2 and s["stages"][0] == ("pictures, music and render", 400.0)
    assert s["total"] == 490.0 and s["parts"][0] == ("Remotion render", 300.0)
    t = timings.text()
    assert "Average of the last 2 Shorts" in t and "pictures, music and render" in t and "%" in t
    assert "Short #2" in timings.text(2)
    d = dashboard._timings()
    assert d["shorts"] == 2 and d["stages"][0][0] == "pictures, music and render"


def test_the_dashboard_page_shows_where_the_minutes_go():
    from studio import dashboard
    html = dashboard.PAGE.read_text(encoding="utf-8")
    assert 'id="timings"' in html and "Where the minutes go" in html and "d.timings" in html
