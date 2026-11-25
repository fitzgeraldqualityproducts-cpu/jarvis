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
    "wave": "a standing wave with its peaks marked: {label, spot, span, value}  spot = what a peak is "
            "(e.g. 'hot spot'), span 'gap' (between two neighbouring peaks = half a wavelength) or 'wavelength' "
            "(a full wave = two gaps), value = that distance with its unit, e.g. '6.1 cm' (or '' if unknown)",
    "measure": "a ruler between two things with the distance: {from, to, value, unit, label}  e.g. from 'hot "
               "spot', to 'next hot spot', value '6.1', unit 'cm'",
}


NEW_TEMPLATES = ("wave", "measure")      # offered to the planner only with config "shots" on (see art.shots_on)


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
        if t == "wave":
            return str(s.get("span") or "gap") in ("gap", "wavelength")
        if t == "measure":
            return bool(str(s["value"]).strip()) and bool(str(s.get("from") or "").strip())
    except (KeyError, TypeError, ValueError):
        return False
    return False


def storyboard_on():
    """Config "storyboard" (on): the visuals are planned as one story with a main subject running through."""
    try:
        return config.general().get("storyboard", True) is not False
    except Exception:
        return True


def plan(script_row):
    """One scene per line: [{"type", ...}] (Claude picks the template and fills it from the line)."""
    from studio import claude
    st = json.loads(script_row["structure"])
    lines = [ln["text"] for ln in st["lines"]]
    lines[0] = script_row["hook"]
    calcs = "\n".join(f"- {c.get('expr')} = {c.get('actual') or c.get('result')} {c.get('unit', '')}: {c.get('says', '')}"
                      for c in st.get("calcs") or [])
    from studio import art as _art
    tmpl = "\n".join(f"- {k}: {v}" for k, v in TEMPLATES.items()
                     if k not in NEW_TEMPLATES or _art.shots_on())    # the new diagrams wait for Connor's OK
    from studio import lessons
    learned = lessons.advice("video")
    learned = ("\n" + learned) if learned else ""
    story = ""
    if storyboard_on():
        story = """
Plan it as ONE story, not line by line. First pick the Short's through-line: one real, filmable main subject
(e.g. "the Moon", "a falling raindrop") that every scene's "art" keeps showing, and decide how each scene moves
the story on from the one before (closer, further, before/after, inside/outside, a different angle on the
same subject). A scene's picture must show what its own line is about, using the main subject wherever the line
allows. Never a loose mood picture (a sunset for a line about the Moon's temperature)."""
    board = ""
    if storyboard_on():
        board = """
For every scene also give the storyboard: "purpose" (what this moment does for the story, under 10 words, e.g.
"hook: show the puzzle", "explain: why waves add up", "payoff: reveal the answer"), "shots": 1-3 shots that
change what the viewer sees about every 1.5-2 seconds of narration (a line of under 6 words gets 1 shot, 6-11
words 2, longer 3), each {"art": 5-12 words for the picture, "camera": one of push-in, pull-out, pan-left,
pan-right, close-up, tilt-up, "shows": what it shows, under 8 words}. Shots in one scene are the same moment from
a new angle or closer, never a different subject. "transition" into the scene: cut, fade or zoom. "sfx": whoosh,
pop, tick, rise or none (only where it marks something: a reveal, a number landing, a new idea).
Honesty: AI pictures show places and objects. Never a staged "experiment result", a measurement reading, a
chart or a lab photo, as if it were recorded evidence. A demonstration or measurement is drawn as a diagram
(the scene "type"), never faked as a photo."""
    prompt = f"""Plan the on-screen visual for each narrated line of a science Short.{story}{board}
One scene per line, chosen from:
{tmpl}
Use only numbers from the line or these checked calculations:
{calcs or '- (none)'}
Keep text short (labels under 6 words, headline under 9). Prefer number/equation/compare/scale when the line
has numbers; use steps for a process; headline otherwise. Writer's visual notes are hints only.
Also give every scene "art": 5-12 words describing a picture of the scene's real-world subject as a place or
objects seen from a person's point of view (e.g. "a crowded beach at Waikiki seen from the sand"); no text,
numbers, diagrams, faces or famous people. The picture maker cannot write: never ask for a sign, label, screen,
gauge, map or anything that only works with writing on it (a line about "a speed-limit sign" gets the road or
the sky it stands for, and the number comes from the scene type). It also doesn't know specific named machines
(an experimental jet, a probe): describe one by its plain visible shape (e.g. "a long needle-nosed white jet"),
seen whole and from a distance, never a close-up of its surface. And "footage": a 1-3 word stock-video search (e.g. "rocket launch",
"ocean waves", "city traffic") when the line's subject is a real thing a camera could film, else "".{learned}

Lines:
""" + "\n".join(f"{i + 1}. {t}  (note: {st['lines'][i].get('visual', '')})" for i, t in enumerate(lines)) + \
        ('\n\nJSON object: {"subject": "the main subject", "story": "what the viewer sees from start to finish, in '
         'two sentences", "scenes": [exactly one object per line, in order, e.g. {"type": "headline", "text": "...", '
         '"art": "...", "footage": "...", "purpose": "...", "shots": [{"art": "...", "camera": "push-in", '
         '"shows": "..."}], "transition": "cut", "sfx": "none"}]}' if story else
         '\n\nJSON array with exactly one object per line, in order, e.g. [{"type": "headline", "text": "..."}]')
    scenes = []
    try:
        ans = claude.ask_json(prompt, timeout=300, web=False, purpose="visual plan")
        if isinstance(ans, dict):
            scenes = ans.get("scenes") if isinstance(ans.get("scenes"), list) else []
        else:
            scenes = ans if isinstance(ans, list) else []
    except Exception:
        scenes = []
    out = []
    for i, line in enumerate(lines):
        s = scenes[i] if i < len(scenes) else None
        out.append(s if _valid(s) else _fallback(line))
    if out:
        out[0] = hook_scene(lines[0], out[0])
    for i, sc in enumerate(out):                   # the picture idea survives a replaced or fixed-up scene
        src = scenes[i] if i < len(scenes) and isinstance(scenes[i], dict) else {}
        art = src.get("art")
        if art and not sc.get("art"):
            out[i] = sc = dict(sc, art=str(art)[:160])
        fq = " ".join(re.sub(r"[^A-Za-z0-9 '-]", " ", str(src.get("footage") or "")).split()[:4])
        if fq and not sc.get("footage"):
            out[i] = sc = dict(sc, footage=fq[:40])
        out[i] = board_fields(out[i], src, lines[i])
    return out


CAMERAS = ("push-in", "pull-out", "pan-left", "pan-right", "close-up", "tilt-up")
TRANSITIONS = ("cut", "fade", "zoom")
SFX = ("whoosh", "pop", "tick", "rise", "none")
WORDS_PER_SHOT = 5.5           # about 2 seconds of narration at the channel's pace


def shots_for(line):
    n = len(str(line).split())
    return max(1, min(3, int(math.ceil(n / WORDS_PER_SHOT)) if n >= 6 else 1))


def board_fields(sc, src, line):
    """The storyboard fields of one scene, checked: purpose, 1-3 shots (art, camera, shows), transition, sfx.
    Missing or odd values get a safe default, so an older plan still renders exactly as before."""
    src = src if isinstance(src, dict) else {}
    sc = dict(sc)
    sc["purpose"] = " ".join(str(src.get("purpose") or sc.get("purpose") or "").split())[:80]
    shots = []
    for sh in (src.get("shots") or sc.get("shots") or [])[:3]:
        if isinstance(sh, dict) and str(sh.get("art") or "").strip():
            cam = str(sh.get("camera") or "").lower()
            shots.append({"art": " ".join(str(sh["art"]).split())[:160],
                          "camera": cam if cam in CAMERAS else CAMERAS[len(shots) % 2],
                          "shows": " ".join(str(sh.get("shows") or "").split())[:60]})
    want = shots_for(line)
    if shots:
        shots = shots[:max(1, want)]
        if sc.get("art"):
            shots[0]["art"] = sc["art"]                   # the scene's own picture opens it
    if shots:
        sc["shots"] = shots
    else:
        sc.pop("shots", None)
    tr = str(src.get("transition") or sc.get("transition") or "").lower()
    sc["transition"] = tr if tr in TRANSITIONS else "cut"
    fx = str(src.get("sfx") or sc.get("sfx") or "").lower()
    sc["sfx"] = fx if fx in SFX else "whoosh"          # the usual whoosh on the cut, as before storyboards
    return sc


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


def draw_scene(scene, pal, handle="", bg=None):
    """The scene as a PIL RGB image (1080x1920). Returns (image, bounding box of what was drawn). bg: an AI
    picture to draw on (see studio/art.py) instead of the plain background."""
    from PIL import ImageDraw
    img = bg.copy() if bg is not None else background(pal)
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
    elif t == "wave":
        from studio import edit
        span = "wavelength" if str(scene.get("span") or "").lower().startswith("wave") else "gap"
        geo = edit.wave_geometry(width, edit.WAVE_PEAKS, span)
        mid, amp = y0 + 420, 150
        for sign, col in ((1, cyan), (-1, _rgb(pal["muted"]))):      # the two extremes of the standing wave
            pts = [(x0 + x, mid - sign * amp * math.sin(math.pi * x / (width / edit.WAVE_PEAKS)))
                   for x in range(0, width + 1, 6)]
            d.line(pts, fill=col, width=7)
        for x in geo["peaks"]:
            d.ellipse([x0 + x - 22, mid - amp - 22, x0 + x + 22, mid - amp + 22], fill=amber)
        ya = mid + amp + 70
        d.line([(x0 + geo["a"], ya), (x0 + geo["b"], ya)], fill=amber, width=6)
        for x in (geo["a"], geo["b"]):
            d.line([(x0 + x, ya - 24), (x0 + x, ya + 24)], fill=amber, width=6)
        drawn.append((x0, mid - amp - 22, x1, ya + 24))
        if scene.get("value"):
            put((0, ya + 40), str(scene["value"])[:16], _font(72), amber)
        if scene.get("label"):
            put((0, y0 + 80), str(scene["label"])[:40], _font(56, False), text)
    elif t == "measure":
        ya = y0 + 470
        d.line([(x0 + 40, ya), (x1 - 40, ya)], fill=amber, width=8)
        for x in (x0 + 40, x1 - 40):
            d.line([(x, ya - 40), (x, ya + 40)], fill=amber, width=8)
        drawn.append((x0 + 40, ya - 40, x1 - 40, ya + 40))
        f = _font(46, False)
        d.text((x0 + 40, ya + 60), str(scene.get("from") or "")[:18], font=f, fill=muted)
        drawn.append(d.textbbox((x0 + 40, ya + 60), str(scene.get("from") or "")[:18], font=f))
        to = str(scene.get("to") or "")[:18]
        d.text((x1 - 40 - d.textlength(to, font=f), ya + 60), to, font=f, fill=muted)
        put((0, ya - 200), f"{scene['value']} {scene.get('unit') or ''}".strip()[:20], _font(120), amber)
        if scene.get("label"):
            put((0, y0 + 80), str(scene["label"])[:40], _font(56, False), text)
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


# ---------- the cinematic look (Connor's reference style) ----------
# A full-screen picture of the subject for every scene (slow push-in), a small counter in the top left (an
# all-caps label, a big serif number that counts, an all-caps line under it) that stays on screen and moves
# on with the story, the hook as a serif question at the top of the first scene, and one calm italic serif
# sentence at a time as the caption. The numbers and words are drawn by Jarvis, never by the image model.
HUD_X, HUD_Y = BOX["x0"] + 10, BOX["y0"] + 20
TITLE_TOP = 650                  # the hook question on the first scene (same in studio/remotion/src/Short.tsx)
LINE_CAPTION = {"y": 0.66, "left": 80, "width": W - 80 - 150, "size": 54, "lineHeight": 1.25}
SERIF = {False: ["C:/Windows/Fonts/georgia.ttf", "/System/Library/Fonts/Supplemental/Georgia.ttf",
                 "/Library/Fonts/Georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"],
         True: ["C:/Windows/Fonts/georgiai.ttf", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
                "/Library/Fonts/Georgia Italic.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"]}


# the bold type system (config "type_style": "bold"; "serif" is the reference look and stays the default until
# Connor approves a sample): a heavy sans-serif for the hook, counter, captions and labels, 1-2 short lines of
# caption at a time with key words and numbers in the accent colour, the payoff line as a bigger reveal
BLACK = ["C:/Windows/Fonts/ariblk.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
         "/Library/Fonts/Arial Black.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
BOLD_CAPTION = {"y": 0.66, "left": 80, "width": W - 80 - 150, "size": 66, "lineHeight": 1.15}
BOLD_WORDS = 6           # at most this many words on screen at once (1-2 lines)


def type_style():
    try:
        return "bold" if str(config.general().get("type_style", "serif")).lower() == "bold" else "serif"
    except Exception:
        return "serif"


def _black(size):
    from PIL import ImageFont
    for n in BLACK:
        if Path(n).exists():
            return ImageFont.truetype(n, size)
    return _font(size, True)


def _even_groups(d, f, words, idx):
    """idx split into the fewest near-equal groups of at most BOLD_WORDS that fit two caption lines, each break
    moved by a word where that lands it right after punctuation (so a phrase stays together)."""
    if not idx:
        return []
    for n in range(max(1, math.ceil(len(idx) / BOLD_WORDS)), len(idx) + 1):
        cuts, prev = [], 0
        for c in range(1, n):
            b = round(c * len(idx) / n)
            for cand in (b, b - 1, b + 1):
                if prev < cand < len(idx) and re.search(r"[.,;:!?]$", words[idx[cand - 1]]["w"]):
                    b = cand
                    break
            b = max(prev + 1, min(b, len(idx) - (n - c)))
            cuts.append(b)
            prev = b
        bounds = [0] + cuts + [len(idx)]
        groups = [idx[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]
        if all(len(g) <= BOLD_WORDS + 1 and
               len(_wrap(d, " ".join(words[j]["w"] for j in g), f, BOLD_CAPTION["width"])) <= 2 for g in groups):
            return groups
    return [[i] for i in idx]


def _bold_caption_props(lines, words, starts, beats=None):
    """Short bold caption pages: each line's words in groups of at most BOLD_WORDS that fit two lines, timed to
    the voice, breaking after punctuation where it can. "hi" marks numbers and key words (accent colour);
    "reveal" marks the payoff line. Returns (pages, caption boxes for the safe-area check)."""
    from PIL import Image, ImageDraw
    from clipper import captions
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = _black(BOLD_CAPTION["size"])
    keys = captions.pick_keywords(words, [], every=5)
    beats = beats or []
    pages, boxes = [], []
    for k in range(len(lines)):
        s0, s1 = starts[k], starts[k + 1]
        idx = [i for i, w in enumerate(words) if s0 <= float(w["s"] or 0) < s1 or
               (k == len(lines) - 1 and float(w["s"] or 0) >= s0)]
        if k == 0:                       # the hook is already on screen as the title: shown, but not as a caption
            pages.append({"s": s0, "e": s1, "text": "", "words": idx})
            continue
        groups = _even_groups(d, f, words, idx)
        for g_n, g in enumerate(groups):
            a = s0 if g_n == 0 else float(words[g[0]]["s"])
            b = float(words[groups[g_n + 1][0]]["s"]) if g_n + 1 < len(groups) else s1
            text = " ".join(words[j]["w"] for j in g)
            pages.append({"s": round(a, 3), "e": round(b, 3), "words": g, "text": text,
                          "hi": [n for n, j in enumerate(g) if j in keys or re.search(r"\d", words[j]["w"])],
                          **({"reveal": True} if k < len(beats) and beats[k] == "payoff" else {})})
            n = len(_wrap(d, text, f, BOLD_CAPTION["width"]))
            h = int(n * BOLD_CAPTION["size"] * BOLD_CAPTION["lineHeight"] * (1.12 if pages[-1].get("reveal") else 1))
            boxes.append((BOLD_CAPTION["left"], int(BOLD_CAPTION["y"] * H - h / 2), BOLD_CAPTION["width"], h))
    return pages, boxes


def _serif(size, italic=False):
    from PIL import ImageFont
    for n in SERIF[italic]:
        if Path(n).exists():
            return ImageFont.truetype(n, size)
    return _font(size, False)


def _split_num(v):
    """("2,970 t") -> (2970.0, decimals, "t") or None when there's no number to count."""
    m = re.match(r"^\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(.*)$", str(v))
    if not m:
        return None
    n = m.group(1).replace(",", "")
    return float(n), (len(n.split(".")[1]) if "." in n else 0), m.group(2).strip()


CARRY_SCENES = 1          # a counter stays up over this many following scenes that have no number of their own


def huds(scenes):
    """The counter for every scene: {"label", "value", "sub"} plus "num"/"from"/"dec"/"unit" when the value
    is a number that can count up. A scene without a number keeps the last counter, like the reference."""
    out, last, held = [], None, 0
    for sc in scenes:
        t, h = sc.get("type"), None
        if t == "number":
            h = {"label": sc.get("label") or "", "value": f"{sc['value']} {sc.get('unit') or ''}".strip()}
        elif t == "equation":
            h = {"label": sc.get("left") or "", "value": str(sc["right"]), "sub": sc.get("label") or ""}
        elif t in ("compare", "scale") and sc.get("items"):
            top = max(sc["items"], key=lambda i: _num(i["value"]))
            h = {"label": str(top.get("label") or ""), "value": f"{top['value']} {sc.get('unit') or ''}".strip()}
        if h:
            h["label"] = " ".join(str(h["label"]).split()[:6]).upper()
            h["sub"] = " ".join(str(h.get("sub") or "").split()[:6]).upper()
            parts = _split_num(h["value"])
            if parts:
                prev = _split_num(last["value"]) if last else None
                h.update(num=parts[0], dec=min(parts[1], 2), unit=parts[2],
                         **{"from": prev[0] if prev and prev[2] == parts[2] and prev[0] < parts[0] else 0.0})
            last = h
            out.append(dict(h))
            held = 0
            continue
        # a scene without a number keeps the last counter for one scene, standing still; after that it's stale
        held += 1
        out.append(dict(last, **({"from": last["num"]} if "num" in last else {}), carried=True)
                   if last and held <= CARRY_SCENES else None)
    if out and out[0]:
        out[0]["from"] = out[0].get("num", 0.0)          # the first frame already shows the hook's number
    return out


def draw_cinematic(bg, hud, pal, title="", bold=False):
    """A cinematic scene still: the picture, the counter (final value) and, on the hook, the question.
    Returns (PIL image, bounding box of what was drawn)."""
    from PIL import ImageDraw
    img = bg.copy()
    d = ImageDraw.Draw(img)
    white, soft = (255, 255, 255), (235, 240, 248)
    drawn = []

    def put(xy, text, f, fill, shadow=True):
        if shadow:
            for dx, dy in ((0, 3), (2, 2), (-2, 2)):
                d.text((xy[0] + dx, xy[1] + dy), text, font=f, fill=(0, 0, 0))
        d.text(xy, text, font=f, fill=fill)
        drawn.append(d.textbbox(xy, text, font=f))
        return drawn[-1]

    y = HUD_Y
    if hud:
        width = BOX["x1"] - HUD_X
        if hud.get("label"):
            f, ls, _ = _fit(d, hud["label"], 24, width, 1)
            y = put((HUD_X, y), ls[0], f, soft)[3] + 10
        big = _black if bold else _serif
        f = big(84 if bold else 88)
        while d.textlength(hud["value"], font=f) > width and f.size > 50:
            f = big(f.size - 6)
        y = put((HUD_X, y), hud["value"], f, white)[3] + 12
        if hud.get("sub"):
            f, ls, _ = _fit(d, hud["sub"], 22, width, 1, bold=True)
            y = put((HUD_X, y), ls[0], f, soft)[3]
    if title:
        f = _black(68) if bold else _serif(66)
        ls = _wrap(d, title, f, BOX["x1"] - BOX["x0"] - 20)[:3]
        yy = max(y + 40, TITLE_TOP)
        for ln in ls:
            x = BOX["x0"] + (BOX["x1"] - BOX["x0"] - d.textlength(ln, font=f)) / 2
            yy = put((x, yy), ln, f, white)[3] + 12
    box = [min(b[0] for b in drawn), min(b[1] for b in drawn), max(b[2] for b in drawn),
           max(b[3] for b in drawn)] if drawn else [BOX["x0"], BOX["y0"], BOX["x0"] + 1, BOX["y0"] + 1]
    return img, box


def _line_caption_props(lines, words, starts):
    """One italic sentence at a time: the narrated line's own text while it's spoken (every word of it is
    counted as shown). Also the caption boxes for the safe-area check."""
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = _serif(LINE_CAPTION["size"], True)
    pages, boxes = [], []
    for k, text in enumerate(lines):
        s0, s1 = starts[k], starts[k + 1]
        idx = [i for i, w in enumerate(words) if s0 <= float(w["s"] or 0) < s1 or (k == len(lines) - 1 and float(w["s"] or 0) >= s0)]
        n = len(_wrap(d, text, f, LINE_CAPTION["width"]))
        h = int(n * LINE_CAPTION["size"] * LINE_CAPTION["lineHeight"])
        # the hook is already on screen as the title, so its words aren't repeated as a caption
        pages.append({"s": s0, "e": s1, "text": "" if k == 0 else text, "words": idx})
        if k:
            boxes.append((LINE_CAPTION["left"], int(LINE_CAPTION["y"] * H - h / 2), LINE_CAPTION["width"], h))
    return pages, boxes


ANIMATED = ("number", "equation", "compare", "scale", "steps", "headline")


def _manim_clips(scenes, starts, folder, pal, handle, bg, bgs=None):
    """{scene index: {"video", "hold", "seconds"}} from Manim, for every scene but the hook (whose first
    frame must already show its number). Scenes Manim couldn't make keep their still picture. bgs: the
    scenes' own AI pictures ({index: path}), else the plain background for all."""
    from studio import engines
    bgs = bgs or {}
    jobs_ = [{"spec": sc, "out": str(folder / f"manim_{i + 1:02d}.mp4"), "seconds": starts[i + 1] - starts[i],
              **({"background": str(bgs[i])} if i in bgs else {})}
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


MOVE_WORKERS = 3          # camera moves drawn at once (the processor has 8 threads; the card encodes up to 5 streams)


SHOT_CUT = 0.12          # a new shot fades in over this many seconds


def shot_times(span, n):
    """Where each of n shots starts inside a scene of span seconds (equal parts; the first at 0)."""
    return [round(span * j / n, 3) for j in range(n)]


def _depth_moves(raws, enh, starts, folder, pal, used, scenes=None, shot_raws=None, shot_enh=None):
    """{scene: {"video", "kind", "shots"?}}: a camera move through each picture that has a depth map
    (studio/motion.py). A scene with extra storyboard shots gets a move per shot, each over its part of the scene,
    with the storyboard's camera; "shots" lists [{"at", "video"}] for the 2nd and 3rd."""
    from concurrent.futures import ThreadPoolExecutor
    from studio import art, motion
    scenes, shot_raws, shot_enh = scenes or [], shot_raws or {}, shot_enh or {}
    out, errs = {}, []
    todo = [(k, rp) for k, rp in sorted(raws.items()) if enh.get(k, {}).get("depth") and k + 1 < len(starts)]
    plan = {}                                   # scene -> [(shot, raw, enh, camera, seconds, at)]
    for k, rp in todo:
        sc = scenes[k] if k < len(scenes) and isinstance(scenes[k], dict) else {}
        shots = sc.get("shots") or []
        extra = [j for j in range(1, len(shots)) if (k, j) in shot_raws and shot_enh.get((k, j), {}).get("depth")]
        span = starts[k + 1] - starts[k]
        n = 1 + len(extra)
        at = shot_times(span, n)
        cams = [shots[0].get("camera") if shots else None] + [shots[j].get("camera") for j in extra]
        parts = [(0, rp, enh[k], cams[0])] + [(j, shot_raws[(k, j)], shot_enh[(k, j)], cams[m + 1])
                                              for m, j in enumerate(extra)]
        plan[k] = [(j, r, e, cam, (at[m + 1] if m + 1 < n else span) - at[m] + (FADE + 0.1 if m + 1 == n else SHOT_CUT),
                    at[m]) for m, (j, r, e, cam) in enumerate(parts)]

    def one(k, j, rp, e, cam, seconds):
        _shaded, img, shade = art.prepare(rp, pal, "hud", hd=e.get("hd"), parts=True)
        name = f"move_{k + 1:02d}.mp4" if j == 0 else f"move_{k + 1:02d}_{j + 1}.mp4"
        return motion.depth_clip(img, motion.load_depth(e["depth"]), seconds, k + j, folder / name, FPS, shade,
                                 camera=cam if (j or len(plan[k]) > 1) else None)
    # each move is its own clip, so drawing a few at once gives the very same clips, sooner
    with ThreadPoolExecutor(max_workers=max(1, MOVE_WORKERS)) as pool:
        jobs_ = {(k, j): pool.submit(one, k, j, r, e, cam, secs) for k, parts in plan.items()
                 for j, r, e, cam, secs, _at in parts}
    n_shots = 0
    for k, parts in plan.items():
        clips = []
        for j, r, e, cam, secs, at in parts:
            try:
                try:
                    p = jobs_[(k, j)].result()
                except Exception:
                    p = one(k, j, r, e, cam, secs)     # once more on its own (e.g. after a graphics-card encode failure)
                clips.append((j, p, at, cam))
            except Exception as ex:
                errs.append(str(ex)[:160])
        if not clips or clips[0][0] != 0:
            continue
        cam0 = clips[0][3] if len(parts) > 1 else None
        out[k] = {"video": clips[0][1].name, "kind": motion.move_for(k, cam0)[0]}
        if len(clips) > 1:
            out[k]["shots"] = [{"at": at, "video": p.name} for _j, p, at, _c in clips[1:]]
            n_shots += len(clips) - 1
    if n_shots:
        used["shot_moves"] = f"{n_shots} extra shots with their own camera move"
    used["motion"] = (f"depth camera moves: {len(out)} of {len(raws)} scenes" if out else "flat push-in") + \
        (f" ({errs[0]})" if errs else "")
    return out


def _footage_fits(scenes, got, folder, report=None):
    """Keeps the footage clips whose middle frame OpenCLIP says shows what the scene is about (its search and its
    planned picture). Returns (kept, dropped scene numbers). Not checked (kept) when OpenCLIP can't run."""
    from studio import art
    if not got or not art.fit_on():
        return got, []
    import cv2
    frames = []
    for k, p, _ in got:
        cap = cv2.VideoCapture(str(p))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n // 2))
        ok, fr = cap.read()
        cap.release()
        f = folder / f"footage_{k + 1:02d}_mid.png"
        if not ok or not cv2.imwrite(str(f), fr):
            return got, []
        frames.append(f)
    texts = [f"{scenes[k].get('footage')}. {art.subject(scenes[k], '')}"[:300] for k, _, _ in got]
    try:
        sims = art.clip_scores(frames, texts)
    except Exception:
        return got, []
    floor = art._cfg_num("footage_fit_floor", art.FIT_FLOOR)
    if report is not None:
        report.update({g[0] + 1: round(float(sims[i][i]), 3) for i, g in enumerate(got)})
    keep = [g for i, g in enumerate(got) if float(sims[i][i]) >= floor]
    return keep, [g[0] + 1 for i, g in enumerate(got) if float(sims[i][i]) < floor]


def _footage_moves(video_id, scenes, starts, folder, used, record):
    """{scene: {"video", "kind"}}: real stock clips (Pexels, Pixabay or NASA) for the scenes the planner gave a
    footage search."""
    from studio import art, footage
    if not footage.ready():
        used["footage"] = "none (no footage library: run Connect Stock Footage)"
        return {}
    spans = [starts[k + 1] - starts[k] for k in range(len(scenes))]
    out, why, seen, libs, got = {}, [], {}, [], []
    shade = None
    for k in footage.pick_scenes(scenes, spans):
        need = spans[k] + FADE + 0.1
        src, info = footage.fetch(scenes[k]["footage"], need, used=set(seen))
        if not src:
            why.append(info)
            continue
        # a clip used again starts further in, so the same shot never plays twice
        start = max(0.0, min(seen.get(info["id"], info.get("skip") or 0.0), info["duration"] - need - 0.2))
        try:
            shade = shade or footage.shade_png(art.hud_shade(W, H), folder / "shade.png")
            p = footage.scene_clip(src, need, folder / f"footage_{k + 1:02d}.mp4", shade, W, H, FPS, start=start,
                                   layout=info.get("layout") or "cover")
        except Exception as e:
            why.append(str(e)[:120])
            continue
        seen[info["id"]] = start + need
        got.append((k, p, info))
    fscores = {}
    got, dropped = _footage_fits(scenes, got, folder, fscores)
    if fscores:
        used["footage_scores"] = ", ".join(f"scene {k} {v:.2f}" for k, v in sorted(fscores.items()))
    if dropped:
        why.append(f"{len(dropped)} clip{'s' if len(dropped) != 1 else ''} didn't show the line, kept the AI picture")
    for k, p, info in got:
        out[k] = {"video": p.name, "kind": "footage"}
        site = info.get("source") or "pexels"
        libs.append(footage.NAMES[site])
        if record:
            db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", video_id, "footage:scene", str(p), "stock", info["page"] or None,
                      footage.LICENCES[site], info.get("credit") or f"Video by {info['user'] or 'a Pexels creator'} on Pexels",
                      _sha(p), time.time())
    names = " + ".join(dict.fromkeys(libs))
    used["footage"] = (f"{names}: {len(out)} scene{'s' if len(out) != 1 else ''}" if out else "none") + (f" ({why[0]})" if why else "")
    return out


def render(video_id, progress=lambda s: None, engines_override=None, out_name="short.mp4", record=True,
           voice_path=None, footage_terms=False):
    """Makes studio_data/videos/<id>/<out_name> from the passed script and narration. Returns a report.
    The upgraded tools (whisperX, Manim, Remotion; see studio/engines.py) are used when switched on and
    working; each falls back to the classic way on its own. record=False (a sample) changes nothing in
    the database."""
    import cv2
    from clipper import captions, media
    from studio import edit, engines, script
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
    if footage_terms and not any(isinstance(s, dict) and s.get("footage") for s in scenes):
        # a Short planned before footage existed: ask only for the footage searches, keep every other choice
        progress("Planning footage searches")
        fresh = plan(row)
        scenes = [dict(s, footage=f["footage"]) if isinstance(s, dict) and isinstance(f, dict) and f.get("footage")
                  else s for s, f in zip(scenes, fresh)] + scenes[len(fresh):]
        if record:
            db.set_video(video_id, meta=json.dumps(dict(meta, scenes=scenes)))
    pal = palette()
    handle = (db.one("SELECT handle FROM channel WHERE id=1") or {"handle": ""})["handle"] or ""
    look = eng.get("look", "diagram")
    bgs, bg_paths, raws, enh = {}, {}, {}, {}
    shot_raws, shot_enh = {}, {}                             # (scene, shot) -> extra storyboard shots
    if eng["images"] == "sdxl" or look == "hud":
        progress("Painting a picture for every scene (SDXL-Turbo)")
        try:
            from studio import art
            raws = art.raw_pictures(video_id, scenes, lines, look)
            if raws and art.fit_on():
                progress("Checking every picture fits its line (OpenCLIP)")
                try:
                    raws, fitrep = art.fit(video_id, scenes, lines, raws, look)
                    used["fit"] = (f"OpenCLIP: lowest {min(fitrep['scores'].values()):.2f}, "
                                   f"retaken {fitrep['retaken'] or 'none'}, plain {fitrep['plain'] or 'none'}")
                    used["fit_scores"] = ", ".join(f"scene {k} {v:.2f}" for k, v in sorted(fitrep["scores"].items()))
                except Exception as e:
                    used["fit"] = f"not checked ({str(e)[:160]})"
            sharpen, depth = eng["sharpen"] == "esrgan", eng["motion"] == "depth" and look == "hud"
            if raws and depth and art.shots_on() and any(isinstance(sc, dict) and len(sc.get("shots") or []) > 1
                                                         for sc in scenes):
                progress("Painting the extra shots the storyboard asks for")
                shot_raws = art.shot_pictures(video_id, scenes, look)
                shot_raws, shot_fit = art.fit_shots(scenes, lines, shot_raws)
                if shot_raws:
                    shot_enh = art.enhance(shot_raws, sharpen=sharpen, depth=True)
                used["shots"] = (f"{len(shot_raws)} extra shots" + (f", dropped {shot_fit['dropped']}"
                                                                    if shot_fit.get("dropped") else ""))
            if raws and (sharpen or depth):
                progress("Sharpening the pictures and finding their depth")
                enh = art.enhance(raws, sharpen=sharpen, depth=depth)
                n_hd = sum(1 for k in raws if enh.get(k, {}).get("hd"))
                used["sharpen"] = (f"Real-ESRGAN: {n_hd} of {len(raws)} pictures" if sharpen else "off") + \
                    (f" ({enh['_note'][:160]})" if enh.get("_note") else "")
            for k, rp in sorted(raws.items()):
                bgs[k] = art.prepare(rp, pal, look, hd=enh.get(k, {}).get("hd"))
                bg_paths[k] = folder / f"bg_{k + 1:02d}.png"
                bgs[k].save(bg_paths[k])
            if look == "hud" and len(bgs) < len(scenes):
                raise engines.EngineError(f"only {len(bgs)} of {len(scenes)} pictures came out")
            used["images"] = f"SDXL-Turbo: {len(bgs)} of {len(scenes)} scenes"
        except Exception as e:
            bgs, bg_paths = {}, {}
            used["images"] = f"drawn backgrounds (the AI pictures weren't used: {str(e)[:200]})"
    else:
        used["images"] = "drawn backgrounds"
    if look == "hud" and not bgs:
        look = "diagram"
        used["look"] = "diagram (the cinematic look needs the AI pictures)"
    else:
        used["look"] = "cinematic (the reference style)" if look == "hud" else "diagram"
    hud_list = huds(scenes) if look == "hud" else None
    progress("Drawing the scenes")
    imgs, boxes, stills = [], [], []
    if record:
        db.run("DELETE FROM assets WHERE video_id=? AND origin='generated'", video_id)
        from studio import art
        for k, bp in bg_paths.items():
            db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", video_id, "art:scene", str(bp), "generated", None, art.LICENCE,
                      art.prompt(scenes[k], lines[k] if k < len(lines) else "", look)[:300], _sha(bp), time.time())
    for i, sc in enumerate(scenes):
        if look == "hud":
            img, box = draw_cinematic(bgs[i], hud_list[i], pal, title=lines[0] if i == 0 else "",
                                      bold=type_style() == "bold")
        else:
            img, box = draw_scene(sc, pal, handle, bg=bgs.get(i))
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
    # The music (graphics card) is made while the camera moves are drawn (processor), so neither waits for the
    # other. Footage comes first: its picture check needs the graphics card too.
    import threading
    from studio import timings
    box, vid_timed = {}, timings.current()

    def make_music():
        try:
            with timings.video(vid_timed):
                from studio import music, topics
                vrow = db.video(video_id)
                tp = topics.topic(vrow["topic_id"]) if vrow and vrow["topic_id"] else None
                box["music"] = music.make(video_id, end, tp)
        except Exception as e:
            box["error"] = e
        finally:
            db.close_thread()                      # this helper thread's own database connection
    moves = {}                                               # scene -> {"video", "kind"} for the cinematic look
    if look == "hud" and eng["compose"] == "remotion" and eng["footage"] == "pexels":
        moves.update(_footage_moves(video_id, scenes, starts, folder, used, record))
    worker = None
    if eng["music"] == "acestep":
        progress("Making the background music (ACE-Step)")
        worker = threading.Thread(target=make_music, daemon=True)
        worker.start()
    try:
        if look == "hud" and eng["compose"] == "remotion" and eng["motion"] == "depth":
            with timings.step("camera moves (depth)"):
                moves.update({k: v for k, v in _depth_moves(raws, enh, starts, folder, pal, used, scenes, shot_raws,
                                                            shot_enh).items() if k not in moves})
    finally:
        if worker:
            worker.join()
    if worker:
        try:
            if "error" in box:
                raise box["error"]
            from studio import music
            raw_music, mood = box["music"]
            mixed = folder / "voice_music.wav"
            music.mix(padded, raw_music, end + 0.05, mixed)
            padded = mixed
            used["music"] = f"ACE-Step: {mood}"
            if record:
                db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, "
                          "created) VALUES(?,?,?,?,?,?,?,?,?)", video_id, "music", str(raw_music), "generated", None,
                          music.LICENCE, mood[:300], _sha(raw_music), time.time())
        except Exception as e:
            used["music"] = f"none (the music wasn't used: {str(e)[:200]})"
    else:
        used["music"] = "none"
    beats = edit.beats_of(st["lines"], len(scenes))
    cue_list = edit.cues(starts, beats, hud_list, words)
    from studio import art as _art
    board_on = _art.shots_on()
    if board_on:
        cue_list = edit.board_cues(cue_list, scenes)       # the storyboard's own sound and transition per cut
    if eng["sfx"] == "synth":
        try:
            from studio import sound
            fx = sound.track(cue_list, end + 0.05)
            if fx is not None:
                mixed = folder / "voice_sfx.wav"
                sound.mix(padded, fx, mixed, end + 0.05)
                padded = mixed
                used["sfx"] = f"Jarvis's own sound effects: {sum(1 for c in cue_list if c['kind'] != 'jump')} cues"
            else:
                used["sfx"] = "none (no cues)"
        except Exception as e:
            used["sfx"] = f"none (the sound effects weren't used: {str(e)[:200]})"
    lively = eng["edit"] == "lively"
    ovs = edit.overlays(scenes, look) if lively else {}
    if lively:
        for k, ov in ovs.items():                            # the diagrams count as drawn visuals for the safe area
            b, o = boxes[k], edit.overlay_box(ov)
            boxes[k] = [min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3])]
        used["edit"] = f"lively: {len(ovs)} animated diagrams, {len(cue_list)} cues"
    else:
        used["edit"] = "calm"
    clips = {}
    if look == "hud":
        used["visuals"] = "full-screen pictures with a slow push-in (the cinematic look doesn't use Manim)"
    elif eng["visuals"] == "manim":
        progress("Animating the scenes (Manim)")
        try:
            bg = folder / "background.png"
            background(pal).save(bg)
            clips, used["visuals"] = _manim_clips(scenes, starts, folder, pal, handle, bg, bg_paths)
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
            if look == "hud":
                if type_style() == "bold":
                    pages_, cap_boxes = _bold_caption_props(lines, words, starts, beats)
                    cap_props = {"pages": pages_, "keywords": [], "caption": dict(BOLD_CAPTION, maxLines=2),
                                 "look": "hud", "type": "bold"}
                    used["type"] = "bold sans-serif captions (1-2 lines, key words highlighted)"
                else:
                    pages_, cap_boxes = _line_caption_props(lines, words, starts)
                    cap_props = {"pages": pages_, "keywords": [], "caption": dict(LINE_CAPTION, maxLines=3), "look": "hud"}
            else:
                cap_props, cap_boxes = _caption_props(words, pal)
            props = {"fps": FPS, "width": W, "height": H, "duration": round(end, 3), "audio": padded.name,
                     "words": [{"w": w["w"], "s": float(w["s"]), "e": float(w["e"])} for w in words],
                     "palette": {k: pal[k] for k in ("cyan", "amber", "text", "background")},
                     "fade": FADE, "punch": PUNCH, "loopBlend": LOOP_BLEND, **cap_props,
                     "scenes": [{"start": starts[k], "end": starts[k + 1], "image": stills[k].name,
                                 **({"video": Path(clips[k]["video"]).name, "videoSeconds": clips[k]["seconds"],
                                     "hold": Path(clips[k]["hold"]).name} if k in clips else {}),
                                 **({"bg": bg_paths[k].name, "hud": hud_list[k], "title": lines[0] if k == 0 else ""}
                                    if look == "hud" else {}),
                                 **({"motion": moves[k]["video"]} if k in moves else {}),
                                 **({"shots": moves[k]["shots"]} if moves.get(k, {}).get("shots") else {}),
                                 **({"overlay": ovs[k]} if k in ovs else {}),
                                 **({"transition": scenes[k]["transition"]}
                                    if scenes[k].get("transition") in ("fade", "zoom") and board_on else {})}
                                for k in range(len(scenes))],
                     **({"fx": cue_list} if lively else {})}
            with engines.heavy("Remotion render"):
                rr = engines.remotion_render(props, folder, tmp)
            tmp.replace(out)
            used["encoder"] = (rr or {}).get("encoder", "libx264")
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
        used["encoder"] = wr.encoder
        rep = {"caption_boxes": caption_boxes[:: max(1, len(caption_boxes) // 40)],
               "caption_words_shown": len(cap.shown_words)}
    rep = {"path": str(out), "duration": round(end, 2), "frames": int(math.ceil(end * FPS)), "scenes": len(scenes),
           "timing": timing, "speech_start": round(float(words[0]["s"] or 0), 2) if words else None,
           "tail_seconds": round(end - max(last_word, speech_end), 2),
           "trimmed_lead": round(lead - 0.08, 3) if lead > 0.25 else 0.0,
           "scene_boxes": boxes, "words": len(words), "engines": used,
           "line_starts": [round(float(x), 3) for x in starts], **rep}
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
    from studio import packaging
    titles = packaging.pick(titles)                          # a title style that clearly works goes first
    title = (titles[0] if titles else t["title"])[:100]
    used = db.q("SELECT DISTINCT url FROM sources WHERE video_id=? AND verdict='supported' AND url IS NOT NULL", video_id)
    srcs = [r["url"] for r in used] or [s["url"] for s in (t.get("sources") or []) if s.get("verdict") == "reachable"]
    kws = [k for k in json.loads(t.get("keywords") or "[]") if isinstance(k, str)][:8]
    tags_hash = ["#Shorts"] + ["#" + re.sub(r"[^A-Za-z0-9]", "", k.title()) for k in kws[:2] if k.strip()]
    first = " ".join(json.loads(row["structure"])["lines"][1]["text"].split()[:30]) if len(st["lines"]) > 1 else ""
    desc = (f"{title}\n\n{first}\n\nSources:\n" + "\n".join(f"- {u}" for u in srcs[:6]) +
            "\n\nEvery number here is worked out from the sources above. Narration is AI-generated (a synthetic voice); "
            "the script was written and fact-checked with AI and checked against the sources.\n\n" + " ".join(tags_hash))
    meta = json.loads(v["meta"] or "{}")
    eng_used = (meta.get("render") or {}).get("engines") or {}
    ai_pictures = str(eng_used.get("images", "")).startswith("SDXL")
    ai_music = str(eng_used.get("music", "")).startswith("ACE-Step")
    from studio import footage
    libs = str(eng_used.get("footage", "")).split(":", 1)[0].split(" + ")
    libs = [n for n in libs if n in footage.NAMES.values()]
    stock = bool(libs)
    if stock:
        credits = [r["attribution"] for r in db.q("SELECT DISTINCT attribution FROM assets WHERE video_id=? AND "
                                                  "kind='footage:scene'", video_id) if r["attribution"]]
        where = " and ".join(libs)
        stock_line = f"Some scenes are stock footage from {where}" + (" (" + "; ".join(credits[:4]) + ")" if credits else "") + "."
        if "NASA" in libs:
            stock_line += " NASA does not endorse this channel."
    if ai_pictures or ai_music or stock:          # said plainly, before the hashtags
        pics = ("The other pictures are AI-generated illustrations." if stock else
                "The pictures are AI-generated illustrations, not real footage.") if ai_pictures else ""
        extra = " ".join(x for x in (pics, stock_line if stock else "",
                                     "The background music is AI-generated." if ai_music else "") if x)
        desc = desc.replace("\n\n" + " ".join(tags_hash), " " + extra + "\n\n" + " ".join(tags_hash))
    up = {"title": title, "description": desc[:4900], "tags": kws, "categoryId": "27", "defaultLanguage": "en",
          "defaultAudioLanguage": "en-US", "madeForKids": False, "containsSyntheticMedia": ai_pictures,
          "synthetic_reason": ("AI-generated pictures of real places and things are shown, so YouTube's altered or "
                               "synthetic content label is switched on." if ai_pictures else
                               "No realistic people, places or events are shown: original diagrams and a generic "
                               "synthetic narrator voice (disclosed in the description)."),
          "sources": srcs}
    meta["upload"] = up
    db.set_video(video_id, title=title, meta=json.dumps(meta))
    (data_dir("videos", str(video_id)) / "upload.json").write_text(json.dumps(up, indent=2))
    if v["path"]:
        try:
            packaging.record(video_id, title, v["path"], row["hook"])     # its title and first-frame style
        except Exception:
            pass
    return up
