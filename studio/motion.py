"""Camera moves through a still picture (the "2.5D" look): with the picture's depth map (studio/art.py enhance),
near things move more than far ones, so a still AI picture plays like a short camera shot.

depth_clip() renders one scene's background as a silent H.264 clip (1080x1920, 30 fps) that the Remotion
composition plays under the counter and captions. Frame 0 is exactly the still picture (so the hook's first frame
and the seamless loop still match), then the camera eases into its move. Each scene gets one of a few moves in
turn, so consecutive scenes don't feel the same. Everything is plain numpy + OpenCV on the processor."""
import math
from pathlib import Path

import numpy as np

# (name, zoom of the nearest things, sideways drift in px, vertical drift in px) at the end of the scene
MOVES = [("push in", 0.13, 0, 0), ("drift right", 0.07, 46, 0), ("rise", 0.08, 0, -40), ("drift left", 0.07, -46, 0)]
FAR_SHARE = 0.3          # far things move this share of what near things do
FOCUS = (0.5, 0.45)      # the camera pushes in towards here (fraction of width, height)


def _ease(k):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, k)))


def load_depth(path, W=1080, H=1920):
    """The depth map covered to W x H like its picture, smoothed (so object edges stretch softly instead of
    tearing) and scaled 0 (far) .. 1 (near)."""
    import cv2
    from PIL import Image
    from studio import art
    d = Image.open(path)
    a = np.asarray(d).astype(np.float32)
    a = a / (65535.0 if a.max() > 255 else 255.0)
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    a = np.asarray(art.cover(img, W, H)).astype(np.float32) / 255.0
    a = cv2.GaussianBlur(a, (0, 0), 9)
    lo, hi = np.percentile(a, 2), np.percentile(a, 98)
    return np.clip((a - lo) / max(1e-4, hi - lo), 0, 1).astype(np.float32)


def move_for(index):
    return MOVES[index % len(MOVES)]


def frame_maps(depth, k, move, W=1080, H=1920, grid=None):
    """cv2.remap maps for progress k (0..1) of a move: each output pixel looks back to where it came from."""
    _, zoom, dx, dy = move
    xs, ys = grid if grid is not None else np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    e = _ease(k)
    near = FAR_SHARE + (1 - FAR_SHARE) * depth          # how strongly this pixel follows the camera
    s = 1.0 + zoom * e * near
    cx, cy = FOCUS[0] * W, FOCUS[1] * H
    # an overall zoom that keeps the frame's edges covered while things shift sideways
    edge = 1.0 + (abs(dx) / W + abs(dy) / H) * 2.2 * e
    mx = cx + (xs - cx) / (s * edge) - dx * e * (near - 0.5)
    my = cy + (ys - cy) / (s * edge) - dy * e * (near - 0.5)
    return mx.astype(np.float32), my.astype(np.float32)


def depth_clip(img, depth, seconds, index, out, fps=30, shade=None):
    """Writes the scene's camera move to out (MP4, no sound). img: PIL RGB W x H (unshaded); depth: load_depth();
    shade: the cinematic look's (H, W, 1) shade, put on after the move so it stays behind the counter and caption.
    Returns out."""
    import cv2
    from clipper import media
    import subprocess
    a = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    H, W = a.shape[:2]
    grid = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    move = move_for(index)
    n = max(1, int(math.ceil(seconds * fps)))
    keep = None if shade is None else (1.0 - shade)
    proc = media.popen(["-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", fps, "-i", "-",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", 17, "-pix_fmt", "yuv420p",
                        "-r", fps, "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for f in range(n):
            if f == 0:
                fr = a
            else:
                mx, my = frame_maps(depth, f / max(1, n - 1), move, W, H, grid)
                fr = cv2.remap(a, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)
            if keep is not None:
                fr = np.clip(fr.astype(np.float32) * keep, 0, 255).astype(np.uint8)
            proc.stdin.write(np.ascontiguousarray(fr).tobytes())
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        code = proc.wait()
    if code != 0 or not Path(out).exists():
        raise media.MediaError("the camera-move clip didn't encode: " + proc.stderr.read().decode("utf-8", "replace")[-300:])
    return Path(out)
