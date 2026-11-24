"""Free stock footage without Pexels: Pixabay (free key) and the NASA Image and Video Library (no key)."""
import json

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def _clip(out, size, secs=12):
    from clipper import media
    media.run(["-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=25:d={secs}", "-c:v", "libx264", "-pix_fmt",
               "yuv420p", str(out)])


NASA_SEARCH = {"collection": {"items": [
    {"href": "https://images-assets.nasa.gov/video/talk/collection.json",
     "data": [{"nasa_id": "talk", "media_type": "video", "title": "Artemis briefing", "description": "Live coverage"}]},
    {"href": "https://images-assets.nasa.gov/video/esa1/collection.json",
     "data": [{"nasa_id": "esa1", "media_type": "video", "title": "Comet flyby", "description": "Credit: ESA, © 2020"}]},
    {"href": "https://images-assets.nasa.gov/video/short1/collection.json",
     "data": [{"nasa_id": "short1", "media_type": "video", "title": "Rocket launch", "description": "B-roll"}]},
    {"href": "https://images-assets.nasa.gov/video/good1/collection.json",
     "data": [{"nasa_id": "good1", "media_type": "video", "title": "SLS rocket launch B-roll",
               "description": "Footage of the Space Launch System lifting off; delivered to the press site"}]}]}}


def _fake_nasa(monkeypatch, calls):
    from studio import footage

    def get(url):
        calls.append(url)
        if url.startswith(footage.NASA_API):
            return NASA_SEARCH
        nid = url.split("/video/")[1].split("/")[0]
        if url.endswith("collection.json"):
            base = f"http://images-assets.nasa.gov/video/{nid}/{nid}"
            return [base + "~orig.mp4", base + "~large.mp4", base + "~thumb.jpg",
                    f"http://images-assets.nasa.gov/video/{nid}/metadata.json"]
        if url.endswith("metadata.json"):
            return {"QuickTime:Duration": "2.5 s" if nid == "short1" else "0:00:12", "File:FileType": "MP4"}
        raise AssertionError(url)

    def download(url, out):
        calls.append(url)
        if url.endswith("~orig.mp4"):
            raise ValueError("file too big")             # the original is skipped, the large copy is used
        _clip(out, "1920x1080")
    monkeypatch.setattr(footage, "NASA_GET", get)
    monkeypatch.setattr(footage, "DOWNLOAD", download)


def test_pixabay_key_is_told_apart_and_choice_prefers_sharp_portrait(studio_home):  # noqa: F811
    from studio import footage
    assert footage.site_of("12345678-0123456789abcdef0123456789ab") == "pixabay"
    assert footage.site_of("A" * 56) == "pexels" and footage.site_of("not a key") is None
    footage.save_key("12345678-0123456789abcdef0123456789ab")
    assert footage.sources() == ["pixabay", "nasa"] and footage.key("pixabay").startswith("12345678-")
    hit = lambda i, dur, files: {"id": i, "duration": dur, "user": "Mo", "pageURL": f"https://pixabay.com/videos/{i}/",
                                 "videos": files}
    ans = {"hits": [
        hit(1, 3, {"large": {"url": "https://cdn/1.mp4", "width": 1080, "height": 1920}}),           # too short
        hit(2, 20, {"large": {"url": "https://cdn/2l.mp4", "width": 3840, "height": 2160},
                    "medium": {"url": "https://cdn/2m.mp4", "width": 1920, "height": 1080},
                    "small": {"url": "https://cdn/2s.mp4", "width": 1280, "height": 720}}),
        hit(3, 20, {"large": {"url": "", "width": 0, "height": 0},
                    "medium": {"url": "https://cdn/3m.mp4", "width": 1080, "height": 1920}})]}
    best = footage.choose_pixabay(ans, 5)
    assert best["url"] == "https://cdn/3m.mp4" and best["id"] == "pixabay_3" and best["credit"] == "Video by Mo on Pixabay"
    assert footage.choose_pixabay(ans, 5, used={"pixabay_3"})["url"] == "https://cdn/2l.mp4"   # 4K crops sharply
    ans["hits"][1]["videos"].pop("large")
    assert footage.choose_pixabay(ans, 5, used={"pixabay_3"})["url"] == "https://cdn/2m.mp4"   # 1080p: cropped and sharpened


def test_nasa_needs_no_key_skips_talks_and_others_material_and_fills_the_screen(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import footage
    calls = []
    _fake_nasa(monkeypatch, calls)
    assert footage.ready() and footage.sources() == ["nasa"]
    assert [c["nasa_id"] for c in footage.nasa_candidates(NASA_SEARCH)] == ["short1", "good1"]
    assert footage._nasa_seconds({"QuickTime:Duration": "0:01:05"}) == 65.0
    out, info = footage.fetch("rocket launch", 5.0)
    assert out and out.name == "nasa_good1.mp4" and info["source"] == "nasa"
    assert info["layout"] == "cover-sharpen" and 0 < info["skip"] <= 8.0 and info["credit"] == "Video: NASA"
    assert info["page"] == "https://images.nasa.gov/details/good1"
    assert not any("talk" in c or "esa1" in c for c in calls)                   # never even looked at
    assert "https://images-assets.nasa.gov/video/good1/good1~large.mp4" in calls  # https, the large copy
    from clipper import media
    for layout in ("cover-sharpen", "framed"):
        clip = footage.scene_clip(out, 2.0, tmp_path / f"s_{layout}.mp4", None, start=info["skip"], layout=layout)
        p = media.probe(clip)
        assert (p["width"], p["height"]) == (1080, 1920) and abs(p["duration"] - 2.0) < 0.2
    from studio import config
    config.set_values(footage_wide="framed")                       # the other way to show a small wide clip
    assert footage._layout(out)[0] == "framed"
    # switched off in settings: NASA isn't asked
    config.set_values(footage_nasa=False)
    assert footage.fetch("rocket launch", 5.0)[0] is None


def test_footage_scenes_credit_nasa_and_say_it_does_not_endorse(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import db, footage, video
    _fake_nasa(monkeypatch, [])
    scenes = [{"art": "x"}] + [{"art": "x", "footage": "rocket launch"}] * 3
    used = {}
    vid = db.insert("INSERT INTO videos(state, created, updated) VALUES('rendering', 0, 0)")
    out = video._footage_moves(vid, scenes, [0, 2, 4, 6, 8], tmp_path, used, True)
    assert used["footage"].startswith("NASA: 2 scenes") and sorted(out) == [1, 2]
    a = db.one("SELECT * FROM assets WHERE video_id=? AND kind='footage:scene'", vid)
    assert a["license"] == footage.LICENCES["nasa"] and a["attribution"] == "Video: NASA"
    assert a["source_url"] == "https://images.nasa.gov/details/good1"


def test_bad_key_is_refused(studio_home):  # noqa: F811
    from studio import footage
    with pytest.raises(ValueError):
        footage.save_key("pixabay key please")
    assert json.dumps(footage.sources()) == '["nasa"]'
