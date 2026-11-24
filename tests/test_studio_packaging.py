"""Titles and first frames that learn from real results (index and hook hold; no CTR exists for Shorts)."""
import json

import numpy as np

from test_studio_foundation import studio_home  # noqa: F401


def test_title_and_frame_styles():
    from studio import packaging as p
    assert p.style("Why is the sky blue?") == ["question"]
    assert p.style("How long would you fall through Earth") == ["question", "you"]
    assert p.style("A neutron star is 20 km wide") == ["number"]
    assert p.style("The Moon is bigger than you think") == ["you", "comparison"]
    assert p.style("Lightning is hotter than the Sun's surface") == ["comparison"]
    assert p.style("The ocean hides a waterfall") == ["statement"]
    dark = np.zeros((1920, 1080, 3), dtype=np.uint8)
    assert p.frame_style(dark, "It takes 8 minutes") == ["dark", "calm", "number on screen"]
    noisy = (np.random.default_rng(1).random((1920, 1080, 3)) * 255).astype(np.uint8)
    assert "busy" in p.frame_style(noisy)


def _shorts(rows):
    from studio import db
    idx = {}
    for k, (title, frame, index, hold) in enumerate(rows):
        vid = db.insert("INSERT INTO videos(state, meta, created, updated) VALUES('published', ?, 0, 0)",
                        json.dumps({"packaging": {"title": __import__("studio.packaging").packaging.style(title),
                                                  "frame": frame}}))
        idx[f"yt{k}"] = {"video_id": vid, "index": index, "hook_hold": hold}
    return idx


def test_a_clearly_better_style_becomes_advice_and_goes_first(studio_home):  # noqa: F811
    from studio import lessons, packaging
    idx = _shorts([("Why is the sky blue?", ["bright", "calm"], 70, 0.82),
                   ("How deep is the ocean?", ["bright", "calm"], 64, 0.80),
                   ("What is inside the Sun?", ["bright", "busy"], 66, 0.79),
                   ("The ocean hides a waterfall", ["dark", "busy"], 48, 0.70),
                   ("Lava cools in hours", ["dark", "calm"], 50, 0.71),
                   ("Ice can burn skin", ["dark", "busy"], 52, 0.69)])
    d = packaging.learn(idx)
    assert d["n"] == 6 and d["title"]["question"]["index"] == 66.667 and d["title"]["question"]["n_other"] == 3
    w = packaging.winners()
    assert "question" in w["title"] and "statement" in w["behind"] and "bright" in w["frame"]
    adv = packaging.advice()
    assert "Titles asked as a question did better: index 67 over 3 Shorts vs 50" in adv
    assert "bright first frame kept more viewers past the start: 80% vs 70%" in adv
    assert adv in lessons.advice("script")
    assert packaging.pick(["Lava is hot", "How hot is lava?"]) == ["How hot is lava?", "Lava is hot"]


def test_nothing_changes_without_clear_evidence(studio_home):  # noqa: F811
    from studio import packaging
    packaging.learn(_shorts([("Why is the sky blue?", ["bright"], 55, 0.8),
                             ("The ocean hides a waterfall", ["dark"], 52, 0.78),
                             ("How deep is the ocean?", ["bright"], 50, 0.75)]))
    assert packaging.advice() == "" and packaging.pick(["B title", "A title?"]) == ["B title", "A title?"]
