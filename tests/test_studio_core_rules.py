"""Connor's core rules for every Short (2026-11-27): studio/rules.py and where it is wired in."""
import json

import numpy as np
import pytest

from studio import rules as _rules

REAL_MODE = _rules.mode                     # captured before conftest switches the rules off for other tests


@pytest.fixture(autouse=True)
def core_rules_on(monkeypatch):
    from studio import config, rules, script
    monkeypatch.setattr(rules, "mode", REAL_MODE)
    monkeypatch.setattr(config, "general", lambda: {"core_rules": "block"})
    monkeypatch.setattr(script, "WORDS_MIN", rules.WORDS[0])
    monkeypatch.setattr(script, "WORDS_MAX", rules.WORDS[1])
    monkeypatch.setattr(script, "HOOK_MAX_WORDS", rules.HOOK_MAX_WORDS)
    monkeypatch.setattr(script, "HOOK_GRAB_WORDS", rules.HOOK_GRAB_WORDS)


def test_cta_types():
    from studio import rules
    assert rules.cta_type("Comment 14.8 if this surprised you.") == "comment"
    assert rules.cta_type("Would you rather live through 15 days of sun or 15 days of night?") == "opinion"
    assert rules.cta_type("Part 2: What happens at the lunar poles?") == "series"
    assert rules.cta_type("Night falls on your light, so ask:") is None          # test 36's loop ending
    assert rules.cta_type("So how long is a day there?") is None                  # a loop question, not a CTA
    assert rules.cta_problems("Comment 14.8 if this surprised you.") == []
    assert any("generic" in p for p in rules.cta_problems("Like and subscribe for more!"))
    assert any("exactly what" in p for p in rules.cta_problems("Comment below."))


def test_fix_cta_uses_the_shorts_own_number():
    from studio import rules
    d = {"lines": [{"text": "How long is daylight on the far side?", "beat": "hook"},
                   {"text": "A Moon day lasts 29.53 days.", "beat": "setup"},
                   {"text": "29.53 divided by 2.", "beat": "math"},
                   {"text": "About 14.8 days of sun, then 14.8 of night.", "beat": "payoff"},
                   {"text": "Night falls on your light, so ask:", "beat": "loop"}]}
    fixed, note = rules.fix_cta(d)
    assert fixed["lines"][-1]["text"] == "Comment 14.8 if this surprised you."
    assert fixed["lines"][-1]["beat"] == "loop" and "CTA" in note
    same, note2 = rules.fix_cta(fixed)
    assert same is fixed and note2 == ""


def test_script_rules_and_robotic_phrasing():
    from studio import rules
    lines = [{"text": "Why is the far side always dark?", "beat": "hook"},
             {"text": "Let's delve into this fascinating question.", "beat": "setup"},
             {"text": "The Moon spins once per lap.", "beat": "math"},
             {"text": "So it gets 14.8 days of sun.", "beat": "payoff"},
             {"text": "Thanks for watching.", "beat": "loop"}]
    probs = rules.script_problems(lines, words=False)
    assert any("robotic" in p and "delve" in p for p in probs)
    assert any("CTA" in p for p in probs)
    assert not any("words" in p.split(":")[1] for p in probs if p.startswith("rule: ") and "Connor's limit" in p)
    many = [{"text": "Hook 1.5 and 2.5?", "beat": "hook"}, {"text": "Then 3.5, 4.5 and 5.5 and 6.5.", "beat": "setup"},
            {"text": "Result 7.5.", "beat": "payoff"}, {"text": "Comment 7.5 if this surprised you.", "beat": "loop"}]
    assert any("different numbers" in p for p in rules.script_problems(many, words=False))
    assert rules.robotic("It is big. It is not small. You are here. That is it.")[-1].startswith("no contractions")


def test_critic_gate_scores():
    from studio import rules
    out, probs = rules.critic({"critic": {"hook": 9, "pacing": 7, "cta": 9, "note": "line 4 drags"}},
                              "Comment 14.8 if this surprised you.")
    assert out["pacing"] == 7 and probs and "pacing 7/10" in probs[0] and "line 4 drags" in probs[0]
    out, probs = rules.critic({"critic": {"hook": 9, "pacing": 9, "cta": 10}}, "So how long is a day?")
    assert out["cta"] == 4.0 and "cta 4/10" in probs[0]                  # not a CTA at all, whatever the score
    out, probs = rules.critic({}, "Comment 14.8 if this surprised you.")
    assert probs == []                                                     # an older checker answer: no failure


def test_strong_hook_swap_and_title_order():
    from studio import rules, script
    hooks = ["What happens to the Moon at night?", "Why is half the Moon dark?"]
    ratings = {0: {"strength": 6}, 1: {"strength": 9}}
    assert rules.strong_hook(hooks, ratings, script.hook_checks) == 1
    assert rules.title_order(["A", "B", "C"], {"0": 5, "1": 9, "2": 7}) == ["B", "C", "A"]
    assert rules.title_order(["A", "B"], None) == ["A", "B"]


def test_word_and_hook_limits_follow_the_rules():
    from studio import rules, script
    assert rules.WORDS == (75, 90)
    src = open(script.__file__).read()
    assert "WORDS_MIN, WORDS_MAX = rules.WORDS" in src and "HOOK_MAX_WORDS = rules.HOOK_MAX_WORDS" in src
    assert script.hook_checks("How long is daylight on the Moon?") == []
    assert any("said by 3" in p for p in script.hook_checks("How long does daylight last on the Moon's far side?"))
    assert rules.est_seconds(88) == round(88 / rules.PACE, 1)


def test_shot_variety_never_two_wide_surface_shots_in_a_row():
    from studio import rules
    scenes = [{"type": "headline", "art": "a wide grey lunar landscape at noon"},
              {"type": "headline", "art": "the lunar surface plain stretching to the horizon"},
              {"type": "equation", "left": "29.53 / 2", "right": "14.8"},
              {"type": "headline", "shots": [{"art": "a desert landscape", "camera": "pan-left"},
                                             {"art": "a wide plain under stars", "camera": "push-in",
                                              "category": "wide surface"}]}]
    out, changed = rules.vary_shots(scenes)
    cats = [s["category"] for s in out]
    assert cats[0] == "wide surface" and cats[1] == "close detail" and "close-up" in out[1]["art"]
    assert out[2]["category"] == "orbital/diagram"
    assert out[3]["shots"][1]["category"] == "close detail" and out[3]["shots"][1]["camera"] == "close-up"
    assert len(changed) == 2
    assert rules.visual_problems(out) == []
    assert rules.first_diagram(out) == 3


def test_only_one_or_two_subtle_sound_effects():
    from studio import edit, rules
    starts = [0, 2.5, 6, 10, 14, 19, 24, 30]
    beats = ["hook", "setup", "math", "setup", "rehook", "math", "payoff"]
    scenes = [{"type": "headline"}, {"type": "headline"}, {"type": "equation"}, {"type": "headline"},
              {"type": "headline"}, {"type": "number", "value": "14.8"}, {"type": "number", "value": "14.8"}]
    cues = edit.cues(starts, beats)
    assert len([c for c in cues if c["kind"] != "jump"]) > 4
    fx = rules.sfx_cues(cues, scenes, beats)
    assert 1 <= len(fx) <= rules.SFX_MAX
    assert {c["kind"] for c in fx} <= {"payoff", "cut"} and any(c["kind"] == "payoff" for c in fx)
    from studio import sound
    buf = sound.track(fx, 31, levels=rules.SFX_LEVELS)
    assert buf is not None and float(np.max(np.abs(buf))) <= 0.2


def test_pattern_interrupt_between_12_and_18_seconds():
    from studio import rules
    starts = [0, 3, 8, 13.5, 20, 30]
    beats = ["hook", "setup", "math", "setup", "rehook"]
    ic = rules.interrupt_cue(starts, beats)
    assert ic and ic["kind"] == "rehook" and 12 <= ic["t"] <= 18 and ic["interrupt"]
    assert rules.interrupt_cue([0, 3, 8, 15, 20, 30], ["hook", "setup", "math", "rehook", "payoff"]) is None


def test_captions_lead_and_timing_report():
    from studio import rules
    pages = [{"s": 0.0, "e": 2.0, "text": ""}, {"s": 2.0, "e": 4.0, "text": "a"}, {"s": 4.0, "e": 6.0, "text": "b"}]
    led = rules.lead_pages(pages)
    assert led[1]["s"] == round(2.0 - rules.CAPTION_LEAD, 3) and led[0]["e"] == led[1]["s"]
    assert led[2]["s"] < 4.0
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("How long is daylight on the Moon?".split())]
    tm = rules.timing(words, ["How long is daylight on the Moon?"], [0, 30, 40], 40.0,
                      [{"t": 15.0, "kind": "rehook"}])
    assert tm["problems"] == [] and tm["hook_end"] <= 3.0 and tm["interrupt"] == 15.0
    tm = rules.timing(words, ["How long is daylight on the Moon?"], [0, 30], 30.0, [])
    assert any("30.0 s long" in p for p in tm["problems"]) and any("interrupt" in p for p in tm["problems"])


def test_end_card_and_number_hold():
    from studio import rules
    lines = [{"text": "How long is daylight there?", "beat": "hook"},
             {"text": "About 14.8 days of sun.", "beat": "payoff"},
             {"text": "Comment 14.8 if this surprised you.", "beat": "loop"}]
    card = rules.end_card(lines, 33.0)
    assert card["sub"] == "14.8" and card["text"].startswith("Comment") and card["seconds"] == 2.0
    assert rules.end_card(lines, 40.0)["seconds"] == rules.END_CARD[0]
    huds = rules.hud_hold([{"value": "14.8 days"}, None])
    assert huds[0]["holdFor"] == rules.NUMBER_HOLD[1] and huds[1] is None


def test_description_and_cover_question():
    from studio import rules
    lines = [{"text": "How long is daylight on the far side?", "beat": "hook"},
             {"text": "A Moon day lasts 29.53 days.", "beat": "setup"},
             {"text": "About 14.8 days of sun, then 14.8 of night.", "beat": "payoff"},
             {"text": "Part 2: What happens at the lunar poles?", "beat": "loop"}]
    h, fact, ask = rules.description_parts(lines[0]["text"], lines)
    assert h.endswith("?") and "14.8 days" in fact and ask.startswith("Comment 14.8") and "Part 2" in ask
    desc = f"Title\n\n{h}\n{fact}\n\n{ask}\n\nSources:\n- x"
    assert rules.description_problems(desc, lines, lines[0]["text"]) == []
    assert rules.description_problems("Title\n\nSources:\n- x", lines, lines[0]["text"])
    assert rules.cover_question("How long is daylight on the far side?", []) == "How long is daylight on the far side?"
    assert rules.cover_question("Daylight on the Moon lasts weeks and weeks and weeks", ["Why so long?"]) == "Why so long?"


def test_feedback_advice_needs_real_evidence():
    from studio import rules
    assert rules.feedback_advice({}) == ""
    groups = {"cta": {"comment": {"n": 3, "mean": 64}, "series": {"n": 2, "mean": 41}},
              "hook": {"question": {"n": 1, "mean": 90}, "number": {"n": 1, "mean": 10}}}
    adv = rules.feedback_advice(groups)
    assert "prefer comment challenge" in adv and "avoid series tease" in adv and "hook styles" not in adv
    s = rules.structure_of([{"text": "Why?", "beat": "hook"}, {"text": "x", "beat": "setup"},
                            {"text": "y", "beat": "setup"}, {"text": "Comment 3.5 if this surprised you.",
                                                             "beat": "loop"}])
    assert s == {"hook": "question", "cta": "comment", "shape": "hook-setup-loop"}


def test_ambient_bed_is_soft_and_made_here():
    from studio import sound
    a = sound.ambient(5)
    assert a.dtype == np.float32 and 0 < float(np.max(np.abs(a))) <= 0.5 and len(a) == int(48000 * 6)


def test_mode_switch(monkeypatch):
    from studio import config, rules
    monkeypatch.setattr(config, "general", lambda: {"core_rules": "advise"})
    assert rules.mode() == "advise" and rules.on()
    monkeypatch.setattr(config, "general", lambda: {"core_rules": False})
    assert rules.mode() == "off" and rules.script_problems([{"text": "x"}]) == [] and not rules.look_on()
    monkeypatch.setattr(config, "general", lambda: {"core_rules": "block", "core_rules_look": True})
    assert rules.look_on()


def test_opening_and_final_frame_checks():
    from studio import qa
    flat = np.full((1920, 1080, 3), 30, np.uint8)
    ok, _ = qa.final_frame_clean(flat)
    assert not ok
    card = flat.copy()
    card[700:900, 100:980] = 240
    ok, why = qa.final_frame_clean(card)
    assert ok, why


def test_pack_markdown_has_every_part():
    from studio import pack
    p = {"video": 7, "title": "Daylight", "script": {"lines": [{"n": 1, "beat": "hook", "text": "Why?"}], "words": 80,
                                                       "estimated_seconds": 32.8, "cta_type": "comment challenge"},
         "shots": [{"scene": 1, "start": 0, "end": 2.5, "narration": "Why?", "visual": "the Moon", "category":
                    "starfield", "camera": "push-in", "on_screen": ["Why?"], "sfx": ""}],
         "titles": {"recommended": "A", "alternates": ["B", "C"], "scores": {}},
         "thumbnail": {"question": "Why?", "big_number": "14.8 days", "look": "high contrast"},
         "description": "Why?\nFact.\n\nComment 14.8 if this surprised you.", "notes": ["Critic scores: hook 9/10."]}
    md = pack.markdown(p)
    for part in ("## Final script", "## Shot list", "Recommended: A", "Alternate: C", "## Thumbnail concept",
                 "## Description", "## Notes on the creative choices", "| 1 | 0.0-2.5 |"):
        assert part in md, part
