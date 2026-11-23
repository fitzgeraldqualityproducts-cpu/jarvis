"""Video production (Phase 7): timings, visual plan, original scenes, captions, render, metadata.

word_times()   when each script word is spoken: Whisper heard it, then matched to the KNOWN script (so
               captions are the exact words), falling back to an even spread over the speech if needed.
plan()         Claude turns each script line into one scene from a fixed set of templates (headline,
               number, equation, compare bars, scale circles, steps). Unknown or broken specs fall back
               to a headline, so the plan can never break the render.
draw_scene()   draws a scene at 1080x1920 from shapes and text only (original, nothing downloaded);
               every visual stays inside the area Shorts' buttons and title never cover.
render()       scenes with a slow push-in and short crossfades, a thin progress bar, word-synced
               captions (the clipper's caption engine) and the narration -> H.264/AAC MP4.
metadata()     title, description (sources + AI-narration note), tags, category and flags.
Every generated image is recorded in the assets table with its licence (original) and checksum."""
import hashlib
import json
import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from studio import config, data_dir, db, guard

W, H, FPS = 1080, 1920, 30
BOX = {"x0": 80, "x1": W - 150, "y0": 250, "y1": 1150}       # visuals; captions sit below, Shorts UI around
CAPTION_Y = 0.70
FADE = 0.25
TEMPLATES = {
    "headline": "big statement: {text, label?}",
    "number": "one huge number: {value, unit, label}",
    "equation": "a calculation: {left, right, label}  e.g. left '2,290 ÷ 2,970', right '0.77'",
    "compare": "2-4 horizontal bars: {items: [{label, value}], unit}",
    "scale": "2-3 circles sized by area: {items: [{label, value}], unit}",
    "steps": "a short numbered list, one item lit: {items: [2-4 short strings], current}",
}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def palette():
    from studio import branding
    b = db.get("brand")
    b = json.loads(b) if isinstance(b, str) else (b or {})
    return {**branding.PALETTE, **(b.get("palette") or {})}


# ---------- timings ----------

def _norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


TRANSCRIBE = None     # tests swap this: audio_path -> [{w, s, e}]


def _heard(audio, text):
    if TRANSCRIBE:
        return TRANSCRIBE(audio)
    from clipper import transcribe
    r = transcribe.transcribe(str(audio), prompt=text[:200])
    return r.get("words") if isinstance(r, dict) else r


def _speech_span(audio):
    from clipper import media
    x, rate = media.read_wav(audio)
    x = x.mean(axis=1) if x.ndim > 1 else x
    hop = int(rate * 0.02)
    rms = np.sqrt(np.array([np.mean(x[i:i + hop] ** 2) for i in range(0, max(len(x) - hop, 1), hop)]) + 1e-12)
    on = np.where(rms > max(rms.max() * 0.05, 1e-4))[0]
    dur = len(x) / rate
    if not len(on):
        return 0.0, dur, dur
    return on[0] * 0.02, min(dur, (on[-1] + 1) * 0.02), dur


def word_times(audio, text):
    """[{w, s, e}] for every script word (exact text), plus how they were timed."""
    words = text.split()
    heard = []
    try:
        heard = [w for w in (_heard(audio, text) or []) if str(w.get("w", "")).strip()]
    except Exception:
        heard = []
    out = [{"w": w, "s": None, "e": None} for w in words]
    matched = 0
    if heard:
        a, b = [_norm(w) for w in words], [_norm(h["w"]) for h in heard]
        for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                out[blk.a + k]["s"], out[blk.a + k]["e"] = float(heard[blk.b + k]["s"]), float(heard[blk.b + k]["e"])
                matched += 1
    ratio = matched / len(words) if words else 0.0
    if ratio < 0.5:                                   # not enough to trust: spread evenly over the speech
        start, end, _ = _speech_span(audio)
        total = sum(len(w) + 2 for w in words) or 1
        t = start
        for o in out:
            d = (end - start) * (len(o["w"]) + 2) / total
            o["s"], o["e"] = round(t, 3), round(t + d * 0.92, 3)
            t += d
        return out, {"method": "even spread", "matched": round(ratio, 2)}
    known = [i for i, o in enumerate(out) if o["s"] is not None]
    for i, o in enumerate(out):                        # fill gaps between matched words
        if o["s"] is not None:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        lo = out[prev]["e"] if prev is not None else 0.0
        hi = out[nxt]["s"] if nxt is not None else lo + 0.4 * (i - (prev or 0) + 1)
        n = (nxt if nxt is not None else len(out)) - (prev if prev is not None else -1) - 1
        pos = i - (prev if prev is not None else -1)
        step = (hi - lo) / (n + 1)
        o["s"], o["e"] = round(lo + step * (pos - 0.5), 3), round(lo + step * (pos + 0.4), 3)
    for i in range(1, len(out)):                       # keep it monotonic
        if out[i]["s"] < out[i - 1]["s"]:
            out[i]["s"] = out[i - 1]["s"]
        out[i]["e"] = max(out[i]["e"], out[i]["s"] + 0.05)
    return out, {"method": "whisper matched to the script", "matched": round(ratio, 2)}


def line_starts(lines, words):
    """Start time of each script line from the word timings (lines' words are in order)."""
    starts, k = [], 0
    for ln in lines:
        starts.append(words[min(k, len(words) - 1)]["s"] if words else 0.0)
        k += len(str(ln).split())
    return starts


# ---------- the visual plan ----------

def _fallback(line):
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", line)
    if nums:
        unit = re.search(re.escape(nums[0]) + r"\s*([A-Za-z%°]+)", line)
        return {"type": "number", "value": nums[0], "unit": unit.group(1) if unit else "",
                "label": " ".join(line.split()[:8])}
    return {"type": "headline", "text": " ".join(line.split()[:9])}


def _valid(s):
    t = s.get("type") if isinstance(s, dict) else None
    try:
        if t == "headline":
            return bool(str(s["text"]).strip())
        if t == "number":
            return bool(str(s["value"]).strip())
        if t == "equation":
            return bool(str(s["left"]).strip()) and bool(str(s["right"]).strip())
        if t in ("compare", "scale"):
            items = s["items"]
            return 2 <= len(items) <= 4 and all(float(str(i["value"]).replace(",", "")) > 0 for i in items)
        if t == "steps":
            return 2 <= len(s["items"]) <= 4
    except (KeyError, TypeError, ValueError):
        return False
    return False


def plan(script_row):
    """One scene per line: [{"type", ...}] (Claude picks the template and fills it from the line)."""
    from studio import claude
    st = json.loads(script_row["structure"])
    lines = [ln["text"] for ln in st["lines"]]
    lines[0] = script_row["hook"]
    calcs = "\n".join(f"- {c.get('expr')} = {c.get('actual') or c.get('result')} {c.get('unit', '')}: {c.get('says', '')}"
                      for c in st.get("calcs") or [])
    tmpl = "\n".join(f"- {k}: {v}" for k, v in TEMPLATES.items())
    prompt = f"""Plan the on-screen visual for each narrated line of a science Short. One scene per line, chosen from:
{tmpl}
Use only numbers from the line or these checked calculations:
{calcs or '- (none)'}
Keep text short (labels under 6 words, headline under 9). Prefer number/equation/compare/scale when the line
has numbers; use steps for a process; headline otherwise. Writer's visual notes are hints only.

Lines:
""" + "\n".join(f"{i + 1}. {t}  (note: {st['lines'][i].get('visual', '')})" for i, t in enumerate(lines)) + \
        '\n\nJSON array with exactly one object per line, in order, e.g. [{"type": "headline", "text": "..."}]'
    scenes = []
    try:
        ans = claude.ask_json(prompt, timeout=300, web=False, purpose="visual plan")
        scenes = ans if isinstance(ans, list) else []
    except Exception:
        scenes = []
    out = []
    for i, line in enumerate(lines):
        s = scenes[i] if i < len(scenes) else None
        out.append(s if _valid(s) else _fallback(line))
    return out


# ---------- drawing ----------

def _font(size, bold=True):
    from studio.toolchain import _font as f
    return f(size, bold)


def _wrap(d, text, f, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and d.textlength(t, font=f) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _fit(d, text, size, width, max_lines, bold=True, floor=40):
    while True:
        f = _font(size, bold)
        lines = _wrap(d, text, f, width)
        if (len(lines) <= max_lines and all(d.textlength(ln, font=f) <= width for ln in lines)) or size <= floor:
            return f, lines[:max_lines], size
        size -= 6


def background(pal):
    from PIL import Image, ImageDraw
    y = np.linspace(0, 1, H)[:, None]
    top, bottom = np.array(_rgb(pal["background"])), np.array(_rgb(pal["panel"]))
    grad = (top * (1 - y) + bottom * y).astype("uint8")
    img = Image.fromarray(np.repeat(grad[:, None, :], W, axis=1), "RGB")
    d = ImageDraw.Draw(img)
    grid = tuple(min(255, c + 10) for c in _rgb(pal["background"]))
    for x in range(0, W, 90):
        d.line([x, 0, x, H], fill=grid, width=1)
    for yy in range(0, H, 90):
        d.line([0, yy, W, yy], fill=grid, width=1)
    return img


def _num(v):
    return float(str(v).replace(",", ""))


def _fmtnum(v):
    v = _num(v)
    if v >= 100 or v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def draw_scene(scene, pal, handle=""):
    """The scene as a PIL RGB image (1080x1920). Returns (image, bounding box of what was drawn)."""
    from PIL import ImageDraw
    img = background(pal)
    d = ImageDraw.Draw(img)
    text, muted, cyan, amber = _rgb(pal["text"]), _rgb(pal["muted"]), _rgb(pal["cyan"]), _rgb(pal["amber"])
    x0, x1, y0, y1 = BOX["x0"], BOX["x1"], BOX["y0"], BOX["y1"]
    width = x1 - x0
    drawn = []

    def put(xy, s, f, fill, center=True):
        w = d.textlength(s, font=f)
        x = x0 + (width - w) / 2 if center else xy[0]
        d.text((x, xy[1]), s, font=f, fill=fill)
        bb = d.textbbox((x, xy[1]), s, font=f)
        drawn.append(bb)
        return bb

    t = scene.get("type")
    if t == "number":
        f, lines, _ = _fit(d, str(scene["value"]), 230, width, 1)
        bb = put((0, y0 + 220), lines[0], f, amber)
        if scene.get("unit"):
            put((0, bb[3] + 30), str(scene["unit"])[:20], _font(80), cyan)
        if scene.get("label"):
            fl, ll, _ = _fit(d, str(scene["label"]), 60, width, 3, bold=False)
            yy = y0 + 640
            for ln in ll:
                put((0, yy), ln, fl, muted)
                yy += fl.size + 14
    elif t == "equation":
        fl, ll, _ = _fit(d, str(scene["left"]), 110, width, 2)
        yy = y0 + 160
        for ln in ll:
            bb = put((0, yy), ln, fl, text)
            yy = bb[3] + 20
        d.line([x0 + 120, yy + 30, x1 - 120, yy + 30], fill=cyan, width=6)
        fr, lr, _ = _fit(d, "= " + str(scene["right"]), 170, width, 1)
        bb = put((0, yy + 70), lr[0], fr, amber)
        if scene.get("label"):
            fb, lb, _ = _fit(d, str(scene["label"]), 56, width, 2, bold=False)
            yy = bb[3] + 60
            for ln in lb:
                put((0, yy), ln, fb, muted)
                yy += fb.size + 12
    elif t == "compare":
        items = scene["items"][:4]
        vals = [_num(i["value"]) for i in items]
        top = max(vals)
        n = len(items)
        row = (y1 - y0 - 100) / n
        fl = _font(50)
        for k, (it, v) in enumerate(zip(items, vals)):
            yy = y0 + 60 + k * row
            put((x0, yy), str(it["label"])[:28], fl, text, center=False)
            bw = max(12, (width - 10) * v / top)
            d.rounded_rectangle([x0, yy + 70, x0 + bw, yy + 70 + min(90, row - 120)], radius=14,
                                fill=amber if v == top else cyan)
            drawn.append((x0, yy + 70, x0 + bw, yy + 70 + min(90, row - 120)))
            put((x0, yy + 80 + min(90, row - 120)), f"{_fmtnum(v)} {scene.get('unit', '')}".strip(), _font(42, False), muted,
                center=False)
    elif t == "scale":
        items = scene["items"][:3]
        vals = [_num(i["value"]) for i in items]
        rmax = min(250, width / (2 * len(items)) - 10)
        top = max(vals)
        cx_step = width / len(items)
        cy = y0 + 380
        for k, (it, v) in enumerate(zip(items, vals)):
            r = max(6, rmax * math.sqrt(v / top))
            cx = x0 + cx_step * (k + 0.5)
            d.ellipse([cx - r, cy + rmax - 2 * r, cx + r, cy + rmax], outline=amber if v == top else cyan, width=8)
            drawn.append((cx - r, cy + rmax - 2 * r, cx + r, cy + rmax))
            for j, s in enumerate((str(it["label"])[:16], f"{_fmtnum(v)} {scene.get('unit', '')}".strip())):
                f = _font(44 if j == 0 else 40, j == 0)
                w = d.textlength(s, font=f)
                d.text((cx - w / 2, cy + rmax + 40 + j * 56), s, font=f, fill=text if j == 0 else muted)
                drawn.append(d.textbbox((cx - w / 2, cy + rmax + 40 + j * 56), s, font=f))
    elif t == "steps":
        items = [str(i) for i in scene["items"][:4]]
        cur = int(scene.get("current") or 0) if str(scene.get("current", "0")).isdigit() else 0
        yy = y0 + 80
        for k, s in enumerate(items):
            on = k == cur
            d.ellipse([x0, yy, x0 + 90, yy + 90], fill=amber if on else _rgb(pal["panel"]), outline=cyan, width=4)
            f = _font(52)
            n = str(k + 1)
            d.text((x0 + 45 - d.textlength(n, font=f) / 2, yy + 14), n, font=f, fill=_rgb(pal["background"]) if on else text)
            fs, ls, _ = _fit(d, s, 54, width - 130, 2, bold=on)
            for j, ln in enumerate(ls):
                d.text((x0 + 130, yy + 10 + j * 64), ln, font=fs, fill=text if on else muted)
                drawn.append(d.textbbox((x0 + 130, yy + 10 + j * 64), ln, font=fs))
            drawn.append((x0, yy, x0 + 90, yy + 90))
            yy += 200
    else:
        f, lines, _ = _fit(d, str(scene.get("text") or ""), 96, width, 5)
        yy = y0 + max(80, (y1 - y0 - len(lines) * (f.size + 20)) / 2 - 40)
        for ln in lines:
            bb = put((0, yy), ln, f, text)
            yy = bb[3] + 20
        if scene.get("label"):
            put((0, yy + 30), str(scene["label"])[:40], _font(52, False), cyan)
    if handle:
        f = _font(36, False)
        s = "@" + handle.lstrip("@")
        d.text(((W - d.textlength(s, font=f)) / 2, 150), s, font=f, fill=muted)
    if drawn:
        box = (min(b[0] for b in drawn), min(b[1] for b in drawn), max(b[2] for b in drawn), max(b[3] for b in drawn))
    else:
        box = (0, 0, 0, 0)
    return img, box


def inside_safe_area(box):
    return box[0] >= BOX["x0"] - 2 and box[2] <= BOX["x1"] + 2 and box[1] >= BOX["y0"] - 2 and box[3] <= BOX["y1"] + 2


# ---------- rendering ----------

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zoom(frame, z):
    import cv2
    if z <= 1.0001:
        return frame
    m = cv2.getRotationMatrix2D((W / 2, H * 0.4), 0, z)
    return cv2.warpAffine(frame, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def render(video_id, progress=lambda s: None):
    """Makes studio_data/videos/<id>/short.mp4 from the passed script and narration. Returns a report."""
    import cv2
    from clipper import captions, media
    from studio import script
    guard.allow("media.local", f"render video {video_id}", record=False)
    row = script.latest(video_id, "passed")
    voice = db.one("SELECT * FROM voiceovers WHERE video_id=? ORDER BY id DESC LIMIT 1", video_id)
    if not row or not voice:
        raise RuntimeError("the video needs a passed script and its narration first")
    folder = data_dir("videos", str(video_id))
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    st = json.loads(row["structure"])
    lines = [ln["text"] for ln in st["lines"]]
    lines[0] = row["hook"]
    progress("Timing every word")
    words, timing = word_times(voice["path"], " ".join(lines))
    if "scenes" not in meta:
        progress("Planning the visuals")
        meta["scenes"] = plan(row)
        db.set_video(video_id, meta=json.dumps(meta))
    scenes = meta["scenes"]
    pal = palette()
    handle = (db.one("SELECT handle FROM channel WHERE id=1") or {"handle": ""})["handle"] or ""
    progress("Drawing the scenes")
    imgs, boxes = [], []
    db.run("DELETE FROM assets WHERE video_id=? AND origin='generated'", video_id)
    for i, sc in enumerate(scenes):
        img, box = draw_scene(sc, pal, handle)
        p = folder / f"scene_{i + 1:02d}.png"
        img.save(p)
        imgs.append(cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR))
        boxes.append(box)
        db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, created) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", video_id, f"scene:{sc.get('type')}", str(p), "generated", None,
                  "original (drawn by Jarvis from shapes and text)", "", _sha(p), time.time())
    dur = media.probe(voice["path"])["duration"]
    starts = line_starts(lines, words) + [dur + 0.4]
    starts[0] = 0.0
    end = dur + 0.4
    brand = {"color": pal["cyan"], "highlight": pal["amber"]}
    stl = captions.resolve("highlight", {"brand": brand}, {"y": CAPTION_Y})
    stl["y"] = CAPTION_Y
    cap = captions.Captioner([dict(w) for w in words], stl, W, H, captions.pick_keywords(words, []))
    out = folder / "short.mp4"
    tmp = folder / "short.tmp.mp4"
    padded = folder / "voice_end.wav"                         # a short silent tail so the last scene can settle
    media.run(["-y", "-i", voice["path"], "-af", f"apad=pad_dur={end - dur + 0.2:.2f}", "-ar", "48000", str(padded)])
    wr = media.FrameWriter(tmp, W, H, FPS, padded)
    n = int(math.ceil(end * FPS))
    cyan = np.array(_rgb(pal["cyan"])[::-1], dtype=np.uint8)
    caption_boxes = []
    progress("Rendering")
    try:
        for f in range(n):
            t = f / FPS
            k = max(i for i in range(len(scenes)) if starts[i] <= t or i == 0)
            span = max(0.5, starts[k + 1] - starts[k])
            z = 1.0 + 0.035 * min(1.0, (t - starts[k]) / span)
            frame = _zoom(imgs[k], z)
            if k > 0 and t - starts[k] < FADE:                      # crossfade from the previous scene
                a = (t - starts[k]) / FADE
                prev = _zoom(imgs[k - 1], 1.035)
                frame = cv2.addWeighted(frame, a, prev, 1 - a, 0)
            frame = frame.copy()
            frame[96:104, : int(W * min(1.0, t / end))] = cyan       # progress bar
            cb = cap.draw(frame, t)
            if cb:
                caption_boxes.append(cb)
            wr.write(frame)
    finally:
        wr.close()
    tmp.replace(out)
    rep = {"path": str(out), "duration": round(end, 2), "frames": n, "scenes": len(scenes), "timing": timing,
           "scene_boxes": boxes, "caption_boxes": caption_boxes[:: max(1, len(caption_boxes) // 40)],
           "caption_words_shown": len(cap.shown_words), "words": len(words)}
    meta["render"] = rep
    db.set_video(video_id, path=str(out), duration=round(end, 2), meta=json.dumps(meta))
    media.save_jpeg(imgs[0], folder / "thumb.jpg", width=540)
    return rep


# ---------- metadata ----------

def metadata(video_id):
    """Title, description, tags and flags for upload (saved in videos.meta["upload"])."""
    from studio import script, topics
    v = db.video(video_id)
    row = script.latest(video_id, "passed")
    st = json.loads(row["structure"])
    t = topics.topic(v["topic_id"]) if v["topic_id"] else {"title": v["title"], "pillar": "", "keywords": "[]"}
    titles = [x for x in (st.get("titles") or []) + [t["title"]] if x and not script.hook_checks_title(x)]
    title = (titles[0] if titles else t["title"])[:100]
    used = db.q("SELECT DISTINCT url FROM sources WHERE video_id=? AND verdict='supported' AND url IS NOT NULL", video_id)
    srcs = [r["url"] for r in used] or [s["url"] for s in (t.get("sources") or []) if s.get("verdict") == "reachable"]
    kws = [k for k in json.loads(t.get("keywords") or "[]") if isinstance(k, str)][:8]
    tags_hash = ["#Shorts"] + ["#" + re.sub(r"[^A-Za-z0-9]", "", k.title()) for k in kws[:2] if k.strip()]
    first = " ".join(json.loads(row["structure"])["lines"][1]["text"].split()[:30]) if len(st["lines"]) > 1 else ""
    desc = (f"{title}\n\n{first}\n\nSources:\n" + "\n".join(f"- {u}" for u in srcs[:6]) +
            "\n\nEvery number here is worked out from the sources above. Narration is AI-generated (a synthetic voice); "
            "the script was written and fact-checked with AI and checked against the sources.\n\n" + " ".join(tags_hash))
    up = {"title": title, "description": desc[:4900], "tags": kws, "categoryId": "27", "defaultLanguage": "en",
          "defaultAudioLanguage": "en-US", "madeForKids": False, "containsSyntheticMedia": False,
          "synthetic_reason": "No realistic people, places or events are shown: original diagrams and a generic "
                              "synthetic narrator voice (disclosed in the description).",
          "sources": srcs}
    meta = json.loads(v["meta"] or "{}")
    meta["upload"] = up
    db.set_video(video_id, title=title, meta=json.dumps(meta))
    (data_dir("videos", str(video_id)) / "upload.json").write_text(json.dumps(up, indent=2))
    return up
