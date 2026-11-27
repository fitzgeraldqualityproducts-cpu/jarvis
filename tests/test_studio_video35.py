"""Video 35 audit (2026-11-27, "How many days of sun does the Moon's far side get?"): the hands-on demonstration
must show the real science, periods name their cycle, numbers land without in-between values, every claim has a
visual, the opening is clean and the export is a real 1080x1920. Each test is one of the regressions Connor
listed, or a correct case that must keep passing."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from test_studio_foundation import studio_home  # noqa: F401


def L(text, beat="setup", **more):
    return dict(text=text, beat=beat, **more)


DEMO = {"objects": [{"thing": "ball", "stands_for": "the Moon"}, {"thing": "lamp", "stands_for": "the Sun"}],
        "observer": {"thing": "you", "stands_for": "someone on Earth"},
        "marker": {"thing": "sticky note", "stands_for": "a spot on the Moon's far side",
                   "where": "the side facing away from you"},
        "moves": "the ball circles you once",
        "rotates": "the ball turns once per circle, so the same side always faces you",
        "fixed": "the lamp stays across the room; you stay in the middle",
        "proves": "the far side gets sunlight half of each cycle",
        "limits": "near the poles the sun stays low and crater floors can stay dark; the equator is the simple case"}

MODEL = {"question": "How many days of sun does a spot on the Moon's far side get?",
         "premise": "the Moon turns once per lap so one side faces Earth; the Sun lights half of it",
         "quantities": [{"name": "P", "means": "New Moon to New Moon", "value": 29.53, "unit": "days", "from": "F1"}],
         "formula": {"expr": "P/2", "means": "half the cycle in sunlight"},
         "result": {"value": 14.765, "unit": "days"}, "answer": "about 14.8 days of sun, then 14.8 of dark",
         "demo": DEMO}

# the script as Video 35 used it (studio.db, video 37, script v3)
V35 = [L("How many days of sun does the Moon's far side get?", "hook"),
       L("Stick a note at the center of a ball's far side.", facts=["example"]),
       L("Circle it around you, turning once per circle. Lamp across the room."),
       L("Ball between you and lamp: New Moon, note lit."),
       L("Halfway round, you're between lamp and ball: Full Moon, note dark."),
       L("Does the note get more night than day?", "rehook"),
       L("New Moon to New Moon takes 29.53 days.", "math"),
       L("The Sun lights half the Moon, so the note is lit half a lap: 29.53 divided by 2.", "math"),
       L("The note gets 14.8 days of sun, then 14.8 of dark.", "payoff"),
       L("So next New Moon, ask:", "loop")]

# the same idea told so the demonstration and the cycle are clear
GOOD = [L("How many days of sun does the Moon's far side get?", "hook"),
        L("Hold a ball for the Moon. A lamp across the room is the Sun."),
        L("Stick a note on the side facing away from you: the ball's far side."),
        L("Walk the ball around you, turning it once per circle, so the note always faces away."),
        L("Ball between you and lamp, New Moon: the note is lit."),
        L("Halfway round, Full Moon: the note is in the dark.", "rehook"),
        L("Sunrise to sunrise at the note is New Moon to New Moon: 29.53 days.", "math"),
        L("Half of that in sun: 29.53 divided by 2.", "math"),
        L("Near the equator, the note gets 14.8 days of sun, then 14.8 of dark.", "payoff"),
        L("So at New Moon, ask again:", "loop")]


def kinds(issues, hard=True):
    return [i["kind"] for i in issues if i["hard"] == hard]


# ---------- 2. the physical demonstration ----------

def test_video_35_demonstration_is_caught_for_its_vague_marker():
    from studio import demo
    found = demo.check(V35, MODEL)
    assert "marker_vague" in kinds(found)                     # "at the center of a ball's far side": far from whom?
    assert "demo_wrong" not in kinds(found)                   # its lit/dark moments were right


def test_a_correct_synchronous_rotation_demonstration_passes():
    from studio import demo
    assert kinds(demo.check(GOOD, MODEL)) == []
    assert demo.synchronous(DEMO) and demo.marker_side(DEMO) == "far"
    # worked out: a far-side spot is lit at New Moon and dark at Full Moon; a near-side one the other way round
    assert demo.lit(180, "far") and not demo.lit(0, "far")
    assert demo.lit(0, "near") and not demo.lit(180, "near")


def test_a_marker_on_the_wrong_side_is_caught():
    from studio import demo
    wrong = dict(MODEL, demo=dict(DEMO, marker={"thing": "sticky note", "stands_for": "a spot facing Earth",
                                                 "where": "the side facing you"}))
    lines = [dict(x) for x in GOOD]
    lines[2] = L("Stick a note on the side facing you: the ball's near side.")
    found = demo.check(lines, wrong)
    assert "demo_wrong" in kinds(found)                       # a near-side note is dark at New Moon, not lit
    assert any("New Moon" in i["text"] for i in found if i["kind"] == "demo_wrong")


def test_an_unexplained_rotation_is_caught():
    from studio import demo
    model = dict(MODEL, demo=dict(DEMO, rotates=""))
    lines = [dict(x) for x in GOOD]
    lines[3] = L("Walk the ball around you and spin it as you go.")
    assert "rotation" in kinds(demo.check(lines, model))
    model2 = dict(MODEL, demo=dict(DEMO, rotates="it spins"))     # said, but not how much or why
    assert "rotation" in kinds(demo.check(lines, model2))


def test_a_hands_on_model_without_its_spec_stops_and_a_half_spec_names_whats_missing():
    from studio import demo
    found = demo.check(GOOD, {k: v for k, v in MODEL.items() if k != "demo"})
    assert kinds(found) == ["demo_missing"]
    half = dict(MODEL, demo={k: v for k, v in DEMO.items() if k not in ("proves", "limits")})
    msg = [i["text"] for i in demo.check(GOOD, half) if i["kind"] == "demo_incomplete"][0]
    assert "proves" in msg and "leaves out" in msg
    assert demo.check([L("The Moon is 384,400 km away.", "hook")], MODEL) == []   # no hands-on model: no demo needed


# ---------- 3. naming the cycle ----------

def test_a_script_that_calls_the_synodic_month_a_lap_is_caught():
    from studio import demo
    found = demo.cycles(V35)
    assert [(i["line"], i["kind"]) for i in found if i["hard"]] == [(8, "cycle")]   # "lit half a lap: 29.53"
    assert "27.3" in found[0]["text"]
    assert [i for i in demo.cycles(GOOD) if i["hard"]] == []


def test_sidereal_period_said_as_sunrise_to_sunrise_is_caught():
    from studio import demo
    bad = [L("Sunrise to sunrise on the Moon takes 27.3 days.", "math")]
    assert kinds(demo.cycles(bad)) == ["cycle"]
    ok = [L("Measured against the stars, the Moon turns once every 27.3 days.", "math")]
    assert demo.cycles(ok) == []
    vague = [L("Then 29.53 days pass.", "math")]
    assert kinds(demo.cycles(vague), hard=False) == ["cycle"]        # unnamed: a note, not a stop


def test_the_story_check_runs_the_demonstration_and_cycle_checks():
    from studio import narrative
    nar = narrative.evaluate({"hooks": [V35[0]["text"]], "lines": V35, "model": MODEL,
                              "calcs": [{"expr": "29.53/2", "result": 14.765, "unit": "days"}]})
    hard = " | ".join(nar["hard"])
    assert "marker" in hard and "29.53" in hard and nar["score"] <= 49
    assert all("(fix: " in h for h in nar["hard"])
    good = narrative.evaluate({"hooks": [GOOD[0]["text"]], "lines": GOOD, "model": MODEL,
                               "calcs": [{"expr": "29.53/2", "result": 14.765, "unit": "days"}]})
    assert not [h for h in good["hard"] if "marker" in h or "29.53" in h or "demonstration" in h]


def test_one_line_demo_problems_go_to_the_line_repair_not_a_full_rewrite():
    from studio import script
    assert script.STORY_LINE.match("narrative: line 2 says where the marker is but not relative to the viewer")
    assert script.STORY_LINE.match("narrative: line 8 calls 29.53 days \"lap\", but 29.53 days is ...")
    assert not script.STORY_LINE.match("narrative: line 2 uses a hands-on model but the script never says ...")


def test_the_writer_is_asked_for_the_demo_spec_and_the_cycle_names():
    from studio import script
    assert "\"demo\"" in script.STORY_FIRST and "facing away from you" in script.STORY_FIRST
    assert "27.3 days" in script.STORY_FIRST and "29.53 days" in script.STORY_FIRST
    assert '"demo": {"objects"' in script.MODEL_SHAPE


# ---------- 4. numbers on screen ----------

def test_numbers_land_as_themselves_without_counting_up():
    from studio import edit, video
    h = video.huds([{"type": "headline", "text": "Q"},
                    {"type": "number", "value": "29.53", "unit": "days", "label": "New Moon to New Moon"},
                    {"type": "equation", "left": "29.53 ÷ 2", "right": "≈ 14.8 days", "label": "half in sun"}])
    assert h[1]["from"] == h[1]["num"] == 29.53
    starts = [0, 21.13, 24.97, 31.08]
    assert edit.shown_values(h[1], starts[2] - starts[1]) == ["29.53 days"]          # one value, never 20.33
    assert edit.number_motion(h, starts) == []


def test_an_animation_with_unexplained_in_between_values_is_caught():
    from studio import edit
    # what Video 35 drew: 29.53 days counting up from 0 over 70% of a 3.8 s scene
    hud = {"value": "29.53 days", "num": 29.53, "from": 0.0, "dec": 2, "unit": "days"}
    shown = edit.shown_values(hud, 3.84)
    assert len(shown) > 5 and "29.53 days" == shown[-1]
    probs = edit.number_motion([None, hud], [0, 21.13, 24.97])
    assert probs and "never saw" in probs[0]


def test_a_valid_animated_number_transition_passes():
    from studio import edit, video
    h = video.huds([{"type": "headline", "text": "Q"},
                    {"type": "number", "value": "14.8", "unit": "days", "label": "sun"},
                    {"type": "number", "value": "29.53", "unit": "days", "label": "cycle", "count_from": "14.8"}])
    assert h[2]["from"] == 14.8                                     # an intentional start the viewer already saw
    starts = [0, 2, 5, 9]
    assert edit.number_motion(h, starts) == []
    assert edit.count_seconds(4) == 0.8 and 4 - edit.count_seconds(4) >= edit.HOLD_MIN
    assert edit.count_seconds(1.6) == 0.0                         # too short to count and still be read
    bad = video.huds([{"type": "headline"}, {"type": "number", "value": "29.53", "unit": "days", "count_from": "3.17"}])
    assert bad[1]["from"] == 29.53                                # 3.17 was never shown: no count


def test_a_number_loses_neither_its_unit_nor_its_reading_time():
    from studio import edit
    probs = edit.number_motion([None, {"value": "29.53", "num": 29.53, "from": 29.53, "unit": "days"}], [0, 1, 1.5])
    assert any("unit" in p for p in probs) and any("only 0.5 s" in p for p in probs)


def test_a_correct_29_53_divided_by_2_card_passes_and_a_wrong_one_is_fixed():
    from studio import preflight
    assert preflight.calc_problem({"type": "equation", "left": "29.53 ÷ 2", "right": "≈ 14.8 days"}) is None
    why, fixed = preflight.calc_problem({"type": "equation", "left": "29.53 ÷ 2", "right": "≈ 15.8 days"})
    assert "14.765" in why and fixed == "≈ 14.8 days"


def test_counter_cues_follow_the_new_rules():
    from studio import edit
    cues = edit.cues([0, 2, 6, 9], ["hook", "math", "payoff"],
                     [None, {"num": 29.53, "from": 29.53}, {"num": 14.8, "from": 0.0}])
    kinds_ = [(c["kind"], c["scene"]) for c in cues]
    assert ("pop", 1) in kinds_ and ("count", 1) not in kinds_
    count = [c for c in cues if c["kind"] == "count"]
    assert count and count[0]["until"] - count[0]["t"] <= edit.COUNT_MAX


# ---------- 5. the demonstration drawn, and the storyboard ----------

STRUCT = {"lines": V35, "model": MODEL}
PLANNED = ([{"type": "headline", "text": "How many days", "art": "a pale Moon", "real": "Moon"},
            {"type": "headline", "text": "A note on the far side", "art": "a pale grey ball on a table"},
            {"type": "steps", "items": ["Circle the ball around you", "Turn it once per circle", "Lamp"],
             "art": "a ball in a dim room"},
            {"type": "headline", "text": "New Moon: note lit", "art": "a ball in front of a lamp"},
            {"type": "headline", "text": "Full Moon: note dark", "art": "a ball lit on its near face"},
            {"type": "headline", "text": "More night than day?", "art": "a ball half in light"},
            {"type": "number", "value": "29.53", "unit": "days", "label": "New Moon to New Moon"},
            {"type": "equation", "left": "29.53 ÷ 2", "right": "≈ 14.8 days"},
            {"type": "compare", "items": [{"label": "sun", "value": "14.8"}, {"label": "dark", "value": "14.8"}],
             "unit": "days"},
            {"type": "headline", "text": "Far side: dark at New Moon?", "art": "a pale Moon"}])


def test_the_demonstration_lines_are_drawn_and_end_lit_or_dark_as_the_line_says():
    from studio import demo, edit, preflight
    scenes, rep = preflight.run(STRUCT, V35[0]["text"], PLANNED, offered=True)
    orbit = {k + 1: s for k, s in enumerate(scenes) if s["type"] == "orbit"}
    assert sorted(orbit) == [2, 3, 4, 5, 6]
    assert orbit[3]["to"] == "cycle"                                   # turning once per circle: a whole lap
    assert orbit[4]["to"] == "new" and edit.orbit_overlay(orbit[4])["litEnd"] is True
    assert orbit[5]["to"] == "full" and edit.orbit_overlay(orbit[5])["litEnd"] is False
    assert orbit[2]["labels"]["marker"] == "note = far-side spot" and orbit[2]["labels"]["lamp"] == "lamp = Sun"
    assert scenes[6]["type"] == "number" and scenes[7]["type"] == "equation"    # numbers keep their cards
    assert len(rep["fixed"]) >= 5
    # the script's own problems are listed by area with the exact reason
    sci = [p for p in rep["problems"] if p["area"] == "scientific"]
    assert any("marker" in p["reason"] for p in sci) and any("29.53" in p["reason"] for p in sci)
    assert all(p["fix"] for p in sci)
    for k, sc in orbit.items():
        ov = edit.overlay(sc)
        assert ov["kind"] == "orbit"
        from studio import video
        assert video.inside_safe_area(edit.overlay_box(ov))
        _img, box = video.draw_scene(sc, video.palette())
        assert video.inside_safe_area(box)
    assert demo.lit(edit.orbit_overlay(orbit[4])["a1"] % 360, "far")


def test_the_storyboard_has_every_field_for_every_scene():
    from studio import preflight
    scenes, rep = preflight.run(STRUCT, V35[0]["text"], PLANNED, offered=True)
    board = rep["storyboard"]
    assert len(board) == len(scenes)
    for b in board:
        assert set(b) >= {"scene", "narration", "concept", "visual", "framing", "motion", "on_screen_text", "numbers",
                          "understanding", "transition"}
    assert board[7]["numbers"] == [29.53, 2.0, 14.8] and "calculation card" in board[7]["visual"]
    assert "demonstration from above" in board[3]["visual"]


def test_a_scene_with_narration_but_no_visual_explanation_is_caught():
    from studio import preflight
    lines = [L("Why is the sky blue?", "hook"), L("Sunlight hits air molecules and scatters.", "setup"),
             L("Blue light scatters about 5.5 times more than red.", "math")]
    scenes = [{"type": "headline", "text": "Why blue?"},
              {"type": "headline", "text": "", "art": "a cozy cabin by a lake at sunset"},
              {"type": "headline", "text": "", "art": "a field of flowers"}]
    gaps = preflight.coverage(scenes, lines)
    assert [n for n, _ in gaps] == [2, 3]
    fixed, rep = preflight.run({"lines": lines}, lines[0]["text"], scenes, offered=True)
    assert fixed[2]["type"] == "number" and fixed[2]["value"] == "5.5"           # the number now shows
    assert any(p["area"] == "visual" and p["scene"] == 2 for p in rep["problems"])
    assert preflight.coverage([scenes[0], {"type": "headline", "art": "sunlight scattering off air"}, scenes[2]],
                              lines)[0][0] == 3


def test_the_planner_is_told_about_the_demonstration(studio_home, monkeypatch):  # noqa: F811
    from studio import art, claude, video
    seen = {}
    monkeypatch.setattr(art, "shots_on", lambda: True)
    monkeypatch.setattr(claude, "ask_json", lambda prompt, **k: seen.setdefault("p", prompt) and [])
    row = {"hook": V35[0]["text"], "structure": json.dumps(STRUCT)}
    video.plan(row)
    assert "DEMONSTRATION" in seen["p"] and "orbit" in seen["p"] and "never counts through" in seen["p"]


# ---------- 6. the opening ----------

def _moon(w=900, h=900, stripes=False):
    rng = np.random.default_rng(3)
    img = (110 + 60 * rng.random((h, w))).astype(np.uint8)
    if stripes:                                       # survey-scan missing-data dashes, like Video 35's photo
        for y in range(80, h, 140):
            for x in range(0, w, 60):
                img[y:y + 2, x:x + 30] = 0
    return img


def test_a_photo_with_missing_data_lines_is_refused(tmp_path):
    from PIL import Image
    from studio import art
    good, bad = tmp_path / "good.png", tmp_path / "bad.png"
    Image.fromarray(_moon()).save(good)
    Image.fromarray(_moon(stripes=True)).save(bad)
    assert art.photo_dropouts(Image.open(good)) == 0.0
    with pytest.raises(ValueError):
        art.framed_photo(bad, tmp_path / "out.png")
    # a natural dark sky around a bright Moon isn't mistaken for missing data
    sky = np.zeros((900, 900), np.uint8)
    yy, xx = np.mgrid[:900, :900]
    sky[(yy - 450) ** 2 + (xx - 450) ** 2 < 300 ** 2] = 180
    assert art.photo_dropouts(Image.fromarray(sky)) < art.DROPOUT_MAX


def test_a_real_photo_is_framed_at_full_size_without_an_empty_dark_half(tmp_path):
    from PIL import Image
    from studio import art, qa
    import cv2
    src = tmp_path / "moon.png"
    Image.fromarray(_moon()).save(src)
    info = {}
    out = art.framed_photo(src, tmp_path / "framed.png", info=info)
    im = Image.open(out)
    assert im.size == (1080, 1920) and info["fill"] == 1.0 and art.is_native(out)
    assert qa.dark_share(cv2.imread(str(out))) < 0.05
    wide = tmp_path / "wide.png"
    Image.fromarray(_moon(1800, 600)).save(wide)
    info = {}
    art.framed_photo(wide, tmp_path / "wide_out.png", info=info)
    assert info["fill"] < 1 and qa.dark_share(cv2.imread(str(tmp_path / "wide_out.png"))) < qa.OPEN_DARK_MAX


def test_full_size_pictures_are_not_enlarged_again(studio_home, tmp_path, monkeypatch):  # noqa: F811
    from PIL import Image
    from studio import art, engines
    big, small = tmp_path / "big.png", tmp_path / "small.png"
    Image.new("RGB", (1080, 1920)).save(big)
    Image.new("RGB", (512, 896)).save(small)
    sent = {}
    monkeypatch.setattr(engines, "run", lambda tool, worker, payload, *a, **k: sent.setdefault("p", payload) and {})
    out = art.enhance({0: big, 1: small}, sharpen=True, depth=False)
    jobs = {im["src"]: im for im in sent["p"]["images"]}
    assert str(big) not in jobs and "hd" in jobs[str(small)]
    assert out[0]["hd"] == big


def test_an_opening_with_an_empty_dark_area_is_caught():
    from studio import qa
    frame = np.full((1920, 1080, 3), 120, np.uint8)
    frame[::7, :, :] = 200                                         # texture, so it isn't "flat"
    assert qa.dark_share(frame) == 0.0
    frame[900:, :, :] = 12                                         # Video 35: the lower half empty and dark
    assert qa.dark_share(frame) > qa.OPEN_DARK_MAX
    assert qa.opening_problems.__doc__


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_opening_check_on_a_video(tmp_path):
    from studio import qa
    good, bad = tmp_path / "good.mp4", tmp_path / "bad.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=3:r=30", "-pix_fmt",
                    "yuv420p", str(good)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=3:r=30", "-vf",
                    "drawbox=x=0:y=800:w=1080:h=1120:color=black:t=fill", "-pix_fmt", "yuv420p", str(bad)], check=True)
    assert qa.opening_problems(good) == []
    assert "empty dark" in qa.opening_problems(bad)[0]
    assert "missing-data" in qa.opening_problems(good, {"opening": {"kind": "photo", "dropouts": 0.01}})[0]
    assert "empty dark" in qa.opening_problems(bad, {"opening": {"kind": "photo", "fill": 1.0}})[0]
    assert qa.opening_problems(bad, {"opening": {"kind": "picture"}}) == []      # an AI night sky is the picture


def test_the_title_has_a_backing_so_it_reads_on_any_picture():
    from PIL import Image
    from studio import video
    bg = Image.new("RGB", (1080, 1920), (230, 230, 230))
    img, _box = video.draw_cinematic(bg, None, video.palette(), title="How many days of sun?")
    px = img.getpixel((90, video.TITLE_TOP - 10))
    assert px[0] < 200                                             # darker than the white picture behind it


# ---------- 7. the export ----------

def test_a_low_resolution_export_is_caught():
    from studio import qa
    info = {"width": 512, "height": 910, "fps": 30, "has_video": True, "has_audio": True, "vcodec": "h264",
            "acodec": "aac", "duration": 37}
    assert "512x910" in qa.export_problems(info)[0]
    ok = dict(info, width=1080, height=1920)
    assert qa.export_problems(ok) == []
    assert any("frame rate" in p for p in qa.export_problems(dict(ok, fps=24)))
    assert any("audio" in p for p in qa.export_problems(dict(ok, has_audio=False, acodec="")))
    assert any("black" in p for p in qa.export_problems(ok, black=[3.5]))


def test_missing_scenes_and_broken_diagrams_are_caught():
    from studio import edit, qa
    info = {"width": 1080, "height": 1920, "fps": 30, "has_video": True, "has_audio": True, "vcodec": "h264",
            "acodec": "aac", "duration": 10}
    rep = {"line_starts": [0, 3, 3.1, 7, 10], "overlays": {"1": edit.orbit_overlay({"from": "new", "to": "full"}),
                                                           "2": {"kind": "equation", "terms": [], "right": "4",
                                                                 "x": 80, "y": 760, "w": 850, "h": 380}}}
    probs = qa.export_problems(info, rep, scenes=[{}] * 5)
    assert any("4 scenes in the video for 5" in p for p in probs)
    assert any("scene(s) 2" in p for p in probs)
    assert any("equation diagram is incomplete" in p for p in probs)
    assert not any("orbit" in p for p in probs)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_export_check_on_real_files(tmp_path):
    from clipper import media
    from studio import qa
    good, low = tmp_path / "good.mp4", tmp_path / "low.mp4"
    for out, size in ((good, "1080x1920"), (low, "512x910")):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s={size}:d=2:r=30", "-f", "lavfi",
                        "-i", "sine=d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(out)], check=True)
    assert qa.export_problems(media.probe(good), black=qa.black_frames(good)) == []
    assert "512x910" in qa.export_problems(media.probe(low))[0]


def test_the_gate_refuses_a_wrong_export_size_and_new_looks_paint_bigger_pictures(monkeypatch):
    from studio import art, footage, preflight, video, __main__ as cli
    _s, rep = preflight.run({"lines": [L("Why?", "hook")]}, "Why?", [{"type": "headline", "text": "Why?"}],
                            size=(512, 910))
    assert not rep["ok"] and rep["problems"][-1]["area"] == "technical"
    for mod, name in ((art, "gen_size"), (art, "shots_on"), (video, "type_style"), (footage, "photos_on")):
        monkeypatch.setattr(mod, name, getattr(mod, name))          # put back after the test
    cli._new_looks(["--new-looks"])
    assert art.gen_size() == (art.HD_W, art.HD_H)
