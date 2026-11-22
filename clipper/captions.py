"""Captions: big, readable, synced to every word, inside the area YouTube doesn't cover.

Eight styles (plus three older ones kept so nothing breaks), any setting can be overridden per
channel in "caption_overrides" or per clip in the editor:
  clean        Clean Professional: white sentence-case words, soft shadow, two short lines
  bold         Bold Viral: chunky UPPERCASE, thick outline, the word being said turns yellow
  minimal      Minimal White: small, lowercase-friendly white text, no outline, low on screen
  gaming       Gaming: huge condensed UPPERCASE, two words at a time, green pop, heavy outline
  podcast      Podcast: clean bold words, the word being said sits on a coloured pill
  highlight    Highlighted Keywords: calm white text, key words marked with a highlighter box
  pop          Animated Pop: one big word at a time, bouncing in as it's said
  cinematic    Cinematic: elegant spaced serif capitals that fade in, low and small
  (kinetic, educational, premium: the earlier styles)
"auto" picks by the channel's niche and the clip's kind of moment, and learning.py can switch a
channel to the style that really performs better.

Animations: none, pop (the spoken word swells), bounce (each line rises in), fade, reveal (words
appear as they're said). Highlighting is never random: it marks the clip's punchline and key words
(chosen by Claude from what's said), names, numbers, strong reaction/emotion words and words said
noticeably louder, at most about one word in six.

Words are drawn with Pillow (free Google Fonts, downloaded once, with the computer's own bold fonts
as a fallback) and blended onto each frame by render.py. Only the words actually said are shown:
lone "um"/"uh" can be left out, names can be corrected with "fixes", nothing is reworded."""
import os
import re
import urllib.request

import numpy as np

from clipper import MODELS

FONT_DIR = MODELS / "fonts"
FONTS = {
    "Poppins-Black": "ofl/poppins/Poppins-Black.ttf",
    "Poppins-ExtraBold": "ofl/poppins/Poppins-ExtraBold.ttf",
    "Poppins-Bold": "ofl/poppins/Poppins-Bold.ttf",
    "Poppins-SemiBold": "ofl/poppins/Poppins-SemiBold.ttf",
    "Poppins-Medium": "ofl/poppins/Poppins-Medium.ttf",
    "Anton": "ofl/anton/Anton-Regular.ttf",
    "BebasNeue": "ofl/bebasneue/BebasNeue-Regular.ttf",
    "ArchivoBlack": "ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "DMSerifDisplay": "ofl/dmserifdisplay/DMSerifDisplay-Regular.ttf",
    "LuckiestGuy": "apache/luckiestguy/LuckiestGuy-Regular.ttf",
}
FONT_BASE = "https://raw.githubusercontent.com/google/fonts/main/"
SYSTEM_FALLBACKS = ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]

_BASE = {"font": "Poppins-SemiBold", "size": 66, "uppercase": False, "color": "#ffffff", "active": "#ffffff",
         "keyword": None, "stroke": 0, "stroke_color": "#000000", "shadow": 0.75, "background": None,
         "active_box": None, "keyword_box": None, "tracking": 0, "words_per_line": 4, "max_lines": 2, "y": 0.68,
         "animation": "none", "dim": 1.0}
STYLES = {k: dict(_BASE, **v) for k, v in {
    "clean": {},
    "bold": {"font": "Poppins-ExtraBold", "size": 80, "uppercase": True, "active": "{highlight}", "keyword": "{brand}",
             "stroke": 9, "shadow": 0.6, "words_per_line": 3, "y": 0.66, "animation": "pop"},
    "minimal": {"font": "Poppins-Medium", "size": 54, "shadow": 0.55, "words_per_line": 5, "y": 0.74, "dim": 0.75,
                "animation": "fade"},
    "gaming": {"font": "Anton", "size": 132, "uppercase": True, "active": "#4ade80", "keyword": "{highlight}",
               "stroke": 10, "shadow": 0.7, "words_per_line": 2, "max_lines": 1, "y": 0.64, "animation": "pop"},
    "podcast": {"font": "Poppins-Bold", "size": 70, "active": "#0b0f14", "active_box": "{highlight}",
                "keyword": "{highlight}", "stroke": 0, "shadow": 0.6, "words_per_line": 3, "y": 0.68, "animation": "none"},
    "highlight": {"font": "Poppins-Bold", "size": 68, "keyword": "#0b0f14", "keyword_box": "{highlight}",
                  "stroke": 0, "shadow": 0.6, "words_per_line": 4, "y": 0.68, "animation": "bounce"},
    "pop": {"font": "LuckiestGuy", "size": 140, "uppercase": True, "active": "{highlight}", "keyword": "{brand}",
            "stroke": 10, "shadow": 0.7, "words_per_line": 1, "max_lines": 1, "y": 0.62, "animation": "pop"},
    "cinematic": {"font": "DMSerifDisplay", "size": 58, "uppercase": True, "color": "#f5f1e8", "active": "#ffffff",
                  "tracking": 6, "shadow": 0.8, "words_per_line": 4, "y": 0.78, "animation": "fade", "dim": 0.7},
    # the earlier styles, kept so existing settings keep working
    "kinetic": {"font": "Anton", "size": 128, "uppercase": True, "active": "#4ade80", "keyword": "{highlight}",
                "stroke": 8, "shadow": 0.7, "words_per_line": 2, "max_lines": 1, "y": 0.64, "animation": "pop"},
    "educational": {"font": "Poppins-Bold", "size": 62, "active": "{highlight}", "keyword": "{brand}", "shadow": 0.0,
                    "background": "#000000b8", "words_per_line": 5, "y": 0.70},
    "premium": {"font": "Poppins-SemiBold", "size": 56, "color": "#f5f5f4", "shadow": 0.55, "words_per_line": 5,
                "y": 0.72, "animation": "fade", "dim": 0.62},
}.items()}
STYLE_NAMES = {"clean": "Clean Professional", "bold": "Bold Viral", "minimal": "Minimal White", "gaming": "Gaming",
               "podcast": "Podcast", "highlight": "Highlighted Keywords", "pop": "Animated Pop", "cinematic": "Cinematic",
               "kinetic": "Kinetic (older)", "educational": "Educational (older)", "premium": "Premium (older)"}
ANIMATIONS = ["none", "pop", "bounce", "fade", "reveal"]
POSITIONS = {"top": 0.24, "middle": 0.5, "lower": 0.68, "bottom": 0.76}
NICHE_STYLE = [("gaming", "gaming"), ("stream", "gaming"), ("entertainment", "bold"), ("comedy", "pop"),
               ("podcast", "podcast"), ("interview", "podcast"), ("commentary", "bold"), ("education", "highlight"),
               ("technology", "highlight"), ("finance", "highlight"), ("ai", "highlight"), ("motivation", "cinematic"),
               ("personal development", "cinematic"), ("business", "clean")]
CATEGORY_STYLE = {"gameplay": "gaming", "achievement": "gaming", "fail": "pop", "funny": "bold", "one_liner": "pop",
                  "high_energy": "bold", "reaction": "bold", "educational": "highlight", "story": "podcast",
                  "emotional": "cinematic", "suspense": "cinematic"}
# YouTube's buttons sit on the right and its title/description at the bottom: stay out of both
SAFE = {"left": 80, "right": 150, "top": 0.12, "bottom": 0.80}
REACTION = re.compile(r"^(wow|what|no|yes|why|insane|crazy|unbelievable|literally|never|always|love|hate|dead|"
                      r"amazing|terrible|worst|best|huge|massive|shocked|scared|angry|happy|sad|cried|screamed|"
                      r"exactly|seriously|actually|impossible|incredible|wild|broke|rich|free|secret)$", re.I)
_font_cache = {}


def choose_style(channel, suggestion=None, learned=None, category=None):
    style = channel.get("caption_style", "auto")
    if style in STYLES:
        return style
    best = (learned or {}).get("caption_style")
    if best in STYLES:
        return best
    if channel.get("preset_style") in STYLES:       # the clipping preset's look (e.g. podcast -> podcast)
        return channel["preset_style"]
    if suggestion in STYLES:
        return suggestion
    if category in CATEGORY_STYLE:
        return CATEGORY_STYLE[category]
    for niche in channel.get("niches", []):
        for key, s in NICHE_STYLE:
            if key in niche.lower():
                return s
    return "bold"


def resolve(style, channel, overrides=None):
    """A style's full settings with the channel's (then the clip's) changes and brand colours."""
    brand = channel.get("brand", {})
    st = dict(STYLES.get(style) or STYLES["bold"])
    for over in (channel.get("caption_overrides") or {}, overrides or {}):
        for k, v in over.items():
            if k == "position" and v in POSITIONS:
                st["y"] = POSITIONS[v]
            elif k in st and v not in (None, ""):
                st[k] = v
    for k in ("active", "keyword", "color", "active_box", "keyword_box", "stroke_color"):
        if isinstance(st.get(k), str):
            st[k] = st[k].replace("{highlight}", brand.get("highlight", "#facc15")).replace("{brand}", brand.get("color", "#22d3ee"))
    st["size"] = max(44, min(180, int(st["size"])))          # readability floor on a phone
    st["words_per_line"] = max(1, min(6, int(st["words_per_line"])))
    st["max_lines"] = max(1, min(3, int(st["max_lines"])))
    st["stroke"] = max(0, min(16, int(st["stroke"])))
    st["y"] = max(0.15, min(0.85, float(st["y"])))
    if st.get("animation") not in ANIMATIONS:
        st["animation"] = "none"
    st["name"] = style if style in STYLES else "bold"
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
    """Indexes of words to highlight. Never random: the clip's own key words (Claude picks them from
    what's said: punchline, names, emotional or surprising words), numbers, names, strong reaction
    words and words said with emphasis, at most about one in `every` so highlighting still means
    something. Hint words win over the rest."""
    hint = {h.lower().strip(".,!?\"'") for h in hints for h in str(h).split()}
    marks = {}
    for i, w in enumerate(words):
        bare = w["w"].lower().strip(".,!?\"'")
        raw = w["w"].strip(".,!?\"'")
        if len(bare) < 3 and not bare.isdigit():
            continue
        prev = words[i - 1]["w"] if i else "."
        name = raw[:1].isupper() and not re.search(r"[.!?]$", prev) and bare not in ("i'm", "i've", "i'll", "i'd")
        rank = 0 if bare in hint else 1 if re.search(r"\d", bare) else 2 if w.get("emph") else \
            3 if name else 4 if REACTION.match(bare) else None
        if rank is not None:
            marks[i] = rank
    limit = max(1, len(words) // every)
    keep = sorted(marks, key=lambda i: (marks[i], i))[:limit]
    return set(keep)


def drop_fillers(words):
    """Leaves out a lone "um", "uh", "erm"... from the captions (they're cut from the sound too)."""
    return [w for w in words if not re.match(r"^(um+|uh+|erm+|er|ah+|hmm+|mm+)[.,!?]*$", w["w"].strip(), re.I)]


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
            wide = len(_lines(f, cur + [i], words, st, width * 0.96)) > st["max_lines"]
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
        return f.getlength(text) + st.get("stroke", 0) * 2 + st.get("tracking", 0) * max(0, len(text) - 1)
    except Exception:
        return len(text) * st["size"] * 0.55


def _lines(f, idx, words, st, width):
    lines, cur = [], []
    for i in idx:
        trial = cur + [i]
        extra = (len(trial) - 1) * f.size * 0.16 if (st.get("active_box") or st.get("keyword_box")) else 0
        if cur and (len(trial) > st["words_per_line"] or
                    _text_w(f, " ".join(_disp(words[j]["w"], st) for j in trial), st) + extra > width):
            lines.append(cur)
            cur = [i]
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _draw_text(d, xy, text, f, fill, st, stroke_fill):
    """Draws text, letter by letter when the style spaces its letters out (tracking)."""
    tr = st.get("tracking", 0)
    sw = st.get("stroke", 0)
    if not tr:
        d.text(xy, text, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke_fill)
        return
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, stroke_width=sw, stroke_fill=stroke_fill)
        x += f.getlength(ch) + tr


def render_page(page, words, active, keywords, st, frame_w, scale=1.0, alpha=1.0, reveal=False):
    """The caption image for one moment: a BGRA numpy array. reveal=True hides the words not said
    yet (their space is kept, so the line doesn't jump around)."""
    from PIL import Image, ImageDraw, ImageFilter
    size = max(20, int(st["size"] * scale))
    f = font(st["font"], size)
    width = frame_w - SAFE["left"] - SAFE["right"]
    lines = _lines(f, page["words"], words, st, width)[:st["max_lines"] + 1]
    space = _text_w(f, " ", dict(st, stroke=0, tracking=0)) + st.get("tracking", 0)
    if st.get("active_box") or st.get("keyword_box"):
        space += size * 0.16                              # room for the highlighter box
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
    abox, kbox = _rgba(st.get("active_box")), _rgba(st.get("keyword_box"))
    dim = st.get("dim", 1.0)
    stroke = _rgba(st.get("stroke_color", "#000000"))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if st.get("shadow") else None
    sd = ImageDraw.Draw(shadow) if shadow else None
    asc = size * 0.12
    for li, ln in enumerate(lines):
        x = (W - widths[li]) / 2
        y = pad + li * line_h
        for i in ln:
            text = _disp(words[i]["w"], st)
            tw = _text_w(f, text, st)
            if reveal and active is not None and i > active:
                x += tw + space
                continue
            box = abox if (abox and i == active) else kbox if (kbox and i in keywords) else None
            if box:
                m = size * 0.14
                d.rounded_rectangle([x - m, y + asc - m * 0.4, x + tw + m, y + size * 1.08 + m * 0.2],
                                    radius=int(size * 0.22), fill=box)
            if i == active and (act != base or abox):
                color = act
            elif key and i in keywords:
                color = key
            else:
                color = base if dim >= 1 or active is None or i <= (active or 0) else base[:3] + (int(base[3] * dim),)
            if sd is not None and not box:
                off = max(2, size // 18)
                _draw_text(sd, (x + off, y + off), text, f, (0, 0, 0, int(255 * st["shadow"])), dict(st, stroke=0), None)
            _draw_text(d, (x, y), text, f, color, st if not box else dict(st, stroke=0), stroke)
            x += tw + space
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

    HOOK_SECONDS = 2.8

    def __init__(self, words, style, frame_w=1080, frame_h=1920, keywords=(), hook_text="", hook_color="#22d3ee"):
        self.words, self.st, self.w, self.h = words, style, frame_w, frame_h
        self.pages = pages(words, style, frame_w - SAFE["left"] - SAFE["right"])
        self.keys = keywords
        self.cache = {}
        self.hook_text = hook_text.strip()
        self.hook_color = hook_color
        self.hook_box = None
        self.shown_words = set()
        self.overlaps = 0

    def page_at(self, t):
        for k, p in enumerate(self.pages):
            if p["s"] <= t < p["e"]:
                return k, p
        return None, None

    def draw(self, frame, t):
        hook = self._hook(frame, t) if self.hook_text and t < self.HOOK_SECONDS else None
        k, p = self.page_at(t)
        if p is None:
            return None
        active = None
        for i in p["words"]:
            if self.words[i]["s"] <= t:
                active = i
        if active is not None:
            self.shown_words.add(active)
        step, alpha, dy = 1.0, 1.0, 0
        anim = self.st.get("animation")
        if anim == "pop" and active is not None:
            since = t - self.words[active]["s"]
            grow = 0.22 if self.st.get("words_per_line", 3) == 1 else 0.12
            step = round(1.0 + grow * max(0.0, 1 - since / 0.12), 2) if since < 0.12 else 1.0
        elif anim == "fade":
            alpha = round(min(1.0, (t - p["s"]) / 0.15 + 0.2), 1)
        elif anim == "bounce":
            e = min(1.0, (t - p["s"]) / 0.16)
            dy = int(round((1 - e) ** 2 * self.st["size"] * 0.35 / 4)) * 4
            alpha = round(min(1.0, 0.4 + e), 1)
        reveal = anim == "reveal"
        key = (k, active, step, alpha, reveal)
        if key not in self.cache:
            if len(self.cache) > 400:
                self.cache.clear()
            arr = render_page(p, self.words, active, self.keys, self.st, self.w, scale=step, alpha=alpha, reveal=reveal)
            self.cache[key] = (arr, place(arr, self.w, self.h, self.st))
        arr, (x, y) = self.cache[key]
        y += dy
        if hook:                                # never on top of the opening text: move below it
            hx, hy, hw, hh = hook
            if y < hy + hh and y + arr.shape[0] > hy:
                y = hy + hh + 24
                self.overlaps += 1
        blend(frame, arr, x, y)
        return (x, y, arr.shape[1], arr.shape[0])

    def _hook(self, frame, t):
        key = ("hook", round(min(1.0, (self.HOOK_SECONDS - t) / 0.3), 1))
        if key not in self.cache:
            st = dict(STYLES["bold"], size=58, uppercase=True, stroke=0, shadow=0.0, color="#0b0f14", active="#0b0f14",
                      keyword=None, background=self.hook_color + "f0", words_per_line=4, max_lines=3, tracking=0,
                      active_box=None, keyword_box=None)
            words = [{"w": w, "s": 0, "e": 0} for w in self.hook_text.split()]
            page = {"words": list(range(len(words)))}
            arr = render_page(page, words, None, set(), st, self.w, alpha=key[1])
            x = int(SAFE["left"] + (self.w - SAFE["left"] - SAFE["right"]) / 2 - arr.shape[1] / 2)
            self.cache[key] = (arr, (max(0, x), int(self.h * 0.15)))
        arr, (x, y) = self.cache[key]
        blend(frame, arr, x, y)
        return (x, y, arr.shape[1], arr.shape[0])
