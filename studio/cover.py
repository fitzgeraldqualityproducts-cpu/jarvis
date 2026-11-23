"""Each Short's own cover (thumbnail), in the HussUniversity Canva template's layout.

The template was designed in Canva (2026-11-23, "HussUniversity YouTube Shorts cover template"):
a huge condensed hook in the top third with one word in amber, a glowing panel with the key
calculation, a "RUN THE MATH" style pill and the channel tag, on the navy grid. Canva's free plan
can't fill a template from code, so the same layout is drawn here from shapes and text, with the
words of each Short:

  text     the Short's fact-checked hook (what the viewer hears first), or its shortest title
  stat     a checked calculation from the script ("expr = result unit") that fits, else the
           biggest number scene, else nothing (the panel is left out rather than invented)
  pill     the Short's pillar ("SCALE IT RIGHT"), else "RUN THE MATH"

make(video_id) writes studio_data/videos/<id>/cover.jpg (1080x1920, under YouTube's 2 MB
thumbnail limit). The bottom fifth and the right edge stay clear for the Shorts buttons and title.
publish.set_cover() sends it to YouTube after the upload."""
import json
import re
import time
from pathlib import Path

from studio import data_dir, db, guard

W, H = 1080, 1920
LEFT, RIGHT = 120, W - 150          # the right edge stays clear of the like/share buttons
HOOK_TOP, HOOK_BOTTOM = 170, 800
MAX_BYTES = 2 * 1024 * 1024
STOP = {"the", "a", "an", "is", "are", "was", "of", "to", "in", "on", "and", "or", "it", "its", "this", "that",
        "how", "what", "why", "when", "who", "does", "do", "can", "you", "your", "really", "would", "with", "for",
        "from", "than", "into", "if", "be", "at", "by", "as"}

CONDENSED = ["/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",       # Mac
             "/System/Library/Fonts/Supplemental/Impact.ttf", "/Library/Fonts/Impact.ttf",
             "C:/Windows/Fonts/impact.ttf",                                    # Windows
             "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"]


def _font(size, condensed=False, bold=True):
    from PIL import ImageFont
    if condensed:
        for n in CONDENSED:
            if Path(n).exists():
                return ImageFont.truetype(n, size)
    from studio.toolchain import _font as f
    return f(size, bold)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------- what goes on it ----------

def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"#\w+", "", str(s or ""))).strip()


def cover_text(row):
    """The hook (it's what the Short opens with and it passed the fact-check); a short title if the
    hook is long."""
    st = json.loads(row["structure"]) if isinstance(row["structure"], str) else row["structure"]
    hook = _clean(row["hook"])
    if hook and len(hook.split()) <= 9:
        return hook
    from studio import script
    titles = [_clean(t) for t in st.get("titles") or [] if _clean(t) and not script.hook_checks_title(t)]
    titles.sort(key=len)
    return titles[0] if titles and len(titles[0]) < len(hook or "x" * 999) else hook


def _fmt(v):
    from studio.video import _fmtnum
    try:
        return _fmtnum(v)
    except (ValueError, TypeError):
        return str(v).strip()


def stat(st, scenes):
    """(left, right, unit) of the key calculation, or None. Only checked numbers are used."""
    for c in st.get("calcs") or []:
        res = c.get("actual") if c.get("actual") not in (None, "") else c.get("result")
        expr = _clean(c.get("expr"))
        if res in (None, "") or not expr:
            continue
        right, unit = _fmt(res), _clean(c.get("unit"))[:12]
        if len(f"{expr} = {right} {unit}") <= 26:
            return expr, right, unit
    nums = [s for s in scenes or [] if isinstance(s, dict) and s.get("type") == "number" and str(s.get("value", "")).strip()]
    if nums:
        def size(s):
            try:
                return float(str(s["value"]).replace(",", ""))
            except ValueError:
                return 0.0
        s = max(nums, key=size)
        return "", _fmt(s["value"]), _clean(s.get("unit"))[:12]
    for c in st.get("calcs") or []:
        res = c.get("actual") if c.get("actual") not in (None, "") else c.get("result")
        if res not in (None, ""):
            return "", _fmt(res), _clean(c.get("unit"))[:12]
    return None


def highlight_word(words):
    """Index of the word drawn in amber: the longest meaningful word, like "ROCKET," in the template."""
    best, idx = 0, None
    for i, w in enumerate(words):
        core = re.sub(r"[^A-Za-z0-9]", "", w)
        if core.lower() in STOP or len(core) < 4:
            continue
        if len(core) > best:
            best, idx = len(core), i
    return idx


# ---------- drawing ----------

def _wrap(d, words, f, width):
    lines, cur = [], []
    for i, w in enumerate(words):
        trial = " ".join(words[j] for j in cur + [i])
        if cur and d.textlength(trial, font=f) > width:
            lines.append(cur)
            cur = [i]
        else:
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def _fit_hook(d, words, width, height):
    size = 230
    while True:
        f = _font(size, condensed=True)
        lines = _wrap(d, words, f, width)
        tall = len(lines) * int(size * 0.92)
        fits = tall <= height and all(d.textlength(" ".join(words[j] for j in ln), font=f) <= width for ln in lines)
        if fits or size <= 90:
            return f, lines, size
        size -= 8


def _glow_box(img, box, color, radius=34, width=6):
    from PIL import Image, ImageDraw, ImageFilter
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.rounded_rectangle(box, radius=radius, outline=color + (200,), width=width * 3)
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius, fill=(10, 20, 40, 225), outline=color + (255,), width=width)


def _vignette(img, pal):
    """A soft cyan glow behind the panel, like the Canva background."""
    from PIL import Image, ImageDraw, ImageFilter
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse([W * 0.05, 700, W * 0.95, 1250], fill=_rgb(pal["cyan"]) + (34,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(120)))


def draw(text, calc, pill, channel, pal):
    """The cover as a PIL RGB image."""
    from PIL import ImageDraw
    from studio.video import background
    img = background(pal).convert("RGBA")
    _vignette(img, pal)
    d = ImageDraw.Draw(img)
    white, cyan, amber, muted = _rgb(pal["text"]), _rgb(pal["cyan"]), _rgb(pal["amber"]), _rgb(pal["muted"])

    words = (text.upper().split() or ["RUN", "THE", "MATH"])[:16]
    f, lines, size = _fit_hook(d, words, RIGHT - LEFT, HOOK_BOTTOM - HOOK_TOP)
    step = int(size * 0.92)
    keep = max(1, (HOOK_BOTTOM - HOOK_TOP) // step)
    if len(lines) > keep:                                   # never spill into the panel: cut at a word
        lines = lines[:keep]
        last = lines[-1][-1]
        words = words[:last + 1]
        words[last] = words[last].rstrip(",.;:") + "…"
    hi = highlight_word(words)
    y = HOOK_TOP
    for ln in lines:
        x = LEFT
        for j in ln:
            w = words[j] + " "
            d.text((x + 6, y + 8), w, font=f, fill=(0, 0, 0))          # drop shadow for phones in sunlight
            d.text((x, y), w, font=f, fill=amber if j == hi else white)
            x += d.textlength(w, font=f)
        y += step
    y = max(y + 50, 640)

    if calc:
        left, right, unit = calc
        parts = ([(left + " =", cyan)] if left else []) + [(right, amber)] + ([(unit, cyan)] if unit else [])
        ps = 150
        while True:
            fb, fu = _font(ps), _font(int(ps * 0.72))
            widths = [d.textlength(s, font=fu if (s == unit and unit) else fb) for s, _ in parts]
            total = sum(widths) + 34 * (len(parts) - 1)
            if total <= RIGHT - LEFT - 100 or ps <= 60:
                break
            ps -= 8
        box = (LEFT, y, RIGHT, y + ps + 110)
        _glow_box(img, box, cyan)
        d = ImageDraw.Draw(img)
        x = LEFT + (RIGHT - LEFT - total) / 2
        for (s, col), w in zip(parts, widths):
            fnt = fu if (s == unit and unit) else fb
            d.text((x, y + 55 + ps * 0.86), s, font=fnt, fill=col, anchor="ls")     # one shared baseline
            x += w + 34
        y = box[3] + 50

    fp = _font(46)
    label = pill.upper()[:28]
    pw = d.textlength(label, font=fp) + 110
    cx = (LEFT + RIGHT) / 2
    d.rounded_rectangle((cx - pw / 2, y, cx + pw / 2, y + 96), radius=48, fill=(8, 16, 32), outline=cyan, width=5)
    d.text((cx - d.textlength(label, font=fp) / 2, y + 22), label, font=fp, fill=white)
    y += 140

    if channel:
        ft = _font(44, bold=False)
        m = re.match(r"^([A-Z][a-z]+)(.+)$", channel)
        parts = [(m.group(1), cyan), (m.group(2), white)] if m else [(channel, white)]
        total = sum(d.textlength(s, font=ft) for s, _ in parts)
        x = cx - total / 2
        for s, col in parts:
            d.text((x, y), s, font=ft, fill=col)
            x += d.textlength(s, font=ft)
    return img.convert("RGB")


def bottom_clear(img):
    """True when the bottom fifth (YouTube's title and buttons) is free of text."""
    import numpy as np
    a = np.asarray(img.crop((0, int(H * 0.8), W, H)).convert("L"), dtype=np.int16)
    return int((a > 170).sum()) < 50


# ---------- per Short ----------

def make(video_id):
    """Draws and saves this Short's cover. Returns the path."""
    from studio import script
    guard.allow("media.local", f"cover for video {video_id}", record=False)
    from studio.video import palette
    row = script.latest(video_id, "passed")
    if not row:
        raise RuntimeError("the video needs a passed script first")
    st = json.loads(row["structure"])
    v = db.video(video_id)
    meta = json.loads(v["meta"] or "{}")
    pillar = ""
    if v["topic_id"]:
        from studio import topics
        pillar = (topics.topic(v["topic_id"]) or {}).get("pillar") or ""
    ch = db.one("SELECT title FROM channel WHERE id=1")
    channel = (ch["title"] if ch and ch["title"] else "") or ""
    text = cover_text(row)
    calc = stat(st, meta.get("scenes"))
    img = draw(text, calc, pillar or "Run the math", channel, palette())
    out = data_dir("videos", str(video_id)) / "cover.jpg"
    q = 92
    while True:
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size < MAX_BYTES or q <= 60:
            break
        q -= 8
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    meta["cover"] = {"path": str(out), "text": text, "stat": list(calc) if calc else None, "pill": pillar or "Run the math",
                     "bottom_clear": bottom_clear(img), "made": time.time()}
    db.set_video(video_id, meta=json.dumps(meta))
    import hashlib
    db.run("DELETE FROM assets WHERE video_id=? AND kind='cover'", video_id)
    db.insert("INSERT INTO assets(video_id, kind, path, origin, source_url, license, attribution, sha256, created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", video_id, "cover", str(out), "generated", None,
              "original (drawn by Jarvis from the HussUniversity Canva template layout)", "",
              hashlib.sha256(out.read_bytes()).hexdigest(), time.time())
    return out
