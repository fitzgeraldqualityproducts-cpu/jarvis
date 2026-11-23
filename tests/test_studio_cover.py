"""Each Short's cover in the Canva template's layout: right words, checked numbers, clear bottom."""
import json

from test_studio_foundation import studio_home  # noqa: F401


def test_cover_text_prefers_the_short_hook():
    from studio import cover
    row = {"hook": "How fast is a rocket, really?", "structure": json.dumps({"titles": ["A"]})}
    assert cover.cover_text(row) == "How fast is a rocket, really?"
    long = {"hook": "Your phone has more computing power than all of Apollo combined and here is why",
            "structure": json.dumps({"titles": ["Your Phone vs Apollo #Shorts", "The computer in your pocket beats Apollo"]})}
    assert cover.cover_text(long) == "Your Phone vs Apollo"


def test_stat_uses_checked_calculations_only():
    from studio import cover
    st = {"calcs": [{"expr": "384,400 ÷ 5", "result": 76880, "unit": "h"}]}
    assert cover.stat(st, []) == ("384,400 ÷ 5", "76,880", "h")
    long = {"calcs": [{"expr": "a very long expression that will never fit", "actual": 7.8, "unit": "km/s"}]}
    assert cover.stat(long, [{"type": "number", "value": "11", "unit": "x"}]) == ("", "11", "x")
    assert cover.stat(long, []) == ("", "7.8", "km/s")
    assert cover.stat({}, [{"type": "headline", "text": "hi"}]) is None


def test_highlight_word_skips_small_words():
    from studio import cover
    w = "HOW FAST IS A ROCKET, REALLY?".split()
    assert w[cover.highlight_word(w)] == "ROCKET,"


def test_drawn_cover_is_vertical_and_leaves_the_bottom_clear():
    from studio import branding, cover
    for calc in (("", "7.8", "km/s"), None, ("384,400 ÷ 5", "76,880", "h")):
        img = cover.draw("Could you walk to the Moon in a lifetime?", calc, "What If (With Math)", "HussUniversity",
                         branding.PALETTE)
        assert img.size == (1080, 1920) and cover.bottom_clear(img)
    huge = cover.draw(" ".join(["supercalifragilistic"] * 16), ("", "1", ""), "x" * 60, "HussUniversity", branding.PALETTE)
    assert cover.bottom_clear(huge)


def test_thumbnail_permission_is_on_the_allow_list():
    from studio import guard
    assert guard.allow("youtube.set_thumbnail", "test", record=False)
