"""Storyboard shots (a new picture every 1.5-2 s), the wave and ruler diagrams, and the storyboard's own sound and
transition per cut. All of it waits behind config "shots" (off until Connor approves a sample)."""
import json
from pathlib import Path

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401
from test_studio_better_shorts import three, _upgraded_runner  # noqa: F401


def test_shots_and_new_diagrams_ship_switched_off(studio_home):  # noqa: F811
    from studio import art, config
    assert art.shots_on() is False
    config.set_values(shots=True)
    assert art.shots_on() is True


def test_board_fields_check_every_storyboard_value():
    from studio import video
    line = "A microwave's waves bounce between the metal walls and pile up into hot and cold spots"
    src = {"purpose": "explain: why spots form", "transition": "fade", "sfx": "rise",
           "shots": [{"art": "ignored, the scene's own picture opens it", "camera": "pan-left"},
                     {"art": "waves bouncing inside a metal box", "camera": "spin", "shows": "waves bounce"},
                     {"art": "a chocolate bar with melted stripes", "camera": "close-up"},
                     {"art": "a fourth shot is one too many"}]}
    sc = video.board_fields({"type": "headline", "text": "x", "art": "a microwave oven, door open"}, src, line)
    assert sc["purpose"] == "explain: why spots form" and sc["transition"] == "fade" and sc["sfx"] == "rise"
    assert len(sc["shots"]) == 3 and sc["shots"][0]["art"] == "a microwave oven, door open"
    assert sc["shots"][1]["camera"] in video.CAMERAS and sc["shots"][2]["camera"] == "close-up"
    old = video.board_fields({"type": "headline", "text": "x"}, {}, "Short line here")
    assert "shots" not in old and old["transition"] == "cut" and old["sfx"] == "whoosh"   # renders as before
    assert video.shots_for("Five words in this line") == 1 and video.shots_for(line) == 3


def test_shot_times_change_the_picture_every_couple_of_seconds():
    from studio import video
    t = video.shot_times(4.8, 3)
    assert t[0] == 0 and len(t) == 3 and all(1.2 <= b - a <= 2.2 for a, b in zip(t, t[1:] + [4.8]))


def test_storyboard_camera_words_pick_the_move():
    from studio import motion
    assert motion.move_for(0, "pan-left") == motion.BY_CAMERA["pan-left"]
    assert motion.move_for(5, "something odd") == motion.MOVES[5 % len(motion.MOVES)]


def test_wave_diagram_spaces_hot_spots_half_a_wavelength_apart():
    from studio import edit, video
    ov = edit.overlay({"type": "wave", "label": "Microwaves inside the oven", "spot": "hot spot", "span": "gap",
                       "value": "6.1 cm"})
    g = ov["geo"]
    assert ov["kind"] == "wave" and ov["value"] == "6.1 cm" and ov["spot"] == "hot spot"
    step = g["peaks"][1] - g["peaks"][0]
    assert abs((g["b"] - g["a"]) - step) < 0.5                      # neighbouring hot spots: one gap
    full = edit.overlay({"type": "wave", "span": "wavelength", "value": "12.2 cm"})["geo"]
    assert abs((full["b"] - full["a"]) - 2 * step) < 0.5             # a full wavelength is two gaps
    # the peaks sit halfway between the still points (nodes), like a real standing wave
    assert all(abs(p - (a + b) / 2) < 0.2 for p, a, b in zip(g["peaks"], g["nodes"], g["nodes"][1:]))
    assert video.inside_safe_area(edit.overlay_box(ov))
    assert video._valid({"type": "wave"}) and not video._valid({"type": "wave", "span": "sideways"})


def test_ruler_diagram_keeps_the_last_counter():
    from studio import edit, video
    sc = {"type": "measure", "from": "hot spot", "to": "next hot spot", "value": "6.1", "unit": "cm",
          "label": "the gap the turntable fixes"}
    ov = edit.overlay(sc)
    assert ov["kind"] == "measure" and ov["value"] == "6.1 cm" and ov["to"] == "next hot spot"
    assert video._valid(sc) and not video._valid({"type": "measure", "value": "6.1"})
    h = video.huds([{"type": "number", "value": "2.45", "unit": "GHz", "label": "oven"}, sc])
    assert h[1].get("carried") and h[1]["value"] == "2.45 GHz"         # the ruler shows 6.1 cm itself


def test_classic_look_draws_the_new_diagrams_inside_the_safe_area():
    from studio import video
    pal = video.palette()
    for sc in ({"type": "wave", "label": "Standing wave", "span": "wavelength", "value": "12.2 cm"},
               {"type": "measure", "from": "hot spot", "to": "next one", "value": "6.1", "unit": "cm", "label": "gap"}):
        img, box = video.draw_scene(sc, pal)
        assert img.size == (1080, 1920) and box[2] > box[0] and video.inside_safe_area(box), sc["type"]


def test_planner_is_offered_the_new_diagrams_only_when_switched_on(three, monkeypatch):  # noqa: F811
    from studio import claude, config, jobs, script, topics
    fc, tid = three
    seen = []
    base = claude.RUNNER

    def runner(prompt, timeout, web):
        if prompt.startswith("Plan the on-screen visual"):
            seen.append(prompt)
            return "[]"
        return base(prompt, timeout, web)
    monkeypatch.setattr(claude, "RUNNER", runner)
    from studio import video
    t = topics.topic(tid)
    vid = jobs.create(tid)
    script.draft_step(t, vid)
    row = script.latest(vid, "draft")
    video.plan(row)
    config.set_values(shots=True)
    video.plan(row)
    assert "- wave:" not in seen[0] and "- measure:" not in seen[0]
    assert "- wave:" in seen[1] and "- measure:" in seen[1]


def test_board_cues_carry_the_sound_and_transition():
    from studio import edit
    cues = [{"t": 2.0, "kind": "cut", "scene": 1}, {"t": 4.0, "kind": "cut", "scene": 2}, {"t": 4.1, "kind": "pop", "scene": 2}]
    scenes = [{}, {"sfx": "none", "transition": "fade"}, {"sfx": "whoosh", "transition": "cut"}]
    out = edit.board_cues(cues, scenes)
    assert out[0]["sfx"] == "none" and out[0]["transition"] == "fade"
    assert "sfx" not in out[1] and "transition" not in out[1] and "sfx" not in out[2]


def test_a_cut_marked_none_is_silent():
    import numpy as np
    from studio import sound
    assert sound.track([{"t": 1.0, "kind": "cut", "sfx": "none"}], 3) is None
    for fx in ("pop", "tick", "rise", "whoosh"):
        buf = sound.track([{"t": 1.0, "kind": "cut", "sfx": fx}], 3)
        assert buf is not None and float(np.max(np.abs(buf))) <= 0.4, fx


def test_a_short_with_storyboard_shots_end_to_end(three, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from clipper import media
    from studio import config, db, engines
    from test_studio_video import SCENES, _make
    seen, got = [], {}
    monkeypatch.setattr(engines, "RUNNER", _upgraded_runner(seen, got))
    config.set_values(outputs_dir=str(tmp_path / "D-Jarvis" / "studio"), shots=True, type_style="bold")
    engines.use(True, ["images", "look", "compose", "sharpen", "motion", "edit", "sfx"])
    shots = [{"art": "first", "camera": "push-in"}, {"art": "a rocket stage falling away", "camera": "pan-left",
                                                     "shows": "the stage drops"},
             {"art": "a lighter rocket climbing", "camera": "tilt-up"}]
    scenes = [dict(s, art="a launch pad at dawn", shots=shots, sfx="pop" if k == 2 else "whoosh",
                   transition="zoom" if k == 3 else "cut") for k, s in enumerate(SCENES)]
    vid, states = _make(three, fake_voice, monkeypatch, scenes=scenes)
    assert states[-1] == "ready", db.video(vid)["error"]
    used = json.loads(db.video(vid)["meta"])["render"]["engines"]
    prompts = [im["prompt"] for t, p in seen if t == "sdxl" and "images" in p for im in p["images"]]
    assert any("a rocket stage falling away" in x for x in prompts)
    sc = got["scenes"]
    with_shots = [s for s in sc if s.get("shots")]
    assert with_shots, used
    folder = Path(db.video(vid)["path"]).parent
    for s in with_shots:
        span = s["end"] - s["start"]
        assert all(0 < sh["at"] < span for sh in s["shots"])
        for sh in s["shots"]:
            info = media.probe(folder / sh["video"])
            assert (info["width"], info["height"]) == (1080, 1920)
    assert sc[3].get("transition") == "zoom" and "transition" not in sc[2]
    assert any(c["kind"] == "cut" and c.get("sfx") == "pop" for c in got["fx"])
    assert got["type"] == "bold" and all(len(pg["words"]) <= 7 for pg in got["pages"][1:]) and used["type"].startswith("bold")
    qa_row = db.one("SELECT * FROM qa_checks WHERE video_id=? AND check_name='picture keeps changing'", vid)
    assert qa_row and qa_row["passed"] == 1
    assert not db.q("SELECT check_name FROM qa_checks WHERE video_id=? AND passed=0", vid)


# ---------- the bold type system and the picture checks ----------

def _timed(lines):
    words, t, starts = [], 0.2, [0.0]
    for k, ln in enumerate(lines):
        if k:
            starts.append(round(t, 3))
        for w in ln.split():
            words.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3)})
            t += 0.36
    return words, starts + [round(t + 0.3, 3)]


def test_bold_captions_are_short_timed_and_highlight_numbers(studio_home):  # noqa: F811
    from studio import config, video
    assert video.type_style() == "serif"                       # the reference look stays until Connor's OK
    config.set_values(type_style="bold")
    assert video.type_style() == "bold"
    lines = ["Why does your microwave have a spinning plate?",
             "Microwaves at 2.45 GHz have a wavelength of about 12.2 centimetres.",
             "Hot spots sit half a wave apart, so the turntable moves food through them and it heats evenly."]
    words, starts = _timed(lines)
    pages, boxes = video._bold_caption_props(lines, words, starts, ["hook", "setup", "payoff"])
    assert pages[0]["text"] == "" and all(p["s"] >= starts[1] for p in pages[1:])   # the hook is the title
    pages = pages[1:]
    assert all(1 <= len(p["words"]) <= video.BOLD_WORDS + 1 for p in pages)
    assert [i for p in pages for i in p["words"]] == list(range(len(lines[0].split()), len(words)))   # every word once
    assert all(a["e"] <= b["s"] + 1e-6 for a, b in zip(pages, pages[1:]))
    num = [p for p in pages if "2.45" in p["text"]][0]
    assert num["text"].split().index("2.45") in num["hi"]
    assert any(p.get("reveal") for p in pages) and not any(p.get("reveal") for p in pages if p["s"] < starts[2])
    assert any(p["text"].endswith("apart,") for p in pages)             # the break lands after punctuation
    assert all(b[1] + b[3] <= 1920 * 0.80 and b[0] >= 40 and b[0] + b[2] <= 1080 - 120 for b in boxes)


def test_bold_hook_title_is_drawn_inside_the_safe_area():
    from PIL import Image
    from studio import video
    bg = Image.new("RGB", (1080, 1920), (30, 40, 70))
    img, box = video.draw_cinematic(bg, {"label": "FREQUENCY", "value": "2.45 GHz"}, video.palette(),
                                    title="Why does your microwave have a spinning plate?", bold=True)
    assert video.inside_safe_area(box)


def _clip(path, frames):
    import cv2
    import numpy as np
    from clipper import media
    tmp = Path(str(path) + ".raw.avi")
    w = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"MJPG"), 30, (108, 192))
    for f in frames:
        w.write(np.ascontiguousarray(f))
    w.release()
    media.run(["-y", "-i", str(tmp), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    return path


def test_still_stretches_and_repeated_pictures(tmp_path):
    import cv2
    import numpy as np
    from studio import qa
    moving = [np.full((192, 108, 3), (i * 3) % 255, np.uint8) for i in range(30 * 6)]
    still = [np.full((192, 108, 3), 90, np.uint8)] * (30 * 6)
    assert qa.still_stretches(_clip(tmp_path / "m.mp4", moving)) == []
    s = qa.still_stretches(_clip(tmp_path / "s.mp4", moving[:30] + still))
    assert len(s) == 1 and s[0][1] > qa.STATIC_MAX
    a = np.random.default_rng(1).integers(0, 255, (192, 108, 3), dtype=np.uint8)
    b = np.random.default_rng(2).integers(0, 255, (192, 108, 3), dtype=np.uint8)
    for n, im in (("a", a), ("a2", a), ("b", b)):
        cv2.imwrite(str(tmp_path / f"{n}.png"), im)
    assert qa.repeated_pictures([tmp_path / "a.png", tmp_path / "b.png", tmp_path / "a2.png", None]) == [(1, 3)]


def test_visual_qc_advises_by_default_and_can_block(studio_home):  # noqa: F811
    from studio import config, qa
    assert qa.visual_mode() == "advise"
    config.set_values(visual_qc="block")
    assert qa.visual_mode() == "block"


def test_new_looks_flag_is_for_one_run_and_leaves_settings_alone(studio_home, monkeypatch):  # noqa: F811
    from studio import __main__ as cli, art, config, video
    monkeypatch.setattr(art, "shots_on", art.shots_on)
    monkeypatch.setattr(video, "type_style", video.type_style)
    assert cli._new_looks([]) is False and art.shots_on() is False
    assert cli._new_looks(["--new-looks"]) is True
    assert art.shots_on() is True and video.type_style() == "bold"
    assert "shots" not in config.general() and "type_style" not in config.general()


def test_hand_made_short_timings_are_shown(studio_home):  # noqa: F811
    from studio import timings
    timings.add(77, "SDXL-Turbo pictures", 120, "heavy")
    assert "made by hand" in timings.text(77) and "SDXL-Turbo pictures" in timings.text(77)
    timings.add(77, "rendering", 400, "stage")
    assert "of work" in timings.text(77)


def test_a_ruler_does_not_repeat_its_number_in_the_counter():
    from studio import video
    h = video.huds([{"type": "number", "value": "924", "unit": "mph", "label": "speed"},
                    {"type": "measure", "from": "ground", "to": "jet", "value": "55,000", "unit": "ft"}])
    assert h[1] is None or h[1].get("value") != "55,000 ft"
