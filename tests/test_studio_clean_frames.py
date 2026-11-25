"""Short 16 follow-up (2026-11-25): clean text at scene changes and the loop, explanatory diagrams (thermometer),
dates that never count up, and the new QA checks (on-screen numbers, sharpened pictures, text overlaps, clean
decode), plus the editor's visual hook score and part-by-part guess at where viewers might leave."""
import json
import shutil
import subprocess

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401


def _row(hook, lines, calcs=()):
    return {"hook": hook, "body": " ".join(lines),
            "structure": json.dumps({"lines": [{"text": t} for t in lines], "calcs": list(calcs)})}


def test_on_screen_numbers_must_come_from_the_checked_script():
    from studio import qa
    row = _row("Why does the X-59 fly at 55,000 feet?",
               ["At 55,000 feet the air is about 56 degrees below zero.", "Sound moves at 295 m/s up there.",
                "Mach 1.4 is about 413 m/s."], [{"expr": "1.4 * 295", "result": "413"}])
    good = [{"type": "number", "value": "55,000", "unit": "ft", "label": "cruise height"},
            {"type": "temperature", "value": "-56", "unit": "°C", "label": "air at 55,000 ft (standard atmosphere)"},
            {"type": "equation", "left": "1.4 × 295 m/s", "right": "≈ 413 m/s"},
            {"type": "steps", "items": ["1 nose", "2 wing", "3 tail"]}]
    assert qa.unmatched_numbers(good, row) == []
    bad = good + [{"type": "number", "value": "52,690", "unit": "ft", "label": "record height"}]
    assert qa.unmatched_numbers(bad, row) == [(5, "value", 52690.0)]
    assert qa.unmatched_numbers([{"type": "number", "value": "5,000,000"}], _row("x", ["About 5 million of them."])) == []


def test_text_layers_that_overlap_are_found():
    from studio import qa
    rep = {"cinematic": True, "scene_boxes": [(90, 270, 600, 420), (90, 270, 600, 420)],
           "caption_boxes": [(90, 1300, 900, 160)], "overlay_boxes": {"1": [80, 500, 1000, 1100]}}
    assert qa.text_overlaps(rep) == []
    rep["overlay_boxes"] = {"1": [80, 400, 1000, 1350]}
    got = qa.text_overlaps(rep)
    assert any("counter overlaps the diagram" in g for g in got) and any("caption overlaps the diagram" in g for g in got)
    assert qa.text_overlaps(dict(rep, cinematic=False)) == []       # the classic look draws text into one picture


def test_unsharpened_pictures_block_the_short():
    from studio import qa
    assert qa.soft_pictures({"engines": {"sharpen": "Real-ESRGAN: 7 of 7 pictures"}}) == ""
    assert "only 5 of 7" in qa.soft_pictures({"engines": {"sharpen": "Real-ESRGAN: 5 of 7 pictures (worker died)"}})
    assert qa.soft_pictures({"engines": {"sharpen": "off"}}) is None and qa.soft_pictures({}) is None


def test_temperature_scene_is_a_thermometer_in_both_looks():
    from studio import edit, video
    sc = {"type": "temperature", "value": "-56", "unit": "°C", "label": "air at 55,000 ft (standard atmosphere)"}
    ov = edit.overlay(sc)
    assert ov["kind"] == "thermo" and ov["cold"] and ov["value"] == "-56 °C" and 0 < ov["frac"] < 1
    assert video.inside_safe_area(edit.overlay_box(ov))
    _img, box = video.draw_scene(sc, video.palette())
    assert video.inside_safe_area(box)
    assert video._valid(sc) and "temperature" in video.NEW_TEMPLATES
    assert video.huds([sc]) == [None]                     # the thermometer shows the number; no second counter


def test_dates_appear_as_they_are_and_never_count_up():
    from studio import video
    h = video.huds([{"type": "number", "value": "1947", "label": "first supersonic flight"},
                    {"type": "number", "value": "1,500", "unit": "km/h", "label": "speed"}])
    assert h[0]["from"] == h[0]["num"] == 1947
    assert h[1]["from"] == 0.0                           # a real quantity still counts up
    assert not video._year_like("1,947", (1947.0, 0, ""))
    assert video._year_like("2023", (2023.0, 0, ""), "LAUNCH")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_decode_check_catches_a_broken_file(tmp_path):
    from studio import qa
    good = tmp_path / "ok.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=108x192:d=1:r=30", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(good)], check=True)
    assert qa.decodes_cleanly(good)[0]
    broken = tmp_path / "cut.mp4"
    data = good.read_bytes()
    broken.write_bytes(data[: len(data) // 2])
    assert not qa.decodes_cleanly(broken)[0]


def test_final_review_scores_the_visual_hook_and_guesses_where_viewers_leave(fakes, fake_voice, monkeypatch):  # noqa: F811
    from studio import claude, config, db, finalreview
    from test_studio_final_review import _with_review
    from test_studio_video import _make
    config.set_values(final_review="advise")
    seen = []
    answer = {"score": 8, "first_frame": 7, "visual_hook": 6, "problems": [],
              "sections": {"hook": "likely holds: a jet over a shock cone", "payoff": "may lose some: answer is slow",
                           "made_up": "ignored"}}
    monkeypatch.setattr(claude, "RUNNER", _with_review(monkeypatch, answer, seen))
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    assert '"visual_hook"' in seen[0] and "guesses, not measurements" in seen[0]
    fr = json.loads(db.video(vid)["meta"])["final_review"]
    assert fr["visual_hook"] == 6.0 and set(fr["retention_guess"]) == {"hook", "payoff"}
    assert "visual hook 6.0/10" in finalreview.detail(fr)
