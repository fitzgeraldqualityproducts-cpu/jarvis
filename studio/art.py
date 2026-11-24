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
        for (i, w), r in zip(want, ans.get("images") or []):
            if r.get("ok") and Path(w["out"]).exists():
                have[i] = Path(w["out"])
    return have


def prepare(raw, pal, look="diagram", W=1080, H=1920):
    """The raw picture as a scene background: covers the frame, softened, dimmed, darker at the top and
    bottom (where the handle and captions sit). In the cinematic look ("hud") the picture stays bright and
    only a soft shade sits behind the counter and the caption. Returns a PIL RGB image."""
    from PIL import Image, ImageFilter
    img = Image.open(raw).convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(0.6 if look == "hud" else 1.5))
    if look == "hud":
        a = np.asarray(img).astype(np.float32)
        yy = np.linspace(0, 1, H)[:, None, None]
        xx = np.linspace(0, 1, W)[None, :, None]
        hud = np.clip(1 - np.hypot((xx - 0.18) / 0.55, (yy - 0.2) / 0.16), 0, 1) * 0.5     # behind the counter
        cap = np.exp(-((yy - 0.66) / 0.07) ** 2) * 0.42                                   # behind the caption
        shade = np.clip(hud + cap, 0, 0.6)
        return Image.fromarray(np.clip(a * (1 - shade), 0, 255).astype("uint8"), "RGB")
    a = np.asarray(img).astype(np.float32) * DIM
    bg = np.array([int(pal["background"].lstrip("#")[k:k + 2], 16) for k in (0, 2, 4)], dtype=np.float32)
    yy = np.linspace(0, 1, H)[:, None, None]
    edge = np.clip(np.maximum(1 - yy / 0.18, (yy - 0.62) / 0.38), 0, 1) * 0.75   # top band and caption area
    a = a * (1 - edge) + bg * edge
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8"), "RGB")
