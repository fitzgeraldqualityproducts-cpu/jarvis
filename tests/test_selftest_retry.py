"""A self-check that only timed out (busy computer, mic held for a moment) is retried before
an update is undone; a real failure still counts."""
import sys
import types

import selftest
import updater


def _fake(monkeypatch, runs):
    calls = {"n": 0}

    def run(skip=(), only=None):
        r = runs[min(calls["n"], len(runs) - 1)]
        calls["n"] += 1
        return {k: dict(v) for k, v in r.items() if only is None or k in only}
    monkeypatch.setattr(selftest, "run", run)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater, "_cfg", lambda: {"self_test": True})
    import importlib
    monkeypatch.setattr(importlib, "reload", lambda m: m)
    return calls


OK = {"ok": True, "detail": "opens", "extra": []}
SLOW = {"ok": False, "detail": "took too long", "extra": []}
DEAD = {"ok": False, "detail": "no input device", "extra": []}


def test_timeout_retried_then_passes(monkeypatch):
    calls = _fake(monkeypatch, [{"mic": SLOW, "code": OK}, {"mic": OK}])
    after, problem = updater._selftest({"mic": OK, "code": OK})
    assert problem is None and after["mic"]["ok"] and calls["n"] == 2


def test_timeout_that_keeps_happening_still_fails(monkeypatch):
    _fake(monkeypatch, [{"mic": SLOW, "code": OK}])
    after, problem = updater._selftest({"mic": OK, "code": OK})
    assert problem and "mic" in problem


def test_real_failure_not_retried(monkeypatch):
    calls = _fake(monkeypatch, [{"mic": DEAD, "code": OK}, {"mic": OK}])
    after, problem = updater._selftest({"mic": OK, "code": OK})
    assert problem and calls["n"] == 1
