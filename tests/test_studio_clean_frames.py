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
    rep = {"cinematic": True, "scene_boxes": [(90, 270, 600, 420), (80, 270, 1000, 1100)],
           "text_boxes": [(90, 270, 600, 420), (90, 270, 600, 420)],
           "caption_boxes": [(90, 1300, 900, 160)], "overlay_boxes": {"1": [80, 500, 1000, 1100]}}
    assert qa.text_overlaps(rep) == ([], [])            # the card inside scene_boxes isn't a clash with itself
    rep["overlay_boxes"] = {"1": [80, 400, 1000, 1350]}
    block, advice = qa.text_overlaps(rep)
    assert block == ["scene 2: the counter overlaps the diagram card"]
    assert advice == ["a tall caption may touch a diagram card"]      # caption boxes are worst cases: advice
    rep["overlay_boxes"] = {"1": [80, 500, 1000, 1310]}               # touching by 10 px: line spacing, fine
    assert qa.text_overlaps(rep) == ([], [])
    assert qa.text_overlaps(dict(rep, cinematic=False)) == ([], [])  # the classic look draws text into one picture


def test_unsharpened_pictures_are_found():
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


def test_an_unbacked_comparison_is_removed_not_reworded(studio_home, monkeypatch):  # noqa: F811
    """Short 17 stopped twice on "slower than a snail": the fix step now drops such a comparison and keeps the
    sourced number in a unit a viewer can picture."""
    from studio import claude, script
    seen = []

    def runner(prompt, timeout, web):
        seen.append(prompt)
        return json.dumps({"lines": [{"line": 2, "text": "That's about 7 millimetres an hour.", "delete": False}]})
    monkeypatch.setattr(claude, "RUNNER", runner)
    monkeypatch.setattr(script, "WORDS_MIN", 1)
    topic = {"title": "Coriolis in a sink", "sources": [{"claim": "the push is about 2 micrometres a second",
                                                         "url": "https://example.org", "verdict": "reachable"}]}
    draft = {"lines": [{"text": "The push is about 2 micrometres a second."},
                       {"text": "That's slower than a snail."}], "hooks": []}
    fixed = script._fix_lines(draft, topic, ["line 2 is unsupported: no source says this is slower than a snail"])
    assert "remove just\nthe comparison" in seen[0] and "never swap in another" in seen[0]
    assert fixed["lines"][1]["text"] == "That's about 7 millimetres an hour."


def test_survey_strips_and_black_edged_scans_are_not_used_as_photos(tmp_path):
    """Short 17's Olympus Mons "photo" was an orbiter's tilted map strip with black wedges."""
    import numpy as np
    from PIL import Image
    from studio import footage
    ok = tmp_path / "ok.jpg"
    Image.fromarray(np.full((800, 1200, 3), 120, np.uint8)).save(ok)
    strip = tmp_path / "strip.jpg"
    Image.fromarray(np.full((3000, 900, 3), 120, np.uint8)).save(strip)
    edged = tmp_path / "edged.jpg"
    a = np.full((900, 900, 3), 120, np.uint8)
    a[:, :300] = 0
    Image.fromarray(a).save(edged)
    assert footage.photo_problem(ok) == ""
    assert "strip" in footage.photo_problem(strip) and "black" in footage.photo_problem(edged)


def test_bar_units_keep_whole_words():
    from studio import edit
    ov = edit.overlay({"type": "compare", "unit": "× farther than high",
                       "items": [{"label": "Steep (5°)", "value": 11}, {"label": "Shallow (2°)", "value": 29}]})
    assert [i["text"] for i in ov["items"]] == ["11 × farther than high", "29 × farther than high"]
    assert edit._whole_words("tonnes of rocket fuel burned", 12) == "tonnes of"


def test_a_chosen_example_value_must_be_labelled_as_the_narrators_pick():
    """Short 18 stopped twice on a car length (4.7 m) the writer assumed as if it were a fact."""
    import inspect
    from studio import script
    src = inspect.getsource(script)
    assert 'mark that line\'s facts ["example"]' in src and "say the car is 4.5 m long" in src
    facts = [{"claim": "A car's wheels are 1.6 m apart", "url": "https://example.org"}]
    lines = [{"text": f"Line {i} about turning.", "facts": ["F1"], "beat": "setup"} for i in range(5)]
    lines[2] = {"text": "Say the car is 4.5 m long.", "facts": ["example"], "beat": "math"}
    ans = {"hooks": ["a?", "b?", "c?"], "lines": lines, "calcs": []}
    assert script._draft_problem(ans, facts) != "a fact id that doesn't exist"
    lines[2] = dict(lines[2], facts=["made-up"])
    assert script._draft_problem(ans, facts) == "a fact id that doesn't exist"


def test_the_main_subject_keeps_one_look_and_pictures_stay_empty_of_people():
    from studio import art, video
    hero = video._hero({"subject": "the Terra satellite", "look": "a boxy silver satellite with one long dark solar wing"})
    sc = {"art": "Terra satellite above the clouds", "shots": [{"art": "close-up of the satellite"}]}
    assert video._shows(sc, hero[0]) and not video._shows({"art": "a phone alarm on a desk"}, hero[0])
    p = art.prompt(dict(sc, look=hero[1]), "line", "hud")
    assert "one long dark solar wing" in p and p.endswith(art.NO_PEOPLE)
    assert art.prompt(sc, "line", "hud") == f"Terra satellite above the clouds, {art.STYLE['hud']}"   # old Shorts unchanged


def test_no_counter_repeats_the_number_on_its_card():
    from studio import video
    huds = [{"value": "1999"}, {"value": "705 km"}, {"value": "5,924 s"}]
    assert video.no_double_numbers(huds, {2: {"kind": "equation"}, 0: {"kind": "bars"}}) == \
        [{"value": "1999"}, {"value": "705 km"}, None]


def test_a_scene_showing_the_main_subject_gets_no_stock_clip(monkeypatch, tmp_path):
    from studio import footage, video
    asked = []
    monkeypatch.setattr(footage, "ready", lambda: True)
    monkeypatch.setattr(footage, "pick_scenes", lambda scenes, spans: [1, 2])
    monkeypatch.setattr(footage, "fetch", lambda q, need, used=(): (asked.append(q), (None, "no clip"))[1])
    scenes = [{"type": "headline"}, {"footage": "space station", "look": "a boxy silver satellite"},
              {"footage": "ocean waves", "look": ""}]
    used = {}
    video._footage_moves(1, scenes, [0, 2, 4, 6], tmp_path, used, record=False)
    assert asked == ["ocean waves"]


def test_visual_plan_waits_for_the_daily_reset_instead_of_a_plain_plan(studio_home, monkeypatch):  # noqa: F811
    """Short 20 was rendered from the plain fallback plan (no diagrams) after the day's Claude calls ran out."""
    import time
    from studio import claude, video
    from studio.jobs import QuotaWait

    def out_of_calls(*a, **k):
        raise QuotaWait("the studio has used today's 60 Claude calls", time.time() + 60)
    monkeypatch.setattr(claude, "ask_json", out_of_calls)
    row = {"hook": "Why fold paper?", "structure": json.dumps({"lines": [{"text": t} for t in
                                                                         ("Why fold paper?", "Fold it once.", "Then twelve times.")]})}
    with pytest.raises(QuotaWait):
        video.plan(row)

    def broken(*a, **k):
        raise RuntimeError("bad JSON")
    monkeypatch.setattr(claude, "ask_json", broken)
    assert len(video.plan(row)) == 3                    # any other failure still falls back to a plain plan


def test_small_numbers_appear_as_they_are():
    from studio import video
    h = video.huds([{"type": "number", "value": "0.1", "unit": "millimeters", "label": "say your tissue is"}])
    assert h[0]["from"] == h[0]["num"] == 0.1
