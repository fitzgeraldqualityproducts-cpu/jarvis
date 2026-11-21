"""Finding the moments worth a Short, scoring them 0-100, keeping their meaning intact, and ranking them.

1. Claude reads the transcript (numbered sentences with times, speakers, loud moments and chat
   spikes) in as few calls as possible and proposes candidates. Each one says what kind of moment it
   is (funny, reaction, story, gameplay...), where its setup starts and where its payoff lands, and
   rates it 0-10 on 12 things:
       hook, entertainment, emotion, curiosity, uniqueness, story, replay, shareability,
       clarity (makes sense on its own), pacing, relevance, payoff
   Clips are given as sentence numbers, so they always start and end on whole sentences.
2. Boundaries are snapped to the exact word timings. A clip that starts mid-thought ("And so he...")
   is extended back to its setup, one that stops mid-sentence or before its payoff is extended
   forward. Lengths follow the content: a great 23-second moment stays 23 seconds, a 75-second story
   is kept whole (up to the hard limit); nothing is padded to fill a template.
3. Things that can be measured are measured, not guessed: words per second, dead air, how soon the
   first words come, how repetitive it is. Clips that are mostly silence, slow to start or repeat
   themselves are marked down or rejected, with the reason recorded.
4. A second Claude pass checks every finalist with the sentences around it (does it misrepresent
   the speaker? does it stand alone? is there a stronger natural first line?) and, in the same call,
   writes its titles and descriptions. Hooks are never invented: only the speaker's own words are
   used, and an earlier opening is only moved later inside the clip if no needed context is lost.
5. One 0-100 "viral potential" score per clip: a weighted mix (hook and payoff count most), the
   context check, the measurements, and small capped nudges learned from real results. It estimates
   engagement from what's observable in the clip; it is not a prediction that anything will go viral.
6. Duplicates and near-duplicates are removed, and the final list is diversified so five clips
   aren't five takes on the same moment.
Without Claude (not logged in, offline) a simpler rule-based finder is used, and clips say so."""
import re

from clipper import llm

CRITERIA = ["hook", "entertainment", "emotion", "curiosity", "uniqueness", "story", "replay", "shareability",
            "clarity", "pacing", "relevance", "payoff"]
WEIGHTS = {"hook": 1.8, "payoff": 1.4, "clarity": 1.3, "entertainment": 1.2, "curiosity": 1.1, "pacing": 1.0,
           "emotion": 0.9, "story": 0.9, "shareability": 0.9, "replay": 0.8, "uniqueness": 0.7, "relevance": 0.7}
# older candidates (and older tests) used these names: map them onto the new ones
OLD_NAMES = {"standalone": "clarity", "retention": "replay", "surprise": "uniqueness", "education": "relevance"}
HOOK_TYPES = ["question", "bold_claim", "story", "surprising_fact", "emotional", "funny", "how_to", "hype",
              "reaction", "conflict", "suspense", "other"]
CATEGORIES = ["funny", "reaction", "controversial", "opinion", "argument", "emotional", "revelation", "gameplay",
              "achievement", "fail", "story", "educational", "high_energy", "one_liner", "suspense", "relatable", "other"]
LENGTHS = {"auto": (12, 60, 90), "15_30": (15, 30, 38), "30_45": (30, 45, 55), "45_60": (45, 60, 70),
           "60_90": (60, 90, 120), "90_180": (90, 180, 180)}
DANGLING = re.compile(r"^(and|but|or|because|which|that's why|that is why|also|then|plus|anyway|"
                      r"like i said|as i said|it|this|that|they|he|she|those|these|him|her|them)\b", re.I)
FILLER = re.compile(r"^(um+|uh+|erm+|er|ah+|hmm+|mm+)[.,!?]*$", re.I)
CHUNK_WORDS = 6000
SORTS = {   # how the dashboard's filters order candidates
    "viral": lambda c: c.get("score", 0),
    "funny": lambda c: c["scores"].get("entertainment", 0) + 3 * (c.get("category") in ("funny", "one_liner", "fail")),
    "entertaining": lambda c: c["scores"].get("entertainment", 0) + c["scores"].get("replay", 0) * 0.3,
    "emotional": lambda c: c["scores"].get("emotion", 0) + 2 * (c.get("category") == "emotional"),
    "interesting": lambda c: c["scores"].get("curiosity", 0) + c["scores"].get("uniqueness", 0) * 0.5,
    "story": lambda c: c["scores"].get("story", 0) + c["scores"].get("payoff", 0) * 0.3,
    "shortest": lambda c: -c.get("duration", 0),
    "longest": lambda c: c.get("duration", 0),
}


def lengths(channel, mode=None):
    """(shortest, preferred longest, hard longest) seconds for a channel (or a per-job choice):
    auto, 15_30, 30_45, 45_60, 60_90, or custom (the channel's own clip_min/clip_max/clip_hard_max)."""
    mode = mode or channel.get("length_mode") or "auto"
    if isinstance(mode, (list, tuple)) and len(mode) in (2, 3):
        lo, hi = float(mode[0]), float(mode[1])
        hard = float(mode[2]) if len(mode) == 3 else min(180.0, hi * 1.15)
        return lo, hi, max(hi, min(180.0, hard))
    if mode in LENGTHS:
        return tuple(float(x) for x in LENGTHS[mode])
    return float(channel["clip_min"]), float(channel["clip_max"]), float(channel["clip_hard_max"])


def with_lengths(channel, mode=None):
    lo, hi, hard = lengths(channel, mode)
    return dict(channel, clip_min=lo, clip_max=hi, clip_hard_max=max(hi, hard))


def min_score(channel):
    """The channel's cut-off on the 0-100 scale (older settings were out of 10)."""
    v = float(channel.get("min_score", 60))
    return v * 10 if v <= 10 else v


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
        if s.get("pause_after", 0) >= 2.0:
            tags.append(f"PAUSE {s['pause_after']:.0f}s")
        hot = [n for t, n in chat if s["s"] - 5 <= t <= s["e"] + 10]
        if hot:
            tags.append(f"CHAT x{max(hot):.0f}")
        lines.append(f"[{s['id']}] ({_fmt(s['s'])}{', ' + ', '.join(tags) if tags else ''}) {s['text']}")
    return "\n".join(lines)


PROMPT = """You are a senior short-form editor choosing YouTube Shorts from a video. Find the moments people would
actually want to watch: funny moments, unexpected reactions, strong opinions, heated arguments, emotional moments,
surprising revelations, great gameplay, achievements, fails, stories, clear explanations, high energy, one-liners,
suspense, relatable situations, moments that make you need to see what happens next.

Length: let the content decide. Typical {lo}-{hi} seconds, up to {hard} only if the moment truly needs it.
A great 20-second moment stays short; never pad. A great story is kept whole.
Channel: {channel}. Niches: {niches}. Audience: {audience}. Video: "{title}" by {creator}.
{priorities}
{focus}
Transcript: [sentence number] (time{speaker_note}) text. LOUD = loudness spike (laughing, shouting).
PAUSE Ns = silence after the line. CHAT xN = viewers' chat went N times faster than usual.
{lines}

Rules:
- A clip is a run of whole sentences start_id..end_id (inclusive). Include the setup a joke, claim or story
  needs (look at what comes BEFORE) and run through the payoff (look at what comes AFTER). Never cut a punchline,
  the context behind a statement, or the end of a story.
- The opening should grab attention within 1-3 seconds (question, bold claim, conflict, surprise, strong reaction,
  the start of a story) without misleading. The ending must land.
- Skip dead air, small talk, repetition, moments that take too long to get going, and clips with no payoff.
- Never pick a cut that makes someone seem to say what they did not mean. Genuine value beats cheap outrage.
- No two clips of the same moment. Quality over quantity: return fewer if few are good.
- Scores 0-10, honest and spread out: most real clips are 4-7, a 9 is rare.
Return ONLY a JSON array (best first, at most {n}), each:
{{"start_id": int, "end_id": int, "setup_id": first sentence needed for context, "payoff_id": sentence where it lands,
 "category": one of {categories}, "hook": "opening line, verbatim", "hook_type": one of {hook_types},
 "reason": "why it works, one short sentence", "topic": "2-4 words",
 "strongest": "the single strongest line in the clip, verbatim", "audience": "who it's for, 2-5 words",
 "weakness": "its biggest weakness in a few words, or empty if none",
 "highlight": ["up to 6 words said in the clip worth highlighting: punchline, names, emotional or surprising words"],
 "scores": {{{criteria}}}, "context_complete": 0-10, "title": "accurate, curious, under 70 characters"}}"""


REVIEW = """You are checking YouTube Shorts clips before they're published, then writing their metadata.
For each clip you get the two sentences before it, the clip itself, and the two sentences after it.

Check, strictly:
- accurate: on its own, does the clip faithfully represent what the speaker meant? (false if the cut changes meaning)
- standalone: will a viewer understand it without the rest of the video?
- verdict: "keep", "extend_start" (needs the sentence before), "extend_end" (needs the sentence after), or "reject"
- better_start_id: if a LATER sentence inside the clip is a stronger natural opening and nothing needed is lost
  and the meaning doesn't change, its number; otherwise null. Never suggest a misleading opening.
- hook: 0-10, how strongly the first 3 seconds (of the start you recommend) grab attention.

Then metadata, for the channel "{channel}" (niches: {niches}; voice: {voice}; audience: {audience}), from what is
actually said in the clip. Never invent events or claims. No clickbait the clip doesn't deliver, no ALL CAPS,
at most one emoji, no #shorts (added automatically):
- titles: 5 options, under 70 characters, curious and accurate, natural, relevant keywords
- descriptions: 3 options, 1-2 short sentences each
- hashtags: 3-5 relevant; keywords: 6-10 search terms
- hook_texts: 2 short on-screen openers (2-6 words) that are accurate to the clip (or [] if none fits)
- pinned: 2 pinned comment ideas inviting discussion of the clip's real topic
- caption_fixes: words in the CLIP the speech-to-text clearly misheard or spelled inconsistently (names, game
  terms, slang), as {{"heard": "meant"}}, single words only, for the captions; {{}} if none. Never reword speech.

{clips}

Return ONLY a JSON array: [{{"clip": n, "accurate": bool, "standalone": bool, "verdict": "...", "better_start_id": int or null,
"hook": 0-10, "note": "short reason", "titles": [...], "descriptions": [...], "hashtags": [...], "keywords": [...],
"hook_texts": [...], "pinned": [...], "caption_fixes": {{}}}}]"""


def find(tr, channel, video, max_clips=5, signals=None, learned=None, progress=lambda f, d="": None, report=None):
    """-> (ranked candidates, method). Each candidate has start/end seconds, its 0-100 score with the
    parts it's made of, and everything the later stages need. `report` (a dict) gets the rejected
    candidates and why."""
    report = report if report is not None else {}
    report.setdefault("rejected", [])
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
            report["llm_error"] = str(e)[:300]
            cands = []
    if not cands:
        method = "rules"
        cands = heuristic(tr, channel)
    final = []
    for c in cands:
        f = finalize(c, tr, channel)
        if f is None:
            report["rejected"].append({"title": c.get("title"), "start_id": c.get("start_id"), "why": "too long to keep whole"})
        elif f.get("reject"):
            report["rejected"].append({"title": f.get("title"), "start": f["start"], "end": f["end"], "why": f["reject"]})
        else:
            final.append(f)
    for c in final:
        c["score"] = score(c, channel, learned)
    cands = dedupe(sorted(final, key=lambda c: -c["score"]))
    if method == "claude" and cands:
        progress(0.9, "double-checking context and writing titles")
        top = cands[:want + 2]
        try:
            cands = verify(top, tr, channel, video, report) + cands[want + 2:]
        except llm.LLMError as e:
            print(f"(clipper: context check unavailable, using rule checks only: {e})")
    for c in cands:
        c["score"] = score(c, channel, learned)
        c["method"] = method
    cands = diversify(dedupe(sorted(cands, key=lambda c: -c["score"])))
    cut = min_score(channel)
    keep = []
    for c in cands:
        if c["score"] >= cut and c.get("context_ok", True):
            keep.append(c)
        else:
            report["rejected"].append({"title": c.get("title"), "start": c["start"], "end": c["end"], "score": c["score"],
                                       "why": "needs context it doesn't have" if not c.get("context_ok", True)
                                       else f"scored {c['score']:.0f}, under the {cut:.0f} cut-off"})
    if not keep and cands:                   # nothing great: still offer the single best, clearly marked
        best = dict(cands[0], weak=True)
        keep = [best] if best.get("context_ok", True) else []
    for i, c in enumerate(keep):
        c["rank"] = i + 1
    return keep[:max(want, 1)], method


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
    if channel.get("lessons"):                      # learned from the owner's own edits and rejections
        pri_text = (pri_text + "\n" + "\n".join(channel["lessons"])).strip()
    per = min(12, max(4, int(want * 2 / len(chunks)) + 2))
    out = []
    for k, (a, b) in enumerate(chunks):
        progress(k / len(chunks) * 0.85, f"reading the transcript ({k + 1}/{len(chunks)})")
        prompt = PROMPT.format(
            lo=int(channel["clip_min"]), hi=int(channel["clip_max"]), hard=int(channel["clip_hard_max"]), channel=channel["name"],
            niches=", ".join(channel.get("niches") or []), audience=channel.get("audience", "general"),
            title=str(video.get("title") or "")[:150], creator=video.get("creator") or "unknown",
            priorities=pri_text, focus=channel.get("preset_focus") or "", speaker_note=", speaker" if tr.get("speakers", 1) > 1 else "",
            lines=transcript_lines(tr, a, b, signals), n=per, hook_types=HOOK_TYPES, categories=CATEGORIES,
            criteria=", ".join(f'"{c}": 0-10' for c in CRITERIA))
        got = llm.ask_json(prompt, timeout=420)
        if isinstance(got, dict):
            got = got.get("clips") or got.get("candidates") or []
        for c in got if isinstance(got, list) else []:
            if not isinstance(c, dict):
                continue
            try:
                c["start_id"], c["end_id"] = int(c["start_id"]), int(c["end_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= c["start_id"] <= c["end_id"] < len(sents):
                out.append(c)
    return out


# ---------- boundaries, context and measurements ----------

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def finalize(c, tr, channel):
    """Turns sentence numbers into exact times, extends for context and payoff, measures the clip.
    None = can't be kept whole within the hard limit. A dict with "reject" = measured as unusable."""
    sents, words = tr["sentences"], tr["words"]
    a, b = int(c["start_id"]), int(c["end_id"])
    lo, hi, hard = float(channel["clip_min"]), float(channel["clip_max"]), float(channel["clip_hard_max"])
    notes = list(c.get("notes", []))

    def length(x, y):
        return sents[y]["e"] - sents[x]["s"]
    setup, payoff = _int(c.get("setup_id")), _int(c.get("payoff_id"))
    if setup is not None and 0 <= setup < a and a - setup <= 4 and not c.get("start_locked"):
        a = setup                                       # the setup the moment needs
        notes.append("starts at the setup")
    if payoff is not None and not c.get("end_locked") and b < payoff < len(sents) and payoff - b <= 4:
        b = payoff                                      # through the payoff
        notes.append("runs through the payoff")
    steps = 0
    while a > 0 and not c.get("start_locked") and DANGLING.match(sents[a]["text"]) and \
            length(a - 1, b) <= hard and steps < 2:
        a -= 1                                          # "And that's why..." needs what came before
        steps += 1
        notes.append("started earlier for context")
    while b < len(sents) - 1 and not c.get("end_locked") and not re.search(r"[.!?…]['\")\]]*$", sents[b]["text"].strip()) and \
            length(a, b + 1) <= hard:
        b += 1                                          # don't stop mid-sentence
        notes.append("ended later to finish the sentence")
    while length(a, b) < lo and b < len(sents) - 1 and length(a, b + 1) <= hi and not c.get("end_locked"):
        b += 1                                          # under the shortest length: carry on a sentence
    if length(a, b) > hard:
        return None                      # cutting the ending off could change the point: drop it instead
    if length(a, b) < max(3.0 if c.get("end_locked") else 5.0, lo * 0.6):
        return None
    w0, w1 = words[sents[a]["i0"]], words[sents[b]["i1"]]
    prev_end = words[sents[a]["i0"] - 1]["e"] if sents[a]["i0"] > 0 else 0.0
    next_start = words[sents[b]["i1"] + 1]["s"] if sents[b]["i1"] + 1 < len(words) else w1["e"] + 1.0
    start = max(prev_end + 0.02, w0["s"] - 0.12, 0.0)
    end = min(next_start - 0.03, w1["e"] + 0.35)
    text = " ".join(s["text"] for s in sents[a:b + 1])
    raw = dict(c.get("scores") or {})
    for old, new in OLD_NAMES.items():
        if old in raw and new not in raw:
            raw[new] = raw[old]
    scores = {k: _num(raw.get(k), 5.0) for k in CRITERIA}
    ctx = _num(c.get("context_complete"), 6.0)
    if DANGLING.match(sents[a]["text"]):
        ctx = min(ctx, 5.0)
    m = measure(words, sents, a, b, start, end, tr.get("loud", []))
    out = dict(c, start_id=a, end_id=b, start=round(start, 3), end=round(end, 3), duration=round(end - start, 3),
               transcript=text, scores=scores, context_complete=ctx, notes=notes, measures=m,
               hook=(c.get("hook") or sents[a]["text"])[:300],
               hook_type=c.get("hook_type") if c.get("hook_type") in HOOK_TYPES else "other",
               category=c.get("category") if c.get("category") in CATEGORIES else "other",
               highlight=[str(k) for k in (c.get("highlight") or c.get("keywords") or [])][:8],
               keywords=[str(k) for k in (c.get("keywords") or [])][:10],
               strongest=str(c.get("strongest") or "")[:300], audience=str(c.get("audience") or "")[:60],
               weakness=str(c.get("weakness") or "")[:160],
               speakers=sorted({s["speaker"] for s in sents[a:b + 1]}))
    out.pop("reject", None)
    out["context_ok"] = ctx >= 5.0
    out["strongest_at"] = _strongest_at(out["strongest"], sents, a, b, start)
    out["weaknesses"] = weaknesses(out, m)
    lively = m["loud"] > 0 or out["category"] in ("gameplay", "reaction", "high_energy", "achievement", "fail")
    if m["words_per_sec"] < 0.7 and not lively:
        out["reject"] = "mostly silence"
    elif m["dead_air"] > 0.45 and not channel.get("jump_cuts", True):
        out["reject"] = "too much dead air"
    elif m["unique_ratio"] < 0.3 and m["words"] > 40:
        out["reject"] = "repeats itself"
    return out


def _toks(text):
    return [t for t in re.sub(r"[^a-z0-9' ]", " ", str(text).lower()).split() if t]


def _strongest_at(line, sents, a, b, start):
    """Seconds into the clip where its strongest line is said (the sentence that best matches it)."""
    want = set(_toks(line))
    if not want:
        return None
    best, at = 0.0, None
    for s in sents[a:b + 1]:
        have = set(_toks(s["text"]))
        share = len(want & have) / len(want) if have else 0.0
        if share > best:
            best, at = share, s["s"]
    return round(max(0.0, at - start), 2) if at is not None and best >= 0.5 else None


def weaknesses(c, m):
    """Honest weak spots: the reviewer's own note plus what was measured."""
    out = []
    if c.get("weakness"):
        out.append(str(c["weakness"])[:160])
    if m.get("first_word", 0) > 1.0:
        out.append(f"first word only after {m['first_word']:.1f}s")
    elif m.get("words_first_3s", 9) < 4 and not m.get("loud"):
        out.append("slow first 3 seconds")
    if m.get("dead_air", 0) > 0.2:
        out.append(f"{round(m['dead_air'] * 100)}% pauses (jump cuts tighten these)")
    if m.get("fillers", 0) >= 4:
        out.append(f"{m['fillers']} filler words")
    if c.get("context_complete", 6) < 6:
        out.append("may need more context")
    if not re.search(r"[.!?…]['\")\]]*$", str(c.get("transcript", "")).strip()):
        out.append("ending may not land")
    return out[:5]


def measure(words, sents, a, b, start, end, loud=()):
    """What can be measured about a clip (no guessing): speech rate, dead air, time to first word,
    repetition, loud moments."""
    ws = words[sents[a]["i0"]:sents[b]["i1"] + 1]
    dur = max(0.1, end - start)
    gaps = [n["s"] - w["e"] for w, n in zip(ws, ws[1:])]
    dead = sum(g for g in gaps if g > 0.7)
    toks = [re.sub(r"[^a-z0-9']", "", w["w"].lower()) for w in ws]
    toks = [t for t in toks if t]
    first3 = sum(1 for w in ws if w["s"] - start <= 3.0)
    return {"words": len(ws), "words_per_sec": round(len(ws) / dur, 2), "dead_air": round(dead / dur, 3),
            "longest_pause": round(max(gaps or [0.0]), 2), "first_word": round(max(0.0, ws[0]["s"] - start), 2) if ws else 0.0,
            "words_first_3s": first3, "unique_ratio": round(len(set(toks)) / max(1, len(toks)), 3),
            "fillers": sum(1 for w in ws if FILLER.match(w["w"].strip())),
            "loud": sum(1 for t, _ in loud if start <= t <= end)}


def _num(v, default):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return default


def score(c, channel, learned=None):
    """0-100 viral-potential estimate. Also stores the parts on the candidate: c["parts"]."""
    pri = {OLD_NAMES.get(k, k): v for k, v in (channel.get("priorities") or {}).items()}
    if "education" in (channel.get("priorities") or {}):
        pri["clarity"] = (channel.get("priorities") or {})["education"]      # clear explanations
    pw = channel.get("preset_weights") or {}
    w = {k: WEIGHTS[k] * float(pri.get(k, 1.0)) * float(pw.get(k, 1.0)) for k in CRITERIA}
    raw = dict(c.get("scores") or {})
    for old, new in OLD_NAMES.items():
        if old in raw and new not in raw:
            raw[new] = raw[old]
    c["scores"] = {k: _num(raw.get(k), 5.0) for k in CRITERIA}       # older saved candidates use other names
    base = sum(c["scores"][k] * w[k] for k in CRITERIA) / sum(w.values())
    total = (base * 0.85 + c.get("context_complete", 6.0) * 0.15) * 10
    m = c.get("measures") or {}
    adj = 0.0
    if m:
        if m.get("first_word", 0) > 1.0:
            adj -= min(6.0, (m["first_word"] - 1.0) * 3)             # slow to start
        if m.get("words_first_3s", 9) < 4 and not m.get("loud"):
            adj -= 3.0                                                # nothing much in the first 3 seconds
        if m.get("dead_air", 0) > 0.2 and not channel.get("jump_cuts", True):
            adj -= min(8.0, (m["dead_air"] - 0.2) * 30)
        if m.get("unique_ratio", 1) < 0.4:
            adj -= 4.0
        if m.get("loud"):
            adj += min(3.0, 1.0 * m["loud"])                         # laughing/shouting: real energy
    preset = max(-6.0, min(6.0, float((channel.get("category_bonus") or {}).get(c.get("category"), 0.0))))
    adj += preset                                                     # the preset's kind of moment
    bonus = 0.0
    for key, table in (learned or {}).items():          # small, capped nudges from real results
        if not isinstance(table, dict):
            continue
        if key == "hook_type":
            bonus += float(table.get(c.get("hook_type"), 0.0))
        elif key == "duration":
            bonus += float(table.get(duration_bucket(c.get("duration", 0)), 0.0))
        elif key == "topic":
            bonus += float(table.get(str(c.get("topic", "")).lower(), 0.0))
        elif key == "category":
            bonus += float(table.get(c.get("category"), 0.0))
    learn = max(-7.5, min(7.5, bonus * 10))
    total += adj + learn
    if not c.get("context_ok", True):
        total = min(total, 40.0)
    total = round(max(0.0, min(100.0, total)), 1)
    c["parts"] = {"hook": round(c["scores"]["hook"] * 10), "entertainment": round(c["scores"]["entertainment"] * 10),
                  "context": round(c.get("context_complete", 6.0) * 10), "content": round(base * 10, 1),
                  "measured": round(adj - preset, 1), "preset": round(preset, 1), "learned": round(learn, 1)}
    return total


def duration_bucket(d):
    return "under_30" if d < 30 else "30_45" if d < 45 else "45_60" if d <= 60 else "over_60"


def overlap(c, k):
    inter = max(0.0, min(c["end"], k["end"]) - max(c["start"], k["start"]))
    union = max(c["end"], k["end"]) - min(c["start"], k["start"])
    return inter / union if union else 0.0


def dedupe(cands, max_overlap=0.3, similar=0.5):
    kept = []
    for c in cands:
        dup = None
        for k in kept:
            if overlap(c, k) > max_overlap:
                dup = k
                break
            a, b = set(c["transcript"].lower().split()), set(k["transcript"].lower().split())
            if a and b and len(a & b) / len(a | b) > similar:
                dup = k
                break
        if dup is None:
            kept.append(c)
        else:
            dup.setdefault("similar", []).append({"start": c["start"], "end": c["end"], "title": c.get("title")})
    return kept


def diversify(cands, penalty=6.0):
    """Reorders so the top clips cover different kinds of moments and topics (a clip loses a few
    points for each better clip already of the same category or topic). Scores are not changed."""
    left, out = list(cands), []
    while left:
        def adjusted(c):
            same = sum(1 for o in out if o.get("category") == c.get("category") and c.get("category") != "other")
            topic = str(c.get("topic") or "").lower()
            same_topic = sum(1 for o in out if topic and str(o.get("topic") or "").lower() == topic)
            return c["score"] - penalty * same - penalty * 1.5 * same_topic
        best = max(left, key=adjusted)
        left.remove(best)
        out.append(best)
    return out


def verify(cands, tr, channel, video=None, report=None):
    """Claude's context check on the finalists (see REVIEW), which also writes each clip's titles and
    descriptions. Returns the clips that pass, adjusted."""
    sents = tr["sentences"]
    blocks = []
    for n, c in enumerate(cands):
        a, b = c["start_id"], c["end_id"]
        before = " ".join(s["text"] for s in sents[max(0, a - 2):a]) or "(start of video)"
        after = " ".join(s["text"] for s in sents[b + 1:b + 3]) or "(end of video)"
        inside = " ".join(f"[{s['id']}] {s['text']}" for s in sents[a:b + 1])
        blocks.append(f"CLIP {n} ({c['duration']:.0f}s, {c.get('category', 'other')})\nBEFORE: {before}\nCLIP: {inside}\nAFTER: {after}")
    got = llm.ask_json(REVIEW.format(
        clips="\n\n".join(blocks), channel=channel.get("name", ""), niches=", ".join(channel.get("niches") or []),
        voice=(channel.get("brand") or {}).get("voice", ""), audience=channel.get("audience", "general")), timeout=360)
    verdicts = {}
    for v in got if isinstance(got, list) else []:
        if isinstance(v, dict) and _int(v.get("clip")) is not None:
            verdicts[_int(v["clip"])] = v
    out = []
    for n, c in enumerate(cands):
        v = verdicts.get(n)
        if not v:
            out.append(c)
            continue
        meta = {k: v.get(k) for k in ("titles", "descriptions", "hashtags", "keywords", "hook_texts", "pinned") if v.get(k)}
        c = dict(c, check=str(v.get("note", ""))[:300], meta=meta)
        if v.get("accurate") is False or v.get("verdict") == "reject":
            if report is not None:
                report.setdefault("rejected", []).append({"title": c.get("title"), "start": c["start"], "end": c["end"],
                                                          "why": "context check: " + (c["check"] or "rejected")})
            continue
        changed = None
        if v.get("verdict") == "extend_start" and c["start_id"] > 0:
            changed = dict(c, start_id=c["start_id"] - 1, setup_id=None)
        elif v.get("verdict") == "extend_end" and c["end_id"] < len(sents) - 1:
            changed = dict(c, end_id=c["end_id"] + 1, payoff_id=None)
        bs = _int(v.get("better_start_id"))
        if changed is None and bs is not None and c["start_id"] < bs <= c["end_id"] and channel.get("hook_optimization", True):
            trial = dict(c, start_id=bs, start_locked=True, setup_id=None,
                         notes=c.get("notes", []) + ["opens on a stronger line"])
            trial["hook"] = sents[bs]["text"]
            changed = trial
        if changed:
            fixed = finalize(changed, tr, channel)
            if fixed and not fixed.get("reject") and fixed["duration"] >= float(channel["clip_min"]) * 0.8:
                c = dict(fixed, check=c.get("check", ""), meta=meta)
            elif v.get("verdict", "").startswith("extend"):
                if report is not None:
                    report.setdefault("rejected", []).append({"title": c.get("title"), "start": c["start"], "end": c["end"],
                                                              "why": "needs more context than fits"})
                continue
        if _int(v.get("hook")) is not None:
            # the reviewer saw the clip with its surroundings: average its hook rating in
            c["scores"] = dict(c["scores"], hook=round((c["scores"]["hook"] + _num(v["hook"], c["scores"]["hook"])) / 2, 2))
        if v.get("standalone") is False:
            c["context_complete"] = min(c.get("context_complete", 6), 4.5)
            c["context_ok"] = False
        from clipper import caption_fix
        fixes = caption_fix.clean_fixes(v.get("caption_fixes"), c.get("transcript", ""))
        if fixes:
            c["caption_fixes"] = fixes
        c["verified"] = True
        out.append(c)
    return out


def sort_by(cands, key="viral"):
    return sorted(cands, key=lambda c: -SORTS.get(key, SORTS["viral"])(c))


# ---------- rule-based finder (no Claude) ----------

HOOK_WORDS = re.compile(r"\b(secret|truth|mistake|never|nobody|everyone|most people|worst|best|biggest|crazy|"
                        r"insane|wait|here's the thing|the reason|what if|why|how to|changed my life|no way|"
                        r"\d+ (?:dollars|years|percent|million|thousand))\b", re.I)
STORY_WORDS = re.compile(r"\b(i was|years ago|one day|when i|i remember|true story|so basically)\b", re.I)
FUNNY_WORDS = re.compile(r"\b(haha+|lol|lmao|bro|dude|what the|oh my god|no way|i can't)\b", re.I)


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
        funny = 4.0 + 2.5 * bool(FUNNY_WORDS.search(body)) + 0.5 * energy / 4
        qa = 2.0 if any(s["question"] for s in sents[a:b]) and len({s["speaker"] for s in sents[a:b + 1]}) > 1 else 0.0
        scores = {"hook": min(10, hook), "curiosity": min(10, 4 + 2 * bool(HOOK_WORDS.search(body)) + qa),
                  "emotion": min(10, energy), "entertainment": min(10, max(energy, funny)), "story": min(10, story),
                  "uniqueness": 5.0, "replay": min(10, (hook + story) / 2), "shareability": 5.0, "clarity": 6.0,
                  "pacing": 5.0, "relevance": 5.0, "payoff": 5.0 + bool(re.search(r"[.!?]$", sents[b]["text"]))}
        category = "story" if STORY_WORDS.search(body) else "funny" if FUNNY_WORDS.search(body) else \
            "high_energy" if energy > 6 else "opinion" if HOOK_WORDS.search(first) else "other"
        out.append({"start_id": a, "end_id": b, "hook": first, "hook_type": "question" if sents[a]["question"] else "other",
                    "reason": "rule-based pick (Claude wasn't available)", "topic": "", "scores": scores, "category": category,
                    "context_complete": 6.0, "title": first[:68], "keywords": []})
    return out
