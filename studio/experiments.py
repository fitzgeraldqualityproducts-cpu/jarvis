"""Experiments: one variable at a time, a fixed number of Shorts per arm, and a result only when the
difference is clear. A finished experiment becomes a SUGGESTION for Connor; nothing changes by itself.
Experiments start only when Connor starts one (`python -m studio experiment start hook_style`).

  CATALOG     the experiments the studio knows how to run (each changes one thing in the script prompt)
  start(name) / stop()           one experiment at a time
  instruction(topic)             the running experiment's arm for this topic (assigned once, arms take turns),
                                 as a line for the writer's prompt; "" when nothing is running
  evaluate()                     compares the arms on the channel's own performance index (analytics.index_all:
                                 50 = this channel's typical Short), once every arm has enough measured Shorts

Uses the experiments table (variable, arms, status, result). Assignments are stored in the result JSON."""
import json
import math
import statistics
import time

from studio import db

CATALOG = {
    "hook_style": {
        "variable": "the hook's shape",
        "arms": {"question": "Make line 1 a question the Short then answers (still with its number in the first "
                             "words).",
                 "number": "Make line 1 a plain statement that leads with the surprising number itself (not a "
                           "question)."},
        "per_arm": 6,
    },
    "length": {
        "variable": "script length",
        "arms": {"short": "Aim for the short end of the length range (about 65-75 words).",
                 "long": "Aim for the long end of the length range (about 90-100 words)."},
        "per_arm": 6,
    },
}
CLEAR_GAP = 8.0          # index points between the arms' means
CLEAR_T = 2.0            # and Welch's t at least this big


class ExperimentError(Exception):
    pass


def running():
    r = db.one("SELECT * FROM experiments WHERE status='running' ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    r = dict(r)
    r["arms"], r["result"] = json.loads(r["arms"] or "[]"), json.loads(r["result"] or "{}")
    return r


def start(name, now=None):
    if name not in CATALOG:
        raise ExperimentError(f"no experiment called {name!r}; known: {', '.join(CATALOG)}")
    if running():
        raise ExperimentError("an experiment is already running; stop it first (one variable at a time)")
    spec = CATALOG[name]
    eid = db.insert("INSERT INTO experiments(name, variable, arms, status, result, started) VALUES(?,?,?,?,?,?)",
                    name, spec["variable"], json.dumps(list(spec["arms"])), "running",
                    json.dumps({"assign": {}, "per_arm": spec["per_arm"]}), now or time.time())
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", now or time.time(), "experiment",
              f"started {name}: {spec['variable']}", json.dumps({"arms": list(spec["arms"])}))
    return eid


def stop(reason="stopped by Connor", now=None):
    r = running()
    if not r:
        return None
    res = dict(r["result"], stopped=reason)
    db.run("UPDATE experiments SET status='stopped', result=?, ended=? WHERE id=?", json.dumps(res), now or time.time(), r["id"])
    return r["id"]


def instruction(topic):
    """The writer's extra line for this topic under the running experiment ("" if none). Arms take turns."""
    r = running()
    if not r or not topic or topic.get("id") is None:
        return ""
    spec = CATALOG.get(r["name"])
    if not spec:
        return ""
    assign = r["result"].setdefault("assign", {})
    key = str(topic["id"])
    arm = assign.get(key)
    if arm not in spec["arms"]:
        counts = {a: list(assign.values()).count(a) for a in spec["arms"]}
        arm = min(spec["arms"], key=lambda a: (counts[a], list(spec["arms"]).index(a)))
        assign[key] = arm
        db.run("UPDATE experiments SET result=? WHERE id=?", json.dumps(r["result"]), r["id"])
    return f"EXPERIMENT ({spec['variable']}, arm \"{arm}\"): {spec['arms'][arm]}"


def _welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return abs(statistics.mean(a) - statistics.mean(b)) / se if se > 0 else (math.inf if statistics.mean(a) != statistics.mean(b) else 0.0)


def evaluate(now=None, index=None):
    """Checks the running experiment. Returns {"state": "waiting"|"clear"|"no difference", ...} or None."""
    from studio import analytics
    r = running()
    if not r:
        return None
    spec = CATALOG[r["name"]]
    idx = index if index is not None else analytics.index_all(now)
    by_topic = {str(v.get("topic_id")): v["index"] for v in idx.values() if v.get("index") is not None}
    vals = {a: [] for a in spec["arms"]}
    for tid, arm in (r["result"].get("assign") or {}).items():
        if tid in by_topic and arm in vals:
            vals[arm].append(by_topic[tid])
    need = r["result"].get("per_arm") or spec["per_arm"]
    have = {a: len(v) for a, v in vals.items()}
    if min(have.values()) < need:
        return {"state": "waiting", "measured": have, "need": need}
    a, b = list(spec["arms"])
    ma, mb = statistics.mean(vals[a]), statistics.mean(vals[b])
    t = _welch(vals[a], vals[b])
    clear = abs(ma - mb) >= CLEAR_GAP and t >= CLEAR_T
    winner = (a if ma > mb else b) if clear else None
    res = dict(r["result"], means={a: round(ma, 1), b: round(mb, 1)}, t=round(t, 2), measured=have, winner=winner)
    db.run("UPDATE experiments SET status='done', result=?, ended=? WHERE id=?", json.dumps(res), now or time.time(), r["id"])
    from studio import health
    if clear:
        msg = (f"The {r['name'].replace('_', ' ')} test is done: \"{winner}\" did better ({res['means'][winner]} vs "
               f"{res['means'][b if winner == a else a]} on the channel's index, {need}+ Shorts each). It's only a "
               "suggestion: I haven't changed how Shorts are written. Say the word if you want it kept.")
    else:
        msg = (f"The {r['name'].replace('_', ' ')} test is done: no clear difference ({a} {res['means'][a]}, "
               f"{b} {res['means'][b]}). Nothing changes.")
    health.notify(f"experiment-{r['id']}", msg, "info")
    return {"state": "clear" if clear else "no difference", **res}


def maybe_evaluate(now=None):
    from studio import config
    if not config.enabled() or not running():
        return None
    return evaluate(now)


def status_text():
    r = running()
    if not r:
        last = db.one("SELECT name, status, result FROM experiments ORDER BY id DESC LIMIT 1")
        if not last:
            return "No experiment has run yet. Known: " + ", ".join(CATALOG)
        res = json.loads(last["result"] or "{}")
        return f"No experiment running. The last one, {last['name']}, ended {last['status']}: " + json.dumps(
            {k: res.get(k) for k in ("means", "winner", "measured", "stopped") if k in res})
    assign = r["result"].get("assign") or {}
    return f"Running {r['name']} ({CATALOG[r['name']]['variable']}): " + ", ".join(
        f"{a} {list(assign.values()).count(a)} Shorts" for a in r["arms"])
