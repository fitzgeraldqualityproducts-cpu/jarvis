"""Phase 2: checks that the whole free production toolchain is really there and works on this Mac.

check()        each tool: installed?, version, licence read from the installed package, cost $0
benchmark(dir) makes a real 1080x1920 test Short end to end (Kokoro narration -> Pillow graphics
               -> FFmpeg H.264/AAC) and times every step, so later phases know what the Mac can do
Results are saved in kv "toolchain" and shown in `python -m studio toolchain`."""
import json
import platform
import shutil
import sys
import time
from importlib import metadata
from pathlib import Path

from studio import ROOT, db, guard

W, H, FPS = 1080, 1920, 30


def _pkg(name):
    try:
        md = metadata.metadata(name)
        lic = md.get("License-Expression") or md.get("License") or ""
        if not lic or len(lic) > 60:
            lic = next((c.split("::")[-1].strip() for c in (md.get_all("Classifier") or []) if c.startswith("License ::")), lic[:60])
        return {"installed": True, "version": metadata.version(name), "license": lic or "unknown"}
    except metadata.PackageNotFoundError:
        return {"installed": False, "version": "", "license": ""}


def check():
    """{tool: {installed, version, license, ok, note}} for every tool the studio relies on."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    out = {}
    for tool, pkg in (("kokoro", "kokoro-onnx"), ("faster-whisper", "faster-whisper"), ("pillow", "Pillow"),
                      ("numpy", "numpy"), ("opencv", "opencv-python-headless"), ("imageio-ffmpeg", "imageio-ffmpeg")):
        out[tool] = _pkg(pkg)
    onnx, voices = ROOT / "models" / "kokoro-v1.0.onnx", ROOT / "models" / "voices-v1.0.bin"
    out["kokoro"]["models"] = onnx.exists() and voices.exists()
    out["kokoro"]["note"] = "model weights Apache-2.0 (hexgrad/Kokoro-82M)"
    try:
        from clipper import media
        exe = media.ffmpeg()
        out["ffmpeg"] = {"installed": bool(exe), "version": media.run(["-version"]).stdout.split("\n")[0][:60],
                         "license": "LGPL/GPL"}
    except Exception as e:
        out["ffmpeg"] = {"installed": False, "note": str(e)[:120]}
    try:
        import brain
        out["claude-code"] = {"installed": bool(brain.find_claude()), "license": "Anthropic terms (Connor's plan)",
                              "note": "no API key used"}
    except Exception as e:
        out["claude-code"] = {"installed": False, "note": str(e)[:120]}
    out["sqlite"] = {"installed": True, "version": __import__("sqlite3").sqlite_version, "license": "public domain"}
    for name, info in out.items():
        info["cost_usd"] = 0
        info["ok"] = bool(info.get("installed")) and info.get("models", True) is not False
        usable = db.one("SELECT status FROM tools WHERE name=?", name)
        info["registered"] = bool(usable)
    db.put("toolchain_check", {"t": time.time(), "tools": out, "machine": machine()})
    return out


def machine():
    return {"system": platform.system(), "release": platform.mac_ver()[0] or platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
            "cpus": __import__("os").cpu_count(), "disk_free_gb": round(shutil.disk_usage(str(ROOT)).free / (1 << 30), 1)}


# ---------- a real test Short ----------

def _font(size, bold=True):
    from PIL import ImageFont
    names = (["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"] if bold else []) + [
        "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for n in names:
        if Path(n).exists():
            return ImageFont.truetype(n, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def card(path, headline, sub="", accent=(255, 196, 0)):
    """An original 1080x1920 title card (gradient, accent bar, wrapped text, safe margins)."""
    from PIL import Image, ImageDraw
    import numpy as np
    y = np.linspace(0, 1, H)[:, None]
    top, bottom = np.array([14, 18, 40]), np.array([40, 12, 60])
    grad = (top * (1 - y) + bottom * y).astype("uint8")
    img = Image.fromarray(np.repeat(grad[:, None, :].reshape(H, 1, 3), W, axis=1), "RGB")
    d = ImageDraw.Draw(img)
    d.rectangle([90, 640, 110, 1180], fill=accent)
    f1, f2 = _font(92), _font(54, bold=False)
    yy = 660
    for line in _wrap(d, headline, f1, W - 260)[:5]:
        d.text((150, yy), line, font=f1, fill=(255, 255, 255))
        yy += 110
    yy += 30
    for line in _wrap(d, sub, f2, W - 260)[:4]:
        d.text((150, yy), line, font=f2, fill=(215, 220, 235))
        yy += 70
    img.save(path)
    return path


def render_cards(cards, audio, out):
    """Cards [(png, seconds)] with a slow push-in, crossfades, and the narration -> H.264/AAC MP4."""
    from clipper import media
    args, filters, n = ["-y"], [], len(cards)
    for i, (png, sec) in enumerate(cards):
        args += ["-loop", "1", "-t", f"{sec + 0.5:.2f}", "-i", str(png)]
        frames = int((sec + 0.5) * FPS)
        filters.append(f"[{i}:v]scale={W * 2}:{H * 2},zoompan=z='min(1+0.0008*on,1.08)':d={frames}:"
                       f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},setsar=1,format=yuv420p[v{i}]")
    args += ["-i", str(audio)]
    last, offset = "[v0]", 0.0
    for i in range(1, n):
        offset += cards[i - 1][1]
        filters.append(f"{last}[v{i}]xfade=transition=fade:duration=0.5:offset={offset:.2f}[x{i}]")
        last = f"[x{i}]"
    args += ["-filter_complex", ";".join(filters), "-map", last, "-map", f"{n}:a"]
    media.run_h264(args, ["-pix_fmt", "yuv420p", "-r", FPS, "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                          "-movflags", "+faststart", "-shortest", str(out)], 20, timeout=900)
    return out


TEST_SCRIPT = ("A piece of a neutron star the size of a sugar cube would weigh about a billion tons on Earth. "
               "Here's why. When a giant star collapses, gravity crushes its atoms so tightly that protons and "
               "electrons merge into neutrons.")


def benchmark(folder):
    """Makes one real test Short and returns timings and checks. Never uploads anything."""
    from clipper import media
    from studio import narration
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    guard.register_built_ins()
    t, res = time.time(), {"machine": machine()}
    vo = narration.narrate(TEST_SCRIPT, folder / "voice.wav")
    res["narration_s"], res["audio_s"] = round(time.time() - t, 1), vo["seconds"]
    t = time.time()
    third = vo["seconds"] / 3
    cards = [(card(folder / "c1.png", "A sugar cube of neutron star", "would weigh about a billion tons on Earth"), third),
             (card(folder / "c2.png", "Here's why", "a giant star collapses under its own gravity", (90, 200, 255)), third),
             (card(folder / "c3.png", "Protons + electrons = neutrons", "packed as tightly as an atom's nucleus", (255, 110, 160)), third)]
    res["graphics_s"] = round(time.time() - t, 1)
    t = time.time()
    out = render_cards(cards, folder / "voice.wav", folder / "test-short.mp4")
    res["render_s"] = round(time.time() - t, 1)
    p = media.probe(out)
    res["video"] = {k: p.get(k) for k in ("width", "height", "fps", "vcodec", "acodec", "duration")}
    res["black"] = media.detect(out, "black")
    res["ok"] = (p["width"], p["height"]) == (W, H) and p["has_audio"] and not res["black"] and abs(p["duration"] - vo["seconds"]) < 1.5
    res["voice"] = {k: vo[k] for k in ("voice", "speed", "model_version", "loudness")}
    db.put("toolchain_benchmark", res)
    return res


if __name__ == "__main__":
    print(json.dumps(check() if len(sys.argv) < 2 else benchmark(sys.argv[1]), indent=2, default=str))
