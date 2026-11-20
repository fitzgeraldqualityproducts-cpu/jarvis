"""Sentences, boundaries, context protection, scoring, dedupe, Claude JSON handling."""
import json

from conftest import SCRIPT, make_words


def tr():
    from clipper import transcribe
    return transcribe.build(make_words(SCRIPT, gaps={6: 1.0}))


def test_sentences_and_pauses():
    t = tr()
    assert len(t["sentences"]) == len(SCRIPT)
    assert t["sentences"][7]["question"] is True
    assert t["sentences"][6]["pause_after"] >= 1.0
    w = t["words"]
    assert all(a["e"] <= b["s"] for a, b in zip(w, w[1:]))


def test_finalize_snaps_to_words_and_extends_dangling_start():
    from clipper import moments, settings
    t = tr()
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60)
    c = moments.finalize({"start_id": 5, "end_id": 6, "scores": {}}, t, ch)   # starts with "And then"
    assert c["start_id"] == 4 and "context" in c["notes"][0]
    first = t["words"][t["sentences"][4]["i0"]]
    last = t["words"][t["sentences"][6]["i1"]]
    assert first["s"] - 0.13 <= c["start"] <= first["s"]
    assert last["e"] <= c["end"] <= last["e"] + 0.36
    assert c["transcript"].startswith("I was broke")


def test_too_long_is_rejected():
    from clipper import moments, settings
    ch = dict(settings.channel(), clip_min=5, clip_max=10, clip_hard_max=12)
    assert moments.finalize({"start_id": 0, "end_id": 11, "scores": {}}, tr(), ch) is None


def test_dedupe_and_ranking_with_fake_claude(monkeypatch):
    from clipper import llm, moments, settings
    ch = dict(settings.channel(), clip_min=5, clip_max=40, clip_hard_max=60, min_score=5)
    good = {"hook": 9, "curiosity": 8, "emotion": 7, "education": 7, "entertainment": 6, "story": 8, "surprise": 6,
            "standalone": 9, "relevance": 8, "retention": 8}
    meh = {k: 5 for k in good}
    calls = []

    def fake(prompt, timeout):
        calls.append(prompt)
        if "checking Shorts clips" in prompt:
            return json.dumps([{"clip": 0, "accurate": True, "standalone": True, "verdict": "keep"},
                               {"clip": 1, "accurate": False, "standalone": True, "verdict": "reject", "note": "misleading"}])
        return "Here you go:\n```json\n" + json.dumps([
            {"start_id": 2, "end_id": 6, "hook": "Here's the thing", "hook_type": "bold_claim", "scores": good, "context_complete": 9, "title": "A"},
            {"start_id": 3, "end_id": 6, "scores": good, "context_complete": 8, "title": "dup of A"},
            {"start_id": 7, "end_id": 10, "scores": meh, "context_complete": 7, "title": "B"},
            {"start_id": 99, "end_id": 100, "scores": good}]) + "\n```"
    monkeypatch.setattr(llm, "RUNNER", fake)
    cands, method = moments.find(tr(), ch, {"title": "t"}, max_clips=3)
    assert method == "claude"
    assert [c["title"] for c in cands] == ["A"]               # duplicate dropped, misleading one rejected
    assert cands[0]["verified"] and cands[0]["rank"] == 1


def test_rules_fallback_without_claude(no_llm):
    from clipper import moments, settings
    ch = dict(settings.channel(), clip_min=8, clip_max=30, clip_hard_max=40, min_score=0)
    cands, method = moments.find(tr(), ch, {"title": "t"}, max_clips=2)
    assert method == "rules" and cands
    assert all(not moments.DANGLING.match(c["transcript"]) for c in cands)


def test_learned_nudges_are_capped():
    from clipper import moments, settings
    ch = settings.channel()
    c = {"scores": {k: 6 for k in moments.CRITERIA}, "context_complete": 6, "duration": 30, "hook_type": "question", "context_ok": True}
    base = moments.score(c, ch)
    assert moments.score(c, ch, {"hook_type": {"question": 5.0}}) == round(base + 0.75, 2)
    c["context_ok"] = False
    assert moments.score(c, ch, {"hook_type": {"question": 5.0}}) <= 4.0   # learning can't rescue a context failure


def test_parse_json_variants():
    from clipper import llm
    assert llm.parse_json('blah [{"a": "x]y"}] trailing') == [{"a": "x]y"}]
    assert llm.parse_json('{"clips": []}') == {"clips": []}
