"""Quality check: a clip only moves on if it passes. Rendering finishing is not enough.

Critical (the clip is fixed or rejected):  resolution 1080x1920, H.264 + AAC, length inside the
Shorts limits, sound present and in sync with the picture, loudness in range, no black screens,
captions covering the words, the speaker's face kept in frame, a title and description,
not a duplicate of an earlier clip, and the source still authorized.
Warnings (noted, shown on the dashboard): long silences, frozen picture, quiet speech, clips that
don't end on a full stop.
Recoverable problems are fixed automatically (loudness re-normalised, out-of-sync sound re-muxed,
black lead-in/out trimmed); anything still failing gets a saved report and Jarvis tells you."""
import json
import os
import re

from clipper import db, media

SHORTS_MAX = 180.0          # YouTube Shorts can be up to 3 minutes


def check(path, clip, render_report, video, channel, plan=None, visible=None):
    checks = {}

    def add(name, ok, value, detail, critical=True):
        checks[name] = {"ok": bool(ok), "value": value, "detail": detail, "critical": critical}

    try:
        info = media.probe(path)
    except Exception as e:
        add("readable", False, None, f"the file can't be read: {e}")
        return _result(checks)
    add("readable", True, None, "file opens")
    add("resolution", info["width"] == 1080 and info["height"] == 1920, f"{info['width']}x{info['height']}",
        "must be 1080x1920 (9:16)")
    add("codecs", info["vcodec"] == "h264" and info["acodec"] == "aac", f"{info['vcodec']}/{info['acodec']}",
        "H.264 video with AAC sound")
    dur = info["duration"]
    lo = max(5.0, float(channel.get("clip_min", 20)) * 0.6)
    hi = min(SHORTS_MAX, float(channel.get("clip_hard_max", 90)) + 2)
    add("duration", lo <= dur <= hi, round(dur, 2), f"between {lo:.0f} and {hi:.0f} seconds")
    vd, ad = info.get("video_duration") or dur, info.get("audio_duration") or dur
    add("av_sync", info["has_audio"] and abs(vd - ad) <= 0.12, round(abs(vd - ad), 3),
        "sound and picture the same length (within 0.12 s)")
    want = render_report.get("duration")
    if want:
        add("length_matches_edit", abs(dur - want) <= 0.15, round(dur - want, 3), "the file matches the edit")
    lufs, peak = media.loudness(path)
    add("loudness", lufs is not None and -18.0 <= lufs <= -10.0, lufs, "speech loudness -18 to -10 LUFS")
    add("peaks", peak is not None and peak <= -0.3, peak, "no clipping (peaks under -0.3 dB)")
    black = media.detect(path, "black")
    black_time = sum(b - a for a, b in black)
    add("black_frames", black_time < 0.5, round(black_time, 2), "no black screen")
    silence = media.detect(path, "silence")
    longest = max([((b or dur) - a) for a, b in silence] or [0.0])
    add("silence", longest < 2.5, round(longest, 2), "no long dead air", critical=False)
    frozen = media.detect(path, "freeze")
    frozen_time = sum(((b or dur) - a) for a, b in frozen)
    add("frozen_picture", frozen_time < dur * 0.6, round(frozen_time, 2), "picture isn't frozen", critical=False)
    words = render_report.get("words", 0)
    covered = render_report.get("words_captioned", 0)
    add("captions", words == 0 or covered / words >= 0.97, f"{covered}/{words}", "every spoken word captioned")
    add("caption_readability", render_report.get("font_size", 0) >= 44 and render_report.get("words_per_line", 9) <= 6,
        render_report.get("font_size"), "big enough to read, short lines")
    if visible is not None:
        add("speaker_visible", visible >= 0.9, round(visible, 3), "speaker's face inside the frame 90%+ of the time")
    add("complete_ending", bool(re.search(r"[.!?…]['\")\]]*$", (clip.get("transcript") or "").strip())),
        None, "ends on a finished sentence", critical=False)
    add("metadata", bool(clip.get("title")) and len(clip.get("title", "")) <= 100 and bool(clip.get("description")),
        clip.get("title"), "has a title (100 characters max) and description")
    add("authorized", video.get("auth_status") == "authorized", video.get("auth_basis"),
        "the source is authorized for clipping")
    dup = duplicate_of(clip)
    add("not_duplicate", dup is None, dup, "not the same moment as an earlier clip")
    return _result(checks)


def _result(checks):
    critical = [k for k, c in checks.items() if not c["ok"] and c["critical"]]
    warnings = [k for k, c in checks.items() if not c["ok"] and not c["critical"]]
    return {"passed": not critical, "critical": critical, "warnings": warnings, "checks": checks}


def fingerprint(clip, video_id):
    words = re.findall(r"[a-z0-9']+", (clip.get("transcript") or "").lower())
    return f"{video_id}:{int(clip['start'] // 5)}:{' '.join(words[:12])}"


def _root(c):
    extra = c.get("extra")
    if isinstance(extra, str):
        extra = db.loads(extra)
    return (extra or {}).get("variant_of") or c.get("id")


def duplicate_of(clip):
    """Another clip (not this one) of the same moment: same fingerprint, or mostly the same words
    from the same video."""
    me = clip.get("id")
    rows = db.q("SELECT id, fingerprint, transcript, video_id, start, \"end\", state, extra FROM clips WHERE id != ? AND "
                "state NOT IN ('rejected','qa_failed','regenerate')", me or -1)
    a = set((clip.get("transcript") or "").lower().split())
    root = _root(clip)
    for r in rows:
        if root is not None and _root(r) == root:
            continue                     # a variation you asked for is meant to be the same moment
        if clip.get("fingerprint") and r["fingerprint"] == clip["fingerprint"]:
            return r["id"]
        if r["video_id"] == clip.get("video_id"):
            inter = max(0.0, min(r["end"], clip["end"]) - max(r["start"], clip["start"]))
            if inter > 0.5 * min(r["end"] - r["start"], clip["end"] - clip["start"]):
                return r["id"]
        b = set((r["transcript"] or "").lower().split())
        if len(a) > 15 and b and len(a & b) / len(a | b) > 0.8:
            return r["id"]
    return None


def autofix(path, result):
    """Fixes what can be fixed without re-editing. Returns True if it changed the file."""
    fixed = False
    failing = set(result["critical"])
    if failing & {"loudness", "peaks", "av_sync"}:
        tmp = str(path) + ".fix.mp4"
        media.run(["-y", "-i", path, "-map", "0:v", "-map", "0:a", "-c:v", "copy", "-af",
                   "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", 48000, "-c:a", "aac", "-b:a", "192k",
                   "-shortest", "-movflags", "+faststart", tmp])
        os.replace(tmp, path)
        fixed = True
    if "black_frames" in failing:
        black = media.detect(path, "black")
        dur = media.probe(path)["duration"]
        a = max([b for x, b in black if x < 0.2] or [0.0])
        b = min([x for x, y in black if y >= dur - 0.2] or [dur])
        if b - a > 5 and (a > 0 or b < dur):
            tmp = str(path) + ".fix.mp4"
            media.run(["-y", "-ss", f"{a:.3f}", "-i", path, "-t", f"{b - a:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", 20, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp])
            os.replace(tmp, path)
            fixed = True
    return fixed


def save_report(folder, clip_id, result):
    p = os.path.join(str(folder), f"clip_{clip_id}_qa.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    return p


def describe(result):
    bad = [f"{k} ({result['checks'][k]['value']})" for k in result["critical"]]
    return "passed" if result["passed"] else "failed: " + ", ".join(bad)
