"""Each Short's output pack (Connor's core rules, "Output Format"): everything a person needs to review one Short.

    Final script                 the passed script, line by line with its beat, word count and estimated length
    Shot list with timing        every scene's start and end, narration, visual, asset category, camera move,
                                 on-screen text and sound effect (from the storyboard and the finished edit)
    Recommended title + 2 others the strongest-rated title first
    Thumbnail concept            the cover's short question, its big number and the look
    Description                  the upload description (one-sentence hook, key fact, comment prompt, sources)
    Notes                        why the creative choices were made: the critic scores, the CTA type, any free
                                 fixes (a swapped hook, a replaced CTA, a varied shot), the timing measured
    Performance                  after it is published and measured: average view duration, retention, engagement

save(video_id) writes studio_data/videos/<id>/pack.md and pack.json and keeps it in videos.meta["pack"] (the
dashboard's review page shows it). It is made after every QA run, so a held test Short has one too.

    python -m studio pack <video id>"""
import json
import time

from studio import data_dir, db, rules


def _lines(row):
    st = json.loads(row["structure"] or "{}") if row else {}
    lines = [dict(x) for x in st.get("lines") or [] if isinstance(x, dict)]
    if lines and row:
        lines[0] = dict(lines[0], text=row["hook"])
    return st, lines


def _fmt_t(x):
    try:
        return f"{float(x):.1f}"
    except (TypeError, ValueError):
        return "?"


def build(video_id):
    from studio import cover, script
    v = db.video(video_id)
    if not v:
        raise ValueError(f"no video {video_id}")
    meta = json.loads(v["meta"] or "{}")
    row = script.latest(video_id, "passed")
    st, lines = _lines(row)
    checks = json.loads(row["checks"] or "{}") if row else {}
    rep = meta.get("render") or {}
    starts = rep.get("line_starts") or []
    words = sum(len(str(ln.get("text") or "").split()) for ln in lines)
    out = {"video": video_id, "made": time.time(), "title": v.get("title") or ""}
    out["script"] = {"lines": [{"n": i + 1, "beat": "cta" if i == len(lines) - 1 and i > 1 else ln.get("beat") or "",
                                "text": ln.get("text", "")} for i, ln in enumerate(lines)],
                     "words": words, "estimated_seconds": rules.est_seconds(words),
                     "primary_number": rules.primary_number(lines),
                     "cta_type": rules.CTA_TYPES.get(rules.cta_type(lines[-1].get("text")) if lines else None)}
    board = meta.get("storyboard") or []
    scenes = meta.get("scenes") or []
    shots = []
    for k, sc in enumerate(scenes):
        sc = sc if isinstance(sc, dict) else {}
        b = board[k] if k < len(board) and isinstance(board[k], dict) else {}
        shots.append({"scene": k + 1,
                      "start": starts[k] if k < len(starts) else None,
                      "end": starts[k + 1] if k + 1 < len(starts) else None,
                      "narration": b.get("narration") or (lines[k]["text"] if k < len(lines) else ""),
                      "visual": b.get("visual") or sc.get("art") or sc.get("type") or "",
                      "category": sc.get("category") or rules.category(sc),
                      "camera": b.get("motion") or "",
                      "on_screen": b.get("on_screen_text") or [],
                      "sfx": sc.get("sfx") or ""})
    card = (rep.get("core_rules") or {}).get("end_card")
    if card:
        shots.append({"scene": "end card", "start": card.get("start"), "end": rep.get("duration"),
                      "narration": "", "visual": "the answer's number and the CTA on a dark backing",
                      "category": "end card", "camera": "still (clean if paused)",
                      "on_screen": [x for x in (card.get("sub"), card.get("text")) if x], "sfx": ""})
    out["shots"] = shots
    up = meta.get("upload") or {}
    titles = rules.title_order(st.get("titles") or [], checks.get("title_scores"))
    rec = up.get("title") or (titles[0] if titles else v.get("title") or "")
    out["titles"] = {"recommended": rec, "alternates": [t for t in titles if t != rec][:2],
                     "scores": checks.get("title_scores") or {}}
    q = rules.cover_question(row["hook"] if row else "", st.get("titles") or []) if row else ""
    stat = cover.stat(st, scenes) if row else None
    out["thumbnail"] = {"question": q or (cover.cover_text(row) if row else ""),
                        "big_number": (" ".join(x for x in (stat[1], stat[2]) if x) if stat else None),
                        "look": "high contrast: huge white condensed question on the navy grid, one word in amber, "
                                "the big number in a glowing panel",
                        "file": rep.get("cover")}
    if up.get("description"):
        out["description"] = up["description"]
    elif row:
        h, fact, ask = rules.description_parts(row["hook"], lines)
        out["description"] = "\n".join(x for x in (h, fact, "", ask) if x is not None)
    else:
        out["description"] = ""
    out["notes"] = notes(checks, st, rep, meta)
    perf = meta.get("performance")
    if perf:
        out["performance"] = perf
    return out


def notes(checks, st, rep, meta):
    """Why the creative choices were made, in plain words."""
    n = []
    crit = checks.get("critic") or {}
    if any(isinstance(crit.get(k), (int, float)) for k in rules.CRITIC):
        n.append("Critic scores: " + ", ".join(f"{k} {crit[k]:g}/10" for k in rules.CRITIC
                                               if isinstance(crit.get(k), (int, float)))
                 + (f" ({crit['note']})" if crit.get("note") else "") + ".")
    if crit.get("hook_swapped"):
        n.append("The first-choice hook scored under 8 for strength, so a stronger checked hook was used instead.")
    for f in st.get("rules_fixed") or []:
        n.append("CTA: " + f + ".")
    lines = [x for x in st.get("lines") or [] if isinstance(x, dict)]
    kind = rules.cta_type(lines[-1].get("text")) if lines else None
    if kind:
        n.append(f"Ends on a {rules.CTA_TYPES[kind]}: it asks the viewer to do one specific thing.")
    if kind == "series":
        n.append("The series tease is a promise: it is saved on the follow-up list (studio_series_teases) so Part 2 "
                 "gets made.")
    num = rules.primary_number(lines)
    if num:
        n.append(f"One primary number: {num}, the punchline answer; the end card holds it so the final frame reads on "
                 "pause.")
    pre = meta.get("preflight") or {}
    for f in (pre.get("fixed") or [])[:4]:
        n.append("Visual fix: " + f + ".")
    cr = rep.get("core_rules") or {}
    if cr:
        n.append(f"Timing: hook said by {cr.get('hook_end')} s, pattern interrupt at {cr.get('interrupt')} s, "
                 f"{cr.get('duration')} s long" + (f"; still to fix: {'; '.join(cr['problems'])}" if cr.get("problems")
                                                    else "") + ".")
        if cr.get("sfx"):
            n.append(f"Sound: {cr['sfx']}.")
        for p in (cr.get("visual") or [])[:3]:
            n.append("Visual note: " + p + ".")
    for p in [p for p in checks.get("rules") or [] if p.startswith("editor: ")][:3]:
        n.append("Advice: " + p[len("editor: rule: "):] + ".")
    fr = meta.get("final_review") or {}
    if fr.get("phone"):
        ph = fr["phone"]
        n.append("Phone check: " + ("would stop a scroller" if ph.get("stops_scroll") else
                                    "might not stop a scroller" if ph.get("stops_scroll") is False else "not judged")
                 + (f" ({ph['why']})" if ph.get("why") else "") + ".")
    return n


def markdown(p):
    out = [f"# Short {p['video']}: {p.get('title') or ''}".rstrip(), ""]
    s = p["script"]
    out += ["## Final script", f"{s['words']} words, about {s['estimated_seconds']:g} s of narration"
            + (f"; CTA: {s['cta_type']}" if s.get("cta_type") else "") + ".", ""]
    out += [f"{ln['n']}. [{ln['beat']}] {ln['text']}" for ln in s["lines"]] + [""]
    out += ["## Shot list", "| # | Time (s) | Narration | Visual | Category | Camera | On screen |",
            "|---|---|---|---|---|---|---|"]
    for sh in p["shots"]:
        cell = lambda x: str(x).replace("|", "/").replace("\n", " ")[:140]
        out.append(f"| {sh['scene']} | {_fmt_t(sh['start'])}-{_fmt_t(sh['end'])} | {cell(sh['narration'])} | "
                   f"{cell(sh['visual'])} | {sh['category']} | {cell(sh['camera'])} | "
                   f"{cell(', '.join(map(str, sh['on_screen'])))} |")
    t = p["titles"]
    out += ["", "## Titles", f"Recommended: {t['recommended']}"] + [f"Alternate: {a}" for a in t["alternates"]]
    th = p["thumbnail"]
    out += ["", "## Thumbnail concept", f"Question: {th['question']}",
            f"Big number: {th['big_number'] or '(none: the question carries it)'}", f"Look: {th['look']}"]
    out += ["", "## Description", p["description"] or "(not made yet)"]
    out += ["", "## Notes on the creative choices"] + [f"- {x}" for x in p["notes"] or ["(none)"]]
    if p.get("performance"):
        pf = p["performance"]
        out += ["", "## Performance (real viewers)"]
        for k, label in (("avg_view_seconds", "Average view duration (s)"), ("avg_view_pct", "Average viewed (%)"),
                         ("hook_hold", "Still watching at 10%"), ("views", "Views"), ("likes", "Likes"),
                         ("comments", "Comments"), ("shares", "Shares"), ("engaged_per_1k", "Engaged per 1,000 views"),
                         ("index", "Index (50 = typical here)")):
            if pf.get(k) is not None:
                out.append(f"- {label}: {pf[k]:.3g}" if isinstance(pf[k], float) else f"- {label}: {pf[k]}")
        for lk in pf.get("leaks") or []:
            out.append(f"- Viewers left fastest at the {lk}")
    return "\n".join(out) + "\n"


def save(video_id):
    p = build(video_id)
    folder = data_dir("videos", str(video_id))
    (folder / "pack.json").write_text(json.dumps(p, indent=2))
    (folder / "pack.md").write_text(markdown(p), encoding="utf-8")
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    db.set_video(video_id, meta=json.dumps(dict(meta, pack=p)))
    lines = p["script"]["lines"]
    if lines:
        rules.remember_tease(video_id, lines[-1]["text"])
    return p


def record_performance(video_id, perf, shape=None):
    """Feedback memory: a measured Short's real results (and its leaks, line by line) go into its pack."""
    v = db.video(video_id)
    if not v:
        return
    meta = json.loads(v["meta"] or "{}")
    ret = db.get(f"retention:{video_id}") or {}
    leaks = [f"{x.get('beat') or 'unlabelled'} line \"{str(x.get('text'))[:80]}\" ({100 * float(x.get('lost') or 0):.0f} "
             "points)" for x in ret.get("lines") or [] if isinstance(x, dict) and x.get("leak")]
    pf = dict({k: v2 for k, v2 in (perf or {}).items() if k not in ("youtube_id",)}, leaks=leaks[:3],
              **({"structure": shape} if shape else {}))
    meta["performance"] = pf
    if isinstance(meta.get("pack"), dict):
        meta["pack"]["performance"] = pf
    db.set_video(video_id, meta=json.dumps(meta))
    try:
        folder = data_dir("videos", str(video_id))
        if (folder / "pack.json").exists() and isinstance(meta.get("pack"), dict):
            (folder / "pack.json").write_text(json.dumps(meta["pack"], indent=2))
            (folder / "pack.md").write_text(markdown(meta["pack"]), encoding="utf-8")
    except OSError:
        pass
