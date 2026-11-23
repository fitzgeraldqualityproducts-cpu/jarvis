"""The mandatory gate before a Short can be published (Phase 7).

check(video_id) runs every check, saves each one to qa_checks and returns {"ok", "checks", "failed"}.
Technical: 1080x1920, H.264 + AAC, 30 fps, 15-60 s, audio length matches, loudness -18 to -10 LUFS,
           peak below -0.5 dBTP, no blank stretches.
Content:   passed fact-checked script, 2+ supported sources, every caption word shown, captions and
           visuals inside the safe area, every asset original or licensed with a checksum, title and
           description rules (sources + AI-narration note), not a remake of an earlier video."""
import json
import os
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
    _add(checks, "file size", os.path.getsize(path) < 256 * 1024 * 1024, f"{os.path.getsize(path) / 1e6:.1f} MB")
    row = script.latest(video_id, "passed")
    _add(checks, "fact-checked script", bool(row), "" if row else "no passed script")
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
    guard.audit("qa.check", True, {"video": video_id, "failed": [c["name"] for c in checks if not c["passed"]]})
    return _save(video_id, checks)


def _save(video_id, checks):
    now = time.time()
    db.run("DELETE FROM qa_checks WHERE video_id=?", video_id)
    for c in checks:
        db.insert("INSERT INTO qa_checks(video_id, check_name, passed, detail, t) VALUES(?,?,?,?,?)",
                  video_id, c["name"], int(c["passed"]), c["detail"], now)
    failed = [c for c in checks if not c["passed"]]
    return {"ok": not failed, "checks": checks, "failed": failed}
