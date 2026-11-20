"""Editing: jump cuts keep sound and picture in sync, framing, captions, rendering, quality checks."""
import numpy as np
import pytest

from conftest import make_video, make_words


def test_keep_intervals_and_time_maps():
    from clipper import render
    words = [{"w": "a", "s": 1.0, "e": 1.3}, {"w": "b", "s": 1.4, "e": 1.7}, {"w": "c", "s": 3.0, "e": 3.4}]
    iv = render.keep_intervals(words, 0.9, 3.6, jump_cuts=True, max_gap=0.55)
    assert len(iv) == 2 and iv[0][1] == pytest.approx(1.85) and iv[1][0] == pytest.approx(2.88)
    assert render.to_output_time(3.0, iv) == pytest.approx((1.85 - 0.9) + 0.12)
    assert render.to_source_time(render.to_output_time(3.2, iv), iv) == pytest.approx(3.2)
    assert render.to_output_time(2.5, iv) is None            # inside the cut
    assert render.keep_intervals(words, 0.9, 3.6, jump_cuts=False) == [(0.9, 3.6)]


def test_crop_plan_layouts():
    from clipper import crop
    n = 50

    def analysis(faces_per_sample):
        return {"w": 640, "h": 360, "samples": [{"t": i / 5, "faces": f, "act": [0.5 * (j + 1) for j in range(len(f))]}
                                                 for i, f in enumerate(faces_per_sample)]}
    none = analysis([[] for _ in range(n)])
    assert crop.plan(none, 10)["layout"] == "fit"
    one = analysis([[(0.6, 0.3, 0.1, 0.18, 0.9)] for _ in range(n)])
    p = crop.plan(one, 10)
    assert p["layout"] == "track" and all(abs(x - 0.65) < 0.01 for x in p["x"])
    assert crop.visible_share(p, one) == 1.0
    cam = analysis([[(0.84, 0.75, 0.07, 0.12, 0.9)] for _ in range(n)])   # facecam in the corner
    assert crop.plan(cam, 10)["layout"] == "stack"
    assert crop.plan(one, 10, src_w=1080, src_h=1920)["layout"] == "scale"


def test_speaker_switching_has_hysteresis():
    from clipper import crop
    trs = [{"id": 0, "size": 0.1, "act": {i: (1.0 if i < 25 else 0.1) for i in range(50)}, "boxes": {}},
           {"id": 1, "size": 0.1, "act": {i: (0.1 if i < 25 else 1.0) for i in range(50)}, "boxes": {}}]
    who = crop.speaking_track(trs, 50, [True] * 50)
    assert who[5] == 0 and who[45] == 1
    assert sum(1 for a, b in zip(who, who[1:]) if a != b) == 1


def test_captions_pages_safe_area_and_render():
    from clipper import captions, settings
    st = captions.resolve("bold", settings.channel())
    words = make_words(["This is a test of the caption system with many words.", "Second sentence here."])
    pages = captions.pages(words, st, 1080 - 230)
    assert all(len(p["words"]) <= st["words_per_line"] * st["max_lines"] for p in pages)
    assert [i for p in pages for i in p["words"]] == list(range(len(words)))
    arr = captions.render_page(pages[0], words, pages[0]["words"][0], set(), st, 1080)
    x, y = captions.place(arr, 1080, 1920, st)
    assert x >= 0 and x + arr.shape[1] <= 1080 - 60 and y >= 1920 * 0.12 and y + arr.shape[0] <= 1920 * 0.78
    frame = np.zeros((1920, 1080, 3), np.uint8)
    cap = captions.Captioner(words, st)
    assert cap.draw(frame, words[0]["s"] + 0.01) and frame.max() > 200
    assert captions.apply_fixes("hey jarvis", {"jarvis": "JARVIS"}) == "hey JARVIS"
    for name in captions.STYLES:
        captions.render_page(pages[0], words, None, {1}, captions.resolve(name, settings.channel()), 1080)


def test_render_and_qa_pass(tmp_path, no_llm):
    from clipper import captions, crop, media, qa, render, settings, sources
    src = make_video(tmp_path / "src.mp4", 14)
    v, job, _ = sources.add(str(src))
    ch = dict(settings.channel(), clip_min=8)
    words = make_words(["Here is a sentence that is spoken in this video right now.", "And here is another one that keeps going.",
                        "Then a final line ends it."], start=1.0, gaps={0: 1.2})
    clip = {"id": 1, "start": 0.9, "end": words[-1]["e"] + 0.3, "keywords": ["sentence"], "hook_text": "Test hook",
            "transcript": "Then a final line ends it.", "title": "A title", "description": "d", "video_id": v["id"]}
    a = crop.analyze(str(src), clip["start"], clip["end"] - clip["start"])
    plan = crop.plan(a, clip["end"] - clip["start"], [], 1920, 1080)
    rep = render.render(tmp_path, clip, words, plan, (str(src), 0.0), ch, captions.resolve("bold", ch), settings.general())
    info = media.probe(rep["path"])
    assert (info["width"], info["height"], info["vcodec"], info["acodec"]) == (1080, 1920, "h264", "aac")
    assert rep["cuts"] == 1                                            # the 1.2 s pause was tightened
    assert abs(info["duration"] - rep["duration"]) < 0.15
    result = qa.check(rep["path"], clip, rep, v, ch, plan)
    assert result["passed"], qa.describe(result)


def test_qa_catches_bad_files(tmp_path):
    from clipper import media, qa, settings
    bad = tmp_path / "wide_silent.mp4"
    media.run(["-y", "-f", "lavfi", "-i", "color=black:size=1280x720:rate=30:duration=25", "-f", "lavfi", "-i",
               "anullsrc=r=48000:cl=stereo", "-t", "25", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(bad)])
    r = qa.check(str(bad), {"transcript": "x", "title": "", "start": 0, "end": 25}, {"duration": 25, "words": 10, "words_captioned": 2},
                 {"auth_status": "awaiting_authorization"}, settings.channel())
    assert not r["passed"]
    for k in ("resolution", "black_frames", "loudness", "captions", "metadata", "authorized"):
        assert k in r["critical"], k


def test_qa_autofix_loudness(tmp_path):
    from clipper import media, qa
    quiet = tmp_path / "quiet.mp4"
    media.run(["-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=21", "-f", "lavfi", "-i",
               "sine=frequency=300:sample_rate=48000:duration=21", "-af", "volume=0.02", "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", "-c:a", "aac", str(quiet)])
    lufs, _ = media.loudness(str(quiet))
    assert lufs < -25
    assert qa.autofix(str(quiet), {"critical": ["loudness"]})
    lufs2, _ = media.loudness(str(quiet))
    assert -16 <= lufs2 <= -12
