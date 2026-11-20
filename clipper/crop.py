"""Smart 9:16 framing: find the faces, follow whoever is talking, never cut off a head.

1. Sample the clip 5 times a second and find faces (OpenCV's YuNet face model, about 230 KB,
   downloaded once; the built-in Haar detector if that fails).
2. Link faces over time into tracks (people), dropping one-off false detections.
3. Who's talking: mouth-area movement per face while there's speech, plus the transcript's speaker
   turns. Shots switch only when someone else clearly takes over (no jitter).
4. Pick a layout for the clip:
     track   one person: a 9:16 window that glides smoothly to keep the speaker centred
     split   two people trading lines quickly: both stacked, top and bottom
     stack   streamer layout (small facecam in a corner + gameplay): facecam on top, game below
     fit     no clear faces (screens, gameplay, slides): the whole frame over a blurred copy of itself
   Vertical videos are just scaled.
The plan is saved per clip and render.py follows it frame by frame."""
import urllib.request

import numpy as np

from clipper import MODELS, media

YUNET = MODELS / "face_detection_yunet_2023mar.onnx"
YUNET_URLS = ["https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"]
SAMPLE_FPS = 5
ANALYSIS_W = 640
_det = {}


def _cv2():
    import cv2
    return cv2


def detector(w, h):
    cv2 = _cv2()
    if "kind" not in _det:
        try:
            if not YUNET.exists():
                MODELS.mkdir(exist_ok=True)
                try:
                    import ssl
                    import certifi
                    ctx = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    ctx = None
                data = b""
                for url in YUNET_URLS:
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "jarvis-clipper"})
                        data = urllib.request.urlopen(req, timeout=60, context=ctx).read()
                    except Exception:
                        data = b""
                    if len(data) > 100000:
                        break
                if len(data) < 100000:
                    raise RuntimeError("couldn't download the face model")
                YUNET.write_bytes(data)
            _det["yunet"] = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.6, 0.3, 50)
            _det["kind"] = "yunet"
        except Exception as e:
            print(f"(clipper: using the basic face detector: {e})")
            _det["haar"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            _det["kind"] = "haar"
    if _det["kind"] == "yunet":
        _det["yunet"].setInputSize((w, h))
    return _det["kind"]


def faces(frame):
    """[(x, y, w, h, confidence)] in the frame's pixels."""
    cv2 = _cv2()
    h, w = frame.shape[:2]
    kind = detector(w, h)
    if kind == "yunet":
        _, found = _det["yunet"].detect(frame)
        if found is None:
            return []
        return [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])) for f in found if f[2] > 12]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found = _det["haar"].detectMultiScale(gray, 1.15, 6, minSize=(max(24, w // 40), max(24, w // 40)))
    return [(float(x), float(y), float(fw), float(fh), 0.8) for x, y, fw, fh in found]


def analyze(src, start, duration, progress=lambda f, d="": None):
    """Samples the clip and returns {"w", "h", "samples": [{"t", "faces": [...], "act": [...]}]}
    with times relative to `start` and coordinates normalised 0..1."""
    cv2 = _cv2()
    reader = media.FrameReader(src, start, duration, SAMPLE_FPS, max_h=ANALYSIS_W, max_w=ANALYSIS_W)
    samples, prev = [], None
    try:
        k = 0
        while True:
            frame = reader.read()
            if frame is None:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found = faces(frame)
            act = []
            for (x, y, fw, fh, _) in found:
                mx0, my0 = int(x + fw * 0.2), int(y + fh * 0.6)          # mouth/jaw area
                mx1, my1 = int(x + fw * 0.8), int(min(gray.shape[0], y + fh * 1.05))
                if prev is not None and mx1 > mx0 + 2 and my1 > my0 + 2:
                    a = cv2.resize(gray[max(0, my0):my1, max(0, mx0):mx1], (24, 12)).astype(np.float32)
                    b = cv2.resize(prev[max(0, my0):my1, max(0, mx0):mx1], (24, 12)).astype(np.float32)
                    act.append(float(np.mean(np.abs(a - b))))
                else:
                    act.append(0.0)
            samples.append({"t": round(k / SAMPLE_FPS, 3),
                            "faces": [(x / reader.w, y / reader.h, fw / reader.w, fh / reader.h, c) for x, y, fw, fh, c in found],
                            "act": act})
            prev = gray
            k += 1
            if k % 25 == 0:
                progress(min(0.99, k / (duration * SAMPLE_FPS)), "finding faces")
    finally:
        reader.close()
    return {"w": reader.w, "h": reader.h, "samples": samples}


def tracks(analysis, min_share=0.25):
    """Links faces over time. Returns [{"id", "boxes": {sample_index: (cx, cy, w, h)}, "act": {...}}]."""
    out = []
    n = len(analysis["samples"])
    for i, s in enumerate(analysis["samples"]):
        used = set()
        for j, (x, y, w, h, c) in enumerate(s["faces"]):
            cx, cy = x + w / 2, y + h / 2
            best, bd = None, 1e9
            for t in out:
                last = max(t["boxes"])
                if i - last > SAMPLE_FPS * 2 or id(t) in used:
                    continue
                lx, ly, lw, lh = t["boxes"][last]
                d = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
                if d < max(lw, w) * 1.2 and d < bd:
                    best, bd = t, d
            if best is None:
                best = {"id": len(out), "boxes": {}, "act": {}}
                out.append(best)
            used.add(id(best))
            best["boxes"][i] = (cx, cy, w, h)
            best["act"][i] = s["act"][j] if j < len(s["act"]) else 0.0
    keep = [t for t in out if len(t["boxes"]) >= max(3, n * min_share)]
    for k, t in enumerate(keep):
        t["id"] = k
        t["size"] = float(np.median([b[2] for b in t["boxes"].values()]))
        t["x"] = float(np.median([b[0] for b in t["boxes"].values()]))
        t["y"] = float(np.median([b[1] for b in t["boxes"].values()]))
    return keep


def _interp(track, n):
    """A box for every sample (holding the nearest known one through gaps)."""
    known = sorted(track["boxes"])
    out = []
    for i in range(n):
        j = min(known, key=lambda k: abs(k - i))
        out.append(track["boxes"][j])
    return out


def speaking_track(trs, n, speech, hold=1.5):
    """Which track is talking at each sample, with hysteresis. speech[i] = someone is talking."""
    if not trs:
        return [None] * n
    if len(trs) == 1:
        return [0] * n
    act = np.zeros((len(trs), n))
    for k, t in enumerate(trs):
        for i, v in t["act"].items():
            act[k, i] = v / max(0.02, t["size"])               # bigger faces move more pixels
    win = SAMPLE_FPS                                            # one-second moving average
    kernel = np.ones(win) / win
    smooth = np.vstack([np.convolve(a, kernel, mode="same") for a in act])
    cur = int(np.argmax(smooth.sum(axis=1)))
    since, out = 0, []
    for i in range(n):
        if speech[i]:
            best = int(np.argmax(smooth[:, i]))
            if best != cur and smooth[best, i] > smooth[cur, i] * 1.35 and since >= hold * SAMPLE_FPS:
                cur, since = best, 0
        since += 1
        out.append(cur)
    return out


def plan(analysis, duration, words=(), src_w=1920, src_h=1080, out_w=1080, out_h=1920, prefer=None):
    """Chooses the layout and makes the camera path. Returns a JSON-able plan."""
    samples = analysis["samples"]
    n = len(samples)
    aspect = src_w / max(1, src_h)
    if aspect <= out_w / out_h * 1.15:
        return {"layout": "scale", "reason": "already vertical"}
    trs = tracks(analysis)
    speech = [any(w["s"] <= s["t"] <= w["e"] + 0.3 for w in words) for s in samples] if words else [True] * n
    with_face = sum(1 for s in samples if s["faces"]) / max(1, n)
    if not trs or with_face < 0.4:
        return {"layout": "fit", "reason": "no steady faces"}
    big = [t for t in trs if t["size"] >= 0.035]
    # a streamer's facecam: one small face that stays in a corner (podcast faces sit mid-height)
    small_corner = [t for t in trs if t["size"] < 0.12 and (t["x"] < 0.3 or t["x"] > 0.7) and
                    (t["y"] < 0.33 or t["y"] > 0.67) and len(t["boxes"]) > n * 0.6]
    if prefer in (None, "stack") and small_corner and len(trs) == 1:
        t = small_corner[0]
        boxes = _interp(t, n)
        cx = float(np.median([b[0] for b in boxes]))
        cy = float(np.median([b[1] for b in boxes]))
        fw = float(np.median([b[2] for b in boxes]))
        return {"layout": "stack", "reason": "facecam + gameplay", "cam": [cx, cy, fw], "top_share": 0.36}
    who = speaking_track(big or trs, n, speech)
    people = big or trs
    if len(people) >= 2 and prefer in (None, "split"):
        switches = sum(1 for a, b in zip(who, who[1:]) if a != b)
        both = sum(1 for s in samples if len(s["faces"]) >= 2) / max(1, n)
        if both > 0.7 and switches / max(1.0, duration) > 1 / 4.0:
            order = sorted(range(len(people)), key=lambda k: people[k]["x"])[:2]
            return {"layout": "split", "reason": "two people trading lines",
                    "people": [[people[k]["x"], people[k]["y"], people[k]["size"]] for k in order]}
    # one window following the speaker, smoothed
    crop_w = (src_h * out_w / out_h) / src_w                  # crop width as a share of the frame width
    paths = [_interp(t, n) for t in people]
    target = []
    for i in range(n):
        k = who[i] if who[i] is not None else 0
        target.append(paths[k][i][0])
    xs = _smooth_path(target, crop_w)
    shots = [i for i in range(1, n) if who[i] != who[i - 1]]
    return {"layout": "track", "reason": f"following the speaker ({len(people)} on screen)", "crop_w": crop_w,
            "x": [round(x, 4) for x in xs], "fps": SAMPLE_FPS, "shots": [round(i / SAMPLE_FPS, 2) for i in shots],
            "speaker": who, "face_y": round(float(np.median([t["y"] for t in people])), 3)}


def _smooth_path(target, crop_w, dead=0.06, ease=0.18):
    """Camera centre over time: holds still inside a dead zone, eases toward the speaker otherwise,
    and jumps (a cut) when the speaker changes to someone far away."""
    half = crop_w / 2
    x = min(1 - half, max(half, target[0] if target else 0.5))
    out = []
    for t in target:
        t = min(1 - half, max(half, t))
        if abs(t - x) > crop_w * 0.9:
            x = t                                   # a different person: cut, don't pan across
        elif abs(t - x) > dead * crop_w:
            x += (t - x) * ease
        out.append(x)
    return out


def camera_at(p, t):
    """Camera centre x (0..1) at time t (seconds into the clip) for a track plan."""
    xs = p["x"]
    if not xs:
        return 0.5
    f = t * p["fps"]
    i = int(f)
    if i >= len(xs) - 1:
        return xs[-1]
    a = f - i
    return xs[i] * (1 - a) + xs[i + 1] * a


def visible_share(p, analysis):
    """For QA: how often the chosen speaker's whole face is inside the crop."""
    if p.get("layout") != "track":
        return 1.0
    trs = tracks(analysis)
    people = [t for t in trs if t["size"] >= 0.035] or trs
    n = len(analysis["samples"])
    ok = total = 0
    for i in range(n):
        k = p["speaker"][i] if i < len(p["speaker"]) else None
        if k is None or k >= len(people) or i not in people[k]["boxes"]:
            continue
        cx, _, w, _ = people[k]["boxes"][i]
        c = p["x"][i]
        total += 1
        if cx - w / 2 >= c - p["crop_w"] / 2 - 0.005 and cx + w / 2 <= c + p["crop_w"] / 2 + 0.005:
            ok += 1
    return ok / total if total else 1.0
