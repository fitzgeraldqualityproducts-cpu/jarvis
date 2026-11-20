"""Captions: big, readable, synced to every word, inside the area YouTube doesn't cover.

Five styles (any setting can be overridden per channel in "caption_overrides"):
  clean        white sentence-case words, soft shadow, two short lines
  bold         chunky UPPERCASE with a thick outline; the word being said turns yellow, key words in
               the brand colour (the classic podcast-clip look)
  kinetic      one or two huge words at a time that pop in as they're said (gaming, entertainment)
  educational  high-contrast text on a dark panel, key terms highlighted (tech, finance, how-tos)
  premium      small elegant sentence case; the spoken word brightens, the rest stays soft
"auto" picks by the channel's niche (and the clip's own suggestion), and learning.py can switch a
channel to the style that really performs better.

Words are drawn with Pillow (fonts: free Google Fonts, downloaded once, with the computer's own
bold fonts as a fallback) and blended onto each frame by render.py. Only the words actually said
are shown: names can be corrected with the channel's "fixes", nothing is reworded."""
import os
import re
import urllib.request

import numpy as np

from clipper import MODELS

FONT_DIR = MODELS / "fonts"
FONTS = {
    "Poppins-ExtraBold": "ofl/poppins/Poppins-ExtraBold.ttf",
    "Poppins-Bold": "ofl/poppins/Poppins-Bold.ttf",
    "Poppins-SemiBold": "ofl/poppins/Poppins-SemiBold.ttf",
    "Anton": "ofl/anton/Anton-Regular.ttf",
}
FONT_BASE = "https://raw.githubusercontent.com/google/fonts/main/"
SYSTEM_FALLBACKS = ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]

STYLES = {
    "clean": {"font": "Poppins-SemiBold", "size": 66, "uppercase": False, "color": "#ffffff", "active": "#ffffff",
              "keyword": None, "stroke": 0, "stroke_color": "#000000", "shadow": 0.75, "background": None,
              "words_per_line": 4, "max_lines": 2, "y": 0.68, "animation": "none", "dim": 1.0},
    "bold": {"font": "Poppins-ExtraBold", "size": 80, "uppercase": True, "color": "#ffffff", "active": "{highlight}",
             "keyword": "{brand}", "stroke": 9, "stroke_color": "#000000", "shadow": 0.6, "background": None,
             "words_per_line": 3, "max_lines": 2, "y": 0.66, "animation": "pop", "dim": 1.0},
    "kinetic": {"font": "Anton", "size": 128, "uppercase": True, "color": "#ffffff", "active": "#4ade80",
                "keyword": "{highlight}", "stroke": 8, "stroke_color": "#000000", "shadow": 0.7, "background": None,
                "words_per_line": 2, "max_lines": 1, "y": 0.64, "animation": "pop", "dim": 1.0},
    "educational": {"font": "Poppins-Bold", "size": 62, "uppercase": False, "color": "#ffffff", "active": "{highlight}",
                    "keyword": "{brand}", "stroke": 0, "stroke_color": "#000000", "shadow": 0.0,
                    "background": "#000000b8", "words_per_line": 5, "max_lines": 2, "y": 0.70, "animation": "none", "dim": 1.0},
    "premium": {"font": "Poppins-SemiBold", "size": 56, "uppercase": False, "color": "#f5f5f4", "active": "#ffffff",
                "keyword": None, "stroke": 0, "stroke_color": "#000000", "shadow": 0.55, "background": None,
                "words_per_line": 5, "max_lines": 2, "y": 0.72, "animation": "fade", "dim": 0.62},
}
NICHE_STYLE = [("gaming", "kinetic"), ("entertainment", "kinetic"), ("comedy", "kinetic"), ("podcast", "bold"),
               ("interview", "bold"), ("commentary", "bold"), ("education", "educational"), ("technology", "educational"),
               ("finance", "educational"), ("ai", "educational"), ("motivation", "premium"),
               ("personal development", "premium"), ("business", "bold")]
# YouTube's buttons sit on the right and its title/description at the bottom: stay out of both
SAFE = {"left": 80, "right": 150, "top": 0.12, "bottom": 0.78}
_font_cache = {}


def choose_style(channel, suggestion=None, learned=None):
    style = channel.get("caption_style", "auto")
    if style in STYLES:
        return style
    best = (learned or {}).get("caption_style")
    if best in STYLES:
        return best
    for niche in channel.get("niches", []):
        for key, s in NICHE_STYLE:
            if key in niche.lower():
                return s
    return suggestion if suggestion in STYLES else "bold"


def resolve(style, channel):
    brand = channel.get("brand", {})
    st = dict(STYLES[style])
    st.update({k: v for k, v in (channel.get("caption_overrides") or {}).items() if k in st})
    for k in ("active", "keyword", "color"):
        if isinstance(st.get(k), str):
            st[k] = st[k].replace("{highlight}", brand.get("highlight", "#facc15")).replace("{brand}", brand.get("color", "#22d3ee"))
    st["size"] = max(44, int(st["size"]))                   # readability floor on a phone
    st["name"] = style
    return st


# ---------- fonts ----------

def font_path(name):
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    local = FONT_DIR / (name + ".ttf")
    if local.exists() and local.stat().st_size > 10000:
        return str(local)
    if name in FONTS:
        try:
            try:
                import ssl
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            req = urllib.request.Request(FONT_BASE + FONTS[name], headers={"User-Agent": "jarvis-clipper"})
            data = urllib.request.urlopen(req, timeout=30, context=ctx).read()
            if len(data) > 10000:
                local.write_bytes(data)
                return str(local)
        except Exception as e:
            print(f"(clipper: couldn't download font {name}: {e})")
    if os.path.isfile(name):
        return name
    for f in SYSTEM_FALLBACKS:
        if os.path.isfile(f):
            return f
    return None


def font(name, size):
    from PIL import ImageFont
    key = (name, size)
    if key not in _font_cache:
        path = font_path(name)
        try:
            _font_cache[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _rgba(color, alpha=255):
    if not color:
        return None
    c = color.lstrip("#")
    if len(c) == 8:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


# ---------- words -> pages ----------

def apply_fixes(text, fixes):
    for wrong, right in (fixes or {}).items():
        text = re.sub(rf"(?i)\b{re.escape(wrong)}\b", right, text)
    return text


def pick_keywords(words, hints=(), every=6):
    """Indexes of words to highlight: the clip's key terms, numbers, and words said with emphasis,
    at most about one in `every` so highlighting still means something."""
    hint = {h.lower().strip(".,!?") for h in hints for h in str(h).split()}
    marks = []
    for i, w in enumerate(words):
        bare = w["w"].lower().strip(".,!?\"'")
        if len(bare) < 3 and not bare.isdigit():
            continue
        if bare in hint or re.search(r"\d", bare) or w.get("emph"):
            marks.append(i)
    limit = max(1, len(words) // every)
    if len(marks) > limit:
        pri = sorted(marks, key=lambda i: (words[i]["w"].lower().strip(".,!?") not in hint, not words[i].get("emph")))
        marks = sorted(pri[:limit])
    return set(marks)


def pages(words, st, width, max_gap=0.45):
    """Groups output-timeline words ([{w, s, e}]) into caption pages that fit on screen."""
    f = font(st["font"], st["size"])
    per_page = st["words_per_line"] * st["max_lines"]
    out, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            full = len(cur) >= per_page
            stop = re.search(r"[.!?]$", prev["w"]) or w["s"] - prev["e"] > max_gap
            wide = _text_w(f, " ".join(_disp(words[j]["w"], st) for j in cur + [i]), st) > width * st["max_lines"] * 0.92
            if full or stop or wide:
                out.append(cur)
                cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    result = []
    for k, idx in enumerate(out):
        start = words[idx[0]]["s"]
        nxt = words[out[k + 1][0]]["s"] if k + 1 < len(out) else words[idx[-1]]["e"] + 0.8
        end = min(nxt, words[idx[-1]]["e"] + 0.7)
        result.append({"words": idx, "s": start, "e": max(end, start + 0.3)})
    return result


def _disp(text, st):
    return text.upper() if st.get("uppercase") else text


def _text_w(f, text, st):
    try:
        return f.getlength(text) + st.get("stroke", 0) * 2
    except Exception:
        return len(text) * st["size"] * 0.55


def _lines(f, idx, words, st, width):
    lines, cur = [], []
    for i in idx:
        trial = cur + [i]
        if cur and (len(trial) > st["words_per_line"] or
                    _text_w(f, " ".join(_disp(words[j]["w"], st) for j in trial), st) > width):
            lines.append(cur)
            cur = [i]
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_page(page, words, active, keywords, st, frame_w, scale=1.0, alpha=1.0):
    """The caption image for one moment: RGBA numpy array plus its top-left (x, y)."""
    from PIL import Image, ImageDraw, ImageFilter
    size = max(20, int(st["size"] * scale))
    f = font(st["font"], size)
    width = frame_w - SAFE["left"] - SAFE["right"]
    lines = _lines(f, page["words"], words, st, width)[:st["max_lines"] + 1]
    space = _text_w(f, " ", dict(st, stroke=0))
    line_h = int(size * 1.18)
    pad = int(size * 0.35) + st.get("stroke", 0)
    widths = [sum(_text_w(f, _disp(words[i]["w"], st), st) for i in ln) + space * (len(ln) - 1) for ln in lines]
    W = int(min(width + pad * 2, max(widths) + pad * 2)) + 4
    H = int(line_h * len(lines) + pad * 2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if st.get("background"):
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=int(size * 0.35), fill=_rgba(st["background"]))
    base, act, key = _rgba(st["color"]), _rgba(st.get("active") or st["color"]), _rgba(st.get("keyword"))
    dim = st.get("dim", 1.0)
    stroke = _rgba(st.get("stroke_color", "#000000"))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if st.get("shadow") else None
    sd = ImageDraw.Draw(shadow) if shadow else None
    for li, ln in enumerate(lines):
        x = (W - widths[li]) / 2
        y = pad + li * line_h
        for i in ln:
            text = _disp(words[i]["w"], st)
            if i == active:
                color = act
            elif key and i in keywords:
                color = key
            else:
                color = base if dim >= 1 or active is None or i < (active or 0) else base[:3] + (int(base[3] * dim),)
            if sd is not None:
                off = max(2, size // 18)
                sd.text((x + off, y + off), text, font=f, fill=(0, 0, 0, int(255 * st["shadow"])))
            d.text((x, y), text, font=f, fill=color, stroke_width=st.get("stroke", 0), stroke_fill=stroke)
            x += _text_w(f, text, st) + space
    if shadow is not None:
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, size // 16)))
        shadow.alpha_composite(img)
        img = shadow
    arr = np.array(img)
    if alpha < 1.0:
        arr[..., 3] = (arr[..., 3] * alpha).astype(np.uint8)
    return arr[..., [2, 1, 0, 3]]                      # RGBA -> BGRA for OpenCV frames


def place(arr, frame_w, frame_h, st):
    """Top-left corner for a caption image, centred in the safe area."""
    h, w = arr.shape[:2]
    cx = SAFE["left"] + (frame_w - SAFE["left"] - SAFE["right"]) / 2
    x = int(cx - w / 2)
    y = int(frame_h * st["y"] - h / 2)
    y = max(int(frame_h * SAFE["top"]), min(y, int(frame_h * SAFE["bottom"]) - h))
    return max(0, x), max(0, y)


def blend(frame, arr, x, y):
    """Alpha-blends a BGRA image onto a BGR frame in place."""
    h, w = arr.shape[:2]
    H, W = frame.shape[:2]
    if x >= W or y >= H:
        return
    w2, h2 = min(w, W - x), min(h, H - y)
    a = arr[:h2, :w2, 3:4].astype(np.float32) / 255.0
    region = frame[y:y + h2, x:x + w2].astype(np.float32)
    frame[y:y + h2, x:x + w2] = (region * (1 - a) + arr[:h2, :w2, :3].astype(np.float32) * a).astype(np.uint8)


class Captioner:
    """Draws captions (and the opening hook text) onto frames by output time."""

    def __init__(self, words, style, frame_w=1080, frame_h=1920, keywords=(), hook_text="", hook_color="#22d3ee"):
        self.words, self.st, self.w, self.h = words, style, frame_w, frame_h
        self.pages = pages(words, style, frame_w - SAFE["left"] - SAFE["right"])
        self.keys = keywords
        self.cache = {}
        self.hook_text = hook_text.strip()
        self.hook_color = hook_color
        self.shown_words = set()

    def page_at(self, t):
        for k, p in enumerate(self.pages):
            if p["s"] <= t < p["e"]:
                return k, p
        return None, None

    def draw(self, frame, t):
        if self.hook_text and t < 2.8:
            self._hook(frame, t)
        k, p = self.page_at(t)
        if p is None:
            return None
        active = None
        for i in p["words"]:
            if self.words[i]["s"] <= t:
                active = i
        if active is not None:
            self.shown_words.add(active)
        step = 1.0
        anim = self.st.get("animation")
        if anim == "pop" and active is not None:
            since = t - self.words[active]["s"]
            step = 1.0 + 0.12 * max(0.0, 1 - since / 0.12) if since < 0.12 else 1.0
            step = round(step, 2)
        alpha = 1.0
        if anim == "fade":
            alpha = round(min(1.0, (t - p["s"]) / 0.15 + 0.2), 1)
        key = (k, active, step, alpha)
        if key not in self.cache:
            if len(self.cache) > 400:
                self.cache.clear()
            arr = render_page(p, self.words, active, self.keys, self.st, self.w, scale=step, alpha=alpha)
            self.cache[key] = (arr, place(arr, self.w, self.h, self.st))
        arr, (x, y) = self.cache[key]
        blend(frame, arr, x, y)
        return (x, y, arr.shape[1], arr.shape[0])

    def _hook(self, frame, t):
        key = ("hook", round(min(1.0, (2.8 - t) / 0.3), 1))
        if key not in self.cache:
            st = dict(STYLES["bold"], size=58, uppercase=True, stroke=0, shadow=0.0, color="#0b0f14", active="#0b0f14",
                      keyword=None, background=self.hook_color + "f0", words_per_line=4, max_lines=3)
            words = [{"w": w, "s": 0, "e": 0} for w in self.hook_text.split()]
            page = {"words": list(range(len(words)))}
            arr = render_page(page, words, None, set(), st, self.w, alpha=key[1])
            x = int(SAFE["left"] + (self.w - SAFE["left"] - SAFE["right"]) / 2 - arr.shape[1] / 2)
            self.cache[key] = (arr, (max(0, x), int(self.h * 0.15)))
        arr, (x, y) = self.cache[key]
        blend(frame, arr, x, y)

