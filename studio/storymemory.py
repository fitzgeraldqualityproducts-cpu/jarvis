"""Story failures, remembered one by one (Video 20 audit, priority 9).

Every time the Narrative Integrity Engine (studio/narrative.py) blocks a script, each problem is saved in the
narrative_failures table of studio.db with the exact sentence, what the viewer was missing, the maths issue, the
root cause and the prevention rule. When a later version of the same Short passes, those rows are marked as fixed
(worked = 1); when the Short is given up, as not fixed (worked = 0).

advice() turns the recent rows into specific instructions for the writer (never a generic "improve the
storytelling"): the exact sentence that failed and the rule that would have prevented it, with the rules that
did NOT fix things last time marked so the writer takes them more seriously. Free: plain SQL."""
import time

from studio import db

WINDOW_DAYS = 30
MAX_EXAMPLES = 4


def record(issues, topic=None, video_id=None, version=None):
    """Saves the hard story problems of one failed script version. Returns how many rows were saved."""
    topic = topic or {}
    now, n = time.time(), 0
    for it in issues or []:
        if not it.get("hard"):
            continue
        db.insert("INSERT INTO narrative_failures(t, video_id, topic_id, topic, script_version, kind, beat, line_no, "
                  "sentence, missing, math_issue, root_cause, fix, worked) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                  now, video_id, topic.get("id"), str(topic.get("title") or "")[:200], version, it.get("kind"),
                  it.get("beat"), it.get("line"), str(it.get("sentence") or "")[:300],
                  str(it.get("missing") or it.get("text") or "")[:300], str(it.get("math") or "")[:200] or None,
                  str(it.get("root_cause") or "")[:200], str(it.get("fix") or "")[:300])
        n += 1
    return n


def outcome(video_id, worked):
    """Marks this Short's open story failures as fixed (its script passed) or not (it was given up)."""
    if video_id is None:
        return 0
    return db.run("UPDATE narrative_failures SET worked=? WHERE video_id=? AND worked IS NULL", 1 if worked else 0,
                  video_id) or 0


def recent(days=WINDOW_DAYS, now=None):
    since = (now or time.time()) - days * 86400
    return [dict(r) for r in db.q("SELECT * FROM narrative_failures WHERE t>=? ORDER BY id DESC", since)]


def advice(now=None):
    """A few lines for the writer's prompt ("" when nothing failed recently)."""
    rows = recent(now=now)
    if not rows:
        return ""
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    order = sorted(by_kind, key=lambda k: (-sum(1 for r in by_kind[k] if r["worked"] == 0), -len(by_kind[k])))
    out = []
    for k in order[:MAX_EXAMPLES]:
        r = by_kind[k][0]
        failed_again = sum(1 for x in by_kind[k] if x["worked"] == 0)
        example = f"\"{r['sentence'][:120]}\"" if r.get("sentence") else "(whole script)"
        what = r.get("missing") or ""
        math = f" [{r['math_issue']}]" if r.get("math_issue") else ""
        firm = "MUST: " if len(by_kind[k]) >= 3 or failed_again else ""
        out.append(f"{firm}{r['fix'] or what} (on \"{(r.get('topic') or 'an earlier Short')[:60]}\", line "
                   f"{r.get('line_no') or '-'} {example}: {what[:160]}{math}"
                   + (f"; {failed_again} rewrite(s) still failed this" if failed_again else "") + ")")
    return "Story mistakes this channel made before (don't repeat them): " + " / ".join(out)


def summary(now=None):
    rows = recent(now=now)
    if not rows:
        return "No story problems in the last 30 days."
    fixed = sum(1 for r in rows if r["worked"] == 1)
    failed = sum(1 for r in rows if r["worked"] == 0)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return (f"{len(rows)} story problem(s) in the last 30 days ("
            + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
            + f"); {fixed} fixed by a rewrite, {failed} not"
            + (f", {len(rows) - fixed - failed} still being worked on" if len(rows) - fixed - failed else "") + ".")
