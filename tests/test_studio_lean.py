"""Leaner, faster studio Claude calls: the command, JSON repair, usage totals, parallel source checks,
visuals planned during narration."""
import json
import subprocess
import threading
import time

import pytest

from test_studio_foundation import studio_home  # noqa: F401


def test_lean_command_text_job_has_no_tools():
    from studio import claude
    cmd = claude.command("claude", "Write it", web=False)
    assert cmd[cmd.index("--tools") + 1] == ""
    sp = cmd[cmd.index("--system-prompt") + 1]
    assert "JSON" in sp and "WebSearch" not in sp and "Today is" in sp
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert "--model" not in cmd and "--effort" not in cmd          # quality settings are never changed


def test_lean_command_web_job_keeps_web_search():
    from studio import claude
    cmd = claude.command("claude", "Check it", web=True)
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert "WebSearch WebFetch" in cmd[cmd.index("--allowedTools") + 1]
    assert "WebSearch" in cmd[cmd.index("--system-prompt") + 1]
    old = claude.command("claude", "Check it", web=True, lean=False)
    assert "--tools" not in old and "--system-prompt" not in old


def _done(stdout, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_runs_in_an_empty_room_and_counts_tokens(monkeypatch):
    import brain
    from studio import claude, db
    seen = {}

    def fake(cmd, timeout, cwd=None):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return _done(json.dumps({"result": "[1, 2]", "is_error": False, "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 90, "cache_read_input_tokens": 4000,
            "output_tokens": 50, "server_tool_use": {"web_search_requests": 2}}}))
    monkeypatch.setattr(brain, "find_claude", lambda: "claude")
    monkeypatch.setattr(brain, "_run_claude", fake)
    assert claude.ask_json("Rate these", purpose="rate topic ideas") == [1, 2]
    assert seen["cwd"].endswith("claude_room") and not list(__import__("pathlib").Path(seen["cwd"]).iterdir())
    u = db.get(f"claude_usage_{claude._today()}")
    assert u["calls"] == 1 and u["input"] == 100 and u["cached"] == 4000 and u["output"] == 50 and u["searches"] == 2
    assert u["by"] == {"rate topic ideas": 1}


def test_plan_limit_is_still_a_wait(monkeypatch):
    import brain
    from studio import claude, jobs
    monkeypatch.setattr(brain, "find_claude", lambda: "claude")
    monkeypatch.setattr(brain, "_run_claude", lambda c, t, cwd=None: _done(json.dumps(
        {"result": "Claude usage limit reached", "is_error": True, "usage": {}}), 1))
    with pytest.raises(jobs.QuotaWait):
        claude.ask("hi")


def test_older_claude_code_falls_back_to_the_old_way(monkeypatch):
    import brain
    from studio import claude
    calls = []
    monkeypatch.setattr(brain, "find_claude", lambda: "claude")
    monkeypatch.setattr(brain, "_run_claude", lambda c, t, cwd=None: calls.append(c) or
                        _done("", 1, "error: unknown option '--no-session-persistence'"))
    monkeypatch.setattr(brain, "run_claude", lambda c, t, cwd=None: calls.append(c) or _done("plain answer"))
    assert claude.ask("hi") == "plain answer"
    assert "--system-prompt" in calls[0] and "--system-prompt" not in calls[1]
    assert claude.ask("again") == "plain answer" and len(calls) == 3   # remembered: no second try
    claude._OLD.clear()


def test_lean_can_be_turned_off(monkeypatch):
    import brain
    from studio import claude, config
    config.set_values(lean_claude=False)
    calls = []
    monkeypatch.setattr(brain, "find_claude", lambda: "claude")
    monkeypatch.setattr(brain, "run_claude", lambda c, t, cwd=None: calls.append(c) or _done("ok"))
    assert claude.ask("hi") == "ok" and "--system-prompt" not in calls[0]


def test_bad_json_is_repaired_without_redoing_the_research(monkeypatch):
    from studio import claude, db
    asked = []

    def runner(prompt, timeout, web):
        asked.append((prompt, web))
        if len(asked) == 1:
            return 'Here you go: {"claims": [{"line": 1, "verdict": "supported"},], oops'
        return '{"claims": [{"line": 1, "verdict": "supported"}]}'
    monkeypatch.setattr(claude, "RUNNER", runner)
    out = claude.ask_json('Check every line.\n\nJSON only: {"claims": [...]}', web=True, purpose="fact-check: x")
    assert out == {"claims": [{"line": 1, "verdict": "supported"}]}
    assert len(asked) == 2 and asked[1][1] is False                 # the repair has no web and is small
    assert 'JSON only: {"claims"' in asked[1][0] and "oops" in asked[1][0]
    assert db.get(f"claude_calls_{claude._today()}") == 2           # the repair counts against the cap


def test_repair_failing_falls_back_to_asking_again(monkeypatch):
    from studio import claude
    answers = iter(["not json", "still not json", '{"ok": true}'])
    asked = []
    monkeypatch.setattr(claude, "RUNNER", lambda p, t, w: asked.append(w) or next(answers))
    assert claude.ask_json("Q\n\nJSON only: {\"ok\": true}", web=True) == {"ok": True}
    assert asked == [True, False, True]


def test_sources_are_checked_in_parallel_and_in_order(monkeypatch):
    from studio import topics
    active, peak = [0], [0]
    lock = threading.Lock()

    def fetch(url):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return 200 if "good" in url else 404
    monkeypatch.setattr(topics, "FETCH", fetch)
    facts = [{"claim": f"c{i}", "url": f"https://{'good' if i % 2 else 'bad'}.example/{i}"} for i in range(8)]
    t = time.time()
    out = topics.check_all(facts)
    assert time.time() - t < 0.3 and peak[0] > 1
    assert [o["claim"] for o in out] == [f"c{i}" for i in range(8)]
    assert [o["http_status"] for o in out] == [404, 200] * 4


def test_visuals_are_planned_while_narrating(monkeypatch):
    from studio import claude, db, pipeline, script, video
    vid = db.insert("INSERT INTO videos(state, title, created) VALUES('generating_audio', 't', ?)", time.time())
    body = {"lines": [{"text": "The Moon is 384,400 km away."}, {"text": "That is 30 Earths."}], "calcs": []}
    db.insert("INSERT INTO scripts(video_id, version, hooks, hook, body, structure, checks, status, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", vid, 1, "[]", "The Moon is 384,400 km away.", "x", json.dumps(body), "{}",
              "passed", time.time())
    scenes = [{"type": "number", "value": "384,400", "label": "km"}, {"type": "headline", "text": "30 Earths"}]
    order = []

    def runner(prompt, timeout, web):
        order.append("plan-start")
        time.sleep(0.2)
        order.append("plan-end")
        return json.dumps(scenes)
    monkeypatch.setattr(claude, "RUNNER", runner)
    from studio import narration

    def narrate(text, out, video_id):
        time.sleep(0.05)
        order.append("narrated")
        raise narration.NarrationError("stop here")
    monkeypatch.setattr(narration, "narrate", narrate)
    with pytest.raises(Exception):
        pipeline.generating_audio(db.video(vid), None)
    assert order == ["plan-start", "narrated", "plan-end"]            # both ran at the same time
    assert json.loads(db.video(vid)["meta"])["scenes"] == scenes      # kept for render(), even after a retry
    calls = len(order)
    monkeypatch.setattr(video, "plan", lambda row: pytest.fail("planned twice"))
    with pytest.raises(Exception):
        pipeline.generating_audio(db.video(vid), None)
    assert len(order) == calls + 1


def test_source_excerpt_picks_the_sentences_with_the_claims_numbers():
    from studio import topics
    page = ("Welcome to the Sun fact sheet. Bulk parameters are listed below. The volumetric mean radius is "
            "695,700 km for the Sun. Earth's mean radius is 6,371 km. Cookies help us improve this site.")
    ex = topics.excerpt(page, "The Sun's mean radius is about 695,700 km.")
    assert "695,700 km" in ex and "Cookies" not in ex and "Welcome" not in ex
    assert topics.excerpt("", "anything") == ""


def test_fact_check_shows_the_checker_the_source_text(monkeypatch):
    from studio import claude, script, topics
    monkeypatch.setattr(topics, "PAGE", lambda url: "The Moon is 384,400 km from Earth on average." if "moon" in url else "")
    seen = {}

    def runner(prompt, timeout, web):
        seen["p"], seen["web"] = prompt, web
        return json.dumps({"claims": [{"line": 1, "verdict": "supported", "fact": "F1"}], "hooks": {"0": {
            "curiosity": 8, "clarity": 8, "specificity": 8, "honesty": 9, "speed": 8}}, "originality": 7,
            "inauthentic_risk": "low", "problems": []})
    monkeypatch.setattr(claude, "RUNNER", runner)
    topic = {"id": 1, "title": "t", "sources": [
        {"claim": "The Moon is 384,400 km away.", "url": "https://nasa.example/moon", "verdict": "reachable"},
        {"claim": "Light takes 1.28 s.", "url": "https://other.example/light", "verdict": "reachable"}]}
    draft = {"hooks": ["How far is the Moon?"], "lines": [{"text": "The Moon is 384,400 km away.", "facts": ["F1"]}],
             "calcs": [{"expr": "384400/299792.458", "result": 1.28}]}
    script.review(draft, topic)
    assert seen["web"] is True                                      # web search stays on for the fact-check
    assert 'page text: "The Moon is 384,400 km from Earth on average."' in seen["p"]
    assert "couldn't read it here: open the page yourself" in seen["p"]


def test_usage_command(monkeypatch, capsys):
    from studio import __main__ as cli, claude, db
    db.put(f"claude_usage_{claude._today()}", {"calls": 3, "input": 10})
    assert cli.main(["usage"]) == 0
    assert '"calls": 3' in capsys.readouterr().out
