"""Where the minutes go: how long each step of each Short took.

Every stage of the line (research, writing, fact-check, voice, render, checks, upload) is timed by the jobs worker,
and inside them every heavy job (SDXL-Turbo pictures, sharpening and depth, Manim, Chatterbox, whisperX, ACE-Step
music, the Remotion render) and every Claude call. Waiting for the graphics card is counted on its own line, so a
slow step and a queue are told apart. Kept in the kv table as "timings:<video id>" (no schema change).

    python -m studio timings [video id]     one Short's steps, or the average of the last 10
"""
import threading
import time
from contextlib import contextmanager

from studio import db

_cur = threading.local()
_lock = threading.Lock()
MAX_STEPS = 200


def _key(vid):
    return f"timings:{int(vid)}"


@contextmanager
def video(vid):
    """Steps timed inside this block (on this thread) belong to Short vid."""
    prev = getattr(_cur, "vid", None)
    _cur.vid = vid
    try:
        yield
    finally:
        _cur.vid = prev


def current():
    return getattr(_cur, "vid", None)


@contextmanager
def step(name, kind="step", vid=None):
    """Times the block and adds it to the current Short (nothing if there isn't one). Never raises."""
    t0 = time.monotonic()
    ok = False
    try:
        yield
        ok = True
    finally:
        add(vid if vid is not None else current(), name, time.monotonic() - t0, kind, ok)


def add(vid, name, seconds, kind="step", ok=True):
    if vid is None:
        return
    try:
        with _lock:
            d = db.get(_key(vid)) or {"steps": []}
            d["steps"] = (d["steps"] + [{"name": str(name)[:80], "kind": kind, "seconds": round(float(seconds), 2),
                                         "ok": bool(ok), "at": round(time.time())}])[-MAX_STEPS:]
            db.put(_key(vid), d)
    except Exception:
        pass


def steps(vid):
    return (db.get(_key(vid)) or {}).get("steps") or []


def _totals(rows, kinds):
    out = {}
    for r in rows:
        if r["kind"] in kinds:
            out[r["name"]] = out.get(r["name"], 0.0) + r["seconds"]
    return out


def report(vid):
    """{"stages": [(name, seconds)], "parts": [(name, seconds)], "total": seconds} for one Short, slowest first.
    stages add up to the Short's working time; parts are the heavy jobs and Claude calls inside them."""
    rows = steps(vid)
    st, parts = _totals(rows, ("stage",)), _totals(rows, ("heavy", "wait", "claude", "step"))
    return {"video_id": vid, "stages": sorted(st.items(), key=lambda x: -x[1]),
            "parts": sorted(parts.items(), key=lambda x: -x[1]), "total": round(sum(st.values()), 1)}


def summary(last=10):
    """The average of the last Shorts that have timings, slowest steps first."""
    keys = [r["key"] for r in db.q("SELECT key FROM kv WHERE key LIKE 'timings:%'")]
    vids = sorted((int(k.split(":")[1]) for k in keys if k.split(":")[1].isdigit()), reverse=True)[:last]
    if not vids:
        return {"shorts": 0, "stages": [], "parts": [], "total": 0}
    st, parts = {}, {}
    for v in vids:
        r = report(v)
        for n, s in r["stages"]:
            st[n] = st.get(n, 0.0) + s
        for n, s in r["parts"]:
            parts[n] = parts.get(n, 0.0) + s
    k = len(vids)
    avg = lambda d: sorted(((n, round(s / k, 1)) for n, s in d.items()), key=lambda x: -x[1])   # noqa: E731
    stages = avg(st)
    return {"shorts": k, "video_ids": vids, "stages": stages, "parts": avg(parts),
            "total": round(sum(s for _, s in stages), 1)}


def _mins(s):
    return f"{s / 60:.1f} min" if s >= 60 else f"{s:.0f} s"


def text(vid=None):
    r = report(vid) if vid else summary()
    head = f"Short #{vid}" if vid else f"Average of the last {r['shorts']} Shorts"
    if not r["stages"] and r["parts"]:                # a Short rendered by hand: only its heavy jobs were timed
        return "\n".join([f"{head}: made by hand, so only its heavy jobs and Claude calls were timed:"] +
                         [f"  {n:<28} {_mins(s):>9}" for n, s in r["parts"][:15]])
    if not r["stages"]:
        return f"{head}: no timings yet (they start with the next Short made after this update)."
    tot = r["total"] or 1
    lines = [f"{head}: {_mins(r['total'])} of work"]
    lines += [f"  {n:<28} {_mins(s):>9}  {100 * s / tot:4.0f}%" for n, s in r["stages"]]
    if r["parts"]:
        lines.append("Inside those steps:")
        lines += [f"  {n:<28} {_mins(s):>9}" for n, s in r["parts"][:15]]
    return "\n".join(lines)
