"""AI illustrations behind the scenes (SDXL-Turbo, see studio/engines.py). Each scene gets one picture of
what its line is about (objects or scenery, never text, numbers or people); Jarvis still draws every number
and word on top, so nothing the viewer reads comes from the image model. The picture is dimmed and softened
so the numbers stay easy to read, and the raw pictures are kept (on D:\\Jarvis on the PC) so a re-render
doesn't make them again."""
import hashlib
from pathlib import Path

import numpy as np

from studio import engines

STYLE = {"diagram": ("stylised digital illustration, clean shapes, soft lighting, deep blue and teal tones, "
                     "cinematic, uncluttered, vertical composition, no text"),
         "hud": ("stylised 3D render, simple low-poly shapes, first-person view, bright natural daylight, soft "
                 "shadows, vivid sky, wide angle, vertical composition, no text")}
GEN_W, GEN_H = 512, 896          # SDXL-Turbo's sweet spot, portrait; scaled up to 1080x1920
HD_W, HD_H = 768, 1344           # config art_size "hd" (OFF until Connor OKs a PC sample): 1.5x more detail per side
DIM = 0.45                       # brightness kept: the numbers on top must stay the brightest thing
LICENCE = "AI illustration made with SDXL-Turbo (Stability AI Community License; commercial use allowed under $1M revenue)"


def subject(scene, line):
    s = " ".join(str(scene.get("art") or "").split())[:160] if isinstance(scene, dict) else ""
    return s or " ".join(str(line).split())[:160]


NO_PEOPLE = "empty of people"          # SDXL-Turbo ignores negative prompts, so this is said in the prompt itself


def _with_look(art, scene):
    """The scene's picture words plus the Short's one fixed description of its main subject (scene "look"), so
    the same satellite or jet looks the same in every picture (Short 19's Terra changed shape every scene)."""
    lk = " ".join(str(scene.get("look") or "").split())[:140] if isinstance(scene, dict) else ""
    return f"{art}, {lk}" if lk else art


def prompt(scene, line, look="diagram"):
    tail = f", {NO_PEOPLE}" if isinstance(scene, dict) and scene.get("look") is not None else ""
    return f"{_with_look(subject(scene, line), scene)}, {STYLE.get(look, STYLE['diagram'])}{tail}"


def gen_size():
    """(width, height) the AI pictures are painted at. Config "art_size": "hd" paints them at 768x1344 so less is
    invented when they are enlarged to 1080x1920 (the text and diagrams are always drawn at full size); the
    default stays 512x896 until a sample on the PC shows the RTX 2060 handles it well."""
    try:
        from studio import config
        if str(config.general().get("art_size") or "").lower() == "hd":
            return HD_W, HD_H
    except Exception:
        pass
    return GEN_W, GEN_H


def _key(p, seed):
    w, h = gen_size()
    return hashlib.sha1(f"{p}|{seed}|{w}x{h}".encode()).hexdigest()[:12]


def raw_pictures(video_id, scenes, lines, look="diagram"):
    """{scene index: raw picture path}, making the ones not made before in one SDXL-Turbo job."""
    folder = engines.outputs_dir("videos", str(video_id), "art")
    want, have = [], {}
    for i, sc in enumerate(scenes):
        p = prompt(sc, lines[i] if i < len(lines) else "", look)
        seed = int(video_id) * 100 + i
        out = folder / f"art_{i + 1:02d}_{_key(p, seed)}.png"
        if out.exists():
            have[i] = out
        else:
            want.append((i, {"prompt": p, "out": str(out), "seed": seed}))
    if want:
        with engines.heavy("AI pictures (SDXL-Turbo)"):
            ans = engines.run("sdxl", "sdxl_worker", {"images": [w for _, w in want], "width": gen_size()[0],
                                                      "height": gen_size()[1], "steps": 4})
        errors = []
        for (i, w), r in zip(want, ans.get("images") or []):
            if r.get("ok") and Path(w["out"]).exists():
                have[i] = Path(w["out"])
            elif r.get("error"):
                errors.append(r["error"])
        if not have and errors:                    # say why, so the report shows the real reason
            raise engines.EngineError(f"SDXL-Turbo made no pictures: {errors[0][:240]}")
    return have


PHOTO_W, PHOTO_H = 1080, 1920     # real photos are framed at full size (Video 35's was squeezed into 512x896)
DROPOUT_MAX = 0.0015              # share of pixels in thin pure-black lines a real photo may have
COVER_KEEP = 0.55                 # a photo fills the frame when cropping keeps at least this share of it


def photo_dropouts(im):
    """Share of the picture in thin pure-black lines inside bright surface: the missing-data dashes and seams of old
    survey scans (Video 35's opening Moon photo). A natural photo has dark areas, but not 1-3 px black lines with
    bright surface right above and below (or left and right)."""
    g = np.asarray(im.convert("L"), dtype=np.int16)
    if g.shape[0] < 16 or g.shape[1] < 16:
        return 0.0
    black = g <= 6
    hits = np.zeros_like(black)
    for d in (2, 3, 4):
        up, down = np.roll(g, d, 0), np.roll(g, -d, 0)
        left, right = np.roll(g, d, 1), np.roll(g, -d, 1)
        hits |= black & (((up > 50) & (down > 50)) | ((left > 50) & (right > 50)))
    hits[:4, :] = hits[-4:, :] = hits[:, :4] = hits[:, -4:] = False
    return float(hits.mean())


def framed_photo(src, out, W=PHOTO_W, H=PHOTO_H, info=None):
    """A real photo for a portrait scene at full 1080x1920 (no shrinking and re-enlarging). It fills the frame
    when cropping keeps most of it; a wide photo is fitted to the width at the top and blends softly into a
    brighter blurred copy of itself below, so there is no hard edge and no empty black half (Video 35's opening).
    Raises ValueError for a photo with missing-data lines. info (a dict) gets what was done. Returns out."""
    from PIL import Image, ImageFilter, ImageEnhance
    im = Image.open(src).convert("RGB")
    drop = photo_dropouts(im)
    if drop > DROPOUT_MAX:
        raise ValueError(f"the photo has missing-data lines ({drop:.2%} of it)")
    k = max(W / im.width, H / im.height)
    keep = min(1.0, (W / k) / im.width) * min(1.0, (H / k) / im.height)
    bg = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    fill = 1.0
    if keep < COVER_KEEP:                          # too much would be cut off: show it whole, blended in
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(36))).enhance(0.8)
        f = W / im.width
        fg = im.resize((W, max(1, round(im.height * f))), Image.LANCZOS)
        if fg.height > H:
            fg = fg.crop((0, (fg.height - H) // 2, W, (fg.height - H) // 2 + H))
        y = max(0, int(H * 0.08))
        fade = 90                                  # the photo's top and bottom edges melt into the blur
        a = np.ones((fg.height, 1), dtype=np.float32)
        r = np.linspace(0, 1, min(fade, fg.height // 3))
        a[:len(r), 0] = r
        a[fg.height - len(r):, 0] = r[::-1]
        mask = Image.fromarray((np.repeat(a, W, 1) * 255).astype("uint8"), "L")
        bg.paste(fg, (0, y), mask)
        fill = round(min(1.0, fg.height / H), 3)
    else:
        keep = round(keep, 3)
    bg.save(out)
    if info is not None:
        info.update({"size": [im.width, im.height], "fill": fill, "kept": round(keep, 3), "dropouts": round(drop, 5)})
    return out


def shots_on():
    """Config "shots" (OFF until Connor approves a sample): scenes the storyboard gives 2-3 shots get a new picture
    for each, so what's on screen changes about every 2 seconds instead of one picture per sentence; the planner
    may also use the wave and ruler diagrams, and each cut gets the storyboard's own sound and transition."""
    try:
        from studio import config
        return config.general().get("shots", False) is True
    except Exception:
        return False


def shot_pictures(video_id, scenes, look="diagram"):
    """{(scene, shot): raw picture} for every scene's 2nd and 3rd storyboard shot, made in one SDXL-Turbo job. The
    first shot is the scene's own picture (raw_pictures). Never raises: a shot that didn't come out is skipped."""
    folder = engines.outputs_dir("videos", str(video_id), "art")
    want, have = [], {}
    for i, sc in enumerate(scenes):
        for j, sh in enumerate((sc.get("shots") or [])[1:3] if isinstance(sc, dict) else [], 1):
            p = (f"{_with_look(' '.join(str(sh.get('art') or '').split())[:160], sc)}, "
                 f"{STYLE.get(look, STYLE['diagram'])}" + (f", {NO_PEOPLE}" if sc.get("look") is not None else ""))
            seed = int(video_id) * 100 + i + 5000 * j
            out = folder / f"shot_{i + 1:02d}_{j + 1}_{_key(p, seed)}.png"
            if out.exists():
                have[(i, j)] = out
            else:
                want.append(((i, j), {"prompt": p, "out": str(out), "seed": seed}))
    if want:
        try:
            with engines.heavy("more AI pictures for the shots (SDXL-Turbo)"):
                engines.run("sdxl", "sdxl_worker", {"images": [w for _, w in want], "width": gen_size()[0], "height": gen_size()[1],
                                                    "steps": 4})
        except Exception:
            pass
        for key, w in want:
            if Path(w["out"]).exists():
                have[key] = Path(w["out"])
    return have


def fit_shots(scenes, lines, shots):
    """Drops the shots OpenCLIP says don't show their line (under FIT_MIN: the scene's own picture is used longer
    instead). Returns (kept shots, {"scores", "dropped"}). Kept as they are when OpenCLIP can't run."""
    if not shots or not fit_on():
        return shots, {}
    keys = sorted(shots)
    texts = [f"{' '.join(str(lines[i] if i < len(lines) else '').split())[:200]}. "
             f"{scenes[i]['shots'][j].get('shows') or scenes[i]['shots'][j].get('art')}"[:300] for i, j in keys]
    try:
        sims = clip_scores([shots[k] for k in keys], texts)
    except Exception:
        return shots, {}
    lo = _cfg_num("picture_fit_min", FIT_MIN)
    score = {k: float(sims[n][n]) for n, k in enumerate(keys)}
    kept = {k: v for k, v in shots.items() if score[k] >= lo}
    return kept, {"scores": {f"{i + 1}.{j + 1}": round(v, 3) for (i, j), v in score.items()},
                  "dropped": [f"{i + 1}.{j + 1}" for (i, j) in keys if score[(i, j)] < lo]}


# ---------- does each picture fit its line? (OpenCLIP, see workers/clip_worker.py) ----------

FIT_MIN = 0.22          # a picture scoring under this (OpenCLIP cosine with its line) gets more takes
FIT_FLOOR = 0.17        # still under this after the takes: a plain background instead of an off-topic picture
TAKES = 2               # extra pictures tried for a weak scene
CLIP_SCORE = None       # tests swap this: (image paths, texts) -> [[similarity]]


def fit_on():
    try:
        from studio import config
        return config.general().get("picture_fit", True) is not False
    except Exception:
        return True


def _cfg_num(key, default):
    try:
        from studio import config
        return float(config.general().get(key, default))
    except (TypeError, ValueError, Exception):
        return default


def clip_scores(images, texts):
    """[[similarity of image i with text j]] from OpenCLIP (in the sdxl environment)."""
    if CLIP_SCORE:
        return CLIP_SCORE([str(p) for p in images], list(texts))
    ans = engines.run("sdxl", "clip_worker", {"images": [str(p) for p in images], "texts": list(texts)})
    if ans.get("error"):
        raise engines.EngineError(ans["error"])
    return ans["sims"]


def line_text(scene, line):
    """What a scene's picture should show: its line and its planned subject."""
    return f"{' '.join(str(line).split())[:200]}. {subject(scene, line)}"[:300]


def plain(out, W=GEN_W, H=GEN_H):
    """A plain dark-blue gradient (the background for a scene no picture fitted)."""
    from PIL import Image
    y = np.linspace(0, 1, H)[:, None, None]
    top, bottom = np.array([18, 32, 58]), np.array([6, 10, 22])
    img = (top * (1 - y) + bottom * y).astype("uint8").repeat(W, axis=1)
    Image.fromarray(img, "RGB").save(out)
    return Path(out)


def fit(video_id, scenes, lines, raws, look="diagram"):
    """Checks every picture against its line; weak ones get TAKES more tries and the best fit is kept; one that
    still doesn't fit becomes a plain background. Returns (raws, report {"scores", "retaken", "plain"})."""
    order = sorted(raws)
    texts = [line_text(scenes[i], lines[i] if i < len(lines) else "") for i in order]
    sims = clip_scores([raws[i] for i in order], texts)
    score = {i: float(sims[k][k]) for k, i in enumerate(order)}
    lo, floor = _cfg_num("picture_fit_min", FIT_MIN), _cfg_num("picture_fit_floor", FIT_FLOOR)
    weak = [i for i in order if score[i] < lo]
    rep = {"scores": {i + 1: round(score[i], 3) for i in order}, "retaken": [], "plain": []}
    if weak:
        folder = engines.outputs_dir("videos", str(video_id), "art")
        want = []
        for i in weak:
            p = prompt(scenes[i], lines[i] if i < len(lines) else "", look)
            for t in range(1, TAKES + 1):
                seed = int(video_id) * 100 + i + 1000 * t
                out = folder / f"art_{i + 1:02d}_{_key(p, seed)}.png"
                want.append((i, out, {"prompt": p, "out": str(out), "seed": seed}))
        todo = [w for _, out, w in want if not out.exists()]
        if todo:
            with engines.heavy("more AI pictures (SDXL-Turbo)"):
                engines.run("sdxl", "sdxl_worker", {"images": todo, "width": gen_size()[0], "height": gen_size()[1], "steps": 4})
        takes = [(i, out) for i, out, _ in want if out.exists()]
        if takes:
            s2 = clip_scores([o for _, o in takes], [texts[order.index(i)] for i, _ in takes])
            for k, (i, out) in enumerate(takes):
                if float(s2[k][k]) > score[i]:
                    score[i], raws[i] = float(s2[k][k]), out
                    if i + 1 not in rep["retaken"]:
                        rep["retaken"].append(i + 1)
        for i in weak:
            if score[i] < floor and i > 0:          # the hook keeps a picture: its first frame must look finished
                raws[i] = plain(folder / f"plain_{i + 1:02d}.png")
                rep["plain"].append(i + 1)
        rep["scores"] = {i + 1: round(score[i], 3) for i in order}
    return raws, rep


ENHANCE_LICENCE = ("sharpened with Real-ESRGAN x4plus (BSD-3-Clause); depth from Depth Anything V2 Small "
                   "(Apache-2.0)")


def is_native(path, W=1080):
    """True for a picture already at the Short's full width (a framed real photo)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width >= W
    except Exception:
        return False


def _hd(raw):
    return Path(raw).with_name(Path(raw).stem + "_hd.png")


def _depth(raw):
    return Path(raw).with_name(Path(raw).stem + "_depth.png")


def enhance(raws, sharpen=True, depth=True):
    """{index: {"hd": path?, "depth": path?}} for the raw pictures: each made 4x sharper (Real-ESRGAN) and its depth
    map (Depth Anything V2 Small), in one job on the graphics card. Kept next to the raw picture, so a re-render
    reuses them. Never raises: a picture without them just keeps the plain one (and the flat push-in)."""
    out = {i: {} for i in raws}
    want, native = [], set()
    for i, rp in raws.items():
        hd, dp = _hd(rp), _depth(rp)
        if is_native(rp):
            native.add(i)                     # already 1080 wide (a framed real photo): nothing to enlarge
            if depth and not dp.exists():
                want.append(i)
            continue
        if sharpen and not hd.exists():
            want.append(i)
        elif depth and not dp.exists():
            want.append(i)
    note = ""
    if want:
        try:
            md = engines.models_dir() or engines.TOOLS / "models"
            with engines.heavy("Sharper pictures and depth"):
                ans = engines.run("sdxl", "enhance_worker", {
                    "models": str(md),
                    "images": [{"src": str(raws[i]), **({"hd": str(_hd(raws[i]))} if sharpen and i not in native
                                                        else {}),
                                **({"depth": str(_depth(raws[i]))} if depth else {})} for i in want]})
            errs = [r["error"] for r in ans.get("images") or [] if r.get("error")]
            note = errs[0] if errs else ""
        except Exception as e:
            note = str(e)[:200]
    for i, rp in raws.items():
        if i in native:
            out[i]["hd"] = Path(rp)               # full size as it is
        elif sharpen and _hd(rp).exists():
            out[i]["hd"] = _hd(rp)
        if depth and _depth(rp).exists():
            out[i]["depth"] = _depth(rp)
    if note:
        out["_note"] = note
    return out


def prepare(raw, pal, look="diagram", W=1080, H=1920, hd=None, parts=False):
    """The raw picture as a scene background: covers the frame, softened, dimmed, darker at the top and
    bottom (where the handle and captions sit). In the cinematic look ("hud") the picture stays bright and
    only a soft shade sits behind the counter and the caption. Returns a PIL RGB image."""
    from PIL import Image, ImageFilter
    img = Image.open(hd if hd and Path(hd).exists() else raw).convert("RGB")
    img = cover(img, W, H)
    # the sharpened picture needs no softening; the plain 512 px one is softened a little so it isn't blocky
    sharp = hd and Path(hd).exists()
    img = img.filter(ImageFilter.GaussianBlur(0.0 if sharp else 0.6 if look == "hud" else 1.5))
    if look == "hud":
        a = np.asarray(img).astype(np.float32)
        shade = hud_shade(W, H)
        shaded = Image.fromarray(np.clip(a * (1 - shade), 0, 255).astype("uint8"), "RGB")
        return (shaded, img, shade) if parts else shaded
    a = np.asarray(img).astype(np.float32) * DIM
    bg = np.array([int(pal["background"].lstrip("#")[k:k + 2], 16) for k in (0, 2, 4)], dtype=np.float32)
    yy = np.linspace(0, 1, H)[:, None, None]
    edge = np.clip(np.maximum(1 - yy / 0.18, (yy - 0.62) / 0.38), 0, 1) * 0.75   # top band and caption area
    a = a * (1 - edge) + bg * edge
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"), "RGB")


def cover(img, W=1080, H=1920):
    """The picture scaled to cover W x H and cropped in the middle."""
    from PIL import Image
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def hud_shade(W=1080, H=1920):
    """The cinematic look's soft shade (0-0.6) behind the counter and the caption, as an (H, W, 1) array."""
    yy = np.linspace(0, 1, H)[:, None, None]
    xx = np.linspace(0, 1, W)[None, :, None]
    hud = np.clip(1 - np.hypot((xx - 0.18) / 0.55, (yy - 0.2) / 0.16), 0, 1) * 0.5     # behind the counter
    cap = np.exp(-((yy - 0.66) / 0.07) ** 2) * 0.42                                   # behind the caption
    return np.clip(hud + cap, 0, 0.6).astype(np.float32)
