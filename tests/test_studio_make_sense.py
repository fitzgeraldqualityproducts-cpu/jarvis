"""Shorts that make sense: one storyboard subject, pictures and footage checked against their line (OpenCLIP),
and the local brain explaining the script back before it is voiced."""
import json

import numpy as np

from test_studio_foundation import studio_home  # noqa: F401


def _png(path, shade):
    from PIL import Image
    Image.fromarray(np.full((896, 512, 3), shade, "uint8"), "RGB").save(path)
    return path


def test_weak_pictures_get_more_takes_and_hopeless_ones_a_plain_background(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import art, engines
    made = []

    def runner(tool, worker, payload, timeout):
        assert (tool, worker) == ("sdxl", "sdxl_worker")
        for im in payload["images"]:
            made.append(im["seed"])
            _png(im["out"], 200)
        return {"ok": True}
    monkeypatch.setattr(engines, "RUNNER", runner)
    # scene 1 fits, scene 2 is weak but a retake fits, scene 3 never fits
    raws = {0: _png(tmp_path / "a.png", 10), 1: _png(tmp_path / "b.png", 10), 2: _png(tmp_path / "c.png", 10)}

    def clip2(images, texts):
        out = []
        for i, p in enumerate(images):
            p = str(p)
            v = 0.3 if p.endswith("a.png") else 0.15 if p.endswith(("b.png", "c.png")) else None
            if v is None:                                   # a retake
                v = 0.26 if "art_02_" in p else 0.1
            out.append([v] * len(texts))
        return out
    monkeypatch.setattr(art, "CLIP_SCORE", clip2)
    scenes = [{"art": "a rocket"}, {"art": "the moon"}, {"art": "lava"}]
    got, rep = art.fit(7, scenes, ["Rockets", "The Moon", "Lava"], dict(raws))
    assert got[0] == raws[0] and "art_02_" in got[1].name
    assert got[2].name == "plain_03.png" and rep["plain"] == [3] and rep["retaken"] == [2]
    assert sorted(made) == [1701, 1702, 2701, 2702]            # two more takes for each weak scene only
    assert rep["scores"][2] == 0.26


def test_the_hook_keeps_a_picture_and_scores_off_means_no_check(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import art, config, engines
    monkeypatch.setattr(engines, "RUNNER", lambda *a: {"ok": True})     # retakes fail to appear
    monkeypatch.setattr(art, "CLIP_SCORE", lambda im, tx: [[0.05] * len(tx) for _ in im])
    raws = {0: _png(tmp_path / "a.png", 10)}
    got, rep = art.fit(1, [{"art": "x"}], ["X"], dict(raws))
    assert got[0] == raws[0] and rep["plain"] == []
    config.set_values(picture_fit=False)
    assert not art.fit_on()


def test_footage_that_doesnt_show_the_line_is_dropped(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from clipper import media
    from studio import art, video
    clips = []
    for k in (1, 2):
        p = tmp_path / f"footage_{k + 1:02d}.mp4"
        media.run(["-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:r=25:d=2", "-pix_fmt", "yuv420p", str(p)])
        clips.append((k, p, {"id": k}))
    monkeypatch.setattr(art, "CLIP_SCORE", lambda im, tx: [[0.3, 0.3], [0.05, 0.05]][:len(im)])
    scenes = [{"art": "a"}, {"art": "moon", "footage": "full moon"}, {"art": "beach", "footage": "beach sunset"}]
    kept, dropped = video._footage_fits(scenes, clips, tmp_path)
    assert [k for k, _, _ in kept] == [1] and dropped == [3]
    monkeypatch.setattr(art, "CLIP_SCORE", lambda im, tx: (_ for _ in ()).throw(RuntimeError("no model")))
    assert video._footage_fits(scenes, clips, tmp_path)[0] == clips          # can't check: keep them all


class FakeLocal:
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def ready(self, part=None):
        return True, ""

    def chat_json(self, messages, **kw):
        self.asked.append(messages[-1]["content"])
        return self.answers.pop(0) if self.answers else None


def _draft():
    return {"hooks": ["The Moon is hotter than boiling water"],
            "lines": [{"text": "The Moon is hotter than boiling water", "beat": "hook"},
                      {"text": "In sunlight its ground reaches 127 C.", "beat": "setup"}]}


def test_explain_it_back_sends_a_confusing_script_back_once(studio_home, monkeypatch):  # noqa: F811
    from studio import script
    fake = FakeLocal([{"takeaway": "Beaches get hot at sunset", "match": 1, "confusing": []}])
    monkeypatch.setattr(script, "_local", lambda: fake)
    topic = {"id": 1, "title": "How hot the Moon gets", "angle": "no air to spread the heat"}
    out = script.explain_back(topic, _draft(), "The Moon is hotter than boiling water", None)
    assert out["unclear"] and out["problem"].startswith("clarity: ") and "Beaches get hot" in out["problem"]
    assert "How hot the Moon gets" in fake.asked[0] and "127 C." in fake.asked[0]
    good = FakeLocal([{"takeaway": "The Moon's day side gets hotter than boiling water", "match": 5}])
    monkeypatch.setattr(script, "_local", lambda: good)
    assert not script.explain_back(topic, _draft(), "x", None).get("unclear")
    # the confused newcomer: a real skipped step sends it back; vague or made-up line numbers don't
    lost = FakeLocal([{"takeaway": "The Moon gets hot", "match": 5,
                       "confusing": [{"line": 2, "why": "why is there no air to cool it?"}, {"line": 9, "why": "nothing here"},
                                     {"line": 1, "why": "?"}]}])
    monkeypatch.setattr(script, "_local", lambda: lost)
    out = script.explain_back(topic, _draft(), "The Moon is hotter than boiling water", None)
    assert out["unclear"] and out["confusing"] == [{"line": 2, "why": "why is there no air to cool it?"}]
    assert "couldn't follow line 2" in out["problem"] and "instead of" not in out["problem"]


def test_explain_it_back_never_costs_a_short(studio_home, monkeypatch):  # noqa: F811
    from studio import config, db, script
    topic = {"id": 1, "title": "t", "angle": "a"}
    miss = {"takeaway": "something else", "match": 1}
    # already sent back once for this Short: not again
    db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", 5, 1, "[]", "h", "b", "{}",
              json.dumps({"explain_back": {"unclear": True}}), "failed", 0)
    monkeypatch.setattr(script, "_local", lambda: FakeLocal([miss]))
    assert not script.explain_back(topic, _draft(), "h", 5).get("unclear")
    # the last allowed try: advice only
    monkeypatch.setattr(script, "_local", lambda: FakeLocal([miss]))
    assert not script.explain_back(topic, _draft(), "h", 6, max_revisions=0).get("unclear")
    # no local brain, or switched off: skipped
    monkeypatch.setattr(script, "_local", lambda: None)
    assert script.explain_back(topic, _draft(), "h", 7) is None
    monkeypatch.setattr(script, "_local", lambda: FakeLocal([miss]))
    config.set_values(explain_back=False)
    assert script.explain_back(topic, _draft(), "h", 7) is None


def test_storyboard_asks_for_one_subject_and_old_answers_still_parse(studio_home, monkeypatch):  # noqa: F811
    from studio import claude, video
    seen = []

    def runner(prompt, timeout, web):
        seen.append(prompt)
        return json.dumps({"subject": "the Moon", "scenes": [{"art": "the Moon's cratered surface in harsh sun"}]})
    monkeypatch.setattr(claude, "RUNNER", runner)
    row = {"hook": "The Moon is hotter than boiling water",
           "structure": json.dumps({"lines": [{"text": "The Moon is hotter than boiling water"},
                                              {"text": "In sunlight its ground reaches 127 C."}]})}
    scenes = video.plan(row)
    assert scenes and isinstance(scenes, list) and "main subject" in seen[0].lower()


def test_best_openings_need_real_evidence_and_reach_the_writer_and_rater(studio_home, monkeypatch):  # noqa: F811
    from studio import db, lessons, packaging
    idx = {}
    for i, (hook, hold) in enumerate([("Your phone is older than the Moon rocks", 0.82), ("A plain one", 0.55),
                                      ("Another plain one", 0.52), ("Weak start", 0.40)], 1):
        vid = db.insert("INSERT INTO videos(state, created, updated) VALUES('published', 0, 0)")
        db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", vid, 1, "[]", hook, "b", "{}", "{}", "passed", 0)
        idx[f"y{i}"] = {"video_id": vid, "index": 50.0, "hook_hold": hold}
    assert packaging.best_openings() == []                      # nothing learned yet
    packaging.learn(dict(list(idx.items())[:2]))
    assert packaging.best_openings() == []                      # under 3 measured Shorts
    packaging.learn(idx)
    best = packaging.best_openings()
    assert [b["hook"] for b in best] == ["Your phone is older than the Moon rocks"]
    assert "older than the Moon rocks" in lessons.advice("script")


def test_a_weak_hook_against_the_best_openings_gets_an_editor_note(studio_home, monkeypatch):  # noqa: F811
    from studio import packaging, script
    monkeypatch.setattr(packaging, "best_openings", lambda n=3: [{"hook": "x", "hold": 0.8}])
    r_weak = {"curiosity": 8, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8, "proven": 3}
    assert script.hook_score(r_weak) < script.hook_score(dict(r_weak, proven=9))
    assert script.hook_score({"curiosity": 8}) == 80.0          # no rating against the best: unchanged


def test_weekly_report_goes_into_the_obsidian_vault(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from studio import obsidian
    v = tmp_path / "Obsidian Vault"
    v.mkdir()
    monkeypatch.setattr(obsidian, "vault", lambda: v)
    lines = obsidian.review({"a": {"title": "Moon heat", "index": 71.0, "views": 1200, "hook_hold": 0.7},
                             "b": {"title": "Dull one", "index": 30.0, "views": 90},
                             "c": {"title": "Typical", "index": 50.0, "views": 400}})
    text = "\n".join(lines)
    assert "## What worked\n\n- Moon heat: index 71" in text and "## What flopped\n\n- Dull one: index 30" in text
    assert "Typical" not in text and "## What changes next" in text
    out = obsidian.write("# Report\n")
    assert out.parent == v / "HussUniversity" / "Weekly reports" and out.read_text().endswith("# Report\n")
    from studio import config
    config.set_values(obsidian_report=False)
    assert obsidian.write("x") is None


def test_payoff_must_answer_the_hooks_question_and_bare_numbers_get_a_note(studio_home):  # noqa: F811
    from studio import lessons, script
    draft = {"hooks": ["How hot does the Moon get?"], "lines": [{"text": "How hot does the Moon get?", "beat": "hook"}],
             "calcs": [{"expr": "2*3", "result": 6, "unit": "x"}]}
    base = {"claims": [{"line": 1, "verdict": "supported", "fact": "F1"}],
            "hooks": {"0": {"curiosity": 9, "clarity": 9, "specificity": 9, "honesty": 9, "speed": 9}}, "loop": 8,
            "rehook": 8, "originality": 8}
    bad = script._judged(draft, dict(base, question="How hot does the Moon get", answered=2, bare_numbers=["127 C"]), None)
    assert any(p.startswith("one question: ") for p in bad["hard_problems"])
    assert any("127 C" in p and p.startswith("editor: ") for p in bad["problems"])
    assert lessons.categorize(bad["hard_problems"][-1]) == "one_question"
    good = script._judged(draft, dict(base, question="q", answered=9, bare_numbers=[]), None)
    assert not any("one question" in p or "comparison" in p for p in good["problems"])
    assert good["answered"] == 9


def _topic():
    return {"id": 1, "title": "Moon heat", "sources": [
        {"claim": "The Moon's day side reaches 127 C", "url": "https://nasa.gov/a", "verdict": "reachable"},
        {"claim": "A lunar day lasts about 29.5 Earth days", "url": "https://nasa.gov/b", "verdict": "reachable"}]}


def test_free_number_check_tags_sourced_numbers_and_looks_up_loose_ones(studio_home):  # noqa: F811
    from studio import script
    draft = {"hooks": ["h"], "calcs": [{"expr": "29.5*24", "result": 708, "unit": "hours"}], "lines": [
        {"text": "The Moon hits 127 C.", "facts": [], "beat": "hook"},
        {"text": "Its day lasts 29.5 of ours.", "facts": [], "beat": "setup"},
        {"text": "That is 708 hours of sun.", "facts": ["F2"], "beat": "math"},
        {"text": "Gravity there is 1.62 m/s^2.", "facts": ["general"], "beat": "setup"},
        {"text": "Its craters reach 450 km across.", "facts": [], "beat": "setup"},
        {"text": "So a 3 step walk is 900 km away.", "facts": ["F1"], "beat": "payoff"}]}
    out, rep = script.tag_numbers(draft, _topic())
    assert out["lines"][1]["facts"] == ["F2"] and 2 in rep["tagged"]
    assert out["lines"][0]["facts"] == ["F1"]                     # the hook is tagged, never turned into a lookup
    assert out["lines"][3]["facts"] == ["general"] and out["lines"][2]["facts"] == ["F2"]
    assert out["lines"][4]["facts"] == ["needs"] and rep["looked_up"] == [5]
    assert out["needs"] == [{"line": 5, "what": "Its craters reach 450 km across.", "value": "450"}]
    assert out["lines"][5]["facts"] == ["F1"]                     # the payoff is left for the fact-check


def test_unsupported_line_gets_a_source_lookup_before_it_is_cut(studio_home, monkeypatch):  # noqa: F811
    from studio import db, script
    lines = [{"text": "The Moon hits 127 C.", "facts": ["F1"], "beat": "hook"},
             {"text": "Its craters reach 450 km across.", "facts": ["F1"], "beat": "setup"},
             {"text": "That's hotter than boiling water.", "facts": ["general"], "beat": "payoff"}]
    st = {"lines": lines, "calcs": [], "titles": []}
    rid = db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", 3, 1, '["h"]', "h", "b", json.dumps(st), "{}", "failed", 0)
    row = dict(db.one("SELECT * FROM scripts WHERE id=?", rid))
    draft = {"hooks": ["h"], "lines": lines, "calcs": []}
    asked = []
    monkeypatch.setattr(script, "look_up", lambda a, t, most=2: (asked.append(a), {2: "F3"})[1])
    rev = {"hard_problems": ["line 2 is unsupported: no source gives the crater size"]}
    new = script._look_up_flagged(_topic(), 3, row, draft, st, rev, {"hard_problems": rev["hard_problems"]})
    assert asked == [{2: {"what": "Its craters reach 450 km across.", "value": "450"}}]
    st2 = json.loads(db.one("SELECT structure FROM scripts WHERE id=?", new)["structure"])
    assert st2["lines"][1]["facts"] == ["F1", "F3"] and st2["lookup_from"] == rid
    # never twice in a row, never for a "wrong" line, nothing found means the usual cut
    assert script._look_up_flagged(_topic(), 3, row, draft, st2, rev, {}) is None
    assert script._look_up_flagged(_topic(), 3, row, draft, st, {"hard_problems": ["line 2 is wrong: x"]}, {}) is None
    monkeypatch.setattr(script, "look_up", lambda a, t, most=2: {})
    assert script._look_up_flagged(_topic(), 3, row, draft, st, rev, {}) is None


def test_stops_report_counts_passes_and_reasons(studio_home):  # noqa: F811
    from studio import db, stops
    def video(state, scripts, err=None):
        vid = db.insert("INSERT INTO videos(state, error, created, updated) VALUES(?, ?, 100, 0)", state, err)
        for i, (status, hard, extra) in enumerate(scripts, 1):
            db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", vid, i, "[]", "h", "b", json.dumps(dict({"lines": []}, **extra)),
                      json.dumps({"hard_problems": hard}), status, 0)
    video("published", [("passed", [], {})])
    video("ready", [("failed", ["line 3 is unsupported: x"], {}), ("passed", [], {"pruned": {}})])
    video("failed", [("failed", ["originality 5/10: generic"], {}), ("failed", ["originality 5/10: x"], {})])
    s = stops.summary(0)
    assert (s["shorts"], s["passed"], s["first_try"], s["cut_lines"], s["stopped"]) == (3, 2, 1, 1, 1)
    assert s["stop_reasons"]["originality"] == 1 and "Passed: 2 of 3" in stops.text(0)
