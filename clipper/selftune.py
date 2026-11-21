"""The clipper learning from its own mistakes and from Connor's decisions, and getting faster.

What it watches (all real events, kept in the `feedback` table):
  approved / rejected / regenerated / deleted   what Connor kept and what he threw away (and why)
  edited          what he changed in the editor: caption style, layout, switches, trim, word fixes
  qa_failed       which quality checks a render failed, and qa_recovered when a safer retry passed
  job_failed      why a job failed (download, disk, no speech, Claude, a bug in the code...)
  bug             a crash inside the clipper's own code, with its traceback (for self-repair)
  speed           how fast each step ran on this computer

What it learns (only from patterns, never from one event; every lesson is listed on the dashboard
with the evidence, and can be undone, after which it isn't learned again):
  - his caption style, layout and editing switches, when he keeps changing them the same way
  - clip length, when he keeps trimming clips shorter (or longer)
  - openings, when he keeps cutting the start later: Claude is told to start on the hook
  - spellings: a word he corrects the same way twice is fixed in captions and given to Whisper
  - which kinds of moment he approves or throws away: small score nudges (same cap as the
    YouTube-results nudges, and never able to override a context failure)
  - his own reasons for rejecting clips: passed to Claude in a sentence or two (few tokens)
Quality checks: a clip that fails a fixable check is rendered once more with a safer setting
(whole picture when a face was cut off, no jump cuts when captions or sync were off), and the rest
of that video's clips use that setting from the start, so the same mistake isn't paid for twice.
Crashes in the clipper's code are handed to Jarvis's self-repair mechanic (repair.py: snapshot,
self-check, rollback, "undo that"), at most twice a day, then the job is retried.
Speed: face analysis is reused when only the edit changes, and the video encoder's speed setting is
chosen from how fast this computer really renders (same quality setting, so quality doesn't drop).
"""
import json
import re
import statistics
import time
import traceback

from clipper import db, settings

MIN_SAME = 3            # the same change this many times before it's learned
TOGGLES = ["jump_cuts", "remove_fillers", "zooms", "punch_ins", "sound_effects", "hook_overlay", "music"]
SHRINK = 4.0
NEGATIVE = ("rejected", "regenerated", "deleted")
# what to change when a quality check fails, for one safer retry
FALLBACKS = {"speaker_visible": {"layout": "fit"},
             "captions": {"jump_cuts": False, "remove_fillers": False},
             "length_matches_edit": {"jump_cuts": False, "remove_fillers": False},
             "av_sync": {"jump_cuts": False, "remove_fillers": False},
             "caption_readability": {"caption": {"words_per_line": 3}}}
REPAIRS_PER_DAY = 2
PRESETS = ["superfast", "veryfast", "faster", "fast"]


# ---------- recording ----------

def record(kind, clip_id=None, job_id=None, channel=None, **detail):
    """Stores one event and re-learns. Never raises: learning must not break clipping."""
    try:
        if clip_id and (job_id is None or channel is None):
            c = db.clip(int(clip_id)) or {}
            job_id = job_id if job_id is not None else c.get("job_id")
            channel = channel or c.get("channel")
            detail.setdefault("category", (c.get("extra") or {}).get("category"))
            detail.setdefault("hook_type", c.get("hook_type"))
        if job_id and channel is None:
            channel = (db.job(int(job_id)) or {}).get("channel")
        channel = channel or "main"
        db.run("INSERT INTO feedback(t, channel, clip_id, job_id, kind, detail) VALUES(?,?,?,?,?,?)",
               time.time(), channel, clip_id, job_id, kind, json.dumps(detail, default=str)[:20000])
        if kind not in ("speed", "bug"):
            learn(channel)
    except Exception as e:
        print(f"(clipper learning: couldn't record {kind}: {e})")


def events(channel="main", kinds=None, last=200):
    sql = "SELECT * FROM feedback WHERE channel=?"
    args = [channel]
    if kinds:
        sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
        args += list(kinds)
    rows = db.q(sql + " ORDER BY id DESC LIMIT ?", *args, int(last))
    for r in rows:
        r["detail"] = db.loads(r.get("detail"), {})
    return rows


def edited(clip, clean):
    """Called by the editor (pipeline.edit) with the clip before the change and the edits."""
    d = {k: v for k, v in clean.items() if k not in ("title", "description", "pinned_comment")}
    if not d:
        return
    d["before"] = {"start": clip["start"], "end": clip["end"], "duration": clip["duration"],
                   "caption_style": clip.get("caption_style"), "layout": clip.get("layout")}
    words = d.get("words")
    if isinstance(words, dict):                      # remember what the corrected words said before
        tr_words = {f"{w['s']:.3f}": w["w"] for w in _clip_words(clip)}
        d["word_fixes"] = [[tr_words.get(k), v] for k, v in words.items() if tr_words.get(k) and str(v).strip()]
    record("edited", clip_id=clip["id"], **d)


def _clip_words(clip):
    try:
        from clipper import media, pipeline
        tr = media.load(pipeline.folder_for(clip["job_id"]) / "transcript.json") or {}
        return [w for w in tr.get("words", []) if clip["start"] - 30 <= w["s"] <= clip["end"] + 30]
    except Exception:
        return []


# ---------- learning ----------

def learn(channel="main"):
    """Works out the lessons from the recorded events and saves them on the channel."""
    try:
        ch = settings.channel(channel)
    except KeyError:
        return {}
    old = ch.get("tuned") or {}
    blocked = set(old.get("blocked") or [])
    lessons = {}
    eds = events(channel, ["edited"], 60)

    def add(key, value, why, n):
        if key not in blocked:
            lessons[key] = {"value": value, "why": why, "n": n, "t": (old.get("lessons", {}).get(key) or {}).get("t", time.time())}

    # caption style and layout he keeps switching to
    for field, label in (("caption_style", "caption style"), ("layout", "layout")):
        picks = [e["detail"][field] for e in eds if e["detail"].get(field) and e["detail"][field] != "auto"]
        if len(picks) >= MIN_SAME:
            top = max(set(picks), key=picks.count)
            if picks.count(top) >= MIN_SAME and picks.count(top) / len(picks) >= 0.75 and ch.get(field) != top:
                add(field, top, f"you switched {picks.count(top)} clips to {top.replace('_', ' ')} {label}", picks.count(top))
    # switches he keeps flipping the same way
    for k in TOGGLES:
        vals = [bool(e["detail"][k]) for e in eds if k in e["detail"]]
        if not vals:
            continue
        cur = bool(ch.get(k, True)) if k != "music" else bool(ch.get("music_folder"))
        flips = [v for v in vals if v != cur]
        if len(flips) >= MIN_SAME and len(flips) == len(vals):
            add(k, flips[0], f"you turned {_nice(k)} {'on' if flips[0] else 'off'} on {len(flips)} clips", len(flips))
    # length and openings
    trims = [e["detail"] for e in eds if "start" in e["detail"] or "end" in e["detail"]]
    if len(trims) >= MIN_SAME + 1:
        ratios = [(t.get("end", t["before"]["end"]) - t.get("start", t["before"]["start"])) / max(1.0, t["before"]["duration"])
                  for t in trims]
        r = statistics.median(ratios)
        if r < 0.85 or r > 1.15:
            same = sum(1 for x in ratios if (x < 1) == (r < 1))
            if same / len(ratios) >= 0.7:
                add("length_scale", round(max(0.7, min(1.3, r)), 2),
                    f"you made {same} of {len(ratios)} edited clips {'shorter' if r < 1 else 'longer'} (by about {abs(1 - r) * 100:.0f}%)",
                    same)
        shifts = [t["start"] - t["before"]["start"] for t in trims if "start" in t]
        later = [s for s in shifts if s > 0.6]
        if len(later) >= MIN_SAME and len(later) / max(1, len(shifts)) >= 0.7:
            add("late_start", True, f"you cut the opening of {len(later)} clips (about {statistics.median(later):.1f}s later)", len(later))
    # spellings he corrects the same way twice
    fixes = {}
    for e in eds:
        for before, after in e["detail"].get("word_fixes") or []:
            b, a = _bare(before), str(after).strip()
            if b and a and _bare(a) != b and len(b) > 1:
                fixes.setdefault(b, []).append(a)
    learned_fixes = {}
    for b, afters in fixes.items():
        top = max(set(afters), key=afters.count)
        if afters.count(top) >= 2:
            learned_fixes[b] = _bare_keep(top)
    if learned_fixes:
        add("fixes", learned_fixes, f"you corrected {len(learned_fixes)} word{'s' if len(learned_fixes) != 1 else ''} the same way "
            f"more than once ({', '.join(list(learned_fixes.values())[:4])})", len(learned_fixes))
    # which kinds of moment he keeps or throws away
    decisions = events(channel, ["approved"] + list(NEGATIVE), 200)
    prefs = {}
    for f in ("category", "hook_type"):
        groups = {}
        for d in decisions:
            k = d["detail"].get(f)
            if k:
                groups.setdefault(k, []).append(1.0 if d["kind"] == "approved" else -1.0)
        table = {}
        for k, vals in groups.items():
            if len(vals) >= 4:
                eff = sum(vals) / (len(vals) + SHRINK)
                if abs(eff) >= 0.25:
                    table[k] = round(max(-0.4, min(0.4, eff * 0.6)), 2)
        if table:
            prefs[f] = table
    if prefs:
        good = [k.replace("_", " ") for t in prefs.values() for k, v in t.items() if v > 0]
        bad = [k.replace("_", " ") for t in prefs.values() for k, v in t.items() if v < 0]
        why = "; ".join(x for x in (f"you usually keep {', '.join(good)}" if good else "",
                                    f"you usually throw away {', '.join(bad)}" if bad else "") if x)
        add("taste", prefs, why, len(decisions))
    reasons = [d["detail"].get("reason") for d in decisions if d["kind"] == "rejected" and len(str(d["detail"].get("reason") or "")) > 3]
    if reasons:
        add("reasons", [str(r)[:120] for r in reasons[:3]], f"your reasons for rejecting {len(reasons)} clip{'s' if len(reasons) != 1 else ''}", len(reasons))
    tuned = {"lessons": lessons, "blocked": sorted(blocked), "updated": time.time()}
    if tuned["lessons"] != old.get("lessons") or tuned["blocked"] != old.get("blocked"):
        new = set(lessons) - set(old.get("lessons") or {})
        settings.replace_channel_value(channel, "tuned", tuned)
        for k in new:
            db.log(f"Learned: {lessons[k]['why']}.", "info")
    return lessons


def _bare(w):
    return re.sub(r"[^\w']", "", str(w or "")).lower()


def _bare_keep(w):
    return re.sub(r"^[^\w']+|[^\w']+$", "", str(w or "").strip())


def _nice(k):
    return {"jump_cuts": "dead-air removal", "remove_fillers": "\"um\" removal", "zooms": "zooms", "punch_ins": "punch-ins",
            "sound_effects": "sound effects", "hook_overlay": "opening text", "music": "music"}.get(k, k.replace("_", " "))


def apply(ch):
    """The channel's settings with what it has learned applied (used for each job)."""
    tuned = (ch.get("tuned") or {}).get("lessons") or {}
    if not tuned:
        return ch
    ch = dict(ch)
    for k in ("caption_style", "layout") + tuple(TOGGLES):
        if k in tuned:
            if k == "music":
                if tuned[k]["value"] is False:
                    ch["music_folder"] = ""
            else:
                ch[k] = tuned[k]["value"]
    if "length_scale" in tuned:
        s = float(tuned["length_scale"]["value"])
        ch["clip_max"] = max(10.0, float(ch["clip_max"]) * s)
        ch["clip_hard_max"] = max(ch["clip_max"], min(180.0, float(ch["clip_hard_max"]) * s))
        ch["clip_min"] = max(6.0, min(float(ch["clip_min"]) * min(1.0, s), ch["clip_max"] * 0.8))
    if "fixes" in tuned:
        ch["fixes"] = {**tuned["fixes"]["value"], **(ch.get("fixes") or {})}
        names = list(ch.get("names") or [])
        for v in tuned["fixes"]["value"].values():
            if v[:1].isupper() and v not in names:
                names.append(v)
        ch["names"] = names[:30]
    notes = []
    if "late_start" in tuned:
        notes.append("The owner keeps trimming slow openings: start each clip right on its hook, skip the lead-in "
                     "(but never lose the context the moment needs).")
    if "reasons" in tuned:
        notes.append("The owner rejected clips because: " + "; ".join(tuned["reasons"]["value"]) + ".")
    if notes:
        ch["lessons"] = notes
    return ch


def preferences(channel="main"):
    """Score nudges from his own decisions (merged with the YouTube-results ones by the pipeline)."""
    try:
        tuned = (settings.channel(channel).get("tuned") or {}).get("lessons") or {}
    except KeyError:
        return {}
    return (tuned.get("taste") or {}).get("value") or {}


def merge_prefs(a, b):
    """Two sets of nudges, added and kept within the usual cap."""
    out = {}
    for src in (a or {}, b or {}):
        for f, table in src.items():
            if isinstance(table, dict):
                t = out.setdefault(f, {})
                for k, v in table.items():
                    t[k] = round(max(-0.5, min(0.5, t.get(k, 0.0) + float(v))), 2)
            else:
                out.setdefault(f, table)
    return out


def forget(channel="main", key=None):
    """Undo one lesson (and never learn it again), or all of them."""
    ch = settings.channel(channel)
    tuned = dict(ch.get("tuned") or {})
    lessons = dict(tuned.get("lessons") or {})
    blocked = set(tuned.get("blocked") or [])
    if key:
        if key not in lessons:
            return "I hadn't learned that."
        lessons.pop(key)
        blocked.add(key)
        msg = "Okay, I've undone that and won't learn it again."
    else:
        blocked |= set(lessons)
        lessons = {}
        msg = "Okay, I've forgotten everything I learned from your edits and choices."
    settings.replace_channel_value(channel, "tuned", {"lessons": lessons, "blocked": sorted(blocked), "updated": time.time()})
    return msg


def user_set(channel, keys):
    """Connor changed these in Settings himself: his choice wins over anything learned."""
    ch = settings.channel(channel)
    tuned = dict(ch.get("tuned") or {})
    lessons = dict(tuned.get("lessons") or {})
    hit = [k for k in keys if k in lessons and k not in ("taste", "reasons", "fixes")]
    if "clip_min" in keys or "clip_max" in keys or "length_mode" in keys:
        hit.append("length_scale")
    hit = [k for k in hit if k in lessons]
    if hit:
        for k in hit:
            lessons.pop(k)
        tuned["lessons"] = lessons
        tuned["blocked"] = sorted(set(tuned.get("blocked") or []) | set(hit))
        settings.replace_channel_value(channel, "tuned", tuned)


# ---------- quality-check fallbacks ----------

def fallback_for(result):
    """A safer setting to try once for a clip that failed a fixable check (or None)."""
    out = {}
    for check in result.get("critical") or []:
        for k, v in (FALLBACKS.get(check) or {}).items():
            out[k] = v
    return out or None


def job_fallback(job_id):
    return ((db.job(job_id) or {}).get("options") or {}).get("qa_fallback") or {}


def remember_fallback(job_id, fb):
    """The rest of this video's clips use the setting that worked, from the start."""
    job = db.job(job_id)
    opts = job["options"]
    opts["qa_fallback"] = {**(opts.get("qa_fallback") or {}), **fb}
    db.set_job(job_id, options=opts)


# ---------- failures and self-repair ----------

ERROR_KINDS = [("network", r"network|connection|timed? ?out|http error|unable to download|resolve host|ssl|reset by peer"),
               ("disk", r"no space|disk (?:space|is full)|errno 28"),
               ("permission", r"not authori[sz]ed|permission|private video|members.only|sign in"),
               ("no_speech", r"hardly any speech|couldn't find any speech"),
               ("claude", r"claude|usage limit|rate.?limit|overloaded"),
               ("media", r"ffmpeg|ffprobe|invalid data|moov atom|codec|can't read"),
               ("bug", r"(?:Type|Attribute|Key|Index|Name|Value|ZeroDivision|UnboundLocal|Import)Error")]


def classify(error):
    e = str(error or "")
    for kind, rx in ERROR_KINDS:
        if re.search(rx, e, re.I):
            return kind
    return "other"


def bug(where, job_id=None, clip_id=None):
    """Called from an except block: records a crash in the clipper's own code (with the traceback)
    so Jarvis's mechanic can look at it."""
    tb = traceback.format_exc()
    if "clipper" not in tb:
        return
    frames = re.findall(r'File "([^"]+)", line (\d+)', tb)
    ours = [f"{p.split('clipper')[-1]}:{n}" for p, n in frames if "/clipper/" in p.replace("\\", "/")]
    last = tb.strip().splitlines()[-1][:300]
    if classify(last) != "bug":
        return
    sig = f"{ours[-1] if ours else where}|{re.sub(r'[0-9]+', '#', last)}"
    record("bug", clip_id=clip_id, job_id=job_id, where=where, error=last, trace=tb[-6000:], sig=sig)


def repair_pending(say=print):
    """Runs in Jarvis (the parent process) after a job: hands new crashes to the self-repair mechanic,
    then retries the job if the fix worked. At most REPAIRS_PER_DAY a day, one try per kind of crash."""
    try:
        import repair
    except Exception:
        return None
    if not repair.enabled():
        return None
    day = time.time() - 86400
    rows = db.q("SELECT * FROM feedback WHERE kind='bug' ORDER BY id DESC LIMIT 20")
    done = db.loads(db.get("repaired_bugs", "{}"), {}) or {}
    today = [t for t in done.values() if t > day]
    for r in rows:
        d = db.loads(r["detail"], {})
        if d.get("sig") in done or len(today) >= REPAIRS_PER_DAY or r["t"] < day:
            continue
        done[d["sig"]] = time.time()
        today.append(time.time())
        db.put("repaired_bugs", json.dumps(done))
        db.log(f"The clipper crashed ({d.get('error')}); sending in the mechanic.", "warn", job_id=r.get("job_id"))
        res = repair.fix("make YouTube Shorts from a video", "clipper", f"{d.get('where')} (job {r.get('job_id')})",
                         d.get("error", ""), extra="Traceback:\n" + d.get("trace", "")) or {}
        record("repaired", job_id=r.get("job_id"), channel=r.get("channel"), sig=d.get("sig"), fixed=bool(res.get("fixed")),
               changed=res.get("changed") or [], cause=str(res.get("cause") or "")[:300])
        if res.get("fixed") and r.get("job_id"):
            j = db.job(r["job_id"]) or {}
            if j.get("state") in ("failed", "queued"):
                from clipper import jobs
                jobs.retry(r["job_id"])
        return res
    return None


# ---------- speed ----------

def speed(kind, seconds, media_seconds):
    """Remembers how fast a step runs on this computer (a smoothed seconds-per-media-second)."""
    if media_seconds <= 0 or seconds <= 0:
        return
    rate = seconds / media_seconds
    key = f"speed_{kind}"
    old = db.loads(db.get(key), None)
    val = rate if not old else old["rate"] * 0.7 + rate * 0.3
    db.put(key, json.dumps({"rate": round(val, 4), "n": (old or {}).get("n", 0) + 1, "t": time.time()}))


def rate(kind):
    return (db.loads(db.get(f"speed_{kind}"), None) or {}).get("rate")


def preset(g):
    """The encoder speed setting: "auto" picks from how fast this computer renders. The quality
    setting (CRF) stays the same, so slower presets only make smaller files, not better-looking ones."""
    p = g.get("x264_preset", "auto")
    if p != "auto":
        return p
    r = rate("render")
    if r is None:
        return "veryfast"
    if r > 3.0:             # over 3 seconds of work per second of clip: go lighter
        return "superfast"
    if r < 0.6:             # a fast computer: spend a little more time for smaller files
        return "faster"
    return "veryfast"


def summary(channel="main"):
    """What the dashboard shows: lessons, mistakes, repairs and speed."""
    try:
        tuned = (settings.channel(channel).get("tuned") or {})
    except KeyError:
        tuned = {}
    week = time.time() - 30 * 86400
    rows = db.q("SELECT kind, detail, t FROM feedback WHERE channel=? AND t>?", channel, week)
    counts = {}
    qa = {}
    fails = {}
    recovered = 0
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        d = db.loads(r["detail"], {})
        if r["kind"] in ("qa_failed", "qa_recovered"):
            for c in d.get("critical") or d.get("checks") or []:
                qa[c] = qa.get(c, 0) + 1
            recovered += r["kind"] == "qa_recovered"
        elif r["kind"] == "job_failed":
            fails[d.get("why", "other")] = fails.get(d.get("why", "other"), 0) + 1
    repairs = [dict(db.loads(r["detail"], {}), t=r["t"]) for r in db.q(
        "SELECT detail, t FROM feedback WHERE kind='repaired' ORDER BY id DESC LIMIT 5")]
    sp = {k: rate(k) for k in ("transcribe", "analyze", "render")}
    return {"lessons": [{"key": k, **v} for k, v in (tuned.get("lessons") or {}).items()],
            "blocked": tuned.get("blocked") or [], "counts": counts, "qa_fails": qa, "qa_recovered": recovered,
            "job_fails": fails, "repairs": repairs, "speed": sp, "preset": preset(settings.general())}


def report(channel="main"):
    s = summary(channel)
    if not s["lessons"]:
        n = sum(s["counts"].get(k, 0) for k in ("approved", "rejected", "edited", "regenerated", "deleted"))
        return (f"I haven't learned anything definite yet. I've seen {n} of your decisions; I need the same thing a few "
                "times before I change how I clip.")
    parts = ["Here's what I've learned: " + "; ".join(l["why"] for l in s["lessons"]) + "."]
    if s["qa_recovered"]:
        parts.append(f"I also fixed {s['qa_recovered']} clip{'s' if s['qa_recovered'] != 1 else ''} that failed a quality check by retrying with a safer setting.")
    return " ".join(parts)
