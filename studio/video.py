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
Optional upgrades (studio/engines.py, each with its own fallback): whisperX forced alignment for the word
timings, Manim animations for every scene after the hook, and Remotion for animated captions.
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
LOOP_BLEND = 0.35        # the last moments blend back into the first frame, so a replay is seamless
PUNCH = 0.06             # the first frame starts slightly zoomed and settles: motion from frame one
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


def _aligned(audio, words):
    """whisperX forced alignment of the known words: [{w, s, e}] for the words it could place."""
    from studio import engines
    with engines.heavy("whisperX alignment"):
        ans = engines.run("whisperx", "whisperx_worker", {"audio": str(audio), "words": words})
    return [w for w in ans.get("words") or [] if w.get("s") is not None]


def word_times(audio, text, align=None):
    """[{w, s, e}] for every script word (exact text), plus how they were timed. align "whisperx" lines
    the known script up with the audio (falls back to Whisper's transcript when that isn't possible)."""
    from studio import engines
    words = text.split()
    heard, method, note = [], "whisper matched to the script", None
    if (align or engines.settings()["align"]) == "whisperx":
        try:
            heard = _aligned(audio, words)
            method = "whisperX forced alignment"
        except Exception as e:
            note = f"whisperX wasn't used: {str(e)[:200]}"
            heard = []
    if not heard:
        method = "whisper matched to the script"
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
        return out, {"method": "even spread", "matched": round(ratio, 2), **({"note": note} if note else {})}
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
    return out, {"method": method, "matched": round(ratio, 2), **({"note": note} if note else {})}


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
    from studio import lessons
    learned = lessons.advice("video")
    learned = ("\n" + learned) if learned else ""
    prompt = f"""Plan the on-screen visual for each narrated line of a science Short. One scene per line, chosen from:
{tmpl}
Use only numbers from the line or these checked calculations:
{calcs or '- (none)'}
Keep text short (labels under 6 words, headline under 9). Prefer number/equation/compare/scale when the line
has numbers; use steps for a process; headline otherwise. Writer's visual notes are hints only.{learned}

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
    if out:
        out[0] = hook_scene(lines[0], out[0])
    return out


def hook_scene(hook, planned=None):
    """The first frame: readable in a glance. The hook's own number big on screen, else the hook itself."""
    if isinstance(planned, dict) and planned.get("type") == "number" and _valid(planned) and \
            str(planned.get("value", "")).replace(",", "") in hook.replace(",", ""):
        return planned
    return _fallback(hook)


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


ANIMATED = ("number", "equation", "compare", "scale", "steps", "headline")


def _manim_clips(scenes, starts, folder, pal, handle, bg):
    """{scene index: {"video", "hold", "seconds"}} from Manim, for every scene but the hook (whose first
    frame must already show its number). Scenes Manim couldn't make keep their still picture."""
    from studio import engines
    jobs_ = [{"spec": sc, "out": str(folder / f"manim_{i + 1:02d}.mp4"), "seconds": starts[i + 1] - starts[i]}
             for i, sc in enumerate(scenes) if i > 0 and sc.get("type") in ANIMATED]
    if not jobs_:
        return {}, "no scenes to animate"
    with engines.heavy("Manim animations"):
        ans = engines.run("manim", "manim_worker", {
            "scenes": jobs_, "palette": pal, "background": str(bg), "handle": handle, "box": BOX,
            "W": W, "H": H, "fps": FPS, "font": "Arial"})
    out = {}
    for j, c in zip(jobs_, ans.get("clips") or []):
        if c.get("ok") and Path(c["out"]).exists() and Path(c.get("hold", "")).exists():
            k = int(Path(c["out"]).stem.split("_")[1]) - 1
            out[k] = {"video": c["out"], "hold": c["hold"], "seconds": float(c.get("seconds") or 1.5)}
    return out, f"{len(out)} of {len(jobs_)} scenes animated"


class _Clip:
    """Reads one Manim clip frame by frame as the timeline moves forward (one clip open at a time, so
    memory stays flat on an 8 GB Mac)."""

    def __init__(self):
        self.k, self.cap, self.n, self.last = None, None, -1, None

    def frame(self, k, path, i):
        import cv2
        if k != self.k:
            if self.cap:
                self.cap.release()
            self.k, self.cap, self.n, self.last = k, cv2.VideoCapture(path), -1, None
        while self.n < i:
            ok, fr = self.cap.read()
            if not ok:
                break
            self.n += 1
            self.last = fr
        return self.last

    def close(self):
        if self.cap:
            self.cap.release()


def _caption_props(words, pal):
    """Caption pages for Remotion, grouped exactly like the classic captions."""
    from clipper import captions
    brand = {"color": pal["cyan"], "highlight": pal["amber"]}
    stl = captions.resolve("highlight", {"brand": brand}, {"y": CAPTION_Y})
    stl["y"] = CAPTION_Y
    left = captions.SAFE["left"]
    width = W - captions.SAFE["left"] - captions.SAFE["right"]
    pages = captions.pages(words, stl, width)
    keys = sorted(captions.pick_keywords(words, []))
    size, lines = 80, 2
    boxes = [(left, int(CAPTION_Y * H - size * lines * 0.62), width, int(size * lines * 1.24)) for _ in pages]
    return {"pages": [{"s": p["s"], "e": p["e"], "words": p["words"]} for p in pages], "keywords": keys,
            "caption": {"y": CAPTION_Y, "left": left, "width": width, "size": size, "maxLines": lines}}, boxes


def render(video_id, progress=lambda s: None, engines_override=None, out_name="short.mp4", record=True,
           voice_path=None):
    """Makes studio_data/videos/<id>/<out_name> from the passed script and narration. Returns a report.
    The upgraded tools (whisperX, Manim, Remotion; see studio/engines.py) are used when switched on and
    working; each falls back to the classic way on its own. record=False (a sample) changes nothing in
    the database."""
    import cv2
    from clipper import captions, media
    from studio import engines, script
    guard.allow("media.local", f"render video {video_id}", record=False)
    eng = engines.settings(engines_override)
    used = {}
    row = script.latest(video_id, "passed")
    voice = db.one("SELECT * FROM voiceovers WHERE video_id=? ORDER BY id DESC LIMIT 1", video_id)
    if not row or not (voice or voice_path):
        raise RuntimeError("the video needs a passed script and its narration first")
    src_voice = voice_path or voice["path"]
    folder = data_dir("videos", str(video_id))
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    st = json.loads(row["structure"])
    lines = [ln["text"] for ln in st["lines"]]
    lines[0] = row["hook"]
    progress("Timing every word")
    words, timing = word_times(src_voice, " ".join(lines), align=eng["align"])
    used["align"] = timing["method"] + (f" ({timing['note']})" if timing.get("note") else "")
    if "scenes" not in meta:
        progress("Planning the visuals")
        meta["scenes"] = plan(row)
        if record:
            db.set_video(video_id, meta=json.dumps(meta))
    scenes = meta["scenes"]
    pal = palette()
    handle = (db.one("SELECT handle FROM channel WHERE id=1") or {"handle": ""})["handle"] or ""
    progress("Drawing the scenes")
    imgs, boxes, stills = [], [], []
    if record:
        db.run("DELETE FROM assets WHERE video_id=? AND origin='generated'", video_id)
    for i, sc in enumerate(scenes):
        img, box = draw_scene(sc, pal, handle)
        p = folder / f"scene_{i + 1:02d}.png"
        img.save(p)
        stills.append(p)
        imgs.append(cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR))
        boxes.append(box)
        if record:
            db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", video_id, f"scene:{sc.get('type')}", str(p), "generated", None,
                      "original (drawn by Jarvis from shapes and text)", "", _sha(p), time.time())
    voice_path = src_voice
    lead = float(words[0]["s"] or 0) if words else 0.0
    if lead > 0.25:                                           # speech must start at once: trim the silence before it
        cut = round(lead - 0.08, 3)
        trimmed = folder / "voice_trim.wav"
        media.run(["-y", "-ss", f"{cut:.3f}", "-i", src_voice, "-ar", "48000", str(trimmed)])
        voice_path = str(trimmed)
        for w in words:
            w["s"], w["e"] = max(0.0, w["s"] - cut), max(0.0, w["e"] - cut)
    dur = media.probe(voice_path)["duration"]
    last_word = max((float(w["e"] or 0) for w in words), default=dur)
    speech_end = _speech_span(voice_path)[1]
    end = min(dur, max(last_word, speech_end) + 0.2) + 0.12   # no long silent tail: the replay starts right away
    starts = line_starts(lines, words) + [end]
    starts[0] = 0.0
    padded = folder / "voice_end.wav"                         # a short silent tail so the last scene can settle
    media.run(["-y", "-i", voice_path, "-af", f"apad=pad_dur=0.3,atrim=0:{end + 0.05:.3f},afade=t=out:st={max(0.0, end - 0.08):.3f}:d=0.08",
               "-ar", "48000", str(padded)])
    clips = {}
    if eng["visuals"] == "manim":
        progress("Animating the scenes (Manim)")
        try:
            bg = folder / "background.png"
            background(pal).save(bg)
            clips, used["visuals"] = _manim_clips(scenes, starts, folder, pal, handle, bg)
            used["visuals"] = "Manim: " + used["visuals"]
            for k, c in clips.items():
                if record:
                    db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, "
                              "created) VALUES(?,?,?,?,?,?,?,?,?)", video_id, f"animation:{scenes[k].get('type')}",
                              c["video"], "generated", None, "original (animated by Jarvis with Manim, MIT)", "",
                              _sha(c["video"]), time.time())
        except Exception as e:
            clips, used["visuals"] = {}, f"still pictures (Manim wasn't used: {str(e)[:200]})"
    else:
        used["visuals"] = "still pictures"
    holds = [cv2.imread(clips[k]["hold"]) if k in clips else imgs[k] for k in range(len(scenes))]
    out = folder / out_name
    tmp = folder / (Path(out_name).stem + ".tmp.mp4")
    rep = None
    if eng["compose"] == "remotion":
        progress("Composing with Remotion")
        try:
            cap_props, cap_boxes = _caption_props(words, pal)
            props = {"fps": FPS, "width": W, "height": H, "duration": round(end, 3), "audio": padded.name,
                     "words": [{"w": w["w"], "s": float(w["s"]), "e": float(w["e"])} for w in words],
                     "palette": {k: pal[k] for k in ("cyan", "amber", "text", "background")},
                     "fade": FADE, "punch": PUNCH, "loopBlend": LOOP_BLEND, **cap_props,
                     "scenes": [{"start": starts[k], "end": starts[k + 1], "image": stills[k].name,
                                 **({"video": Path(clips[k]["video"]).name, "videoSeconds": clips[k]["seconds"],
                                     "hold": Path(clips[k]["hold"]).name} if k in clips else {})}
                                for k in range(len(scenes))]}
            with engines.heavy("Remotion render"):
                engines.remotion_render(props, folder, tmp)
            tmp.replace(out)
            shown = {i for p in cap_props["pages"] for i in p["words"]}
            rep = {"caption_boxes": cap_boxes, "caption_words_shown": len(shown)}
            used["compose"] = "Remotion (animated captions)"
        except Exception as e:
            used["compose"] = f"classic (Remotion wasn't used: {str(e)[:200]})"
            rep = None
    if rep is None:
        if eng["compose"] != "remotion":
            used["compose"] = "classic"
        brand = {"color": pal["cyan"], "highlight": pal["amber"]}
        stl = captions.resolve("highlight", {"brand": brand}, {"y": CAPTION_Y})
        stl["y"] = CAPTION_Y
        cap = captions.Captioner([dict(w) for w in words], stl, W, H, captions.pick_keywords(words, []))
        wr = media.FrameWriter(tmp, W, H, FPS, padded)
        n = int(math.ceil(end * FPS))
        caption_boxes = []
        first = _zoom(imgs[0], 1.0 + PUNCH)                             # exactly the first frame
        clip = _Clip()
        progress("Rendering")
        try:
            for f in range(n):
                t = f / FPS
                k = max(i for i in range(len(scenes)) if starts[i] <= t or i == 0)
                span = max(0.5, starts[k + 1] - starts[k])
                z = 1.0 + 0.035 * min(1.0, (t - starts[k]) / span)
                if k == 0:                                              # punch-in on the hook: it settles in 0.4 s
                    z += PUNCH * max(0.0, 1.0 - t / 0.4)
                base = holds[k]
                if k in clips and t - starts[k] < clips[k]["seconds"] - 1.0 / FPS:
                    fr = clip.frame(k, clips[k]["video"], int((t - starts[k]) * FPS))
                    base = fr if fr is not None else holds[k]
                frame = _zoom(base, z)
                if k > 0 and t - starts[k] < FADE:                      # crossfade from the previous scene
                    a = (t - starts[k]) / FADE
                    prev = _zoom(holds[k - 1], 1.035)
                    frame = cv2.addWeighted(frame, a, prev, 1 - a, 0)
                if end - t < LOOP_BLEND and len(imgs) > 1:              # blend into the first frame for the loop
                    a = 1.0 - (end - t) / LOOP_BLEND
                    frame = cv2.addWeighted(first, a, frame, 1 - a, 0)
                frame = frame.copy()                                    # (no progress bar: it tells people it's ending)
                cb = cap.draw(frame, t)
                if cb:
                    caption_boxes.append(cb)
                wr.write(frame)
        finally:
            wr.close()
            clip.close()
        tmp.replace(out)
        rep = {"caption_boxes": caption_boxes[:: max(1, len(caption_boxes) // 40)],
               "caption_words_shown": len(cap.shown_words)}
    rep = {"path": str(out), "duration": round(end, 2), "frames": int(math.ceil(end * FPS)), "scenes": len(scenes),
           "timing": timing, "speech_start": round(float(words[0]["s"] or 0), 2) if words else None,
           "tail_seconds": round(end - max(last_word, speech_end), 2),
           "trimmed_lead": round(lead - 0.08, 3) if lead > 0.25 else 0.0,
           "scene_boxes": boxes, "words": len(words), "engines": used, **rep}
    if not record:
        return rep
    meta["render"] = rep
    db.set_video(video_id, path=str(out), duration=round(end, 2), meta=json.dumps(meta))
    media.save_jpeg(imgs[0], folder / "thumb.jpg", width=540)
    try:                                                      # the Short's own cover (Canva template layout)
        from studio import cover
        rep["cover"] = str(cover.make(video_id))
    except Exception as e:                                    # a cover problem never stops the video
        rep["cover_error"] = str(e)[:200]
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
