"""The editor: turns a chosen moment into a finished 1080x1920 Short.

For each clip:
  1. Jump cuts (optional): pauses longer than the channel's max_gap are tightened and lone "um"/"uh"
     are cut out, never inside a word, so pacing feels natural. Audio and video share one cut list.
  2. Audio: the clip's sound, cut with tiny crossfades (no clicks), faded in/out, noise reduction
     when the recording is actually noisy, optional licensed background music that ducks under
     speech, optional soft sound effects (made here, no licences needed), then loudness-normalised
     to -14 LUFS (YouTube's level) with peaks kept under -1.5 dB, so speech stays clear.
  3. Video: every output frame is taken from the source at the matching time, framed by the crop
     plan (track / split / stack / fit), with subtle punch-ins on emphasised words and speaker changes
     (never more than one every few seconds), captions and the opening hook text drawn on top. Gentle
     colour correction and sharpening are added only when the picture needs them.
  4. Encoded to H.264 + AAC MP4 (fast start, phone friendly), keeping the source frame rate when
     it's a normal one (24-60 fps), otherwise 30.
Every enhancement is a channel setting (on, off, or auto = only when it helps), and the report says
which ones were used and why."""
import os
import random
import re
from pathlib import Path

import numpy as np

from clipper import captions, crop, media

OUT_W, OUT_H = 1080, 1920


FILLER = re.compile(r"^(um+|uh+|erm+|er|ah+|hmm+|mm+)[.,!?]*$", re.I)


def keep_intervals(words, start, end, jump_cuts=True, max_gap=0.55, pad_after=0.15, pad_before=0.12, fillers=False):
    """Source-time pieces to keep, in order. fillers=True also cuts out lone "um"/"uh"."""
    inside = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]
    if fillers:
        kept = [w for w in inside if not FILLER.match(w["w"].strip())]
        removed = len(inside) - len(kept)
        inside = kept if kept else inside
    else:
        removed = 0
    if not jump_cuts or len(inside) < 2:
        if not removed:
            return [(start, end)]
    out, a = [], start
    for w, nxt in zip(inside, inside[1:]):
        gap = nxt["s"] - w["e"]
        filler_between = removed and any(w["e"] <= f["s"] and f["e"] <= nxt["s"] and FILLER.match(f["w"].strip())
                                         for f in words if w["e"] <= f["s"] <= nxt["s"])
        if (jump_cuts and gap > max_gap) or filler_between:
            after, before = min(pad_after, max(0.03, gap / 2)), min(pad_before, max(0.03, gap / 2))
            if filler_between:                       # cut tight around the "um" so none of it is heard
                fl = [f for f in words if w["e"] <= f["s"] and f["e"] <= nxt["s"] and FILLER.match(f["w"].strip())]
                after = min(after, max(0.0, fl[0]["s"] - w["e"] - 0.01))
                before = min(before, max(0.0, nxt["s"] - fl[-1]["e"] - 0.01))
            out.append((a, w["e"] + after))
            a = nxt["s"] - before
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


def output_words(words, intervals, fixes=None, edits=None):
    """Words on the output timeline (cut words dropped), with name fixes and the editor's
    per-word corrections (edits: {source word start time as "12.345": "new text"}; "" hides a word)."""
    out = []
    edits = edits or {}
    for w in words:
        key = f"{w['s']:.3f}"
        if key in edits:
            if not str(edits[key]).strip():
                continue
            w = dict(w, w=str(edits[key]).strip())
        if to_output_time((w["s"] + w["e"]) / 2, intervals) is None:
            continue                                 # the word was cut out (dead air or a filler)
        s, e = to_output_time(w["s"], intervals), to_output_time(min(w["e"], intervals[-1][1]), intervals)
        if s is None:
            s = to_output_time((w["s"] + w["e"]) / 2, intervals)
        if e is None or e < s:
            e = s + max(0.08, w["e"] - w["s"])
        out.append(dict(w, w=captions.apply_fixes(w["w"], fixes), s=round(s, 3), e=round(e, 3)))
    return out


# ---------- audio ----------

def noise_level(samples, sr):
    """(noise floor dBFS, speech level dBFS) from 50 ms windows: the quiet 10% vs the loud 10%."""
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    n = int(sr * 0.05)
    if len(mono) < n * 10:
        return -90.0, -90.0
    frames = mono[:len(mono) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    return float(np.percentile(db, 10)), float(np.percentile(db, 90))


def _whoosh(sr, dur=0.45, seed=1):
    """A soft airy whoosh: filtered noise that swells and fades (made here, so no licence needed)."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    noise = rng.standard_normal(n).astype(np.float32)
    k = np.linspace(0, 1, n)
    smooth = np.convolve(noise, np.ones(24) / 24, mode="same")            # darker
    env = np.sin(np.pi * k) ** 2
    return (smooth * env * 0.5).astype(np.float32)


def _pop(sr, dur=0.09):
    t = np.arange(int(sr * dur)) / sr
    f = 900 * np.exp(-t * 30) + 180
    return (np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 45) * 0.35).astype(np.float32)


def build_audio(src, offset, intervals, folder, name, channel, rate=48000, sfx_at=(), report=None):
    folder = Path(folder)
    report = report if report is not None else {}
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
    floor, speech = noise_level(out, sr)
    report["noise_floor_db"], report["speech_db"] = round(floor, 1), round(speech, 1)
    nr = channel.get("noise_reduction", "auto")
    noisy = speech - floor < 28 and floor > -60
    use_nr = nr is True or (nr == "auto" and noisy)
    if use_nr:
        report.setdefault("enhancements", []).append("noise reduction" + (" (the recording was noisy)" if nr == "auto" else ""))
    effects = None
    if channel.get("sound_effects") and sfx_at:
        effects = np.zeros(len(out), np.float32)
        for t, kind in sfx_at:
            snd = _whoosh(sr) if kind == "whoosh" else _pop(sr)
            i = int(max(0.0, t) * sr)
            j = min(len(effects), i + len(snd))
            if i < j:
                effects[i:j] += snd[:j - i]
        report.setdefault("enhancements", []).append(f"{len(sfx_at)} soft sound effect{'s' if len(sfx_at) != 1 else ''}")
    cut = folder / f"{name}_cut.wav"
    media.write_wav(cut, out, sr)
    os.remove(raw)
    final = folder / f"{name}_audio.wav"
    voice = (["afftdn=nf=-25"] if use_nr else []) + ["highpass=f=70"]
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11"
    music = _music(channel, name)
    inputs = ["-y", "-i", cut]
    graph = [f"[0:a]{','.join(voice)},asplit=2[v][vk]"]
    mix = ["[v]"]
    if music:
        vol = max(0.0, min(0.3, float(channel.get("music_volume", 0.08))))
        inputs += ["-stream_loop", "-1", "-i", music]
        k = 1
        graph.append(f"[{k}:a]aformat=sample_rates={sr}:channel_layouts=stereo,volume={vol:.3f}[m0]")
        # ducking: the music dips further whenever someone speaks, so it never covers the voice
        graph.append("[m0][vk]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350[m]")
        mix.append("[m]")
        report.setdefault("enhancements", []).append("background music, ducked under speech")
    else:
        graph[0] = f"[0:a]{','.join(voice)}[v]"
    if effects is not None:
        fx = folder / f"{name}_fx.wav"
        media.write_wav(fx, np.repeat(effects[:, None], 2, axis=1), sr)
        inputs += ["-i", fx]
        graph.append(f"[{len(mix)}:a]volume=0.22[fx]")
        mix.append("[fx]")
    if len(mix) > 1:
        graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,{norm}[a]")
    else:
        graph.append(f"[v]{norm}[a]")
    media.run(inputs + ["-filter_complex", ";".join(graph), "-map", "[a]", "-ar", sr, "-ac", 2, "-c:a", "pcm_s16le", final])
    os.remove(cut)
    if effects is not None:
        try:
            os.remove(folder / f"{name}_fx.wav")
        except OSError:
            pass
    return final


def _music(channel, name):
    folder = channel.get("music_folder")
    if not folder or not os.path.isdir(os.path.expanduser(folder)):
        return None
    files = sorted(p for p in Path(os.path.expanduser(folder)).iterdir()
                   if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"))
    return str(random.Random(name).choice(files)) if files else None


def picture_needs(frames, src_h, plan):
    """Decides the gentle picture fixes from real frames: colour correction for a dull/flat
    picture, sharpening when a small source is enlarged. Returns (ffmpeg filter or None, reasons)."""
    import cv2
    sats, cons = [], []
    for f in frames:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        sats.append(float(hsv[..., 1].mean()))
        cons.append(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).std()))
    sat, con = (float(np.median(sats)), float(np.median(cons))) if frames else (100.0, 60.0)
    zoom = float(plan.get("zoom", 1.0))
    if plan.get("layout") in ("track", "split", "stack"):
        enlarged = src_h / zoom < 1200          # a 9:16 slice of a 1080p frame is blown up ~1.8x
    else:
        enlarged = src_h < 700
    return {"saturation": round(sat, 1), "contrast": round(con, 1), "dull": sat < 70 or con < 42, "enlarged": bool(enlarged)}


def video_filter(channel, needs):
    """ffmpeg filter for the gentle colour/sharpness fixes (None = leave the picture alone)."""
    parts, why = [], []
    cc, sh = channel.get("color_correction", "auto"), channel.get("sharpen", "auto")
    if cc is True or (cc == "auto" and needs.get("dull")):
        parts.append("eq=contrast=1.05:saturation=1.12:gamma=1.01")
        why.append("colour correction" + (" (the picture was flat)" if cc == "auto" else ""))
    if sh is True or (sh == "auto" and needs.get("enlarged")):
        parts.append("unsharp=5:5:0.45:5:5:0.0")
        why.append("light sharpening" + (" (the source is enlarged)" if sh == "auto" else ""))
    return (",".join(parts) or None), why


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
        zoom *= float(p.get("zoom", 1.0))
        cw = H * OUT_W / OUT_H / zoom
        ch = H / zoom
        cx = crop.camera_at(p, t) * W
        cy = (p.get("face_y", 0.45) + (0.06 if zoom > 1.05 else 0.0)) * H     # keep headroom when closer
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


def zoom_at(t_out, intervals, p, channel, punches=()):
    """Subtle movement, never constant: a gentle settle over the opening, alternate jump cuts a touch
    closer so they don't look like glitches, and short push-ins on emphasised words and speaker changes."""
    if p.get("layout") not in ("track", "fit", "scale"):
        return 1.0
    z = 1.0
    k_int = _intensity(channel)["zoom"]
    if channel.get("zooms", True):
        if t_out < 0.6:
            z += 0.08 * k_int * (1 - t_out / 0.6) ** 2
        acc = 0.0
        for k, (a, b) in enumerate(intervals):
            if t_out < acc + (b - a):
                if k % 2 == 1:
                    z += 0.05 * k_int
                break
            acc += b - a
    for t0, amount in punches:
        d = t_out - t0
        if 0 <= d < 1.2:
            z += amount * (min(1.0, d / 0.12) if d < 0.12 else max(0.0, 1 - (d - 0.12) / 1.08) ** 2)
    return z


def _intensity(channel):
    from clipper import presets
    return presets.INTENSITY.get(channel.get("edit_intensity") or "normal", presets.INTENSITY["normal"])


def plan_punches(ow, plan, channel, intervals, min_gap=None):
    """When to push in: emphasised (loud) words that are highlights, and speaker changes, at most one
    every few seconds and never in the first second (the opening already settles)."""
    out = []
    k_int = _intensity(channel)
    min_gap = k_int["punch_gap"] if min_gap is None else min_gap
    if channel.get("punch_ins", True):
        for w in ow:
            if w.get("emph") and w["s"] > 1.0:
                out.append((w["s"], 0.07 * k_int["zoom"]))
    if channel.get("transitions", True):
        for t in plan.get("shots") or []:
            to = to_output_time(float(t), [(a - intervals[0][0], b - intervals[0][0]) for a, b in intervals])
            if to is not None and to > 1.0:
                out.append((to, 0.05 * min(1.0, k_int["zoom"])))
    kept = []
    for t, a in sorted(out):
        if not kept or t - kept[-1][0] >= min_gap:
            kept.append((t, a))
    return kept


def _preset(settings_global):
    from clipper import selftune
    return selftune.preset(settings_global)


def pick_fps(settings_global, src_fps):
    want = settings_global.get("fps", "auto")
    if want not in ("auto", None, ""):
        return int(want)
    if 23.5 <= (src_fps or 0) <= 60.5:
        return int(round(src_fps))
    return 30


# ---------- the render ----------

def render(job_folder, clip, words, plan, source, channel, style, settings_global, progress=lambda f, d="": None,
           out_path=None):
    """Makes the clip file. source = (path, offset). clip may carry the editor's changes in
    clip["edits"] (word corrections). Returns a report dict for QA and the dashboard."""
    import cv2  # noqa: F401  (fails early with a clear error if OpenCV is missing)
    job_folder = Path(job_folder)
    src, offset = source
    info = media.probe(src)
    fps = pick_fps(settings_global, info.get("fps"))
    name = f"clip_{clip['id']}"
    edits = clip.get("edits") or {}
    fillers = bool(channel.get("remove_fillers", True))
    intervals = keep_intervals(words, clip["start"], clip["end"], channel.get("jump_cuts", True),
                               float(channel.get("max_gap", 0.55)), fillers=fillers)
    dur = out_duration(intervals)
    report = {"enhancements": []}
    if len(intervals) > 1:
        report["enhancements"].append(f"{len(intervals) - 1} jump cut{'s' if len(intervals) != 2 else ''} (dead air"
                                      + (" and fillers" if fillers else "") + " removed)")
    ow = output_words([w for w in words if clip["start"] - 0.05 <= w["s"] <= clip["end"]], intervals, channel.get("fixes"),
                      edits.get("words"))
    if fillers:
        ow = captions.drop_fillers(ow)
    punches = plan_punches(ow, plan, channel, intervals)
    if punches:
        report["enhancements"].append(f"{len(punches)} punch-in{'s' if len(punches) != 1 else ''}")
    hook_text = (clip.get("hook_text") or "") if channel.get("hook_overlay", True) else ""
    sfx = ([(0.05, "whoosh")] if hook_text else []) + [(t, "pop") for t, _ in punches]
    progress(0.02, "making the sound")
    audio = build_audio(src, offset, intervals, job_folder, name, channel, sfx_at=sfx, report=report)
    keys = captions.pick_keywords(ow, clip.get("highlight") or clip.get("keywords") or [])
    cap = captions.Captioner(ow, style, OUT_W, OUT_H, keys, hook_text=hook_text,
                             hook_color=channel.get("brand", {}).get("color", "#22d3ee"))
    lo, hi = intervals[0][0], intervals[-1][1]
    probe_frames = []                      # look at a few real frames before deciding on colour/sharpness fixes
    for i in (1, 2, 3):
        r = media.FrameReader(src, max(0.0, lo - offset + (hi - lo) * i / 4), 0.5, 2, max_h=360)
        try:
            f = r.read()
            if f is not None:
                probe_frames.append(f)
        finally:
            r.close()
    reader = media.FrameReader(src, max(0.0, lo - offset), hi - lo + 0.5, fps)
    needs = picture_needs(probe_frames, info.get("height") or reader.h, plan)
    vf, why = video_filter(channel, needs)
    report["enhancements"] += why
    report["picture"] = needs
    out = Path(out_path or job_folder / f"{name}.part.mp4")
    writer = media.FrameWriter(out, OUT_W, OUT_H, fps, audio, _preset(settings_global),
                               int(settings_global.get("crf", 20)), vf=vf)
    total = int(round(dur * fps))
    thumb = job_folder / f"{name}.jpg"
    cap_boxes, last = 0, None
    brand = channel.get("brand", {}).get("color", "#22d3ee")
    try:
        for k in range(total):
            t_out = k / fps
            t_src = to_source_time(t_out, intervals)
            frame = reader.seek_index(int(round((t_src - lo) * fps)))
            if frame is None:
                frame = last if last is not None else np.zeros((1080, 1920, 3), np.uint8)
            last = frame
            img = compose(frame, plan, t_src - clip["start"], zoom_at(t_out, intervals, plan, channel, punches), brand)
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
    return dict(report, path=str(out), thumb=str(thumb), duration=round(dur, 3), frames=total, fps=fps,
                intervals=intervals, cuts=len(intervals) - 1, layout=plan.get("layout"), caption_frames=cap_boxes,
                words=len(ow), words_captioned=len(cap.shown_words), caption_pages=len(cap.pages), style=style["name"],
                font_size=style["size"], words_per_line=style["words_per_line"], caption_overlaps=cap.overlaps,
                punches=[round(t, 2) for t, _ in punches])
