"""More facts before the first draft: word-for-word sentences with numbers from Wikipedia, the NASA pages a topic
already cites, and arXiv abstracts (free, no keys)."""
import json
import urllib.parse

from test_studio_foundation import studio_home  # noqa: F401

WIKI_TEXT = ("The speed of sound in dry air at 20 °C is 343 metres per second. "
             "Sound travels about 4.3 times faster in water than in air, at roughly 1,480 metres per second. "
             "The history of acoustics goes back to the ancient Greeks and their musical theories. "
             "In 1947 the Bell X-1 broke the sound barrier, flying faster than sound in level flight. "
             "Thunder can be heard up to 15 kilometres away from a lightning strike.")
NASA_TEXT = ("NASA's X-59 is designed to fly faster than the speed of sound while making a quiet thump. "
             "The X-59 should cruise at Mach 1.4, about 925 miles per hour, at 55,000 feet. "
             "At 55,000 feet the speed of sound is about 660 miles per hour, slower than in warm air near the ground. "
             "Contact our press office.")
ARXIV_XML = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2101.00001v1</id><title>Sound speed in
 dense air</title><summary>We measure the speed of sound in compressed air near 300 bar and find it rises to
 about 520 metres per second. Our method is new.</summary></entry>
<entry><id>http://arxiv.org/abs/2101.00002v1</id><title>Unrelated</title><summary>A galaxy survey of 12,000
 objects found nothing.</summary></entry></feed>"""


def _api(calls):
    def answer(url):
        calls.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if "wikipedia.org" in url and q.get("list") == "search":
            return json.dumps({"query": {"search": [{"title": "Speed of sound"}]}})
        if "wikipedia.org" in url:
            return json.dumps({"query": {"pages": {"1": {"extract": WIKI_TEXT}}}})
        if "arxiv.org" in url:
            return ARXIV_XML
        raise AssertionError(url)
    return answer


def _topic(fp="fp"):
    from studio import db, topics
    tid = db.insert("INSERT INTO topics(title, angle, fingerprint, pillar, kind, status, scores) VALUES(?,?,?,?,?,?,?)",
                    "How fast does sound travel", "the speed of sound in air versus water", fp, "Physics",
                    "evergreen", "idea", "{}")
    for claim, url in (("Sound travels at 343 metres per second in air", "https://www.nasa.gov/x59"),
                       ("Sound is a pressure wave", "https://www.britannica.com/science/sound")):
        db.insert("INSERT INTO sources(topic_id, claim, url, title, verdict, domain, tier, http_status) "
                  "VALUES(?,?,?,?,?,?,?,?)", tid, claim, url, "src", "reachable", topics.domain(url), topics.tier(url), 200)
    return topics.topic(tid)


def test_enrich_adds_new_numbers_word_for_word_once(studio_home, monkeypatch):  # noqa: F811
    from studio import topics
    calls = []
    monkeypatch.setattr(topics, "API", _api(calls))
    monkeypatch.setattr(topics, "PAGE", lambda url: NASA_TEXT if "nasa.gov" in url else "")
    t = _topic()
    n = topics.enrich(t)
    added = [s for s in topics.topic(t["id"])["sources"] if s["note"] == topics.ENRICH_NOTE]
    claims = [s["claim"] for s in added]
    assert n == len(added) and 3 <= n <= topics.ENRICH_MAX
    # Wikipedia: sentences with new numbers about the topic; the history line has no number at all
    assert "Sound travels about 4.3 times faster in water than in air, at roughly 1,480 metres per second." in claims
    assert not any("ancient Greeks" in c for c in claims)
    assert topics.fact_sentences("Sound travels at 343 metres per second in air.", {"sound", "travel"}, {"343"}, 3) == []
    w = next(s for s in added if "1,480" in s["claim"])
    assert w["url"] == "https://en.wikipedia.org/wiki/Speed_of_sound" and w["tier"] == 2 and w["verdict"] == "reachable"
    # the NASA page the topic cites gives its own number; the arXiv abstract only if it's clearly on-topic
    assert any("660 miles per hour" in c and s["url"] == "https://www.nasa.gov/x59" for s, c in zip(added, claims))
    assert not any("Mach 1.4" in c for c in claims)                  # a number, but not about the topic
    arx = [s for s in added if "arxiv.org" in s["url"]]
    assert len(arx) == 1 and arx[0]["url"] == "https://arxiv.org/abs/2101.00001v1" and "520" in arx[0]["claim"]
    assert arx[0]["title"].startswith("arXiv preprint: Sound speed in dense air")
    # the fact-check reads the same text later without downloading it again
    assert "1,480" in topics._cached("https://en.wikipedia.org/wiki/Speed_of_sound")
    # the writer sees the new facts after the old ones (same F numbers as the fact-check)
    from studio import script
    facts = script._facts(topics.topic(t["id"]))
    assert facts[0]["claim"].startswith("Sound travels at 343") and len(facts) == 2 + n
    # once per topic: asked again, nothing is looked up
    before = len(calls)
    assert topics.enrich(topics.topic(t["id"])) == 0 and len(calls) == before


def test_enrich_can_be_switched_off_and_survives_silent_sources(studio_home, monkeypatch):  # noqa: F811
    from studio import config, topics
    t = _topic()
    notes = []
    assert topics.enrich(t, notes.append) == 0                  # every source refused (tests have no web)
    assert any("Wikipedia didn't answer" in x for x in notes)
    assert topics.topic(t["id"])["scores"]["enriched"] == 0
    config.set_values(enrich_facts=False)
    t2 = _topic("fp2")
    monkeypatch.setattr(topics, "API", lambda url: (_ for _ in ()).throw(AssertionError("asked")))
    assert topics.enrich(t2) == 0 and "enriched" not in topics.topic(t2["id"])["scores"]


def test_first_draft_gets_the_extra_facts(studio_home, monkeypatch):  # noqa: F811
    from studio import script, topics
    monkeypatch.setattr(topics, "API", _api([]))
    seen = {}

    def fake_write(topic, feedback=None, previous=None):
        seen["n"] = len(script._facts(topic))
        return {"hooks": ["Sound is fast."], "lines": [{"text": "Sound is fast.", "facts": ["F1"], "beat": "hook"}]}
    monkeypatch.setattr(script, "write", fake_write)
    t = _topic()
    script.draft_step(t, None)
    assert seen["n"] > 2
