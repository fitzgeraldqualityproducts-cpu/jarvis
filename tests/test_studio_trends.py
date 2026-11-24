"""Smarter topics: Wikipedia page views as a free interest signal, and duplicates caught by meaning."""
import datetime
import json
import urllib.parse

import numpy as np

from test_studio_foundation import studio_home  # noqa: F401

TODAY = datetime.date(2026, 11, 24)


def _views(daily, last7):
    return {"items": [{"views": daily} for _ in range(53)] + [{"views": last7} for _ in range(7)]}


def _api(calls, daily=3000, last7=3000):
    def answer(url):
        calls.append(url)
        if "/top/en.wikipedia/" in url:
            return json.dumps({"items": [{"articles": [{"article": "Main_Page"}, {"article": "Special:Search"},
                                                       {"article": "Comet_Lemmon"}, {"article": "Black_hole"}]}]})
        if "/per-article/" in url:
            return json.dumps(_views(daily, last7))
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if q.get("list") == "search":
            return json.dumps({"query": {"search": [{"title": "Black hole"}]}})
        raise AssertionError(url)
    return answer


def test_reading_now_skips_wiki_pages_and_is_cached(studio_home, monkeypatch):  # noqa: F811
    from studio import topics, trends
    calls = []
    monkeypatch.setattr(topics, "API", _api(calls))
    assert trends.reading_now(today=TODAY) == ["Comet Lemmon", "Black hole"]
    assert "/top/en.wikipedia/all-access/2026/11/23" in calls[0]
    assert trends.reading_now(today=TODAY) == ["Comet Lemmon", "Black hole"] and len(calls) == 1
    assert "Comet Lemmon; Black hole" in trends.hint()


def test_wiki_interest_scores_views_and_spots_rising(studio_home, monkeypatch):  # noqa: F811
    from studio import config, topics, trends
    calls = []
    monkeypatch.setattr(topics, "API", _api(calls, daily=1000, last7=1000))
    score, info = trends.wiki_interest("How big is a black hole", ["black hole", "size"], today=TODAY)
    assert score == 3.3 and info == {"article": "Black hole", "daily": 1000, "recent": 1000, "rising": False}
    assert "/per-article/en.wikipedia/all-access/user/Black_hole/daily/20260925/20261123" in calls[-1]
    monkeypatch.setattr(topics, "API", _api([], daily=1000, last7=5000))
    assert trends.wiki_interest("x", ["black hole"], today=TODAY)[1]["rising"] is True
    config.set_values(wiki_trends=False)
    assert trends.wiki_interest("x", ["y"]) == (None, None) and trends.hint() == ""


def _fake_embed(groups):
    """Texts sharing a group word point the same way."""
    def embed(text):
        v = np.zeros(len(groups) + 1, dtype=np.float32)
        for i, words in enumerate(groups):
            if any(w in text.lower() for w in words):
                v[i] = 1.0
        v[-1] = 0.2
        return v / np.linalg.norm(v)
    return embed


def test_same_idea_in_other_words_is_a_duplicate(studio_home, monkeypatch):  # noqa: F811
    from studio import db, topics, trends
    monkeypatch.setattr(trends, "EMBED", _fake_embed([("sky blue", "daylight look blue"), ("volcano",)]))
    db.insert("INSERT INTO topics(title, angle, fingerprint, status) VALUES(?,?,?,?)", "Why is the sky blue",
              "light scattering by air", "fp1", "idea")
    assert topics._overlap("What makes daylight look blue", "Why is the sky blue") < 0.7      # the word check misses it
    dup, what = topics.is_duplicate("What makes daylight look blue", "scattering")
    assert dup and what.startswith("topic ")
    assert topics.is_duplicate("How hot is a volcano", "lava temperature") == (False, None)
    pairs = trends.closest_pairs()
    assert pairs == [] or pairs[0][0] <= 1.0


def test_without_the_model_only_the_word_check_runs(studio_home):  # noqa: F811
    from studio import trends
    assert trends.same_meaning("anything", "") == (0.0, None)
