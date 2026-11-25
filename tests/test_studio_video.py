"""Phase 7: word timings, visual plan, scenes, render, metadata and the QA gate."""
import json
import os

import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import fakes, FakeClaude  # noqa: F401
from test_studio_toolchain import fake_voice  # noqa: F401

SCENES = [{"type": "headline", "text": "Why do rockets throw parts away?"},
          {"type": "number", "value": "2,970", "unit": "tonnes", "label": "Saturn V on the pad"},
          {"type": "compare", "items": [{"label": "Whole rocket", "value": 2970}, {"label": "First stage", "value": 2290}], "unit": "t"},
          {"type": "equation", "left": "2,290 ÷ 2,970", "right": "0.77", "label": "three quarters"},
          {"type": "nonsense"},
          {"type": "scale", "items": [{"label": "Stage 1", "value": 2290}, {"label": "Whole rocket", "value": 2970}], "unit": "t"},
          {"type": "steps", "items": ["Burn", "Drop", "Repeat"], "current": 1}]


def test_word_times_keep_the_exact_script_words(tmp_path, monkeypatch):
    from studio import video
    text = "The Saturn V weighed 2,970 tonnes on the pad."
    heard = [{"w": "The", "s": 0.1, "e": 0.2}, {"w": "Saturn", "s": 0.25, "e": 0.6}, {"w": "five", "s": 0.62, "e": 0.8},
             {"w": "weighed", "s": 0.85, "e": 1.1}, {"w": "2970", "s": 1.15, "e": 1.7}, {"w": "tons", "s": 1.75, "e": 2.0},
             {"w": "on", "s": 2.05, "e": 2.1}, {"w": "the", "s": 2.12, "e": 2.2}, {"w": "pad.", "s": 2.25, "e": 2.5}]
    monkeypatch.setattr(video, "TRANSCRIBE", lambda a: heard)
    words, info = video.word_times("x.wav", text)
    assert [w["w"] for w in words] == text.split()                       # captions show the script, not mishearings
    assert info["method"].startswith("whisper") and info["matched"] >= 0.7
    assert all(words[i]["s"] <= words[i + 1]["s"] for i in range(len(words) - 1))
    assert 0.6 <= words[2]["s"] <= 0.85                                  # "V" placed between its neighbours


def test_even_spread_when_nothing_is_heard(tmp_path, monkeypatch, fake_voice):
    from studio import narration, video
    from test_studio_toolchain import TEXT
    narration.narrate(TEXT, tmp_path / "v.wav")
    monkeypatch.setattr(video, "TRANSCRIBE", lambda a: [])
    words, info = video.word_times(tmp_path / "v.wav", TEXT)
    assert info == {"method": "even spread", "matched": 0.0} and len(words) == len(TEXT.split())


def test_scenes_stay_inside_the_safe_area():
    from studio import video
    pal = video.palette()
    for sc in SCENES[:4] + SCENES[5:] + [{"type": "headline", "text": "word " * 60},
                                         {"type": "number", "value": "123,456,789,000,000", "unit": "km", "label": "x " * 50}]:
        img, box = video.draw_scene(sc, pal, "HussUniversityfr")
        assert img.size == (1080, 1920) and video.inside_safe_area(box), (sc["type"], box)


def test_bad_plan_falls_back(monkeypatch, fakes):
    from studio import claude, video
    assert not video._valid({"type": "compare", "items": [{"label": "a", "value": "x"}]})
    assert video._fallback("It weighed 2,970 tonnes.")["type"] == "number"
    assert video._fallback("Gravity wins.")["type"] == "headline"


def _make(fakes, fake_voice, monkeypatch, scenes=SCENES):
    from studio import claude, config, db, jobs, pipeline, script, video
    fc, tid = fakes
    base = claude.RUNNER

    def runner(prompt, timeout, web):
        if prompt.startswith("Plan the on-screen visual"):
            return json.dumps(scenes)
        return base(prompt, timeout, web)
    monkeypatch.setattr(claude, "RUNNER", runner)

    def heard(audio):
        from clipper import media
        dur = media.probe(audio)["duration"]
        words = script.latest(vid)["body"].split()
        step = (dur - 0.4) / len(words)
        return [{"w": w, "s": 0.2 + i * step, "e": 0.2 + (i + 0.8) * step} for i, w in enumerate(words)]
    monkeypatch.setattr(video, "TRANSCRIBE", heard)
    config.set_values(enabled=True)
    pipeline.register()
    vid = jobs.create()
    states = [jobs.tick() for _ in range(6)]
    return vid, states


def test_full_short_passes_qa(fakes, fake_voice, monkeypatch):
    from clipper import media
    from studio import db, video
    vid, states = _make(fakes, fake_voice, monkeypatch)
    assert states == ["scripting", "fact_checking", "generating_audio", "rendering", "validating", "ready"], db.video(vid)["error"]
    v = db.video(vid)
    info = media.probe(v["path"])
    assert (info["width"], info["height"], info["vcodec"], info["acodec"]) == (1080, 1920, "h264", "aac")
    failed = db.q("SELECT check_name, detail FROM qa_checks WHERE video_id=? AND passed=0", vid)
    assert not failed, [dict(r) for r in failed]
    names = {r["check_name"] for r in db.q("SELECT check_name FROM qa_checks WHERE video_id=?", vid)}
    assert {"fast start", "seamless loop"} <= names                    # hooks start at once, endings loop back
    assets = db.q("SELECT * FROM assets WHERE video_id=?", vid)
    cov = json.loads(db.video(vid)["meta"])["cover"]
    assert os.path.isfile(cov["path"]) and cov["bottom_clear"] and cov["text"]
    assert len(assets) == len(SCENES) + 1 and [a["kind"] for a in assets].count("cover") == 1    # scenes + its cover
    assert all(a["license"].startswith("original") and len(a["sha256"]) == 64 for a in assets)
    up = json.loads(v["meta"])["upload"]
    assert "Sources:" in up["description"] and "https://www.nasa.gov/saturn-v" in up["description"]
    assert "AI-generated" in up["description"] and up["categoryId"] == "27" and up["madeForKids"] is False
    assert os.path.isfile(os.path.join(os.path.dirname(v["path"]), "upload.json"))
    assert json.loads(v["meta"])["scenes"][4]["type"] in ("headline", "number")          # the broken spec fell back


def test_qa_blocks_a_video_with_a_wrong_claim(fakes, fake_voice, monkeypatch):
    from studio import db, qa
    vid, states = _make(fakes, fake_voice, monkeypatch)
    db.insert("INSERT INTO sources(video_id, claim, url, verdict) VALUES(?,?,?,?)", vid, "line 3", "https://x.org", "wrong")
    r = qa.check(vid)
    assert not r["ok"] and [c["name"] for c in r["failed"]] == ["sources"]
