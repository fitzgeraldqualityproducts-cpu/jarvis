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
DIM = 0.45                       # brightness kept: the numbers on top must stay the brightest thing
LICENCE = "AI illustration made with SDXL-Turbo (Stability AI Community License; commercial use allowed under $1M revenue)"


def subject(scene, line):
    s = " ".join(str(scene.get("art") or "").split())[:160] if isinstance(scene, dict) else ""
    return s or " ".join(str(line).split())[:160]


def prompt(scene, line, look="diagram"):
    return f"{subject(scene, line)}, {STYLE.get(look, STYLE['diagram'])}"


def _key(p, seed):
    return hashlib.sha1(f"{p}|{seed}|{GEN_W}x{GEN_H}".encode()).hexdigest()[:12]


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
            ans = engines.run("sdxl", "sdxl_worker", {"images": [w for _, w in want], "width": GEN_W,
                                                      "height": GEN_H, "steps": 4})
        errors = []
        for (i, w), r in zip(want, ans.get("images") or []):
            if r.get("ok") and Path(w["out"]).exists():
                have[i] = Path(w["out"])
            elif r.get("error"):
                errors.append(r["error"])
        if not have and errors:                    # say why, so the report shows the real reason
            raise engines.EngineError(f"SDXL-Turbo made no pictures: {errors[0][:240]}")
    return have


ENHANCE_LICENCE = ("sharpened with Real-ESRGAN x4plus (BSD-3-Clause); depth from Depth Anything V2 Small "
                   "(Apache-2.0)")


def _hd(raw):
    return Path(raw).with_name(Path(raw).stem + "_hd.png")


def _depth(raw):
    return Path(raw).with_name(Path(raw).stem + "_depth.png")


def enhance(raws, sharpen=True, depth=True):
    """{index: {"hd": path?, "depth": path?}} for the raw pictures: each made 4x sharper (Real-ESRGAN) and its depth
    map (Depth Anything V2 Small), in one job on the graphics card. Kept next to the raw picture, so a re-render
    reuses them. Never raises: a picture without them just keeps the plain one (and the flat push-in)."""
    out = {i: {} for i in raws}
    want = []
    for i, rp in raws.items():
        hd, dp = _hd(rp), _depth(rp)
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
                    "images": [{"src": str(raws[i]), **({"hd": str(_hd(raws[i]))} if sharpen else {}),
                                **({"depth": str(_depth(raws[i]))} if depth else {})} for i in want]})
            errs = [r["error"] for r in ans.get("images") or [] if r.get("error")]
            note = errs[0] if errs else ""
        except Exception as e:
            note = str(e)[:200]
    for i, rp in raws.items():
        if sharpen and _hd(rp).exists():
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
