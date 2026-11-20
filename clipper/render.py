"""The editor: turns a chosen moment into a finished 1080x1920 Short.

For each clip:
  1. Jump cuts (optional): pauses longer than the channel's max_gap are tightened, never inside
     a word, so pacing feels natural. Audio and video use exactly the same cut list.
  2. Audio: the clip's sound, cut with tiny crossfades (no clicks), faded in/out, optional noise
     reduction and licensed background music, then loudness-normalised to -14 LUFS (YouTube's level).
  3. Video: every output frame is taken from the source at the matching time, framed by the crop
     plan (track / split / stack / fit), with subtle punch-ins that hide jump cuts, captions and the
     opening hook text drawn on top.
  4. Encoded to H.264 + AAC MP4 (fast start, phone friendly) at a steady frame rate.
"""
import os
import random
from pathlib import Path

import numpy as np

from clipper import captions, crop, media

OUT_W, OUT_H = 1080, 1920


def keep_intervals(words, start, end, jump_cuts=True, max_gap=0.55, pad_after=0.15, pad_before=0.12):
    """Source-time pieces to keep, in order."""
    inside = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
    if not jump_cuts or len(inside) < 2:
        return [(start, end)]
    out, a = [], start
    for w, nxt in zip(inside, inside[1:]):
        if nxt["s"] - w["e"] > max_gap:
            out.append((a, w["e"] + pad_after))
            a = nxt["s"] - pad_before
    out.append((a, end))
    return [(round(x, 3), round(y, 3)) for x, y in out if y - x > 0.05]


def out_duration(intervals):
    return sum(b - a for a, b in intervals)


def to_output_time(t, intervals):
    """Source time -> output time (None if that moment was cut)."""
    acc = 0.0
    for a, b in intervals:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def to_source_time(t_out, intervals):
    acc = 0.0
    for a, b in intervals:
        if t_out <= acc + (b - a):
            return a + (t_out - acc)
        acc += b - a
    return intervals[-1][1]


def output_words(words, intervals, fixes=None):
    out = []
    for w in words:
        s, e = to_output_time(w["s"], intervals), to_output_time(min(w["e"], intervals[-1][1]), intervals)
        if s is None:
            continue
        if e is None or e < s:
            e = s + max(0.08, w["e"] - w["s"])
        out.append(dict(w, w=captions.apply_fixes(w["w"], fixes), s=round(s, 3), e=round(e, 3)))
    return out


# ---------- audio ----------

def build_audio(src, offset, intervals, folder, name, channel, rate=48000):
    folder = Path(folder)
    lo, hi = intervals[0][0], intervals[-1][1]
    raw = folder / f"{name}_raw.wav"
    media.extract_audio(src, raw, start=max(0.0, lo - offset), duration=hi - lo + 0.2, rate=rate, channels=2)
    samples, sr = media.read_wav(raw)
    fade = int(sr * 0.008)
    pieces = []
    for a, b in intervals:
        i0, i1 = int(round((a - lo) * sr)), int(round((b - lo) * sr))
        p = samples[i0:i1].copy()
        if len(p) > 2 * fade:
            ramp = np.linspace(0, 1, fade)[:, None]
            p[:fade] *= ramp
            p[-fade:] *= ramp[::-1]
        pieces.append(p)
    out = np.concatenate(pieces) if pieces else samples
    want = int(round(out_duration(intervals) * sr))
    out = out[:want] if len(out) >= want else np.vstack([out, np.zeros((want - len(out), out.shape[1]), np.float32)])
    n_in, n_out = int(sr * 0.03), int(sr * 0.08)
    out[:n_in] *= np.linspace(0, 1, n_in)[:, None]
    out[-n_out:] *= np.linspace(1, 0, n_out)[:, None]
    cut = folder / f"{name}_cut.wav"
    media.write_wav(cut, out, sr)
    os.remove(raw)
    final = folder / f"{name}_audio.wav"
    filters = []
    if channel.get("noise_reduction"):
        filters.append("afftdn=nf=-25")
    filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    music = _music(channel, name)
    if music:
        args = ["-y", "-i", cut, "-stream_loop", "-1", "-i", music, "-filter_complex",
                f"[1:a]volume=0.09,aformat=sample_rates={sr}:channel_layouts=stereo[m];"
                f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{','.join(filters)}[a]",
                "-map", "[a]", "-ar", sr, "-ac", 2, "-c:a", "pcm_s16le", final]
    else:
        args = ["-y", "-i", cut, "-af", ",".join(filters), "-ar", sr, "-ac", 2, "-c:a", "pcm_s16le", final]
    media.run(args)
    os.remove(cut)
    return final


def _music(channel, name):
    folder = channel.get("music_folder")
    if not folder or not os.path.isdir(os.path.expanduser(folder)):
        return None
    files = sorted(p for p in Path(os.path.expanduser(folder)).iterdir()
                   if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"))
    return str(random.Random(name).choice(files)) if files else None


# ---------- framing ----------

def _cover(img, w, h):
    import cv2
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    r = cv2.resize(img, (max(w, int(iw * s + 0.5)), max(h, int(ih * s + 0.5))), interpolation=cv2.INTER_LINEAR)
    y0, x0 = (r.shape[0] - h) // 2, (r.shape[1] - w) // 2
    return r[y0:y0 + h, x0:x0 + w]


def _blur_bg(frame, w, h):
    import cv2
    small = cv2.resize(frame, (frame.shape[1] // 8 or 1, frame.shape[0] // 8 or 1), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    bg = _cover(small, w, h)
    return (bg * 0.55).astype(np.uint8)


def _window(frame, cx, cy, cw, ch, w, h):
    """Crops a cw x ch window centred near (cx, cy) (clamped inside the frame) and resizes it."""
    import cv2
    H, W = frame.shape[:2]
    cw, ch = min(cw, W), min(ch, H)
    x0 = int(round(min(max(cx - cw / 2, 0), W - cw)))
    y0 = int(round(min(max(cy - ch / 2, 0), H - ch)))
    part = frame[y0:y0 + int(ch), x0:x0 + int(cw)]
    return cv2.resize(part, (w, h), interpolation=cv2.INTER_LINEAR if cw < w else cv2.INTER_AREA)


def compose(frame, p, t, zoom=1.0, brand="#22d3ee"):
    """One finished 1080x1920 frame (before captions) for crop plan p at clip time t."""
    import cv2
    H, W = frame.shape[:2]
    layout = p.get("layout", "fit")
    if layout == "track":
        cw = H * OUT_W / OUT_H / zoom
        ch = H / zoom
        cx = crop.camera_at(p, t) * W
        cy = p.get("face_y", 0.45) * H
        return _window(frame, cx, cy, cw, ch, OUT_W, OUT_H)
    if layout == "split":
        half = OUT_H // 2
        out = np.empty((OUT_H, OUT_W, 3), np.uint8)
        for k, (x, y, size) in enumerate(p["people"][:2]):
            ch = min(H, max(size * W * 3.2, H * 0.55))
            cw = ch * OUT_W / half
            out[k * half:(k + 1) * half] = _window(frame, x * W, y * H, cw, ch, OUT_W, half)
        color = tuple(int(brand.lstrip("#")[i:i + 2], 16) for i in (4, 2, 0))
        out[half - 3:half + 3] = color
        return out
    if layout == "stack":
        top_h = int(OUT_H * p.get("top_share", 0.36))
        cx, cy, fw = p["cam"]
        cw = min(W, fw * W * 3.4)
        ch = cw * top_h / OUT_W
        out = np.empty((OUT_H, OUT_W, 3), np.uint8)
        out[:top_h] = _window(frame, cx * W, cy * H, cw, ch, OUT_W, top_h)
        bot_h = OUT_H - top_h
        gw = min(W, H * OUT_W / bot_h)
        game_cx = W * (0.5 if 0.3 < cx < 0.7 else (0.42 if cx > 0.5 else 0.58))
        out[top_h:] = _window(frame, game_cx, H / 2, gw, H, OUT_W, bot_h)
        return out
    # fit / scale: the whole picture over a blurred copy
    out = _blur_bg(frame, OUT_W, OUT_H)
    s = min(OUT_W / W, OUT_H / H) * zoom
    fg = cv2.resize(frame, (int(W * s) // 2 * 2, int(H * s) // 2 * 2), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    fh, fw = fg.shape[:2]
    if fw > OUT_W:
        x0 = (fw - OUT_W) // 2
        fg, fw = fg[:, x0:x0 + OUT_W], OUT_W
    y = int(OUT_H * (0.40 if layout == "fit" else 0.5) - fh / 2)
    y = max(0, min(OUT_H - fh, y))
    x = (OUT_W - fw) // 2
    out[y:y + fh, x:x + fw] = fg
    return out


def zoom_at(t_out, intervals, p, channel):
    """Subtle punch-ins: a gentle settle over the opening, and alternate cuts slightly closer so
    jump cuts don't look like glitches."""
    if not channel.get("zooms", True) or p.get("layout") not in ("track", "fit", "scale"):
        return 1.0
    z = 1.0
    if t_out < 0.6:
        z += 0.08 * (1 - t_out / 0.6) ** 2
    acc = 0.0
    for k, (a, b) in enumerate(intervals):
        if t_out < acc + (b - a):
            if k % 2 == 1:
                z += 0.06
            break
        acc += b - a
    return z


# ---------- the render ----------

def render(job_folder, clip, words, plan, source, channel, style, settings_global, progress=lambda f, d="": None,
           out_path=None):
    """Makes the clip file. source = (path, offset). Returns a report dict for QA."""
    import cv2  # noqa: F401  (fails early with a clear error if OpenCV is missing)
    job_folder = Path(job_folder)
    src, offset = source
    fps = int(settings_global.get("fps", 30))
    name = f"clip_{clip['id']}"
    intervals = keep_intervals(words, clip["start"], clip["end"], channel.get("jump_cuts", True),
                               float(channel.get("max_gap", 0.55)))
    dur = out_duration(intervals)
    progress(0.02, "making the sound")
    audio = build_audio(src, offset, intervals, job_folder, name, channel)
    ow = output_words([w for w in words if clip["start"] - 0.05 <= w["s"] <= clip["end"]], intervals, channel.get("fixes"))
    keys = captions.pick_keywords(ow, clip.get("keywords") or [])
    cap = captions.Captioner(ow, style, OUT_W, OUT_H, keys,
                             hook_text=(clip.get("hook_text") or "") if channel.get("hook_overlay", True) else "",
                             hook_color=channel.get("brand", {}).get("color", "#22d3ee"))
    lo, hi = intervals[0][0], intervals[-1][1]
    reader = media.FrameReader(src, max(0.0, lo - offset), hi - lo + 0.5, fps)
    out = Path(out_path or job_folder / f"{name}.part.mp4")
    writer = media.FrameWriter(out, OUT_W, OUT_H, fps, audio, settings_global.get("x264_preset", "veryfast"),
                               int(settings_global.get("crf", 20)))
    total = int(round(dur * fps))
    thumb = job_folder / f"{name}.jpg"
    cap_boxes, last = 0, None
    try:
        for k in range(total):
            t_out = k / fps
            t_src = to_source_time(t_out, intervals)
            frame = reader.seek_index(int(round((t_src - lo) * fps)))
            if frame is None:
                frame = last if last is not None else np.zeros((1080, 1920, 3), np.uint8)
            last = frame
            img = compose(frame, plan, t_src - clip["start"], zoom_at(t_out, intervals, plan, channel),
                          channel.get("brand", {}).get("color", "#22d3ee"))
            if cap.draw(img, t_out):
                cap_boxes += 1
            if k == min(total - 1, int(fps * 1.2)):
                media.save_jpeg(img, thumb)
            writer.write(img)
            if k % (fps * 2) == 0:
                progress(0.05 + 0.93 * k / max(1, total), "rendering")
    finally:
        reader.close()
        try:
            writer.close()
        finally:
            try:
                os.remove(audio)
            except OSError:
                pass
    return {"path": str(out), "thumb": str(thumb), "duration": round(dur, 3), "frames": total, "fps": fps,
            "intervals": intervals, "cuts": len(intervals) - 1, "layout": plan.get("layout"),
            "caption_frames": cap_boxes, "words": len(ow), "words_captioned": len(cap.shown_words),
            "caption_pages": len(cap.pages), "style": style["name"], "font_size": style["size"],
            "words_per_line": style["words_per_line"]}
