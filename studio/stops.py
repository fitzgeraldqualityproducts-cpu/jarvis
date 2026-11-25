"""Where Shorts stop before they're made: the script stage, measured from the studio's own records (read only).

summary(since)  every Short started since then: did its script pass (first try, after a fix, after cutting a line)
                or stop, and why. The reasons are the lesson categories of the fact-check's hard problems.
text(since)     the same in plain words. `python -m studio stops [--since YYYY-MM-DD]`"""
import collections
import datetime
import json
import time

from studio import db


def _ts(day):
    return time.mktime(datetime.datetime.strptime(day, "%Y-%m-%d").timetuple())


def summary(since=None):
    from studio import lessons
    since = _ts(since) if isinstance(since, str) else float(since or 0)
    out = {"since": since, "shorts": 0, "passed": 0, "first_try": 0, "after_fix": 0, "cut_lines": 0, "stopped": 0,
           "writing": 0, "stop_reasons": collections.Counter(), "fix_reasons": collections.Counter(),
           "reviews": 0, "stopped_ids": []}
    for v in db.q("SELECT * FROM videos WHERE created>=? ORDER BY id", since):
        rows = db.q("SELECT status, checks, structure FROM scripts WHERE video_id=? ORDER BY version", v["id"])
        if not rows:
            continue
        out["shorts"] += 1
        failed = [r for r in rows if r["status"] == "failed"]
        passed = [r for r in rows if r["status"] == "passed"]
        out["reviews"] += len(failed) + len(passed)
        for r in failed:
            for p in json.loads(r["checks"] or "{}").get("hard_problems") or []:
                out["fix_reasons"][lessons.categorize(p)] += 1
        if passed:
            out["passed"] += 1
            if any("pruned" in json.loads(r["structure"] or "{}") for r in passed):
                out["cut_lines"] += 1
            elif failed:
                out["after_fix"] += 1
            else:
                out["first_try"] += 1
        elif v["state"] in ("failed", "rejected", "cancelled") or (failed and "fact-check" in str(v["error"] or "")):
            out["stopped"] += 1
            out["stopped_ids"].append(v["id"])
            last = json.loads(failed[-1]["checks"] or "{}") if failed else {}
            for c in {lessons.categorize(p) for p in last.get("hard_problems") or []} or {"other"}:
                out["stop_reasons"][c] += 1
        else:
            out["writing"] += 1
    return out


def text(since=None):
    s = summary(since)
    when = datetime.date.fromtimestamp(s["since"]).isoformat() if s["since"] else "the start"
    if not s["shorts"]:
        return f"No Short has had a script written since {when}."
    done = s["passed"] + s["stopped"]
    lines = [f"Shorts with a script since {when}: {s['shorts']} ({s['writing']} still being written)",
             f"- Passed: {s['passed']} of {done} finished" + (f" ({100 * s['passed'] / done:.0f}%)" if done else ""),
             f"  - first try: {s['first_try']}, after a rewrite: {s['after_fix']}, by cutting one or two lines: {s['cut_lines']}",
             f"- Stopped at the script: {s['stopped']}" + (f" (videos {', '.join(map(str, s['stopped_ids']))})" if s["stopped_ids"] else ""),
             f"- Fact-checks run: {s['reviews']} ({s['reviews'] / s['shorts']:.1f} per Short)"]
    if s["stop_reasons"]:
        lines.append("- Why they stopped: " + ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in s["stop_reasons"].most_common()))
    if s["fix_reasons"]:
        lines.append("- Problems the fact-check sent back: " + ", ".join(f"{k.replace('_', ' ')} {n}"
                                                                          for k, n in s["fix_reasons"].most_common(6)))
    return "\n".join(lines)
