"""Real footage for some scenes, from three free libraries, tried in this order for each scene:
  Pexels   free key (new keys are paused by Pexels for now; one saved earlier still works)
  Pixabay  free key (pixabay.com/api/docs); Pixabay Content License: free in videos, commercial use allowed
  NASA     NASA Image and Video Library, no key at all; NASA media is generally not copyrighted in the US
           (items that credit someone else are skipped), and the description never implies NASA endorses the channel
Keys are saved once with "Connect Stock Footage" (`python -m studio footage connect`); the description credits
every clip anyway. Config "footage_nasa": false leaves NASA out.

A portrait clip, or a 4K-ish landscape one, fills the screen (cover-cropped). An ordinary 1080p landscape clip
(most of NASA's) would turn soft if cropped that much, so it's shown whole across the middle over a blurred,
darkened copy of itself ("framed"): sharp, and nothing important is cut off.

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
PIXABAY_API = "https://pixabay.com/api/videos/"
NASA_API = "https://images-api.nasa.gov/search"
LICENCE = "Pexels License (free to use in videos, commercial use allowed, no attribution required)"
LICENCES = {"pexels": LICENCE,
            "pixabay": "Pixabay Content License (free to use in videos, commercial use allowed, no attribution required)",
            "nasa": "NASA media (generally not copyrighted in the US; used without implying NASA endorsement)"}
NAMES = {"pexels": "Pexels", "pixabay": "Pixabay", "nasa": "NASA"}
MAX_SHARE = 0.5           # at most this share of a Short's scenes use footage
MIN_HEIGHT = 1280         # the smallest file that still looks sharp scaled to 1920 tall
COVER_WIDE = 1440         # a landscape clip at least this tall can still be cropped to fill the screen sharply
MAX_BYTES = 400 << 20     # a download bigger than this is given up (NASA's originals can be huge)
NASA_MAX_SECONDS = 900    # longer NASA videos are talks and briefings, not footage
# NASA items with these in the title or description are people talking or someone else's material
NASA_SKIP = ("interview", "interviews", "briefing", "briefings", "conference", "press release", "q&a", "webinar",
             "podcast", "panel", "live coverage", "livestream", "broadcast", "talks", "lecture", "town hall",
             "remarks", "speech", "newscast", "©", "copyright", "courtesy", "rights reserved", "music video", "trailer")
_NASA_SKIP = re.compile("|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in NASA_SKIP))


def _key_file(site="pexels"):
    return data_dir("secrets") / f"{site}.txt"


def key(site="pexels"):
    k = (config.general().get(f"{site}_key") or "").strip()
    if not k:
        try:
            k = _key_file(site).read_text().strip()
        except OSError:
            k = ""
    return k


def site_of(k):
    """Which library a pasted key belongs to: "pixabay" (digits-hex), "pexels" (letters and digits) or None."""
    k = (k or "").strip()
    if re.fullmatch(r"\d{3,12}-[0-9a-f]{16,48}", k):
        return "pixabay"
    if re.fullmatch(r"[A-Za-z0-9]{20,100}", k):
        return "pexels"
    return None


def save_key(k):
    """Saves a Pexels or Pixabay key (told apart by its shape). Returns the file."""
    k = (k or "").strip()
    site = site_of(k)
    if not site:
        raise ValueError("that doesn't look like a Pixabay key (numbers, a dash, then letters and numbers, from "
                         "pixabay.com/api/docs) or a Pexels key (letters and numbers, from pexels.com/api)")
    p = _key_file(site)
    p.write_text(k)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def nasa_on():
    try:
        return config.general().get("footage_nasa", True) is not False
    except Exception:
        return True


def sources():
    """The libraries this computer can use, in the order they're tried."""
    return [s for s in ("pexels", "pixabay") if key(s)] + (["nasa"] if nasa_on() else [])


def ready():
    return bool(sources())


SEARCH = None          # tests swap this: (query, key) -> Pexels' JSON answer
PIXABAY_SEARCH = None  # tests swap this: (query, key) -> Pixabay's JSON answer
NASA_GET = None        # tests swap this: url -> NASA's JSON answer (search, an item's file list, its metadata)
DOWNLOAD = None        # tests swap this: (url, out) -> None


def _get_json(url, headers=None):
    from clipper import youtube
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-studio", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20, context=youtube._ctx()) as r:
        return json.loads(r.read(20 << 20).decode("utf-8", "replace"))


def _search(query):
    if SEARCH:
        return SEARCH(query, key())
    url = API + "?" + urllib.parse.urlencode({"query": query, "orientation": "portrait", "size": "medium",
                                              "per_page": 12})
    return _get_json(url, {"Authorization": key()})


def _pixabay_search(query):
    if PIXABAY_SEARCH:
        return PIXABAY_SEARCH(query, key("pixabay"))
    url = PIXABAY_API + "?" + urllib.parse.urlencode({"key": key("pixabay"), "q": query[:100], "per_page": 20,
                                                      "safesearch": "true"})
    return _get_json(url)


def _nasa_get(url):
    if NASA_GET:
        return NASA_GET(url)
    return _get_json(url)


def _download(url, out):
    if DOWNLOAD:
        return DOWNLOAD(url, out)
    from clipper import youtube
    tmp = Path(out).with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-studio"})
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=120, context=youtube._ctx()) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                got += len(b)
                if got > MAX_BYTES:
                    raise ValueError("file too big")
                f.write(b)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
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
        user = ((v.get("user") or {}).get("name") or "")[:80]
        cand = {"id": v["id"], "url": f["link"], "height": f.get("height"), "user": user, "page": v.get("url") or "",
                "duration": float(v.get("duration") or 0), "source": "pexels",
                "credit": f"Video by {user or 'a Pexels creator'} on Pexels"}
        if best is None or cand["height"] < best["height"]:
            best = cand
    return best


def choose_pixabay(answer, seconds, used=()):
    """The best clip in a Pixabay answer: portrait first, then landscape tall enough to crop, then 1080p landscape
    (shown framed); the smallest sharp file of each. None if nothing fits."""
    best = None
    for v in (answer or {}).get("hits") or []:
        if not isinstance(v, dict) or f"pixabay_{v.get('id')}" in used or float(v.get("duration") or 0) < seconds + 0.3:
            continue
        for f in (v.get("videos") or {}).values():
            if not isinstance(f, dict) or not str(f.get("url") or "").startswith("https://"):
                continue
            w, h = int(f.get("width") or 0), int(f.get("height") or 0)
            if w < h and h >= MIN_HEIGHT:
                rank = 0
            elif w >= h and h >= COVER_WIDE:
                rank = 1
            elif w >= h and h >= 1080:
                rank = 2
            else:
                continue
            user = str(v.get("user") or "")[:80]
            cand = {"id": f"pixabay_{v['id']}", "url": f["url"], "height": h, "user": user,
                    "page": str(v.get("pageURL") or ""), "duration": float(v.get("duration") or 0), "source": "pixabay",
                    "credit": f"Video by {user or 'a Pixabay creator'} on Pixabay", "_rank": (rank, h)}
            if best is None or cand["_rank"] < best["_rank"]:
                best = cand
    if best:
        best.pop("_rank")
    return best


def _nasa_seconds(meta):
    """A NASA item's length in seconds from its metadata.json (0 if it isn't there)."""
    for k, v in (meta or {}).items() if isinstance(meta, dict) else []:
        if not str(k).endswith(":Duration"):
            continue
        v = str(v).strip()
        m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", v)
        if m:
            return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        m = re.match(r"(\d+(?:\.\d+)?)\s*s", v)
        if m:
            return float(m[1])
    return 0.0


def nasa_candidates(answer, used=()):
    """NASA search results worth trying: real footage (not talks or briefings), not credited to someone else."""
    out = []
    for it in ((answer or {}).get("collection") or {}).get("items") or []:
        d = ((it or {}).get("data") or [{}])[0] or {}
        nid = str(d.get("nasa_id") or "")
        text = f" {d.get('title', '')} {d.get('description', '')} ".lower()
        if not nid or f"nasa_{nid}" in used or d.get("media_type") not in (None, "video") or not it.get("href") \
                or _NASA_SKIP.search(text):
            continue
        out.append({"id": f"nasa_{nid}", "nasa_id": nid, "manifest": it["href"], "title": str(d.get("title") or "")[:120],
                    "user": "NASA", "page": f"https://images.nasa.gov/details/{urllib.parse.quote(nid)}",
                    "source": "nasa", "credit": "Video: NASA"})
    return out


def _nasa_files(cand, seconds):
    """The item's mp4s, best first, once its metadata says it's long enough and not too long; [] otherwise."""
    files = [u for u in _nasa_get(cand["manifest"]) or [] if isinstance(u, str)]
    meta = next((u for u in files if u.endswith("metadata.json")), None)
    secs = _nasa_seconds(_nasa_get(meta)) if meta else 0.0
    if secs and not (seconds + 0.3 <= secs <= NASA_MAX_SECONDS):
        return [], secs
    order = ("~orig.mp4", "~large.mp4", "~medium.mp4")
    mp4 = sorted((u for u in files if any(u.endswith(o) for o in order)),
                 key=lambda u: next(i for i, o in enumerate(order) if u.endswith(o)))
    return [u.replace("http://", "https://", 1) for u in mp4], secs


def _layout(path):
    """"cover" (fill the screen) or "framed" (shown whole over a blurred copy) for a downloaded clip."""
    from clipper import media
    info = media.probe(path)
    w, h = info.get("width") or 0, info.get("height") or 0
    return ("framed" if w >= h and h < COVER_WIDE else "cover"), float(info.get("duration") or 0)


def fetch(query, seconds, used=()):
    """(local file, info) for a clip matching the search, downloaded once; or (None, reason). Tries each library
    this computer can use in turn (sources()); info has "source", "credit", "layout" and "skip" (seconds to start
    in, past a title card)."""
    if not ready():
        return None, "no footage library (run Connect Stock Footage)"
    guard.allow("web.read", f"stock footage search: {query}", record=False)
    from studio import engines
    why = []
    for site in sources():
        try:
            out, info = _fetch_from(site, query, seconds, used, engines.outputs_dir("footage"))
        except Exception as e:
            out, info = None, f"{NAMES[site]} search failed: {str(e)[:100]}"
        if out:
            info.setdefault("layout", "cover")
            info.setdefault("skip", 0.0)
            return out, info
        why.append(info)
    return None, "; ".join(why)


def _fetch_from(site, query, seconds, used, folder):
    if site in ("pexels", "pixabay"):
        answer = _search(query) if site == "pexels" else _pixabay_search(query)
        pick = choose if site == "pexels" else choose_pixabay
        info = pick(answer, seconds, used) or pick(answer, seconds)     # a fresh clip, else a used one again
        if not info:
            return None, f"{NAMES[site]}: nothing long enough for \"{query}\""
        name = f"pexels_{info['id']}_{info['height']}.mp4" if site == "pexels" else f"{info['id']}_{info['height']}.mp4"
        out = folder / name
        if not out.exists():
            try:
                _download(info["url"], out)
            except Exception as e:
                return None, f"{NAMES[site]} download failed: {str(e)[:100]}"
        info["layout"], _ = _layout(out)
        return out, info
    # NASA: a few results in turn; the first that's real footage, long enough and downloads is used
    every = nasa_candidates(_nasa_get(NASA_API + "?" + urllib.parse.urlencode({"q": query[:100], "media_type": "video"})))
    cands = ([c for c in every if c["id"] not in used] + [c for c in every if c["id"] in used])[:4]  # fresh first
    for c in cands:
        files, secs = _nasa_files(c, seconds)
        out = folder / f"{c['id']}.mp4"
        for u in files:
            if out.exists():
                break
            try:
                _download(u, out)
            except Exception:
                continue
        if not out.exists():
            continue
        try:
            layout, dur = _layout(out)
        except Exception:
            continue
        dur = dur or secs
        if dur < seconds + 0.3:
            continue
        # NASA clips usually open on a title card: start a little way in
        return out, dict(c, url=str(out), duration=dur, layout=layout, skip=round(min(8.0, 0.15 * dur, dur - seconds - 0.3), 2))
    return None, f"NASA: no footage for \"{query}\""


def _vf(W, H, fps, layout):
    look = "eq=saturation=1.05:contrast=1.03"
    if layout != "framed":
        return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1,{look}[b]"
    w4, h4 = W // 4, H // 4
    # the whole clip across the middle, over a blurred, darker copy of itself that fills the screen
    return (f"[0:v]fps={fps},split[a][f];[a]scale={w4}:{h4}:force_original_aspect_ratio=increase,crop={w4}:{h4},"
            f"boxblur=12:2,scale={W}:{H},eq=brightness=-0.14:saturation=0.9,setsar=1[bg];"
            f"[f]scale={W}:-2,setsar=1[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1,{look}[b]")


def scene_clip(src, seconds, out, shade_png=None, W=1080, H=1920, fps=30, start=0.0, layout="cover"):
    """The footage cut to one scene: W x H ("cover": cropped to fill; "framed": whole, over a blurred copy),
    30 fps, no sound, the shade on top. Returns out."""
    from clipper import media
    vf = _vf(W, H, fps, layout)
    # looped, so a clip that turns out a little shorter than the library said still fills the scene
    args = ["-y", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-t", f"{seconds:.3f}", "-i", str(src)]
    if shade_png:
        args += ["-i", str(shade_png), "-filter_complex", f"{vf};[b][1:v]overlay=0:0[v]", "-map", "[v]"]
    else:
        args += ["-filter_complex", vf, "-map", "[b]"]
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
    """Asks for a key in the terminal and saves it (the launcher runs this)."""
    print("Stock footage for the Shorts, all free:")
    print(" - NASA's video library needs no key and is used already" + ("" if nasa_on() else " (switched off in settings)") + ".")
    print(" - Pixabay: make a free account at https://pixabay.com/accounts/register, then open")
    print("   https://pixabay.com/api/docs while signed in. Your key is shown in green under \"Parameters\".")
    print("   (A Pexels key works too, if you have one.)")
    k = input("Paste your Pixabay key here (or just press Enter to use NASA only): ").strip()
    if not k:
        print("No key saved. NASA footage will be used." if nasa_on() else "No key saved.")
        return None
    p = save_key(k)
    site = site_of(k)
    try:
        info = _search("ocean waves") if site == "pexels" else _pixabay_search("ocean waves")
        n = len(info.get("videos") or info.get("hits") or [])
        print(f"Saved. {NAMES[site]} answered with {n} clips for a test search, so footage is ready.")
    except Exception as e:
        print(f"Saved to {p}, but a test search didn't work yet: {str(e)[:120]}")
    return p
