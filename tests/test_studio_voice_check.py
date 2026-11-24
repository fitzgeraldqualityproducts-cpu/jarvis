"""Voice check: garbled sentences are heard back and recorded again; units and science words are said properly."""
import numpy as np

from test_studio_engines import fake_voice  # noqa: F401
from test_studio_foundation import studio_home  # noqa: F401

RATE = 24000


def test_spoken_units_and_words():
    from studio import voicecheck as v
    assert v.spoken("It moves at 7.8 km/s, about 17,500 mph.") == \
        "It moves at 7.8 kilometers per second, about 17,500 miles per hour."
    assert v.spoken("Water boils at 100°C; CO2 is 0.04% of air.") == \
        "Water boils at 100 degrees Celsius; C O two is 0.04 percent of air."
    assert v.spoken("Gravity is 9.8 m/s² and a 5 kg ball fell for 3 s.") == \
        "Gravity is 9.8 meters per second squared and a 5 kilograms ball fell for 3 seconds."
    assert v.spoken("Betelgeuse is 550 ly away.") == "Beetle-juice is 550 light-years away."
    # plain words that happen to look like units are left alone
    assert v.spoken("I am a man in a room; it is 3 m tall.") == "I am a man in a room; it is 3 meters tall."


def test_connor_can_add_pronunciations(studio_home):  # noqa: F811
    from studio import config, voicecheck as v
    config.set_values(pronounce={"Kuiper": "KY-per"})
    assert v.spoken("The Kuiper belt") == "The KY-per belt"


def test_numbers_compare_as_words():
    from studio import voicecheck as v
    assert v.say_number(1480) == "one thousand four hundred eighty"
    assert v.tokens("In 1947") == ["in", "nineteen", "forty", "seven"]
    assert v.error("It is 1,480 meters per second.", "it is one thousand four hundred and eighty meters per second") == 0
    assert v.error("It is 4.3 times faster.", "It is 4.3 times faster") == 0
    assert v.error("The rocket burns fuel for nine minutes.", "the rocket burns blah fuel for minutes uh") >= 0.25
    assert v.error("Light takes eight minutes to reach us.", "") == 1.0


def test_splice_swaps_only_the_retaken_sentence():
    from studio import voicecheck as v
    a = np.arange(10, dtype=np.float32)
    parts = [{"start": 0, "end": 3}, {"start": 5, "end": 8}]
    out = v.splice(a, parts, {1: np.array([-1, -1], dtype=np.float32)})
    assert out.tolist() == [0, 1, 2, 3, 4, -1, -1, 8, 9]


SENTS = ["Light from the Sun takes about eight minutes to reach Earth.",
         "That means you always see the Sun as it was in the past.",
         "So the sunset you watch has already happened."]


def _runner(calls):
    from clipper import media

    def run(tool, worker, payload, timeout):
        calls.append(payload)
        segs = payload.get("segments") or [{"text": t} for t in SENTS]
        chunks, parts, pos = [], [], 0
        for i, sg in enumerate(segs):
            if chunks:
                chunks.append(np.zeros(int(RATE * 0.2), dtype=np.float32))
                pos += len(chunks[-1])
            t = np.arange(int(RATE * 4.0)) / RATE
            w = (0.3 * np.sin(2 * np.pi * (180 + 40 * i) * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
            chunks.append(w)
            parts.append({"seg": i, "text": sg["text"], "start": pos, "end": pos + len(w)})
            pos += len(w)
        media.write_wav(payload["out"], np.concatenate(chunks), RATE)
        _runner.parts[payload["out"]] = parts
        return {"path": payload["out"], "device": "cpu", "sentences": len(parts), "parts": parts, "took": 1.0}
    _runner.parts = {}
    return run


def _hear(garbled_takes):
    """Hears each part's own text, except the second sentence, which is mumbled in the first
    `garbled_takes` recordings of it."""
    seen = {"n": 0}

    def hear(path):
        words = []
        for p in _runner.parts[str(path)]:
            text = p["text"]
            if text == SENTS[1]:
                seen["n"] += 1
                if seen["n"] <= garbled_takes:
                    text = "that mean you all see"
            ws = text.split()
            d = (p["end"] - p["start"]) / RATE / len(ws)
            words += [{"w": w, "s": p["start"] / RATE + k * d, "e": p["start"] / RATE + (k + 0.8) * d}
                      for k, w in enumerate(ws)]
        return words
    return hear


def test_a_garbled_sentence_is_recorded_again_and_spliced_in(studio_home, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import engines, narration, voicecheck
    calls = []
    monkeypatch.setattr(engines, "RUNNER", _runner(calls))
    monkeypatch.setattr(voicecheck, "HEAR", _hear(garbled_takes=1))
    rec = narration.narrate(" ".join(SENTS), tmp_path / "a.wav", engine="chatterbox", record=False)
    vc = rec["engine_info"]["voice_check"]
    assert rec["engine"] == "chatterbox" and vc["unclear"] == 1 and vc["retaken"] == [1] and vc["rounds"] == 1
    assert vc["still_unclear"] == [] and vc["worst"] < voicecheck.UNCLEAR_AT
    assert len(calls) == 2 and calls[1]["seed"] != calls[0]["seed"]
    assert [s["text"] for s in calls[1]["segments"]] == [SENTS[1]]          # only that sentence again


def test_still_garbled_after_retakes_falls_back_to_kokoro(studio_home, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import engines, narration
    calls = []
    monkeypatch.setattr(engines, "RUNNER", _runner(calls))
    from studio import voicecheck
    monkeypatch.setattr(voicecheck, "HEAR", _hear(garbled_takes=99))
    rec = narration.narrate(" ".join(SENTS), tmp_path / "b.wav", engine="chatterbox", record=False)
    assert rec["engine"] == "kokoro" and "garbled" in rec["fallback"] and len(calls) == 3


def test_clear_recording_is_used_as_is(studio_home, fake_voice, monkeypatch, tmp_path):  # noqa: F811
    from studio import config, engines, narration, voicecheck
    calls = []
    monkeypatch.setattr(engines, "RUNNER", _runner(calls))
    monkeypatch.setattr(voicecheck, "HEAR", _hear(garbled_takes=0))
    rec = narration.narrate(" ".join(SENTS), tmp_path / "c.wav", engine="chatterbox", record=False)
    assert rec.get("engine_info"), rec.get("fallback")
    assert rec["engine_info"]["voice_check"]["unclear"] == 0 and len(calls) == 1
    config.set_values(voice_check=False)
    rec = narration.narrate(" ".join(SENTS), tmp_path / "d.wav", engine="chatterbox", record=False)
    assert rec["engine_info"]["voice_check"] is None
