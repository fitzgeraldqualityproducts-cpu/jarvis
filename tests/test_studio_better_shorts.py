"""Better Shorts: side-by-side drafts with a judge, a re-hook and beats in every script, delivery by beat."""
import json
from pathlib import Path

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import BODY, FakeClaude, fakes  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401

BEATS = ["hook", "setup", "setup", "math", "rehook", "payoff", "loop"]


def _draft(angle, hook, calc_result=0.77, lines=None):
    return {"angle": angle,
            "hooks": [hook, "What if a rocket kept its empty tanks?"],
            "lines": [{"text": t, "facts": ["F1"], "visual": "diagram", "beat": b} for t, b in zip(lines or BODY, BEATS + ["setup"] * 40)],
            "calcs": [{"expr": "2290/2970", "result": calc_result, "unit": "", "says": "share"}],
            "titles": ["Why rockets throw parts away"]}


class ThreeDrafts(FakeClaude):
    """The writer returns three drafts at once; the judge prefers the last good one."""

    def __init__(self):
        super().__init__()
        self.judged = 0
        self.prompts = []

    def __call__(self, prompt, timeout, web):
        if prompt.startswith("Write a YouTube Short script") and '"drafts"' in prompt:
            self.prompts.append(prompt)
            self.writes += 1
            return json.dumps({"drafts": [
                _draft("a kitchen scale", "Why do rockets throw away 88 percent of themselves?"),
                _draft("broken maths", "Why do rockets throw away 88 percent?", calc_result=0.5),
                _draft("a hiker's water bottles", "What does a rocket do with 2,290 tonnes?")]})
        if prompt.startswith("Judge these drafts"):
            assert not web and "Draft A" in prompt and "Draft B" in prompt and "Draft C" not in prompt
            self.judged += 1
            return json.dumps({"scores": {"A": {"hook": 6, "payoff": 6, "clarity": 7, "momentum": 5, "you": 4, "original": 5},
                                          "B": {"hook": 9, "payoff": 8, "clarity": 8, "momentum": 8, "you": 8, "original": 8}},
                               "best": "B", "why": "the water bottles make the drop easy to picture",
                               "notes": {"A": "the hook hands over the answer before asking the question"}})
        return super().__call__(prompt, timeout, web)


@pytest.fixture
def three(fakes, monkeypatch):
    from studio import claude
    fc = ThreeDrafts()
    monkeypatch.setattr(claude, "RUNNER", fc)
    return fc, fakes[1]


def test_three_drafts_are_screened_and_judged(three):
    from studio import jobs, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    st = json.loads(row["structure"])
    assert fc.writes == 1 and fc.judged == 1 and row["status"] == "passed"
    p = fc.prompts[0]
    assert "RE-HOOK" in p and '"beat"' in p and "you" in p and "Write 3 COMPLETE drafts" in p
    picked = st["picked"]
    assert picked["of"] == 3 and picked["passed_screen"] == 2 and picked["angle"] == "a hiker's water bottles"
    assert "1" in picked["dropped"] and "not 0.5" in picked["dropped"]["1"][0]
    assert [ln["beat"] for ln in st["lines"]] == BEATS


def test_revision_and_drafts_setting_write_one(fakes):
    from studio import config, script, topics
    fc, tid = fakes
    config.set_values(drafts=1)
    d = script.write(topics.topic(tid))
    assert fc.writes == 1 and "picked" not in d
    assert d["lines"][0]["beat"] == "hook" and d["lines"][-1]["beat"] == "loop"   # beats filled in when missing
    assert script._drafts_wanted(["line 2 is wrong"], {"lines": []}) == 1


def test_judge_failure_never_stops_the_script(three, monkeypatch):
    from studio import script, topics
    fc, tid = three
    real = fc.__call__

    def broken(prompt, timeout, web):
        if prompt.startswith("Judge these drafts"):
            return "not json at all"
        return real(prompt, timeout, web)
    from studio import claude
    monkeypatch.setattr(claude, "RUNNER", broken)
    d = script.write(topics.topic(tid))
    assert d["picked"]["angle"] == "a kitchen scale"          # the first good draft


def test_screen_catches_stock_rehooks_and_far_off_length():
    from studio import script
    d = script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?"))
    assert script._screen(d) == []
    d["lines"][4]["text"] = "But wait, it gets stranger."
    assert any("stock phrase" in p for p in script._screen(d))
    long = script._clean(_draft("x", "Why do rockets throw away 88 percent?", lines=BODY * 3))
    assert any("words" in p for p in script._screen(long))
    assert script.judge_score({"hook": 10, "payoff": 10, "clarity": 10, "momentum": 10, "you": 10, "original": 10}) == 10


def test_weak_rehook_is_an_editor_note_not_a_failure(fakes, monkeypatch):
    from studio import claude, jobs, script, topics
    fc, tid = fakes
    real = fc.__call__

    def no_rehook(prompt, timeout, web):
        out = real(prompt, timeout, web)
        if prompt.startswith("You are a strict fact-checker"):
            assert '"rehook"' in prompt
            d = json.loads(out)
            d["rehook"] = 2
            return json.dumps(d)
        return out
    monkeypatch.setattr(claude, "RUNNER", no_rehook)
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    ck = json.loads(row["checks"])
    assert row["status"] == "passed" and ck["rehook"] == 2 and any("re-hook" in p for p in ck["problems"])


def test_delivery_follows_the_beats(three):
    from studio import jobs, narration, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    segs = narration.segments(row["body"], vid)
    assert [s["beat"] for s in segs] == BEATS
    pay = segs[BEATS.index("payoff")]
    plain = segs[1]
    assert pay["exaggeration"] > plain["exaggeration"] and pay["pause_before"] > 0.3
    assert pay["cfg_weight"] < plain["cfg_weight"]
    assert narration.segments(row["body"] + " extra words", vid) is None     # text changed: read it evenly
    assert narration.segments(row["body"], None) is None


def test_chatterbox_gets_the_segments(three, fake_voice, monkeypatch, tmp_path):
    from studio import engines, jobs, narration, script, topics
    fc, tid = three
    vid = jobs.create(tid)
    row = script.make(topics.topic(tid), vid, progress=lambda s: None)
    seen = {}

    def runner(tool, worker, payload, timeout):
        seen.update(payload)
        raise engines.EngineError("not installed here")
    monkeypatch.setattr(engines, "RUNNER", runner)
    monkeypatch.setattr(engines, "heavy", lambda what, wait=900: __import__("contextlib").nullcontext())
    with pytest.raises(engines.EngineError):
        narration._chatterbox(row["body"], narration.settings(), tmp_path / "x.wav", vid)
    assert len(seen["segments"]) == 7 and seen["gap"] == narration.GAP


def test_losing_drafts_become_lessons_for_the_writer(three):
    from studio import db, lessons, script, topics
    fc, tid = three
    t = topics.topic(tid)
    script.write(t)
    rows = [dict(r) for r in db.q("SELECT stage, category, detail FROM lessons")]
    assert {r["stage"] for r in rows} == {"draft"}
    cats = {r["category"] for r in rows}
    assert "calc" in cats                                  # the broken-maths draft that was screened out
    assert {"weak_hook", "weak_you"} <= cats                # the judge's clear gaps for the losing draft
    adv = lessons.advice("script")
    assert "lost on the hook" in adv and "hands over the answer" in adv and "Calculations failed" in adv
    assert lessons.risk(t) == 0                             # a lost draft never marks the idea's pillar as failing
    fc.prompts.clear()
    script.write(t)                                         # the next writer is told
    assert "hands over the answer" in fc.prompts[0]



# ---------- the picture and sound upgrades, all switched on ----------

def _upgraded_runner(seen, got, enhance_fails=False):
    from test_studio_art_music import runner_for
    from studio import engines
    base = runner_for(seen)

    def runner(tool, worker, payload, timeout):
        if tool == "sdxl" and worker == "enhance_worker":
            seen.append(("enhance", payload))
            if enhance_fails:
                raise engines.EngineError("sdxl failed: CUDA out of memory")
            import numpy as np
            from PIL import Image
            for im in payload["images"]:
                src = Image.open(im["src"])
                if im.get("hd"):
                    src.resize((src.width * 4, src.height * 4)).save(im["hd"])
                if im.get("depth"):
                    yy = np.linspace(0, 1, src.height)[:, None] * np.ones((1, src.width))
                    Image.fromarray((yy * 65535).astype(np.uint16)).save(im["depth"])
            return {"images": [{"src": im["src"], "hd_ok": True, "depth_ok": True} for im in payload["images"]]}
        if tool == "remotion":
            got.update(payload["props"])
            from clipper import media
            media.run(["-y", "-f", "lavfi", "-i", f"color=c=0x2B3050:s=1080x1920:r=30:d={payload['props']['duration']}",
                       "-i", f"{payload['public']}/{payload['props']['audio']}", "-shortest", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", payload["out"]])
            return {"path": payload["out"]}
        return base(tool, worker, payload, timeout)
    return runner


def _fake_pexels(monkeypatch, tmp_path):
    from studio import footage
    calls = []

    def search(query, key):
        calls.append(query)
        return {"videos": [
            {"id": 11, "duration": 2, "url": "https://www.pexels.com/video/11/", "user": {"name": "Too Short"},
             "video_files": [{"link": "https://v/11.mp4", "file_type": "video/mp4", "width": 1080, "height": 1920}]},
            {"id": 12, "duration": 30, "url": "https://www.pexels.com/video/12/", "user": {"name": "Ana Lens"},
             "video_files": [{"link": "https://v/12-4k.mp4", "file_type": "video/mp4", "width": 2160, "height": 3840},
                             {"link": "https://v/12-hd.mp4", "file_type": "video/mp4", "width": 1080, "height": 1920},
                             {"link": "https://v/12-sd.mp4", "file_type": "video/mp4", "width": 540, "height": 960},
                             {"link": "https://v/12-wide.mp4", "file_type": "video/mp4", "width": 1920, "height": 1080}]}]}

    def download(url, out):
        from clipper import media
        assert url == "https://v/12-hd.mp4"              # the smallest file that's still sharp, portrait
        media.run(["-y", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=12", "-c:v", "libx264", "-pix_fmt",
                   "yuv420p", str(out)])
    monkeypatch.setattr(footage, "SEARCH", search)
    monkeypatch.setattr(footage, "DOWNLOAD", download)
    footage.save_key("A" * 56)
    return calls


def test_every_upgrade_together(three, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    import numpy as np
    from clipper import media
    from studio import config, db, engines, video
    from test_studio_video import SCENES, _make
    seen, got = [], {}
    monkeypatch.setattr(engines, "RUNNER", _upgraded_runner(seen, got))
    calls = _fake_pexels(monkeypatch, tmp_path)
    config.set_values(outputs_dir=str(tmp_path / "D-Jarvis" / "studio"))
    engines.use(True, ["images", "look", "compose", "sharpen", "motion", "edit", "sfx", "footage"])
    scenes = [dict(s, art="a launch pad at dawn", **({"footage": "rocket launch"} if k in (2, 5) else {}))
              for k, s in enumerate(SCENES)]
    vid, states = _make(three, fake_voice, monkeypatch, scenes=scenes)
    assert states[-1] == "ready", db.video(vid)["error"]
    meta = json.loads(db.video(vid)["meta"])
    used = meta["render"]["engines"]
    assert used["sharpen"].startswith("Real-ESRGAN: 7 of 7") and used["motion"].startswith("depth camera moves: 7")
    assert used["footage"] == "Pexels: 2 scenes" and calls == ["rocket launch", "rocket launch"]
    assert used["sfx"].startswith("Jarvis's own sound effects") and used["edit"].startswith("lively: 4 animated")
    sc = got["scenes"]
    assert sc[2]["motion"] == "footage_03.mp4" and sc[1]["motion"] == "move_02.mp4" and sc[0]["motion"] == "move_01.mp4"
    assert {s.get("overlay", {}).get("kind") for s in sc} >= {"bars", "equation", "steps"}
    assert "overlay" not in sc[0]
    kinds = [c["kind"] for c in got["fx"]]
    assert "cut" in kinds and "payoff" in kinds and "rehook" in kinds
    folder = Path(db.video(vid)["path"]).parent
    for k in range(7):
        f = folder / sc[k]["motion"]
        info = media.probe(f)
        assert (info["width"], info["height"]) == (1080, 1920) and not info["has_audio"]
        assert info["duration"] >= sc[k]["end"] - sc[k]["start"]
    # the first frame of the hook's camera move is exactly the still picture (the loop stays seamless)
    import cv2
    cap = cv2.VideoCapture(str(folder / "move_01.mp4"))
    ok, first = cap.read()
    cap.release()
    bg = cv2.imread(str(folder / "bg_01.png"))
    assert ok and float(np.mean(np.abs(first.astype(int) - bg.astype(int)))) < 3.0
    stock = db.one("SELECT * FROM assets WHERE video_id=? AND kind='footage:scene'", vid)
    assert stock["origin"] == "stock" and "Pexels License" in stock["license"] and "Ana Lens" in stock["attribution"]
    desc = meta["upload"]["description"]
    assert "stock footage from Pexels (Video by Ana Lens on Pexels)" in desc and "other pictures are AI-generated" in desc
    assert not db.q("SELECT check_name FROM qa_checks WHERE video_id=? AND passed=0", vid)


def test_upgrades_fall_back_one_by_one(three, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import db, engines
    from test_studio_video import _make
    seen, got = [], {}
    monkeypatch.setattr(engines, "RUNNER", _upgraded_runner(seen, got, enhance_fails=True))
    engines.use(True, ["images", "look", "compose", "sharpen", "motion", "footage"])
    vid, states = _make(three, fake_voice, monkeypatch)
    assert states[-1] == "ready", db.video(vid)["error"]
    used = json.loads(db.video(vid)["meta"])["render"]["engines"]
    assert "CUDA out of memory" in used["sharpen"] and used["motion"].startswith("flat push-in")
    assert "no Pexels key" in used["footage"] and used["edit"] == "calm" and "sfx" not in used
    assert all("motion" not in s for s in got["scenes"]) and "fx" not in got


def test_cues_and_overlays():
    from studio import edit
    starts = [0, 2.5, 6.5, 9.0, 13.0, 16.0]
    huds = [{"num": 5, "from": 5}, {"num": 2970, "from": 0.0}, None, None, None]
    c = edit.cues(starts, ["hook", "setup", "rehook", "payoff", "loop"], huds, [{"s": 4.6}, {"s": 10.9}, {"s": 14.4}])
    kinds = [(x["kind"], x["scene"]) for x in c]
    assert ("count", 1) in kinds and ("pop", 1) in kinds and ("rehook", 2) in kinds and ("payoff", 3) in kinds
    assert ("cut", 0) not in kinds and ("count", 0) not in kinds          # the hook frame never moves
    assert ("jump", 3) in kinds and not any(k == "jump" and s == 4 for k, s in kinds)   # never in the last scene
    j = [x for x in c if x["kind"] == "jump"][0]
    assert j["t"] == 10.9                                                # on a word near the middle
    ov = edit.overlay({"type": "compare", "unit": "km", "items": [{"label": "Moon", "value": "384,400"},
                                                                   {"label": "ISS", "value": 400}]})
    assert ov["kind"] == "bars" and ov["log"] and ov["note"] == "log scale" and ov["items"][0]["top"]
    assert 0.1 < ov["items"][1]["frac"] < 0.2 and ov["items"][0]["text"] == "384,400 km"
    from studio import video
    assert video.inside_safe_area(edit.overlay_box(ov))
    assert edit.overlay({"type": "equation", "left": "2,290 ÷ 2,970", "right": "0.77"})["terms"] == ["2,290", "÷", "2,970"]
    assert edit.overlay({"type": "headline", "text": "x"}) is None
    assert edit.overlays([{"type": "steps", "items": ["a", "b"]}] * 2) == {1: edit.overlay({"type": "steps", "items": ["a", "b"]})}


def test_sound_effects_stay_under_the_voice(tmp_path, fake_voice):  # noqa: F811
    import numpy as np
    from clipper import media
    from studio import narration, sound
    t = np.arange(48000 * 6) / 48000
    voice = (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    media.write_wav(tmp_path / "v.wav", voice, 48000)
    fx = sound.track([{"t": 1.0, "kind": "cut"}, {"t": 2.0, "kind": "count", "until": 4.0}, {"t": 4.0, "kind": "pop"},
                      {"t": 5.0, "kind": "payoff"}], 6)
    assert fx is not None and np.isfinite(fx).all() and float(np.max(np.abs(fx))) <= 0.4
    ticks = np.flatnonzero(np.abs(fx[int(2.0 * 48000):int(4.0 * 48000)]) > 0.02)
    assert ticks.size and ticks[-1] / 48000 < 1.5                         # the ticks stop well before the number lands
    out = sound.mix(tmp_path / "v.wav", fx, tmp_path / "m.wav", 6)
    x, rate = narration.read_any_wav(out)
    assert rate == 48000 and float(np.max(np.abs(x))) <= 0.85


def test_depth_move_parallax():
    import numpy as np
    from studio import motion
    depth = np.zeros((1920, 1080), np.float32)
    depth[960:] = 1.0                                                     # the bottom half is near
    mx, my = motion.frame_maps(depth, 1.0, ("push in", 0.13, 0, 0))
    far = abs(my[200, 540] - 200)                                         # how far each pixel moved
    near = abs(my[1700, 540] - 1700)
    assert near > far > 0
    mx0, my0 = motion.frame_maps(depth, 0.0, ("drift right", 0.07, 46, 0))
    assert np.allclose(mx0[100, :5], np.arange(5)) and motion.move_for(5)[0] == "drift right"


def test_pexels_key_and_choice(studio_home):  # noqa: F811
    from studio import footage
    assert not footage.ready()
    with pytest.raises(ValueError):
        footage.save_key("not a key!")
    footage.save_key("B" * 40)
    assert footage.ready() and footage.key() == "B" * 40
    assert footage.choose({"videos": [{"id": 1, "duration": 3, "video_files": [
        {"link": "x", "file_type": "video/mp4", "width": 1080, "height": 1920}]}]}, 5) is None   # too short
    assert footage.pick_scenes([{"footage": "a"}] * 6, [3, 3, 1, 4, 3, 3]) == [1, 3, 4]       # never the hook, at most half
