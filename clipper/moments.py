"""Finding the moments worth a Short, scoring them, keeping their meaning intact, and ranking them.

1. Claude reads the transcript (numbered sentences with times, speakers, loud moments and
   chat spikes) and proposes candidates, scoring each on 10 criteria (0-10):
       hook, curiosity, emotion, education, entertainment, story, surprise, standalone,
       relevance, retention
   plus context_complete. Clips are given as sentence numbers, so they always start and end on
   whole sentences.
2. Boundaries are snapped to the exact word timings, a clip that starts mid-thought ("And so he...")
   is extended back, one that stops mid-sentence is extended forward, and lengths are kept inside the
   channel's limits.
3. A second Claude pass checks every finalist with the sentences around it: does it misrepresent
   the speaker? does it stand alone? is there a better natural first sentence? Clips that fail
   are extended or dropped. Hooks are never invented: only the speaker's own words are used.
4. One score per clip (weighted criteria, context and small learned adjustments), duplicates of
   the same moment removed, the best N kept.
Without Claude (not logged in, offline) a simpler rule-based finder is used, and clips say so."""
import re

from clipper import llm

CRITERIA = ["hook", "curiosity", "emotion", "education", "entertainment", "story", "surprise",
            "standalone", "relevance", "retention"]
WEIGHTS = {"hook": 1.6, "retention": 1.4, "standalone": 1.2, "curiosity": 1.0, "story": 1.0, "education": 0.9,
           "entertainment": 0.9, "emotion": 0.8, "relevance": 0.8, "surprise": 0.6}
HOOK_TYPES = ["question", "bold_claim", "story", "surprising_fact", "emotional", "funny", "how_to", "hype", "other"]
DANGLING = re.compile(r"^(and|but|or|because|which|that's why|that is why|also|then|plus|anyway|"
                      r"like i said|as i said|it|this|that|they|he|she|those|these|him|her|them)\b", re.I)
CHUNK_WORDS = 5000


def _fmt(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def transcript_lines(tr, i0=0, i1=None, signals=None):
    sents = tr["sentences"][i0:i1]
    multi = tr.get("speakers", 1) > 1
    loud = [t for t, _ in tr.get("loud", [])]
    chat = (signals or {}).get("chat", [])
    lines = []
    for s in sents:
        tags = []
        if multi:
            tags.append(f"S{s['speaker'] + 1}")
        if any(s["s"] - 1 <= t <= s["e"] + 1 for t in loud):
            tags.append("LOUD")
        hot = [n for t, n in chat if s["s"] - 5 <= t <= s["e"] + 10]
        if hot:
            tags.append(f"CHAT x{max(hot):.0f}")
        lines.append(f"[{s['id']}] ({_fmt(s['s'])}{', ' + ', '.join(tags) if tags else ''}) {s['text']}")
    return "\n".join(lines)


PROMPT = """You are the clip editor for a YouTube Shorts channel. Find the moments in this transcript that would make
the strongest standalone YouTube Shorts ({lo}-{hi} seconds; up to {hard} if the moment truly needs it).

Channel: {channel}. Niches: {niches}. Audience: {audience}. Video: "{title}" by {creator}.
{priorities}
Transcript: each line is [sentence number] (time{speaker_note}) text. LOUD = a loudness spike (laughing, shouting).
CHAT xN = viewers' chat went N times faster than usual.
{lines}

Rules:
- A clip is a run of whole sentences: start_id to end_id (inclusive).
- The first sentence must work as an opening with no prior context, ideally a hook within 1-3 seconds:
  a question, a bold claim, a surprising fact, the start of a story, an emotional or funny moment.
- The ending must feel complete (the point lands, the story resolves, the joke pays off).
- Never pick a cut that makes the speaker seem to say something they did not mean. If context is needed, include it.
- Prefer genuine value (insight, story, laughs) over cheap outrage. Controversial does not mean good.
- Don't propose two clips of the same moment.
- Scores are 0-10 and honest; most clips are 5-7, a 9 is rare.
Return ONLY a JSON array (best first, at most {n} items), each:
{{"start_id": int, "end_id": int, "hook": "the opening line, verbatim", "hook_type": one of {hook_types},
 "reason": "why it works, one sentence", "topic": "2-4 words", "keywords": ["up to 4 important words said in the clip"],
 "scores": {{{criteria}}}, "context_complete": 0-10,
 "title": "accurate, curious, under 70 characters", "hook_text": "optional 2-6 word on-screen opener that is accurate, or empty",
 "caption_style": one of ["clean","bold","kinetic","educational","premium"]}}"""


VERIFY = """You are checking Shorts clips before they're published. For each clip you get the two sentences before it,
the clip itself, and the two sentences after it. Judge each one strictly:
- accurate: does the clip, on its own, faithfully represent what the speaker meant? (false if cutting changes the meaning)
- standalone: will a viewer understand it without the surrounding video?
- verdict: "keep", "extend_start" (needs the sentence before), "extend_end" (needs the sentence after), or "reject"
- better_start_id: if a LATER sentence inside the clip would be a stronger natural opening without losing needed
  context or changing meaning, its number; otherwise null.

{clips}

Return ONLY a JSON array: [{{"clip": n, "accurate": bool, "standalone": bool, "verdict": "...",
"better_start_id": int or null, "note": "short reason"}}]"""


def find(tr, channel, video, max_clips=5, signals=None, learned=None, progress=lambda f, d="": None):
    """-> (ranked candidate list, method). Each candidate has start/end seconds and everything the
    later stages need."""
    sents = tr["sentences"]
    if not sents:
        return [], "none"
    want = max(1, int(max_clips))
    method = "claude"
    cands = []
    if llm.available():
        try:
            cands = _ask_claude(tr, channel, video, want, signals, progress)
        except llm.LLMError as e:
            print(f"(clipper: Claude couldn't analyse the transcript, using rules: {e})")
            cands = []
    if not cands:
        method = "rules"
        cands = heuristic(tr, channel)
    cands = [c for c in (finalize(c, tr, channel) for c in cands) if c]
    for c in cands:
        c["score"] = score(c, channel, learned)
    cands = dedupe(sorted(cands, key=lambda c: -c["score"]))
    if method == "claude" and cands:
        progress(0.9, "double-checking context")
        try:
            cands = verify(cands[:want * 2 + 2], tr, channel) + cands[want * 2 + 2:]
        except llm.LLMError as e:
            print(f"(clipper: context check unavailable, using rule checks only: {e})")
    for c in cands:
        c["score"] = score(c, channel, learned)
        c["method"] = method
    cands = dedupe(sorted(cands, key=lambda c: -c["score"]))
    keep = [c for c in cands if c["score"] >= float(channel.get("min_score", 6.0)) and c.get("context_ok", True)]
    if not keep and cands:                   # nothing great: still offer the single best, clearly marked
        best = dict(cands[0], weak=True)
        keep = [best] if best.get("context_ok", True) else []
    for i, c in enumerate(keep[:want]):
        c["rank"] = i + 1
    return keep[:want], method


def _ask_claude(tr, channel, video, want, signals, progress):
    sents = tr["sentences"]
    chunks, start, words = [], 0, 0
    for i, s in enumerate(sents):
        words += len(s["text"].split())
        if words >= CHUNK_WORDS:
            chunks.append((start, i + 1))
            start, words = max(start + 1, i - 15), 0           # overlap so moments on an edge aren't lost
    chunks.append((start, len(sents)))
    pri = channel.get("priorities") or {}
    pri_text = ("Priorities from the owner: " + ", ".join(f"weigh {k} x{v}" for k, v in pri.items()) + ".") if pri else ""
    per = min(12, max(4, int(want * 2.5 / len(chunks)) + 2))
    out = []
    for k, (a, b) in enumerate(chunks):
        progress(k / len(chunks) * 0.85, f"reading the transcript ({k + 1}/{len(chunks)})")
        prompt = PROMPT.format(
            lo=channel["clip_min"], hi=channel["clip_max"], hard=channel["clip_hard_max"], channel=channel["name"],
            niches=", ".join(channel.get("niches") or []), audience=channel.get("audience", "general"),
            title=str(video.get("title") or "")[:150], creator=video.get("creator") or "unknown",
            priorities=pri_text, speaker_note=", speaker" if tr.get("speakers", 1) > 1 else "",
            lines=transcript_lines(tr, a, b, signals), n=per, hook_types=HOOK_TYPES,
            criteria=", ".join(f'"{c}": 0-10' for c in CRITERIA))
        got = llm.ask_json(prompt, timeout=420)
        if isinstance(got, dict):
            got = got.get("clips") or got.get("candidates") or []
        for c in got if isinstance(got, list) else []:
            try:
                c["start_id"], c["end_id"] = int(c["start_id"]), int(c["end_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= c["start_id"] <= c["end_id"] < len(sents):
                out.append(c)
    return out


# ---------- boundaries and context ----------

def finalize(c, tr, channel):
    """Turns sentence numbers into exact times, extends for context, checks length. None = unusable."""
    sents, words = tr["sentences"], tr["words"]
    a, b = int(c["start_id"]), int(c["end_id"])
    lo, hi, hard = float(channel["clip_min"]), float(channel["clip_max"]), float(channel["clip_hard_max"])
    notes = list(c.get("notes", []))

    def length(x, y):
        return sents[y]["e"] - sents[x]["s"]
    steps = 0
    while a > 0 and not c.get("start_locked") and DANGLING.match(sents[a]["text"]) and \
            length(a - 1, b) <= hard and steps < 2:
        a -= 1                                          # "And that's why..." needs what came before
        steps += 1
        notes.append("started earlier for context")
    while b < len(sents) - 1 and not re.search(r"[.!?…]['\")\]]*$", sents[b]["text"].strip()) and \
            length(a, b + 1) <= hard:
        b += 1                                          # don't stop mid-sentence
        notes.append("ended later to finish the sentence")
    while length(a, b) < lo and b < len(sents) - 1 and length(a, b + 1) <= hi:
        b += 1                                          # too short: carry on to the next sentence
    if length(a, b) > hard:
        return None                      # cutting the ending off could change the point: drop it instead
    if length(a, b) < max(5.0, lo * 0.6):
        return None
    w0, w1 = words[sents[a]["i0"]], words[sents[b]["i1"]]
    prev_end = words[sents[a]["i0"] - 1]["e"] if sents[a]["i0"] > 0 else 0.0
    next_start = words[sents[b]["i1"] + 1]["s"] if sents[b]["i1"] + 1 < len(words) else w1["e"] + 1.0
    start = max(prev_end + 0.02, w0["s"] - 0.12, 0.0)
    end = min(next_start - 0.03, w1["e"] + 0.35)
    text = " ".join(s["text"] for s in sents[a:b + 1])
    scores = {k: _num(c.get("scores", {}).get(k), 5.0) for k in CRITERIA}
    ctx = _num(c.get("context_complete"), 6.0)
    if DANGLING.match(sents[a]["text"]):
        ctx = min(ctx, 5.0)
    out = dict(c, start_id=a, end_id=b, start=round(start, 3), end=round(end, 3), duration=round(end - start, 3),
               transcript=text, scores=scores, context_complete=ctx, notes=notes,
               hook=(c.get("hook") or sents[a]["text"])[:300], hook_type=c.get("hook_type") if c.get("hook_type") in HOOK_TYPES else "other",
               keywords=[str(k) for k in (c.get("keywords") or [])][:6], speakers=sorted({s["speaker"] for s in sents[a:b + 1]}))
    out["context_ok"] = ctx >= 5.0
    return out


def _num(v, default):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return default


def score(c, channel, learned=None):
    pri = channel.get("priorities") or {}
    w = {k: WEIGHTS[k] * float(pri.get(k, 1.0)) for k in CRITERIA}
    base = sum(c["scores"][k] * w[k] for k in CRITERIA) / sum(w.values())
    total = base * 0.85 + c.get("context_complete", 6.0) * 0.15
    bonus = 0.0
    for key, table in (learned or {}).items():          # small, capped nudges from real results
        if key == "hook_type":
            bonus += float(table.get(c.get("hook_type"), 0.0))
        elif key == "duration":
            bucket = duration_bucket(c["duration"])
            bonus += float(table.get(bucket, 0.0))
        elif key == "topic":
            bonus += float(table.get(str(c.get("topic", "")).lower(), 0.0))
    total += max(-0.75, min(0.75, bonus))
    if not c.get("context_ok", True):
        total = min(total, 4.0)
    return round(max(0.0, min(10.0, total)), 2)


def duration_bucket(d):
    return "under_30" if d < 30 else "30_45" if d < 45 else "45_60" if d <= 60 else "over_60"


def dedupe(cands, overlap=0.3, similar=0.5):
    kept = []
    for c in cands:
        dup = False
        for k in kept:
            inter = max(0.0, min(c["end"], k["end"]) - max(c["start"], k["start"]))
            union = max(c["end"], k["end"]) - min(c["start"], k["start"])
            if union and inter / union > overlap:
                dup = True
                break
            a, b = set(c["transcript"].lower().split()), set(k["transcript"].lower().split())
            if a and b and len(a & b) / len(a | b) > similar:
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept


def verify(cands, tr, channel):
    """Claude's context check on the finalists (see VERIFY). Returns the clips that pass, adjusted."""
    sents = tr["sentences"]
    blocks = []
    for n, c in enumerate(cands):
        a, b = c["start_id"], c["end_id"]
        before = " ".join(s["text"] for s in sents[max(0, a - 2):a]) or "(start of video)"
        after = " ".join(s["text"] for s in sents[b + 1:b + 3]) or "(end of video)"
        inside = " ".join(f"[{s['id']}] {s['text']}" for s in sents[a:b + 1])
        blocks.append(f"CLIP {n}\nBEFORE: {before}\nCLIP: {inside}\nAFTER: {after}")
    got = llm.ask_json(VERIFY.format(clips="\n\n".join(blocks)), timeout=300)
    verdicts = {int(v.get("clip", -1)): v for v in got if isinstance(v, dict)} if isinstance(got, list) else {}
    out = []
    for n, c in enumerate(cands):
        v = verdicts.get(n)
        if not v:
            out.append(c)
            continue
        c = dict(c, check=v.get("note", ""))
        if v.get("accurate") is False or v.get("verdict") == "reject":
            continue
        changed = None
        if v.get("verdict") == "extend_start" and c["start_id"] > 0:
            changed = dict(c, start_id=c["start_id"] - 1)
        elif v.get("verdict") == "extend_end" and c["end_id"] < len(sents) - 1:
            changed = dict(c, end_id=c["end_id"] + 1)
        bs = v.get("better_start_id")
        if changed is None and isinstance(bs, int) and c["start_id"] < bs <= c["end_id"]:
            trial = dict(c, start_id=bs, start_locked=True, notes=c.get("notes", []) + ["opens on a stronger line"])
            trial["hook"] = sents[bs]["text"]
            changed = trial
        if changed:
            fixed = finalize(changed, tr, channel)
            if fixed and fixed["duration"] >= float(channel["clip_min"]) * 0.8:
                c = dict(fixed, check=c.get("check", ""))
            elif v.get("verdict", "").startswith("extend"):
                continue                                  # needed more context but can't fit: drop it
        if v.get("standalone") is False:
            c["context_complete"] = min(c.get("context_complete", 6), 4.5)
            c["context_ok"] = False
        c["verified"] = True
        out.append(c)
    return out


# ---------- rule-based finder (no Claude) ----------

HOOK_WORDS = re.compile(r"\b(secret|truth|mistake|never|nobody|everyone|most people|worst|best|biggest|crazy|"
                        r"insane|wait|here's the thing|the reason|what if|why|how to|changed my life|"
                        r"\d+ (?:dollars|years|percent|million|thousand))\b", re.I)
STORY_WORDS = re.compile(r"\b(i was|years ago|one day|when i|i remember|true story|so basically)\b", re.I)


def heuristic(tr, channel):
    sents = tr["sentences"]
    loud = [t for t, _ in tr.get("loud", [])]
    lo, hi = float(channel["clip_min"]), float(channel["clip_max"])
    out = []
    for a in range(len(sents)):
        if DANGLING.match(sents[a]["text"]):
            continue
        b = a
        while b + 1 < len(sents) and sents[b + 1]["e"] - sents[a]["s"] <= hi:
            b += 1
            if sents[b]["e"] - sents[a]["s"] >= (lo + hi) / 2 and re.search(r"[.!?]$", sents[b]["text"]):
                break
        if sents[b]["e"] - sents[a]["s"] < lo:
            continue
        first, body = sents[a]["text"], " ".join(s["text"] for s in sents[a:b + 1])
        hook = 4.0 + 2.5 * bool(sents[a]["question"]) + 2.0 * bool(HOOK_WORDS.search(first)) + 1.0 * (len(first.split()) <= 14)
        story = 4.0 + 3.0 * bool(STORY_WORDS.search(body))
        energy = 4.0 + 2.0 * sum(1 for t in loud if sents[a]["s"] <= t <= sents[b]["e"])
        qa = 2.0 if any(s["question"] for s in sents[a:b]) and len({s["speaker"] for s in sents[a:b + 1]}) > 1 else 0.0
        scores = {"hook": min(10, hook), "curiosity": min(10, 4 + 2 * bool(HOOK_WORDS.search(body)) + qa),
                  "emotion": min(10, energy), "education": 5.0, "entertainment": min(10, energy),
                  "story": min(10, story), "surprise": 5.0, "standalone": 6.0, "relevance": 5.0,
                  "retention": min(10, (hook + story) / 2)}
        out.append({"start_id": a, "end_id": b, "hook": first, "hook_type": "question" if sents[a]["question"] else "other",
                    "reason": "rule-based pick (Claude wasn't available)", "topic": "", "scores": scores,
                    "context_complete": 6.0, "title": first[:68], "keywords": []})
    return out
