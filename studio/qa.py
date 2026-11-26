"""The mandatory gate before a Short can be published (Phase 7).

check(video_id) runs every check, saves each one to qa_checks and returns {"ok", "checks", "failed"}.
Technical: 1080x1920, H.264 + AAC, 30 fps, 15-60 s, audio length matches, loudness -18 to -10 LUFS,
           peak below -0.5 dBTP, no blank stretches.
Content:   passed fact-checked script, 2+ supported sources, every caption word shown, captions and
           visuals inside the safe area, every asset original or licensed with a checksum, title and
           description rules (sources + AI-narration note), not a remake of an earlier video.
Visual:    the picture never stands still for long, and no two scenes reuse the same picture. Config
           "visual_qc": "advise" (default: found problems are shown in QA and become lessons, never stop a Short)
           or "block" (they fail QA). "final-cut review" (studio/finalreview.py) runs last."""
import json
import os
import re
import time

import numpy as np

from studio import db, guard


def _add(checks, name, ok, detail=""):
    checks.append({"name": name, "passed": bool(ok), "detail": str(detail)[:400]})


def blank_stretches(path, every=1.0):
    """Seconds sampled where the picture is (nearly) one flat colour."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    bad, step = [], max(1, int(fps * every))
    for f in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, fr = cap.read()
        if not ok:
            continue
        if float(np.std(fr)) < 4.0:
            bad.append(round(f / fps, 1))
    cap.release()
    return bad


STATIC_DIFF = 1.0        # frames half a second apart that differ less than this (0-255 average) count as still
STATIC_MAX = 3.5         # the picture may stand still for at most this long
SAME_PICTURE = 6.0       # two scenes' pictures closer than this (0-255 average, small and grey) are the same one


def _small(fr):
    import cv2
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY) if fr.ndim == 3 else fr
    return cv2.resize(g, (54, 96), interpolation=cv2.INTER_AREA).astype(np.float32)


def still_stretches(path, every=0.5):
    """[(start s, seconds)] where the picture hardly changes for longer than STATIC_MAX."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step, prev, run0, out = max(1, int(round(fps * every))), None, None, []
    for f in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, fr = cap.read()
        if not ok:
            continue
        cur, t = _small(fr), f / fps
        if prev is not None and float(np.mean(np.abs(cur - prev))) < STATIC_DIFF:
            run0 = t - every if run0 is None else run0
        else:
            if run0 is not None and t - every - run0 > STATIC_MAX:
                out.append((round(run0, 1), round(t - every - run0, 1)))
            run0 = None
        prev = cur
    if run0 is not None and prev is not None and (n - 1) / fps - run0 > STATIC_MAX:
        out.append((round(run0, 1), round((n - 1) / fps - run0, 1)))
    cap.release()
    return out


def repeated_pictures(paths):
    """[(scene a, scene b)] (1-based) whose pictures are (nearly) the same image."""
    import cv2
    smalls = []
    for p in paths:
        im = cv2.imread(str(p)) if p and os.path.isfile(str(p)) else None
        smalls.append(None if im is None else _small(im))
    out = []
    for i in range(len(smalls)):
        for j in range(i + 1, len(smalls)):
            if smalls[i] is not None and smalls[j] is not None and \
                    float(np.mean(np.abs(smalls[i] - smalls[j]))) < SAME_PICTURE:
                out.append((i + 1, j + 1))
    return out


# ---------- what's on screen must match the checked script ----------
_NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_SCALED = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b", re.I)


def _nums(text):
    out = []
    for m in _NUM.findall(str(text or "")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def screen_numbers(scenes):
    """[(scene number, field, value)] for every number the plan puts on screen (values, labels, cards)."""
    out = []
    for k, sc in enumerate(scenes or []):
        if not isinstance(sc, dict):
            continue
        for f in ("value", "right", "left", "label", "text", "from", "to", "unit"):
            for v in _nums(sc.get(f)):
                out.append((k + 1, f, v))
        for it in sc.get("items") or []:
            vals = [it.get("value"), it.get("label")] if isinstance(it, dict) else [it]
            for x in vals:
                for v in _nums(x):
                    out.append((k + 1, "items", v))
    return out


def script_numbers(row):
    """Every number in the fact-checked script: its lines, hook and checked calculations (with their results)."""
    st = json.loads(row["structure"] or "{}") if row else {}
    bag = [row["hook"] if row else "", row["body"] if row else ""]
    bag += [ln.get("text", "") for ln in st.get("lines") or []]
    for c in st.get("calcs") or []:
        bag += [c.get("expr"), c.get("result"), c.get("actual"), c.get("says")]
    out = {round(abs(v), 6) for x in bag for v in _nums(x)}
    for x in bag:                                  # "5 million" on screen is 5,000,000
        for m in _SCALED.finditer(str(x or "")):
            try:
                out.add(round(float(m.group(1).replace(",", "")) * _SCALE[m.group(2).lower()], 6))
            except ValueError:
                pass
    return out


def unmatched_numbers(scenes, row):
    """On-screen numbers that aren't in the checked script (allowing rounding to what's shown, and the small
    counting numbers 0-10 that steps and labels use). [(scene, field, value)]"""
    known = script_numbers(row)
    bad = []
    for k, f, v in screen_numbers(scenes):
        if abs(v) <= 10 and v == int(v):
            continue
        if any(abs(abs(v) - n) <= max(1e-9, 0.006 * n) for n in known):   # "-56 °C" can be said "56 below"
            continue
        bad.append((k, f, v))
    return bad


def _hit(a, b, pad=0):
    """Do two boxes (x0, y0, x1, y1) overlap by more than pad pixels both ways?"""
    return min(a[2], b[2]) - max(a[0], b[0]) > pad and min(a[3], b[3]) - max(a[1], b[1]) > pad


CAPTION_SLACK = 24     # a caption box includes its line spacing: the letters start ~this far inside it


def text_overlaps(rep):
    """(blocking, advice) lists of what overlaps among the cinematic look's text layers. A scene's counter against
    its own diagram card is exact, so it blocks. Caption boxes are the tallest a caption page can be and carry no
    scene, so a caption that may touch a card or counter is advice only. (Caption boxes come as x, y, w, h.)"""
    if not rep.get("cinematic"):
        return [], []
    caps = [(c[0], c[1], c[0] + c[2], c[1] + c[3]) for c in rep.get("caption_boxes") or []]
    scene_boxes = rep.get("text_boxes") or rep.get("scene_boxes") or []   # scene_boxes also hold the card
    block, advice = [], []
    for k, ov in (rep.get("overlay_boxes") or {}).items():
        k = int(k)
        if k < len(scene_boxes) and _hit(scene_boxes[k], ov):
            block.append(f"scene {k + 1}: the counter overlaps the diagram card")
    cards = list((rep.get("overlay_boxes") or {}).values())
    if any(_hit(c, ov, CAPTION_SLACK) for c in caps for ov in cards):
        advice.append("a tall caption may touch a diagram card")
    if any(_hit(c, b, CAPTION_SLACK) for c in caps for b in scene_boxes[1:]):
        advice.append("a tall caption may touch a counter")
    return block, advice


def soft_pictures(rep):
    """None when no AI pictures were used or the sharpener is off; else "" when every AI picture was enlarged by
    Real-ESRGAN, or why not. A small AI picture stretched to 1080x1920 without it looks soft, so this blocks."""
    used = rep.get("engines") or {}
    m = re.search(r"Real-ESRGAN: (\d+) of (\d+)", str(used.get("sharpen") or ""))
    if not m:
        return None
    done, total = int(m.group(1)), int(m.group(2))
    return "" if done >= total else f"only {done} of {total} pictures were sharpened; the rest would look soft"


def decodes_cleanly(path):
    """(ok, detail): ffmpeg reads every frame and sample of the file without an error."""
    from clipper import media
    try:
        r = media.run(["-v", "error", "-i", str(path), "-f", "null", "-"], check=False, timeout=600)
        err = (r.stderr or "").strip()
        return (r.returncode == 0 and not err), (err[:160] or "decoded every frame")
    except Exception as e:
        return False, str(e)[:160]


def visual_mode():
    try:
        from studio import config
        m = str(config.general().get("visual_qc", "advise")).lower()
    except Exception:
        m = "advise"
    return m if m in ("advise", "block", "off") else "advise"


def edge_frames(path):
    """The first and last frames of the video (BGR arrays), or (None, None)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, first = cap.read()
    last = None
    for back in (1, 2, 3):
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - back))
        ok2, fr = cap.read()
        if ok2:
            last = fr
            break
    cap.release()
    return (first if ok else None), last


def check(video_id):
    from clipper import media
    from studio import script, video as vid_mod
    v = db.video(video_id)
    meta = json.loads(v["meta"] or "{}")
    checks = []
    path = v["path"]
    if not path or not os.path.isfile(path):
        _add(checks, "file", False, "no rendered video")
        return _save(video_id, checks)
    info = media.probe(path)
    _add(checks, "resolution", (info["width"], info["height"]) == (1080, 1920), f"{info['width']}x{info['height']}")
    _add(checks, "codecs", info["vcodec"] == "h264" and info["acodec"] == "aac", f"{info['vcodec']}/{info['acodec']}")
    _add(checks, "frame rate", abs(info["fps"] - 30) < 0.5, info["fps"])
    _add(checks, "length", 15 <= info["duration"] <= 60, f"{info['duration']:.1f} s")
    voice = db.one("SELECT seconds FROM voiceovers WHERE video_id=? ORDER BY id DESC LIMIT 1", video_id)
    rep = meta.get("render") or {}
    if voice:
        need = voice["seconds"] - float(rep.get("trimmed_lead") or 0) - 0.5     # silence trimmed at the start/end
        _add(checks, "audio complete", info["duration"] >= need,
             f"video {info['duration']:.1f} s, narration {voice['seconds']:.1f} s")
    lufs, peak = media.loudness(path)
    _add(checks, "loudness", lufs is not None and -18 <= lufs <= -10, f"{lufs} LUFS")
    _add(checks, "peak", peak is not None and peak <= -0.5, f"{peak} dBTP")
    blanks = blank_stretches(path)
    _add(checks, "no blank picture", not blanks, f"blank at {blanks[:5]} s" if blanks else "")
    first, last = edge_frames(path)
    if first is not None:
        _add(checks, "fast start", float(np.std(first)) >= 4.0 and (rep.get("speech_start") is None or rep["speech_start"] <= 0.35),
             f"speech starts at {rep.get('speech_start')} s")
    if first is not None and last is not None:
        diff = float(np.mean(np.abs(first.astype(np.int16) - last.astype(np.int16))))
        tail = rep.get("tail_seconds")
        _add(checks, "seamless loop", diff <= 14.0 and (tail is None or tail <= 0.6),
             f"last frame differs from the first by {diff:.1f}; {tail} s after the last word")
    vm = visual_mode()
    if vm != "off":
        still = still_stretches(path)
        pics = [a["path"] for a in db.q("SELECT path FROM assets WHERE video_id=? AND kind IN ('art:scene', 'photo:scene') ORDER BY id",
                                         video_id)]
        same = repeated_pictures(pics)
        found = ([f"picture stands still for {s} s at {t} s" for t, s in still[:3]] +
                 [f"scenes {a} and {b} use the same picture" for a, b in same[:3]])
        _add(checks, "picture keeps changing", vm == "advise" or not found,
             ("; ".join(found) + (" (advice only)" if vm == "advise" else "")) if found else
             f"no still stretch over {STATIC_MAX:.1f} s, {len(pics)} different pictures")
        if found and vm == "advise":
            from studio import lessons, topics
            lessons.record("video", [f"visual qc: {x}" for x in found][:4],
                           topics.topic(v["topic_id"]) if v.get("topic_id") else None, video_id)
    weak = soft_pictures(rep)
    if weak is not None:            # like "picture keeps changing": blocks only with visual_qc "block", else advice
        vq = visual_mode()
        _add(checks, "pictures sharpened to full size", not weak or vq != "block",
             (weak + (" (advice only)" if vq != "block" else "")) if weak else
             (rep.get("engines") or {}).get("sharpen", ""))
    over, maybe = text_overlaps(rep)
    _add(checks, "text layers don't overlap", not over,
         "; ".join(over[:3] + [m + " (advice only)" for m in maybe]))
    if info.get("audio_duration") and info.get("video_duration"):
        gap = abs(float(info["audio_duration"]) - float(info["video_duration"]))
        _add(checks, "sound and picture same length", gap <= 0.25, f"{gap:.2f} s apart")
    ok, why = decodes_cleanly(path)
    _add(checks, "file decodes cleanly", ok, why)
    _add(checks, "file size", os.path.getsize(path) < 256 * 1024 * 1024, f"{os.path.getsize(path) / 1e6:.1f} MB")
    row = script.latest(video_id, "passed")
    _add(checks, "fact-checked script", bool(row), "" if row else "no passed script")
    if row and meta.get("scenes"):
        bad = unmatched_numbers(meta["scenes"], row)
        _add(checks, "on-screen numbers match the script", not bad,
             "; ".join(f"scene {k} {f}: {v:g} isn't in the checked script" for k, f, v in bad[:4]) or
             f"{len(screen_numbers(meta['scenes']))} numbers checked")
    vp = None
    if row:
        from studio import narrative
        nar = (json.loads(row["checks"] or "{}") or {}).get("narrative")
        if narrative.mode() != "off":
            if nar is None:
                _add(checks, "story makes sense", True, "not checked (script written before the story check)")
            else:                                  # a high visual score never makes up for a failed story
                _add(checks, "story makes sense", not nar.get("hard") or narrative.mode() == "advise",
                     "; ".join(h[len("narrative: "):] for h in (nar.get("hard") or [])[:3])
                     or f"story score {nar.get('score')}")
        if meta.get("scenes"):
            st = json.loads(row["structure"] or "{}")
            vp = narrative.visual_purpose(meta["scenes"], st.get("lines") or [])
            if vp["score"] is not None:
                vm2 = visual_mode()
                _add(checks, "visuals explain the story", not vp["problems"] or vm2 != "block",
                     ("; ".join(vp["problems"]) + (" (advice only)" if vm2 != "block" else "")) if vp["problems"]
                     else f"{vp['score']:.0%} of maths lines get an explaining visual")
    sup = db.one("SELECT COUNT(DISTINCT url) n FROM sources WHERE video_id=? AND verdict='supported'", video_id)["n"]
    bad = db.one("SELECT COUNT(*) n FROM sources WHERE video_id=? AND verdict IN ('wrong','unsupported')", video_id)["n"]
    _add(checks, "sources", sup >= 2 and bad == 0, f"{sup} supporting sources, {bad} problems")
    _add(checks, "captions complete", rep.get("words") and rep.get("caption_words_shown", 0) >= rep["words"] * 0.98,
         f"{rep.get('caption_words_shown')} of {rep.get('words')} words shown")
    tm = rep.get("timing") or {}
    _add(checks, "caption timing", tm.get("matched", 0) >= 0.85,
         f"{tm.get('method')}: {tm.get('matched', 0):.0%} of words heard and matched")
    boxes = rep.get("scene_boxes") or []
    _add(checks, "visuals in safe area", boxes and all(vid_mod.inside_safe_area(b) for b in boxes),
         f"{sum(not vid_mod.inside_safe_area(b) for b in boxes)} of {len(boxes)} scenes outside")
    caps = rep.get("caption_boxes") or []
    low = [c for c in caps if c[1] + c[3] > 1920 * 0.80 or c[0] < 40 or c[0] + c[2] > 1080 - 120]
    _add(checks, "captions in safe area", not low, f"{len(low)} caption frames too close to the edges")
    assets = db.q("SELECT * FROM assets WHERE video_id=?", video_id)
    lic = [a for a in assets if not a["license"] or not a["sha256"] or not os.path.isfile(a["path"] or "")]
    _add(checks, "asset licences", assets and not lic, f"{len(assets)} assets, {len(lic)} without licence/checksum/file")
    up = meta.get("upload") or {}
    tproblems = script.hook_checks_title(up.get("title", ""))
    _add(checks, "title", not tproblems, "; ".join(tproblems) or up.get("title"))
    desc = up.get("description", "")
    _add(checks, "description", "Sources:" in desc and "AI-generated" in desc and len(desc) <= 5000,
         f"{len(desc)} characters")
    if row:
        sim, other = script.similar_script(row["body"], video_id)
        _add(checks, "not a remake", sim < script.SIMILAR_SCRIPT, f"{sim:.0%} overlap with video {other}" if other else "")
    if all(c["passed"] for c in checks):          # the editor's look, only for a Short that passed everything else
        from studio import finalreview
        try:
            fr = finalreview.review(video_id)
        except Exception as e:
            if finalreview.mode() == "block":
                raise
            fr = {"ok": True, "skipped": str(e)[:160]}
        if fr is not None:
            _add(checks, "final-cut review", fr["ok"], f"skipped ({fr['skipped']})" if fr.get("skipped")
                 else finalreview.detail(fr))
    guard.audit("qa.check", True, {"video": video_id, "failed": [c["name"] for c in checks if not c["passed"]]})
    ck = json.loads(row["checks"] or "{}") if row else {}
    conf = db.one("SELECT AVG(confidence) c FROM sources WHERE video_id=? AND confidence IS NOT NULL", video_id)["c"]
    q = quality({"lufs": lufs, "loop_diff": diff if first is not None and last is not None else None,
                 "timing": tm.get("matched"), "speech_start": rep.get("speech_start"),
                 "hook": ck.get("hook_score"), "originality": ck.get("originality"), "confidence": conf,
                 "story": (ck.get("narrative") or {}).get("score"), "visual_purpose": (vp or {}).get("score")})
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    db.set_video(video_id, meta=json.dumps(dict(meta, quality=q)))
    return dict(_save(video_id, checks), quality=q)


# The weighted quality score sits NEXT TO the hard checks: it never passes a Short that failed one, and never
# stops one that passed them all. It shows how good a passing Short is, so results can be compared over time.
QUALITY_WEIGHTS = {"loudness": 0.10, "loop": 0.15, "timing": 0.15, "start": 0.10, "hook": 0.20, "originality": 0.20,
                   "confidence": 0.10, "story": 0.20, "visual_purpose": 0.10}


def quality(m):
    """{"score": 0-100 or None, "parts": {name: 0-1}, "missing": [...]} from measured values. A part with no
    measurement is left out (and listed), never guessed."""
    def clip(x):
        return max(0.0, min(1.0, float(x)))
    parts, missing = {}, []
    def put(name, fn, v):
        if v is None:
            missing.append(name)
        else:
            try:
                parts[name] = round(clip(fn(float(v))), 3)
            except (TypeError, ValueError):
                missing.append(name)
    put("loudness", lambda v: 1 - abs(v + 14) / 4, m.get("lufs"))            # -14 LUFS is the target
    put("loop", lambda v: 1 - v / 14, m.get("loop_diff"))                    # 0 = identical first/last frame
    put("timing", lambda v: (v - 0.85) / 0.15, m.get("timing"))              # 85% matched = 0, 100% = 1
    put("start", lambda v: 1 - v / 0.35, m.get("speech_start"))              # speech at 0 s = 1
    put("hook", lambda v: v / 100 if v > 10 else v / 10, m.get("hook"))      # hook score 0-100 (or 0-10)
    put("originality", lambda v: (v - 5) / 5, m.get("originality"))          # 5/10 = 0, 10/10 = 1
    put("confidence", lambda v: v, m.get("confidence"))
    put("story", lambda v: v / 100, m.get("story"))                         # the story score, 0-100
    put("visual_purpose", lambda v: v, m.get("visual_purpose"))             # share of maths lines with an explaining visual
    w = sum(QUALITY_WEIGHTS[k] for k in parts)
    score = round(100 * sum(QUALITY_WEIGHTS[k] * v for k, v in parts.items()) / w, 1) if w else None
    return {"score": score, "parts": parts, "missing": missing}


def _save(video_id, checks):
    now = time.time()
    db.run("DELETE FROM qa_checks WHERE video_id=?", video_id)
    for c in checks:
        db.insert("INSERT INTO qa_checks(video_id, check_name, passed, detail, t) VALUES(?,?,?,?,?)",
                  video_id, c["name"], int(c["passed"]), c["detail"], now)
    failed = [c for c in checks if not c["passed"]]
    return {"ok": not failed, "checks": checks, "failed": failed}
