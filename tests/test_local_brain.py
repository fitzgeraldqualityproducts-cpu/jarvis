"""The local brain (Ollama) and the answer memory (Chroma or its built-in table): small talk without
Claude, strict reuse of answers, studio jobs checked before use, and Claude whenever anything is off."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from test_studio_foundation import studio_home  # noqa: F401
from test_studio_script import BODY, FakeClaude, fakes  # noqa: F401


class FakeOllama:
    """A tiny stand-in for Ollama's HTTP API on 127.0.0.1."""

    def __init__(self):
        self.replies = []           # what /api/chat answers next (str); empty -> "{}"
        self.delay = 0.0
        self.models = ["qwen2.5:3b"]
        self.chats, self.unloads = [], 0
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, lines):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for line in lines:
                    self.wfile.write((json.dumps(line) + "\n").encode())

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send([{"models": [{"name": m} for m in me.models]}])

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/generate":
                    me.unloads += 1
                    return self._send([{"done": True}])
                me.chats.append(body)
                time.sleep(me.delay)
                text = me.replies.pop(0) if me.replies else "{}"
                half = len(text) // 2
                self._send([{"message": {"content": text[:half]}, "done": False},
                            {"message": {"content": text[half:]}, "done": True,
                             "prompt_eval_count": 50, "eval_count": 20}])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def ollama(tmp_path, monkeypatch):
    import local_brain
    fake = FakeOllama()
    cfg = tmp_path / "jarvis-config.json"
    cfg.write_text(json.dumps({"local_brain": {"url": fake.url}}))
    monkeypatch.delenv("JARVIS_NO_LOCAL_BRAIN", raising=False)
    monkeypatch.setattr(local_brain, "CONFIG", cfg)
    monkeypatch.setattr(local_brain, "DATA", tmp_path / "lb")
    monkeypatch.setattr(local_brain, "STATS", tmp_path / "lb" / "stats.json")
    monkeypatch.setattr(local_brain, "_memory", lambda: (0.5, 4.0))
    monkeypatch.setattr(local_brain, "heavy_running", lambda: False)
    local_brain._state.update(up=None, checked=0.0, models=[], loaded_at=0.0)
    yield fake
    fake.server.shutdown()
    local_brain._state.update(up=None, checked=0.0, models=[])


def say(handle, reply=""):
    return json.dumps({"handle": handle, "reply": reply})


# ---------------- small talk ----------------

def test_small_talk_is_answered_locally_and_counted(ollama):
    import local_brain
    ollama.replies.append(say("me", "All good bro, just vibing and ready when you are."))
    got = local_brain.local_reply("yo what's up", [{"user": "hey", "jarvis": "Yo Connor!"}], "You are Jarvis.")
    assert got == "All good bro, just vibing and ready when you are."
    sent = ollama.chats[0]
    assert sent["format"] == "json" and sent["messages"][0]["content"].startswith("You are Jarvis.")
    assert "Recent conversation" in sent["messages"][1]["content"]
    s = local_brain.summary()
    assert s["calls"] == 1 and s["by"] == {"brain": 1} and s["tokens"] == local_brain.TYPICAL["brain"]
    assert "saved 1 Claude calls" in local_brain.report()


@pytest.mark.parametrize("text", [
    "what's the weather tomorrow", "clip my last stream", "how far is the moon", "open Spotify",
    "the StoreForge preview is broken", "remind me at 5", "set a timer for 10 minutes",
    "can you explain black holes", "I want to build a store", "it said error 3",
])
def test_real_questions_and_commands_go_straight_to_claude(ollama, text):
    import local_brain
    ollama.replies.append(say("me", "Sure thing bro."))
    assert local_brain.local_reply(text, [], "") is None
    assert ollama.chats == []                      # not even asked: no extra wait before Claude


@pytest.mark.parametrize("text", ["yo what's up", "that's crazy", "thanks bro", "good night", "haha nice",
                                  "Oh, he's talking. Chill, chill.", "Mm-hmm."])
def test_chit_chat_is_worth_asking(text):
    import local_brain
    assert local_brain.worth_asking(text)


def test_an_answer_to_his_question_goes_to_claude(ollama):
    import local_brain
    ollama.replies.append(say("me", "Alright."))
    assert local_brain.local_reply("No.", [{"user": "hm", "jarvis": "Want me to fix it?"}], "", last_age=20) is None
    assert ollama.chats == []


@pytest.mark.parametrize("reply", ["I've opened Spotify for you.", "It's 72 degrees out bro.", "",
                                   "Check https://x.com", "word " * 40])
def test_risky_local_replies_are_thrown_away(ollama, reply):
    import local_brain
    ollama.replies.append(say("me", reply))
    assert local_brain.local_reply("yo what's up", [], "") is None


def test_local_model_can_hand_it_to_claude(ollama):
    import local_brain
    ollama.replies.append(say("brain"))
    assert local_brain.local_reply("that's crazy", [], "") is None
    assert local_brain.summary()["calls"] == 0


def test_slow_missing_or_busy_means_claude(ollama, monkeypatch):
    import local_brain
    ollama.delay = 5.0                                  # slower than the 4 s it's given
    ollama.replies.append(say("me", "Hey!"))
    t0 = time.time()
    assert local_brain.local_reply("hey", [], "") is None
    assert time.time() - t0 < 5
    ollama.delay = 0
    ollama.models = []                                  # model not downloaded
    local_brain._state["checked"] = 0
    assert local_brain.ready()[0] is False and "isn't downloaded" in local_brain.ready()[1]
    ollama.models = ["qwen2.5:3b"]
    local_brain._state["checked"] = 0
    monkeypatch.setattr(local_brain, "_memory", lambda: (0.95, 0.3))
    assert "memory" in local_brain.ready()[1]
    monkeypatch.setattr(local_brain, "_memory", lambda: (0.5, 4.0))
    monkeypatch.setattr(local_brain, "heavy_running", lambda: True)
    assert "heavy studio job" in local_brain.ready()[1]


def test_nothing_running_is_harmless(monkeypatch, tmp_path):
    import local_brain
    monkeypatch.delenv("JARVIS_NO_LOCAL_BRAIN", raising=False)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"local_brain": {"url": "http://127.0.0.1:9"}}))
    monkeypatch.setattr(local_brain, "CONFIG", cfg)
    local_brain._state.update(up=None, checked=0.0, models=[])
    assert local_brain.ready() == (False, "Ollama isn't running")
    assert local_brain.local_reply("hey", [], "") is None
    assert local_brain.chat([{"role": "user", "content": "hi"}]) is None


def test_turned_off(ollama, monkeypatch):
    import local_brain
    local_brain.CONFIG.write_text(json.dumps({"local_brain": {"enabled": False, "url": ollama.url}}))
    assert local_brain.ready() == (False, "turned off")
    local_brain.CONFIG.write_text(json.dumps({"local_brain": {"chat": False, "url": ollama.url}}))
    assert local_brain.ready("chat") == (False, "turned off") and local_brain.ready()[0]


def test_measured_claude_size_replaces_the_estimate(ollama):
    import local_brain
    for _ in range(5):
        local_brain.claude_used("brain", 12000)
    assert local_brain.typical("brain") == 12000
    local_brain.saved("brain", 2)
    assert local_brain.summary()["tokens"] == 24000


def test_heavy_studio_work_unloads_the_model(ollama, studio_home):  # noqa: F811
    import local_brain
    from studio import engines
    local_brain._refresh(force=True)
    with engines.heavy("test"):
        pass
    assert ollama.unloads == 1


# ---------------- the answer memory ----------------

def _bag(text):
    import re
    v = np.zeros(64, dtype=np.float32)
    for w in re.findall(r"[a-z]+", text.lower()):
        if w not in ("is", "s", "a", "the"):              # like a real model, little words barely count
            v[hash(w) % 64] += 1
    n = np.linalg.norm(v)
    return v / n if n else None


@pytest.fixture
def answers(tmp_path, monkeypatch, ollama):
    import vecstore
    monkeypatch.setattr(vecstore, "EMBED", _bag)
    monkeypatch.setattr(vecstore, "_stores", {})
    monkeypatch.setattr(vecstore, "DATA", tmp_path / "vec")
    monkeypatch.setattr(vecstore, "_persona_key", lambda: "hype")
    return vecstore


def test_same_question_is_answered_from_memory(answers):
    import local_brain
    a = "A J-term is a short mini semester in January, usually three or four weeks of one intensive class."
    assert answers.remember("What is a J-term?", a)
    assert answers.lookup("what's a J-term") == a
    assert local_brain.summary()["by"] == {"cache": 1}


def test_different_questions_never_share_an_answer(answers, monkeypatch):
    answers.remember("What is the capital of France?", "The capital of France is Paris, bro.")
    answers.remember("What is dark matter?", "Dark matter is unseen mass that we only notice through its gravity.")
    monkeypatch.setattr(answers, "_same_question", lambda a, b: False)
    assert answers.lookup("What is the capital of Spain?") is None
    assert answers.lookup("What is dark energy?") is None


def test_reworded_question_needs_the_local_models_ok(answers, ollama, monkeypatch):
    monkeypatch.setattr(answers, "EMBED", lambda t: _bag("far away moon"))    # as close in meaning as can be
    answers.remember("How far away is the moon?", "The moon is about 384,000 kilometres away on average.")
    ollama.replies[:] = [json.dumps({"same": True})]
    assert answers.lookup("How far is the moon from us?") is None             # "us" is personal: never cached
    ollama.replies[:] = [json.dumps({"same": True})]
    assert answers.lookup("How far is the moon?") == "The moon is about 384,000 kilometres away on average."
    ollama.replies[:] = [json.dumps({"same": False})]
    assert answers.lookup("How far is the moon?") is None


@pytest.mark.parametrize("q", ["What's the weather today?", "Who won the game last night?", "What is my schedule?",
                               "What's the latest iPhone?", "What is the price of bitcoin?", "Play some music",
                               "What did you do?", "Who is the president?"])
def test_timely_or_personal_questions_are_never_kept(answers, q):
    assert not answers.remember(q, "Here is a perfectly fine looking answer for that one.")


@pytest.mark.parametrize("a", ["You've hit your 5-hour limit · resets 4pm", "Did you mean the moon or Mars?",
                               "That one is taking me too long, I'm afraid.", "ok"])
def test_bad_answers_are_never_kept(answers, a):
    assert not answers.remember("What is a quasar?", a)


def test_old_or_other_personality_answers_are_not_reused(answers, monkeypatch):
    answers.remember("What is a quasar?", "A quasar is a super bright core of a far galaxy, powered by a black hole.")
    monkeypatch.setattr(answers, "_persona_key", lambda: "butler")
    assert answers.lookup("What is a quasar?") is None
    monkeypatch.setattr(answers, "_persona_key", lambda: "hype")
    real = time.time
    monkeypatch.setattr(answers.time, "time", lambda: real() + 31 * 86400)
    assert answers.lookup("What is a quasar?") is None
    monkeypatch.setattr(answers.time, "time", real)
    assert answers.store().count() == 0                 # expired entries are cleaned up


def test_answer_memory_turned_off(answers):
    import local_brain
    local_brain.CONFIG.write_text(json.dumps({"local_brain": {"answer_cache": False, "url": "x"}}))
    assert not answers.remember("What is a quasar?", "A quasar is a super bright core of a far galaxy.")


# ---------------- studio jobs ----------------

def test_broken_json_is_fixed_locally_without_claude(ollama, studio_home, monkeypatch):  # noqa: F811
    from studio import claude
    calls = []

    def runner(prompt, timeout, web):
        calls.append(prompt)
        return 'Here you go: {"idea": "Moon dust" "score": 7}'
    monkeypatch.setattr(claude, "RUNNER", runner)
    ollama.replies.append(json.dumps({"idea": "Moon dust", "score": 7}))
    assert claude.ask_json('An idea.\n\nJSON only: {"idea": "...", "score": 0}') == {"idea": "Moon dust", "score": 7}
    assert len(calls) == 1                               # no Claude repair call


def test_a_local_json_fix_that_changes_words_goes_to_claude(ollama, studio_home, monkeypatch):  # noqa: F811
    from studio import claude
    calls = []

    def runner(prompt, timeout, web):
        calls.append(prompt)
        return '{"ideas": ["Moon dust"' if len(calls) == 1 else '{"ideas": ["Moon dust"]}'
    monkeypatch.setattr(claude, "RUNNER", runner)
    ollama.replies.append(json.dumps({"ideas": ["Mars dust storms"]}))
    assert claude.ask_json('JSON only: {"ideas": [...]}') == {"ideas": ["Moon dust"]}
    assert len(calls) == 2 and calls[1].startswith("Rewrite the answer")


PADDING = ("Every extra kilogram you carry costs more fuel, and that fuel weighs a lot as well, so the whole "
           "problem keeps feeding on itself over and over.")


def _long_draft():
    return {"hooks": ["Why do rockets throw away 88 percent of themselves?", "What if a rocket kept its tanks?"],
            "lines": [{"text": t, "facts": ["F1"], "visual": "diagram"} for t in BODY + [PADDING] * 3],
            "calcs": [{"expr": "2290/2970", "result": 0.77}], "titles": ["t"]}


def test_word_count_is_fixed_locally_when_numbers_survive(ollama, studio_home, fakes):  # noqa: F811
    from studio import script
    fc, _ = fakes
    d = _long_draft()
    n = len(script.text_of(d, d["hooks"][0]).split())
    assert n > script.WORDS_MAX
    ollama.replies.append(json.dumps({"hooks": d["hooks"], "lines": BODY[:7] + ["Weight.", "Fuel.", "Speed."]}))
    out = script._fit_length(d, n)
    assert fc.fits == 0 and out and [ln["text"] for ln in out["lines"]][:7] == BODY[:7] and out["lines"][0]["facts"] == ["F1"]
    assert out["calcs"] == d["calcs"]                    # the checked calculation is kept as it was


def test_word_count_fix_that_changes_a_number_goes_to_claude(ollama, studio_home, fakes):  # noqa: F811
    from studio import script
    fc, _ = fakes
    d = _long_draft()
    n = len(script.text_of(d, d["hooks"][0]).split())
    changed = [t.replace("2,970", "3,000") for t in BODY[:7]] + ["Weight.", "Fuel.", "Speed."]
    ollama.replies.append(json.dumps({"hooks": d["hooks"], "lines": changed}))
    script._fit_length(d, n)
    assert fc.fits == 1                                  # Claude did it


GOOD_LOCAL = {"hooks": ["What if a rocket kept its empty tanks?", "Why do rockets drop parts?", "Where does 77 percent go?"],
              "lines": [{"text": "A Saturn V weighed 2,970 tonnes on the pad.", "facts": ["F1"], "visual": "number"},
                        {"text": "Its first stage was 2,290 tonnes.", "facts": ["F2"], "visual": "diagram"},
                        {"text": "Divide them and you get about 0.77, so 77 percent is that stage.", "facts": ["F1", "F2"], "visual": "math"},
                        {"text": "So the next stage starts lighter.", "facts": ["general"], "visual": "animation"}],
              "calcs": [{"expr": "2290/2970", "result": 0.77, "unit": "", "says": "share"}], "titles": ["Rockets"]}


def test_local_draft_rules():
    from studio import script
    facts = [{"claim": "Saturn V launch mass 2,970 t"}, {"claim": "S-IC stage mass 2,290 t"}]
    assert script._draft_problem(GOOD_LOCAL, facts) is None
    bad = json.loads(json.dumps(GOOD_LOCAL))
    bad["lines"][1]["text"] = "Its first stage was 2,500 tonnes."
    assert "numbers that aren't in the facts" in script._draft_problem(bad, facts)
    bad = json.loads(json.dumps(GOOD_LOCAL))
    bad["lines"][0]["facts"] = ["F9"]
    assert "fact id" in script._draft_problem(bad, facts)
    bad = json.loads(json.dumps(GOOD_LOCAL))
    bad["calcs"][0]["result"] = 0.5
    assert "calculation" in script._draft_problem(bad, facts)


def test_draft_trial_takes_turns_and_claude_still_fact_checks(ollama, studio_home, fakes):  # noqa: F811
    from studio import db, jobs, script, topics
    fc, tid = fakes
    ollama.replies.append(json.dumps(dict(GOOD_LOCAL, lines=GOOD_LOCAL["lines"] + [
        {"text": t, "facts": ["F1"], "visual": "diagram"} for t in BODY[4:7]] * 3)))
    vid = jobs.create(tid, title="x")
    script.draft_step(topics.topic(tid), vid)
    first = db.one("SELECT structure FROM scripts WHERE video_id=?", vid)
    assert json.loads(first["structure"])["by"] == "local" and fc.writes == 0
    script.check_step(topics.topic(tid), vid)
    assert fc.reviews == 1                               # Claude fact-checked the local draft
    assert script.trial_report()["local"]["n"] == 1
    vid2 = jobs.create(tid, title="y")
    script.draft_step(topics.topic(tid), vid2)           # next turn is Claude's
    assert fc.writes == 1


def test_trial_decides_off_when_local_drafts_do_worse(studio_home):  # noqa: F811
    from studio import db, script
    d = script._trial()
    d["local"] = {"n": 4, "passed": 1, "reviews": 12}
    d["claude"] = {"n": 5, "passed": 4, "reviews": 8}
    db.put(script.TRIAL_KEY, d)
    vid = 99
    db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", vid, 1, "[]", "", "", json.dumps({"by": "local"}), "{}", "failed", time.time())
    script._trial_outcome({"id": 1}, vid, False)
    assert script.trial_report()["verdict"] == "off"
