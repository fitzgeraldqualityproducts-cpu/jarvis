"""More accurate captions: a second careful listen per clip, made-up text dropped, word timings
moved onto the actual sound, and the reviewer's spelling fixes for misheard words."""
import numpy as np

from clipper import caption_fix, media


def _tone_wav(path, spans, total=6.0, rate=16000):
    """Quiet background with loud 'speech' only during spans [(s, e)]."""
    rng = np.random.default_rng(0)
    a = (rng.standard_normal(int(total * rate)) * 0.002).astype(np.float32)
    t = np.arange(len(a)) / rate
    for s, e in spans:
        m = (t >= s) & (t < e)
        a[m] += 0.3 * np.sin(2 * np.pi * 220 * t[m]).astype(np.float32)
    media.write_wav(path, a[:, None], rate)
    return a, rate


def test_stretched_word_starts_when_its_sound_starts(tmp_path):
    audio, rate = _tone_wav(tmp_path / "a.wav", [(2.0, 2.4), (2.5, 2.8)])
    # Whisper's timing: "Whoa," "starts" a second and a half early, during the silence
    words = [{"w": "Whoa,", "s": 0.5, "e": 2.4, "p": 0.6}, {"w": "nice", "s": 2.3, "e": 2.8, "p": 0.9}]
    out = caption_fix.tighten(words, audio, rate)
    assert 1.9 <= out[0]["s"] <= 2.05
    assert out[0]["e"] <= out[1]["s"]                  # no overlap
    assert all(w["e"] - w["s"] >= 0.08 for w in out)


def test_tighten_without_audio_still_limits_and_separates():
    words = [{"w": "I", "s": 1.0, "e": 4.0}, {"w": "know", "s": 1.5, "e": 1.9}]
    out = caption_fix.tighten(words)
    assert out[0]["e"] <= out[1]["s"] and out[0]["e"] - out[0]["s"] <= 2 * caption_fix.expected_length("I")


def test_made_up_text_is_dropped():
    segs = [{"s": 0, "e": 2, "lp": -0.2, "ns": 0.05, "cr": 1.2},
            {"s": 2, "e": 4, "lp": -1.4, "ns": 0.8, "cr": 1.1},        # Whisper itself thinks: not speech
            {"s": 4, "e": 6, "lp": -0.3, "ns": 0.55, "cr": 1.3}]
    words = [{"w": "real", "s": 0.1, "e": 0.4, "p": 0.9, "seg": 0},
             {"w": "Thanks", "s": 2.1, "e": 2.4, "p": 0.4, "seg": 1},
             {"w": "guess", "s": 4.1, "e": 4.4, "p": 0.05, "seg": 2},
             {"w": "kept", "s": 4.5, "e": 4.8, "p": 0.8, "seg": 2}]
    assert [w["w"] for w in caption_fix.drop_made_up(words, segs)] == ["real", "kept"]


def test_phrase_loops_collapse_but_real_repeats_stay():
    loop = [{"w": x, "s": i, "e": i + 0.5} for i, x in enumerate(("thank you " * 6).split())]
    assert len(caption_fix.collapse_loops(loop)) == 4
    nos = [{"w": "no", "s": i, "e": i + 0.5} for i in range(5)]
    assert len(caption_fix.collapse_loops(nos)) == 5


def test_spelling_fixes_are_single_words_from_the_clip():
    text = "cuz he's talking to the hoods then talking to the huzz"
    got = caption_fix.clean_fixes({"hoods": "huzz", "dragon": "Dragon", "talking to": "speaking with",
                                   "the": "the", "huzz": ""}, text)
    assert got == {"hoods": "huzz"}
    assert caption_fix.clean_fixes("not a dict", text) == {}


def _setup(tmp_path):
    audio, rate = _tone_wav(tmp_path / "audio.wav", [(1.0, 1.3), (1.4, 1.7), (1.8, 2.2), (2.4, 2.8)], total=5.0)
    old = [{"w": "Yeah", "s": 1.0, "e": 1.3, "p": 0.9}, {"w": "cuz", "s": 1.4, "e": 1.7, "p": 0.7},
           {"w": "talking", "s": 1.8, "e": 2.2, "p": 0.9}, {"w": "hoods.", "s": 2.4, "e": 2.8, "p": 0.3, "emph": True}]
    return {"words": old}, tmp_path / "audio.wav"


def test_refine_swaps_in_the_careful_listen(tmp_path, monkeypatch):
    tr, wav = _setup(tmp_path)
    seen = {}

    def listen(audio16, name, prompt="", hotwords="", language="en"):
        seen.update(prompt=prompt, hotwords=hotwords, n=len(audio16))
        # times are from the start of the padded window (clip 1.0 s - 1.5 s pad = 0)
        return ([{"w": "Yeah", "s": 1.0, "e": 1.3, "p": 0.95, "seg": 0}, {"w": "cuz", "s": 1.4, "e": 1.7, "p": 0.9, "seg": 0},
                 {"w": "talking", "s": 1.8, "e": 2.2, "p": 0.95, "seg": 0}, {"w": "to", "s": 2.2, "e": 2.3, "p": 0.9, "seg": 0},
                 {"w": "huzz.", "s": 2.4, "e": 2.8, "p": 0.8, "seg": 0}],
                [{"s": 0, "e": 3, "lp": -0.2, "ns": 0.02, "cr": 1.2}])
    monkeypatch.setattr(caption_fix, "listen", listen)
    note = caption_fix.refine(tr, tmp_path, wav, 1.0, 3.0, {"names": ["Sean"], "fixes": {"jarvis": "JARVIS"}})
    assert "correction" in note
    assert "Sean" in seen["prompt"] and "JARVIS" in seen["hotwords"]
    got = caption_fix.words_for(tr["words"], tmp_path)
    assert [w["w"] for w in got] == ["Yeah", "cuz", "talking", "to", "huzz."]
    assert got[-1].get("emph")                               # loudness emphasis carried over
    assert caption_fix.covered(tmp_path, 1.0, 3.0)
    calls = []
    monkeypatch.setattr(caption_fix, "listen", lambda *a, **k: calls.append(1))
    assert caption_fix.refine(tr, tmp_path, wav, 1.0, 3.0, {}) == "" and not calls   # saved, not redone


def test_refine_keeps_the_first_listen_when_the_second_goes_wrong(tmp_path, monkeypatch):
    tr, wav = _setup(tmp_path)
    monkeypatch.setattr(caption_fix, "listen", lambda *a, **k: (
        [{"w": "Subscribe", "s": 1.0, "e": 1.4, "p": 0.5, "seg": 0}], [{"s": 0, "e": 3, "lp": -0.4, "ns": 0.1}]))
    logs = []
    caption_fix.refine(tr, tmp_path, wav, 1.0, 3.0, {}, log=logs.append)
    assert [w["w"] for w in caption_fix.words_for(tr["words"], tmp_path)] == ["Yeah", "cuz", "talking", "hoods."]
    assert logs and "kept the first" in logs[0]


def test_no_model_still_fixes_timings_and_tries_again_later(tmp_path):
    tr, wav = _setup(tmp_path)
    logs = []
    note = caption_fix.refine(tr, tmp_path, wav, 1.0, 3.0, {}, log=logs.append)     # conftest: no model
    assert note == "caption timings matched to the voice" and "Couldn't listen" in logs[0]
    assert not caption_fix.covered(tmp_path, 1.0, 3.0)        # tried again on the next render


def test_off_switch(tmp_path, monkeypatch):
    from clipper import settings
    tr, wav = _setup(tmp_path)
    monkeypatch.setattr(settings, "general", lambda: {"caption_model": "off"})
    assert caption_fix.refine(tr, tmp_path, wav, 1.0, 3.0, {}) == ""


def test_read_window_reads_only_the_stretch(tmp_path):
    rate = 8000
    a = np.linspace(-0.5, 0.5, rate * 4).astype(np.float32)
    media.write_wav(tmp_path / "x.wav", np.stack([a, a], axis=1), rate)
    got, r = caption_fix.read_window(tmp_path / "x.wav", 1.0, 2.0)
    assert r == rate and len(got) == rate and abs(got[0] - a[rate]) < 1e-3


def test_review_spelling_fixes_reach_the_clip(monkeypatch):
    import json
    from clipper import llm, moments, transcribe
    from conftest import make_words
    tr = transcribe.build(make_words(["Yeah cuz he's talking to the hoods.", "Was it worth dying for the huzz?"]))
    c = {"start_id": 0, "end_id": 1, "start": 0.0, "end": tr["duration"], "duration": tr["duration"],
         "transcript": " ".join(s["text"] for s in tr["sentences"]), "scores": {}, "category": "funny"}
    monkeypatch.setattr(llm, "RUNNER", lambda prompt, timeout: json.dumps(
        [{"clip": 0, "accurate": True, "verdict": "keep", "caption_fixes": {"hoods": "huzz", "talking": "walking on"}}]))
    out = moments.verify([c], tr, {"name": "x", "clip_min": 5})
    assert out[0]["caption_fixes"] == {"hoods": "huzz", "talking": "walking on"}
    assert "caption_fixes" in moments.REVIEW


def test_merge_takes_the_best_of_both_listens():
    """From Connor's real clip: the first listen dropped line endings and mistimed "This"; the
    careful one wrote lowercase, misspelled the slang and put "this" 11 seconds early."""
    old = [{"w": "the", "s": 10.0, "e": 10.3, "p": 0.98}, {"w": "huzz", "s": 10.3, "e": 10.8, "p": 0.6},
           {"w": "talking", "s": 12.0, "e": 12.4, "p": 0.99}, {"w": "to", "s": 12.4, "e": 12.6, "p": 0.9},
           {"w": "the", "s": 12.6, "e": 13.2, "p": 0.86},
           {"w": "Whoa,", "s": 14.0, "e": 14.3, "p": 0.65}, {"w": "This", "s": 27.1, "e": 27.6, "p": 0.17},
           {"w": "guy's", "s": 27.6, "e": 28.1, "p": 0.95}, {"w": "you", "s": 28.2, "e": 28.4, "p": 0.27},
           {"w": "can't", "s": 28.4, "e": 28.7, "p": 0.99}]
    new = [{"w": "the", "s": 10.0, "e": 10.3, "p": 0.9}, {"w": "hudz", "s": 10.3, "e": 10.8, "p": 0.7},
           {"w": "talking", "s": 12.0, "e": 12.4, "p": 0.9}, {"w": "to", "s": 12.4, "e": 12.6, "p": 0.9},
           {"w": "the", "s": 12.6, "e": 12.8, "p": 0.9}, {"w": "hudz", "s": 12.8, "e": 13.4, "p": 0.8},
           {"w": "this", "s": 16.0, "e": 16.8, "p": 0.6}, {"w": "guy's", "s": 27.7, "e": 28.1, "p": 0.9},
           {"w": "he", "s": 28.2, "e": 28.4, "p": 0.8}, {"w": "can't", "s": 28.4, "e": 28.7, "p": 0.9}]
    got = caption_fix.merge(old, new)
    assert [w["w"] for w in got] == ["the", "huzz", "talking", "to", "the", "huzz", "Whoa,", "This", "guy's", "he", "can't"]
    this = next(w for w in got if w["w"] == "This")
    assert this["s"] == 27.1                                      # the timing that matches the voice
    the = got[4]
    assert the["e"] <= got[5]["s"]                                # the stretched "the" makes room
