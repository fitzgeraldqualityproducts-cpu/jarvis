"""Final-cut review: Claude looks at the finished Short before it can be uploaded, like an editor would.

Jarvis takes the very first frame and one frame from the middle of every script line, lays them out on one
contact sheet (numbered, with their times), and Claude (Connor's plan; the Read tool only, to open that one
picture) checks them against what the narrator says at each point:
  captions readable, spelled right, not cut off or covering the thing being explained
  the picture fits the line (no wrong planet, wrong animal, wrong number on screen)
  no broken AI artwork (garbled text or symbols inside a picture, melted hands or faces, obvious glitches)
  nothing misleading, nothing that looks like someone else's logo or brand
  the first frame makes sense on its own (it's what people see before they decide to stay)
Each problem is "minor" or "major". Config "final_review": "advise" (default: problems are saved with the Short,
shown in QA and turned into lessons, but never stop it), "block" (a major problem fails QA, so the Short isn't
uploaded) or "off". It runs only after every other QA check passed, once per render."""
import json
import os

from studio import config, db

MODES = ("advise", "block", "off")
MAX_FRAMES = 10
TILE_W, TILE_H = 324, 576          # 0.3 of 1080 x 1920: captions still readable


def _config_mode():
    m = str(config.general().get("final_review", "advise")).lower()
    return m if m in MODES else "advise"


mode = _config_mode          # tests swap this (it's "off" in tests unless a test turns it on)


def moments(rep, duration):
    """[(seconds, line index or None)]: the first frame, then the middle of each line (at most MAX_FRAMES)."""
    starts = [float(x) for x in rep.get("line_starts") or []]
    out = [(0.0, None)]
    for k in range(len(starts) - 1):
        a, b = starts[k], starts[k + 1]
        if b - a > 0.3:
            out.append((round(a + 0.6 * (b - a), 2), k))
    if len(out) == 1:                                  # no line times: evenly through the video
        out += [(round(duration * f, 2), None) for f in (0.2, 0.4, 0.6, 0.8)]
    if len(out) > MAX_FRAMES:
        keep = [out[0]] + [out[1 + round(i * (len(out) - 2) / (MAX_FRAMES - 2))] for i in range(MAX_FRAMES - 1)]
        out = list(dict.fromkeys(keep))
    return out


def sheet(path, times, out):
    """A contact sheet of the frames at these times, numbered with their times. Returns out."""
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    tiles = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, fr = cap.read()
        img = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)) if ok else Image.new("RGB", (1080, 1920))
        tiles.append(img.resize((TILE_W, TILE_H)))
    cap.release()
    cols = min(5, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    head = 34
    board = Image.new("RGB", (cols * (TILE_W + 8) + 8, rows * (TILE_H + head + 8) + 8), (40, 40, 40))
    d = ImageDraw.Draw(board)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    for i, (img, t) in enumerate(zip(tiles, times)):
        x, y = 8 + (i % cols) * (TILE_W + 8), 8 + (i // cols) * (TILE_H + head + 8)
        d.text((x + 4, y + 4), f"{i + 1}  ({t:.1f} s)", fill=(255, 255, 255), font=font)
        board.paste(img, (x, y + head))
    board.save(out)
    return out


def review(video_id):
    """{"ok", "score", "first_frame", "problems": [{frame, issue, severity}], "major", "mode"} or None (off)."""
    m = mode()
    if m == "off":
        return None
    from clipper import media
    from studio import claude, script
    v = db.video(video_id)
    meta = json.loads(v["meta"] or "{}")
    rep = meta.get("render") or {}
    row = script.latest(video_id, "passed")
    lines = [str(ln.get("text", "")) for ln in json.loads(row["structure"] or "{}").get("lines") or []] if row else []
    dur = media.probe(v["path"])["duration"]
    when = moments(rep, dur)
    out = os.path.join(os.path.dirname(v["path"]), "final_review.png")
    sheet(v["path"], [t for t, _ in when], out)
    said = "\n".join(f"Frame {i + 1} ({t:.1f} s): " + (f'the narrator is saying "{lines[k]}"' if k is not None and k < len(lines)
                                                   else "the first frame, before anything is said" if i == 0 else "")
                     for i, (t, k) in enumerate(when))
    up = meta.get("upload") or {}
    ans = claude.ask_json(
        f"""You are the final editor of a science YouTube Short, checking it before upload. The picture is a contact
sheet of {len(when)} frames from the finished 1080x1920 video, numbered in order with their times.
Title: {up.get('title', '')}

What is being said at each frame:
{said}

Check every frame for:
- captions: readable, spelled right, not cut off, not covering the thing being explained
- the picture fits what is being said (wrong object, wrong number on screen, unrelated scene)
- broken AI artwork: garbled text or symbols inside the picture, melted hands or faces, obvious glitches
- anything misleading, or that looks like someone else's logo or brand
- frame 1 must make sense on its own and make a viewer want to stay
Say only what you can actually see. "major" means a viewer would notice and it hurts trust or understanding;
small style preferences are "minor". If everything is fine, give no problems.

JSON only: {{"score": 0-10, "first_frame": 0-10, "problems": [{{"frame": 3, "issue": "what is wrong, briefly",
"severity": "minor|major"}}]}}""", timeout=300, purpose=f"final-cut review: video {video_id}", images=[out])
    probs = []
    for p in (ans.get("problems") if isinstance(ans, dict) else None) or []:
        if isinstance(p, dict) and str(p.get("issue") or "").strip():
            probs.append({"frame": int(p["frame"]) if str(p.get("frame", "")).isdigit() else None,
                          "issue": str(p["issue"])[:200],
                          "severity": "major" if str(p.get("severity")).lower() == "major" else "minor"})
    res = {"score": _num(ans, "score"), "first_frame": _num(ans, "first_frame"), "problems": probs[:12],
           "major": sum(p["severity"] == "major" for p in probs), "mode": m, "frames": len(when)}
    res["ok"] = not (m == "block" and res["major"])
    meta = json.loads(db.video(video_id)["meta"] or "{}")
    db.set_video(video_id, meta=json.dumps(dict(meta, final_review=res)))
    if res["major"]:
        from studio import lessons, topics
        lessons.record("video", [f"final review: {p['issue']}" for p in probs if p["severity"] == "major"][:5],
                       topics.topic(v["topic_id"]) if v.get("topic_id") else None, video_id)
    return res


def _num(ans, key):
    try:
        return max(0.0, min(10.0, float((ans or {}).get(key))))
    except (TypeError, ValueError, AttributeError):
        return None


def detail(res):
    if not res:
        return ""
    head = f"score {res['score']}/10, first frame {res['first_frame']}/10"
    if not res["problems"]:
        return head + ", no problems"
    return head + "; " + "; ".join(f"{p['severity']}: frame {p['frame']}: {p['issue']}" for p in res["problems"][:4])
