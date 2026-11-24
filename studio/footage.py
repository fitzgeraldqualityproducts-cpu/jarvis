"""Real stock footage for some scenes, from Pexels (free; its licence allows use in videos, also commercial, with no
attribution required, though the description credits it anyway). Needs a free Pexels API key, saved once with
"Connect Stock Footage" (`python -m studio footage connect`). Without a key nothing changes.

The visual planner gives a scene a short "footage" search (like "rocket launch") only when its subject is a real
thing a camera could film. Jarvis looks for a portrait (vertical) clip at least as long as the scene, downloads
the smallest file that is still sharp at 1080x1920, and cuts it to the scene (cover-cropped, 30 fps, no sound,
with the cinematic look's shade behind the counter and caption). At most half the scenes use footage, never the
hook scene (its first frame must match the loop's last), and the AI picture stays the fallback for each scene.
Downloads are kept on D:\\Jarvis on the PC, so the same clip is never fetched twice."""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from studio import config, data_dir, guard

API = "https://api.pexels.com/videos/search"
LICENCE = "Pexels License (free to use in videos, commercial use allowed, no attribution required)"
MAX_SHARE = 0.5           # at most this share of a Short's scenes use footage
MIN_HEIGHT = 1280         # the smallest file that still looks sharp scaled to 1920 tall


def _key_file():
    return data_dir("secrets") / "pexels.txt"


def key():
    k = (config.general().get("pexels_key") or "").strip()
    if not k:
        try:
            k = _key_file().read_text().strip()
        except OSError:
            k = ""
    return k


def save_key(k):
    k = (k or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20,100}", k):
        raise ValueError("that doesn't look like a Pexels API key (letters and numbers, from pexels.com/api)")
    p = _key_file()
    p.write_text(k)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def ready():
    return bool(key())


SEARCH = None          # tests swap this: (query, key) -> Pexels' JSON answer
DOWNLOAD = None        # tests swap this: (url, out) -> None


def _search(query):
    if SEARCH:
        return SEARCH(query, key())
    from clipper import youtube
    url = API + "?" + urllib.parse.urlencode({"query": query, "orientation": "portrait", "size": "medium",
                                              "per_page": 12})
    req = urllib.request.Request(url, headers={"Authorization": key(), "User-Agent": "jarvis-studio"})
    with urllib.request.urlopen(req, timeout=20, context=youtube._ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _download(url, out):
    if DOWNLOAD:
        return DOWNLOAD(url, out)
    from clipper import youtube
    tmp = Path(out).with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-studio"})
    with urllib.request.urlopen(req, timeout=120, context=youtube._ctx()) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    tmp.replace(out)


def choose(answer, seconds, used=()):
    """The best clip in a Pexels answer for a scene this long: {"id", "url", "file", "user", "page"} or None."""
    best = None
    for v in (answer or {}).get("videos") or []:
        if not isinstance(v, dict) or v.get("id") in used or float(v.get("duration") or 0) < seconds + 0.3:
            continue
        files = [f for f in v.get("video_files") or [] if isinstance(f, dict) and f.get("link")
                 and str(f.get("file_type", "")).endswith("mp4") and (f.get("height") or 0) >= MIN_HEIGHT
                 and (f.get("width") or 0) < (f.get("height") or 0)]
        if not files:
            continue
        f = min(files, key=lambda f: f.get("height") or 0)           # the smallest that's still sharp
        cand = {"id": v["id"], "url": f["link"], "height": f.get("height"),
                "user": ((v.get("user") or {}).get("name") or "")[:80], "page": v.get("url") or "",
                "duration": float(v.get("duration") or 0)}
        if best is None or cand["height"] < best["height"]:
            best = cand
    return best


def fetch(query, seconds, used=()):
    """(local file, info) for a portrait clip matching the search, downloaded once; or (None, reason)."""
    if not ready():
        return None, "no Pexels key"
    guard.allow("web.read", f"stock footage search: {query}", record=False)
    try:
        answer = _search(query)
        info = choose(answer, seconds, used) or choose(answer, seconds)   # a fresh clip, else a used one again
    except Exception as e:
        return None, f"search failed: {str(e)[:120]}"
    if not info:
        return None, f"nothing long enough for \"{query}\""
    from studio import engines
    out = engines.outputs_dir("footage") / f"pexels_{info['id']}_{info['height']}.mp4"
    if not out.exists():
        try:
            _download(info["url"], out)
        except Exception as e:
            return None, f"download failed: {str(e)[:120]}"
    return out, info


def scene_clip(src, seconds, out, shade_png=None, W=1080, H=1920, fps=30, start=0.0):
    """The footage cut to one scene: cover-cropped to W x H, 30 fps, no sound, the shade on top. Returns out."""
    from clipper import media
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1,"
          "eq=saturation=1.05:contrast=1.03")
    # looped, so a clip that turns out a little shorter than Pexels said still fills the scene
    args = ["-y", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-t", f"{seconds:.3f}", "-i", str(src)]
    if shade_png:
        args += ["-i", str(shade_png), "-filter_complex", f"[0:v]{vf}[b];[b][1:v]overlay=0:0[v]", "-map", "[v]"]
    else:
        args += ["-vf", vf]
    media.run_h264(args + ["-an"], ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], 18)
    return Path(out)


def shade_png(shade, out):
    """The cinematic shade as a black PNG with transparency, for ffmpeg to lay over footage."""
    import numpy as np
    from PIL import Image
    a = (np.clip(shade[:, :, 0], 0, 1) * 255).astype("uint8")
    rgba = np.zeros(a.shape + (4,), dtype="uint8")
    rgba[:, :, 3] = a
    Image.fromarray(rgba, "RGBA").save(out)
    return Path(out)


def pick_scenes(scenes, spans):
    """Which scenes get footage: those the planner gave a search, not the hook, longest first, at most half."""
    want = [k for k, sc in enumerate(scenes) if k > 0 and isinstance(sc, dict)
            and str(sc.get("footage") or "").strip() and spans[k] >= 1.2]
    cap = int(len(scenes) * MAX_SHARE)
    return sorted(sorted(want, key=lambda k: -spans[k])[:cap])


def connect():
    """Asks for the key in the terminal and saves it (the launcher runs this)."""
    print("Stock footage from Pexels (free).")
    print("1. Make a free account at https://www.pexels.com/join and open https://www.pexels.com/api/")
    print("2. Ask for an API key (it's free and instant), copy it, and paste it here.")
    k = input("Pexels API key: ").strip()
    p = save_key(k)
    try:
        info = _search("ocean waves")
        n = len(info.get("videos") or [])
        print(f"Saved. Pexels answered with {n} clips for a test search, so footage is ready.")
    except Exception as e:
        print(f"Saved to {p}, but a test search didn't work yet: {str(e)[:120]}")
