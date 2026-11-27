"""Pipeline order review (2026-11-27): newcomer facts found before writing, a free newcomer read on every draft,
the draft-picking call judging story and originality the fact-check's way with one repair before the paid check,
story repairs re-checked without a full web fact-check, and the stills check before the render."""
import json
import urllib.parse

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_better_shorts import BEATS, _draft  # noqa: F401
from test_studio_script import BODY, FakeClaude, fakes  # noqa: F401

FACE_TEXT = ("The Moon is in synchronous rotation with Earth, so it always shows Earth the same face. "
             "Its orbit takes about 27.3 days. The far side was first photographed in 1959.")


def _wiki(calls, text=FACE_TEXT):
    def answer(url):
        calls.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if q.get("list") == "search":
            return json.dumps({"query": {"search": [{"title": "Tidal locking"}]}})
        return json.dumps({"query": {"pages": {"1": {"extract": text}}}})
    return answer


def _topic(newcomer):
    from studio import db, topics
    tid = db.insert("INSERT INTO topics(title, angle, fingerprint, pillar, kind, status, scores) VALUES(?,?,?,?,?,?,?)",
                    "Why we never see the Moon's far side", "one face", "fp-face", "Space", "question", "idea",
                    json.dumps({"newcomer": newcomer, "enriched": 0}))
    for claim, url in (("The Moon orbits Earth every 27.3 days", "https://example.com/a"),
                       ("The Moon is 384,400 km away", "https://example.com/b")):
        db.insert("INSERT INTO sources(topic_id, claim, url, title, verdict, domain, tier, http_status) "
                  "VALUES(?,?,?,?,?,?,?,?)", tid, claim, url, "src", "reachable", topics.domain(url), 1, 200)
    return topics.topic(tid)


# ---------- idea stage ----------

def test_rating_lists_newcomer_facts_and_too_many_drop_the_idea():
    from studio import topics
    r = {"newcomer": ["the Moon always shows Earth the same face", "none", "x"]}
    assert topics.newcomer_of(r) == ["the Moon always shows Earth the same face"]
    many = {"newcomer": [f"background fact number {i} for a viewer" for i in range(5)], "relevance": 8,
            "feasibility": 8, "originality": 8}
    _, factors = topics.score_one(many, 8)
    why = topics.gate({"_checked": [{"http_status": 200}, {"http_status": 200}]}, many, 8, factors)
    assert any("too much background" in w for w in why)


def test_background_facts_are_found_word_for_word_and_saved_once(studio_home, monkeypatch):  # noqa: F811
    from studio import topics
    calls = []
    monkeypatch.setattr(topics, "API", _wiki(calls))
    monkeypatch.setattr(topics, "PAGE", lambda url: "")
    t = _topic(["the Moon always shows Earth the same face", "the Sun lights half the Moon at all times"])
    bg = topics.background(t)
    assert list(bg["found"]) == ["the Moon always shows Earth the same face"]
    assert "always shows Earth the same face" in bg["found"]["the Moon always shows Earth the same face"]
    assert bg["missing"] == ["the Sun lights half the Moon at all times"]
    added = [s for s in topics.topic(t["id"])["sources"] if s["note"] == topics.BACKGROUND_NOTE]
    assert len(added) == 1 and added[0]["url"].startswith("https://en.wikipedia.org/wiki/")
    n = len(calls)
    assert topics.background(topics.topic(t["id"])) == bg and len(calls) == n      # once per topic


def test_prepare_skips_an_idea_whose_set_up_no_page_states(studio_home, monkeypatch):  # noqa: F811
    from studio import topics
    monkeypatch.setattr(topics, "API", _wiki([], text="Nothing relevant is written on this page at all, sorry."))
    monkeypatch.setattr(topics, "PAGE", lambda url: "")
    t = _topic(["the Moon always shows Earth the same face", "the Sun lights half the Moon at all times"])
    why = topics.prepare(t)
    assert why and "background no source states" in why


def test_offline_never_counts_as_missing(studio_home, monkeypatch):  # noqa: F811
    from studio import topics

    def down(url):
        raise OSError("offline")
    monkeypatch.setattr(topics, "API", down)
    monkeypatch.setattr(topics, "PAGE", lambda url: "")
    t = _topic(["the Moon always shows Earth the same face", "the Sun lights half the Moon at all times"])
    assert topics.prepare(t) is None


def test_writer_gets_the_newcomer_facts_with_their_ids(studio_home, monkeypatch):  # noqa: F811
    from studio import script, topics
    monkeypatch.setattr(topics, "API", _wiki([]))
    monkeypatch.setattr(topics, "PAGE", lambda url: "")
    t = _topic(["the Moon always shows Earth the same face", "the Sun lights half the Moon at all times"])
    topics.background(t)
    t = topics.topic(t["id"])
    txt = script.newcomer_text(t, script._facts(t))
    assert "SAY THESE FIRST" in txt and "same face (F3)" in txt and "no page states it" in txt


# ---------- the editor and the newcomer read, before the paid fact-check ----------

def test_editor_problems_follow_the_fact_checks_rules():
    from studio import script
    lines = [{"beat": b} for b in BEATS]
    assert script.editor_problems({"original": 7, "gaps": [{"line": 4, "missing": "x"}]}, lines) == []
    p = script.editor_problems({"original": 5, "original_fix": "use a fridge", "gaps": []}, lines)
    assert p and p[0].startswith("originality 5/10") and "fridge" in p[0]
    p = script.editor_problems({"original": 8, "gaps": [{"line": 2, "missing": "what a stage is"}]}, lines)
    assert p == ["narrative: line 2 leans on something never established (what a stage is)"]
    assert script.STORY_LINE.match(p[0])
    assert script.read_problems({"match": 4, "confusing": [{"line": 3, "why": "what is a stage"}]})
    assert script.editor_penalty({"original": 5, "gaps": [{"line": 3}]}) > script.editor_penalty({"original": 7})


class EditorClaude(FakeClaude):
    """Three drafts; the judge likes draft A best as a viewer, but the editor finds A too ordinary."""

    def __init__(self):
        super().__init__()
        self.judge_prompts = []

    def __call__(self, prompt, timeout, web):
        if prompt.startswith("Write a YouTube Short script") and '"drafts"' in prompt:
            self.writes += 1
            return json.dumps({"drafts": [
                _draft("the textbook one", "Why do rockets throw away 88 percent of themselves?"),
                _draft("a hiker's water bottles", "What does a rocket do with 2,290 tonnes?")]})
        if prompt.startswith("Judge these drafts"):
            self.judge_prompts.append(prompt)
            return json.dumps({"scores": {"A": {"hook": 9, "payoff": 9, "clarity": 9, "momentum": 9, "you": 9, "original": 9},
                                          "B": {"hook": 8, "payoff": 8, "clarity": 8, "momentum": 8, "you": 8, "original": 8}},
                               "best": "A", "why": "x", "notes": {},
                               "editor": {"A": {"original": 4, "original_fix": "", "premise": 8, "gaps": []},
                                          "B": {"original": 7, "original_fix": "", "premise": 8, "gaps": []}}})
        return super().__call__(prompt, timeout, web)


def test_the_pick_uses_the_fact_checks_originality_ruler(fakes, monkeypatch):  # noqa: F811
    from studio import claude, script, topics
    _, tid = fakes
    fc = EditorClaude()
    monkeypatch.setattr(claude, "RUNNER", fc)
    d = script.write(topics.topic(tid))
    assert d["picked"]["angle"] == "a hiker's water bottles" and d["editor"]["original"] == 7
    assert "fact-checker's scale" in fc.judge_prompts[0] and '"gaps"' in fc.judge_prompts[0]


def test_one_repair_before_the_fact_check(fakes, monkeypatch):  # noqa: F811
    from studio import script, topics
    _, tid = fakes
    t = topics.topic(tid)
    asked = []
    weak = dict(script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?")),
                editor={"original": 5, "original_fix": "use a fridge", "premise": 8, "gaps": []})
    better = script._clean(_draft("y", "What does a rocket do with 2,290 tonnes?"))

    def fake_write(topic, feedback=None, previous=None, only_originality=False, keep_original=False):
        asked.append((feedback, only_originality))
        return dict(better)
    monkeypatch.setattr(script, "write", fake_write)
    out = script._pre_fix(weak, t)
    assert asked and asked[0][1] is True and asked[0][0][0].startswith("originality 5/10")
    assert out["pre_fix"]["kind"] == "originality" and out["editor"]["original"] == 5
    assert out["hooks"] == better["hooks"]
    asked.clear()
    fine = dict(weak, editor={"original": 8, "premise": 8, "gaps": []})
    assert script._pre_fix(fine, t) is fine and not asked                   # nothing found: no call


def test_a_clean_read_before_the_check_is_not_read_again(monkeypatch):
    from studio import script
    d = script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?"))
    hook = d["hooks"][0]
    read = {"takeaway": "rockets drop weight", "match": 5, "confusing": [], "body": script.text_of(d, hook)}
    monkeypatch.setattr(script, "newcomer_read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read again")))
    out = script.explain_back({"title": "t"}, d, hook, None, read=read)
    assert out == {"takeaway": "rockets drop weight", "match": 5, "confusing": []}


# ---------- story repairs: only the new lines and the story are checked again ----------

def test_story_recheck_plan_matches_lines_by_their_words():
    from studio import script
    old = script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?"))
    prior = {"hook_ratings": {"0": {}}, "loop": 7, "originality": 7, "hard_problems": ["narrative: line 3 leans on x"]}
    new = dict(old, lines=old["lines"][:2] + [{"text": "A stage is a rocket section that falls off.", "beat": "setup",
                                               "facts": ["F1"]}] + old["lines"][2:])
    plan = script.story_recheck_plan(old, new, prior)
    assert plan and plan["changed"] == {3} and plan["story"] and plan["kept"][4] == 3
    loop_changed = dict(old, lines=old["lines"][:-1] + [dict(old["lines"][-1], text="A new loop line here.")])
    assert script.story_recheck_plan(old, loop_changed, prior) is None
    assert script.story_recheck_plan(old, new, dict(prior, originality=None)) is None


def test_story_recheck_asks_the_story_again_without_the_web(fakes, monkeypatch):  # noqa: F811
    from studio import claude, script, topics
    _, tid = fakes
    t = topics.topic(tid)
    old = script._clean(_draft("x", "Why do rockets throw away 88 percent of themselves?"))
    new = dict(old, lines=old["lines"][:2] + [{"text": "A stage is a rocket section that falls off.", "beat": "setup",
                                               "facts": ["F1"]}] + old["lines"][2:])
    prior = {"hook_ratings": {"0": {}}, "loop": 7, "originality": 7, "rehook": 7, "hard_problems": [],
             "claims": [{"line": i, "verdict": "supported"} for i in range(1, 8)]}
    plan = script.story_recheck_plan(old, new, prior)
    seen = []

    def runner(prompt, timeout, web):
        seen.append((prompt, web))
        return json.dumps({"claims": [{"line": 3, "verdict": "supported", "fact": "F1"}], "problems": [],
                           "story": {"premise_clear": 9, "model_valid": True, "continuity": [], "conclusion_follows": True,
                                     "stranger": {}}, "question": "why", "answered": 9})
    monkeypatch.setattr(claude, "RUNNER", runner)
    ans = script._recheck(new, t, "F1. x", "L1. x", plan, unread=set())
    assert len(seen) == 1 and seen[0][1] is False and "STORY CHECK" in seen[0][0]
    assert ans["story"]["premise_clear"] == 9 and ans["answered"] == 9 and ans["loop"] == 7
    script._recheck(new, t, "F1. x", "L1. x", plan, unread={"F1"})
    assert seen[1][1] is True                     # a new line on a page not read here: the web is used


# ---------- the stills check ----------

def test_stills_check_is_off_by_default_and_swaps_a_bad_picture(studio_home, monkeypatch, tmp_path):  # noqa: F811
    from PIL import Image
    from studio import art, claude, config, finalreview, video
    raws = {}
    for k in range(3):
        p = tmp_path / f"raw_{k}.png"
        Image.new("RGB", (64, 112), (200, 100, 50)).save(p)
        raws[k] = p
    assert finalreview.stills_check(1, raws, ["a", "b", "c"]) is None
    config.set_values(stills_check=True)
    seen = []

    def runner(prompt, timeout, web, **kw):
        seen.append(prompt)
        return json.dumps({"problems": [{"scene": 2, "issue": "black blocks across the photo", "severity": "major"},
                                        {"scene": 3, "issue": "a bit dark", "severity": "minor"}]})
    monkeypatch.setattr(claude, "RUNNER", runner)
    scenes = [{}, {"shots": [{"a": 1}, {"b": 2}]}, {}]
    photos, used, meta = {1: {"title": "moon"}}, {}, {}
    video._stills_check(1, scenes, ["a", "b", "c"], raws, photos, tmp_path, used, meta)
    assert seen and "a real photo" in seen[0]
    assert raws[1].name == "plain_02.png" and 1 not in photos and len(scenes[1]["shots"]) == 1
    assert raws[0].name == "raw_0.png" and meta["stills_check"]["replaced"] == [2]
    assert "replaced scene(s) 2" in used["stills"]
    assert art.plain is not None
