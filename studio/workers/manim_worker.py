"""Manim scenes (runs inside tools/manim). In: {"scenes": [{"spec": {...}, "out": path, "seconds": n}],
"palette", "background": png, "handle", "box", "W", "H", "fps", "font"}. Out: {"clips": [{"out", "ok",
"error"?}]}.

Each scene is the same layout as the still picture (same template, colours and safe area), but it
moves: numbers count up, equations write themselves and resolve to the answer, bars grow, circles
swell to their sizes, steps light up one by one. Only the motion is rendered (about 1.5 s); the
compositor holds the last frame for the rest of the line. Text only, no LaTeX needed."""
import json
import math
import os
import shutil
import sys
import tempfile

pin, pout = sys.argv[1], sys.argv[2]
job = json.load(open(pin))
W, H = int(job.get("W", 1080)), int(job.get("H", 1920))
U = H / 16.0                                     # pixels per Manim unit (frame is 9 x 16 units)
BOX = job["box"]
PAL = job["palette"]
FONT = job.get("font") or "Arial"


def X(px):
    return (px - W / 2) / U


def Y(py):
    return (H / 2 - py) / U


def num(v):
    return float(str(v).replace(",", ""))


def fmt(v, like=None):
    if like is not None:                                   # keep the writer's own formatting at the end
        return str(like)
    if v >= 100 or v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


try:
    from manim import (BOLD, DOWN, LEFT, ORIGIN, RIGHT, UP, Circle, Create, FadeIn, GrowFromCenter,
                       GrowFromEdge, ImageMobject, Line, RoundedRectangle, Scene, Text, ValueTracker,
                       VGroup, Write, config, linear, rate_functions, smooth, there_and_back)

    def T(s, h_px, color, bold=True, max_w_px=None):
        t = Text(str(s), font=FONT, weight=BOLD if bold else "NORMAL", color=color)
        t.scale_to_fit_height(h_px / U)
        mw = (max_w_px or (BOX["x1"] - BOX["x0"])) / U
        if t.width > mw:
            t.scale_to_fit_width(mw)
        return t

    def at(m, cx_px=None, top_px=None, left_px=None):
        if top_px is not None:
            m.move_to([m.get_center()[0], Y(top_px) - m.height / 2, 0])
        if cx_px is not None:
            m.move_to([X(cx_px), m.get_center()[1], 0])
        if left_px is not None:
            m.move_to([X(left_px) + m.width / 2, m.get_center()[1], 0])
        return m

    CX = (BOX["x0"] + BOX["x1"]) / 2
    WIDTH = BOX["x1"] - BOX["x0"]

    class Short(Scene):
        spec = {}
        seconds = 1.5
        bg = job["background"]

        def construct(self):
            bg = ImageMobject(self.bg)
            bg.height = 16
            self.add(bg)
            if job.get("handle"):
                self.add(at(T("@" + job["handle"].lstrip("@"), 30, PAL["muted"], bold=False), cx_px=W / 2, top_px=150))
            s = self.spec
            t = s.get("type")
            run = min(1.6, max(0.6, self.seconds * 0.5))
            getattr(self, "_" + (t if t in ("number", "equation", "compare", "scale", "steps") else "headline"))(s, run)

        def _number(self, s, run):
            raw = str(s["value"])
            try:
                v = num(raw)
            except ValueError:
                v = None
            tracker = ValueTracker(0)
            big = T(raw, 200, PAL["amber"])
            at(big, cx_px=CX, top_px=BOX["y0"] + 230)
            if v is not None and v != 0:
                dec = 0 if v == int(v) else min(2, len(raw.split(".")[-1]) if "." in raw else 2)

                def shown():
                    x = tracker.get_value()
                    txt = raw if abs(x - v) < 1e-9 else (f"{x:,.{dec}f}")
                    m = T(txt, 200, PAL["amber"])
                    return at(m, cx_px=CX, top_px=BOX["y0"] + 230)
                from manim import always_redraw
                live = always_redraw(shown)
                self.add(live)
                self.play(tracker.animate(rate_func=smooth).set_value(v), run_time=run)
                self.remove(live)
                self.add(big)
            else:
                self.play(FadeIn(big, scale=1.2), run_time=run)
            rest = []
            y = BOX["y0"] + 230 + big.height * U + 30
            if s.get("unit"):
                u = at(T(str(s["unit"])[:20], 70, PAL["cyan"]), cx_px=CX, top_px=y)
                rest.append(u)
            if s.get("label"):
                lab = at(T(str(s["label"])[:60], 52, PAL["muted"], bold=False), cx_px=CX, top_px=BOX["y0"] + 640)
                rest.append(lab)
            if rest:
                self.play(*[FadeIn(m, shift=UP * 0.2) for m in rest], run_time=0.4)

        def _equation(self, s, run):
            left = at(T(s["left"], 100, PAL["text"]), cx_px=CX, top_px=BOX["y0"] + 160)
            y = BOX["y0"] + 160 + left.height * U + 40
            line = Line([X(BOX["x0"] + 120), Y(y), 0], [X(BOX["x1"] - 120), Y(y), 0], color=PAL["cyan"], stroke_width=6)
            right = at(T("= " + str(s["right"]), 160, PAL["amber"]), cx_px=CX, top_px=y + 40)
            self.play(Write(left), run_time=run * 0.55)
            self.play(Create(line), run_time=run * 0.15)
            self.play(FadeIn(right, scale=1.25), run_time=run * 0.3)
            if s.get("label"):
                lab = at(T(str(s["label"])[:60], 50, PAL["muted"], bold=False), cx_px=CX,
                         top_px=y + 40 + right.height * U + 50)
                self.play(FadeIn(lab, shift=UP * 0.2), run_time=0.35)

        def _compare(self, s, run):
            items = s["items"][:4]
            vals = [num(i["value"]) for i in items]
            top = max(vals)
            row = (BOX["y1"] - BOX["y0"] - 100) / len(items)
            anims, labels = [], []
            for k, (it, v) in enumerate(zip(items, vals)):
                yy = BOX["y0"] + 60 + k * row
                labels.append(at(T(str(it["label"])[:28], 46, PAL["text"]), left_px=BOX["x0"], top_px=yy))
                bw = max(12, (WIDTH - 10) * v / top)
                bh = min(90, row - 120)
                bar = RoundedRectangle(corner_radius=14 / U, width=bw / U, height=bh / U, stroke_width=0,
                                       fill_color=PAL["amber"] if v == top else PAL["cyan"], fill_opacity=1)
                bar.move_to([X(BOX["x0"] + bw / 2), Y(yy + 70 + bh / 2), 0])
                vt = at(T(f"{fmt(v, it['value'])} {s.get('unit', '')}".strip(), 38, PAL["muted"], bold=False),
                        left_px=BOX["x0"], top_px=yy + 80 + bh)
                anims.append((bar, vt))
            self.play(*[FadeIn(m) for m in labels], run_time=0.3)
            self.play(*[GrowFromEdge(b, LEFT) for b, _ in anims], run_time=run, rate_func=smooth)
            self.play(*[FadeIn(t) for _, t in anims], run_time=0.3)

        def _scale(self, s, run):
            items = s["items"][:3]
            vals = [num(i["value"]) for i in items]
            rmax = min(250, WIDTH / (2 * len(items)) - 10)
            top = max(vals)
            step = WIDTH / len(items)
            cy = BOX["y0"] + 380
            circles, texts = [], []
            for k, (it, v) in enumerate(zip(items, vals)):
                r = max(6, rmax * math.sqrt(v / top))
                cx = BOX["x0"] + step * (k + 0.5)
                c = Circle(radius=r / U, color=PAL["amber"] if v == top else PAL["cyan"], stroke_width=8)
                c.move_to([X(cx), Y(cy + rmax - r), 0])
                circles.append(c)
                texts.append(at(T(str(it["label"])[:16], 42, PAL["text"], max_w_px=step - 10), cx_px=cx, top_px=cy + rmax + 40))
                texts.append(at(T(f"{fmt(v, it['value'])} {s.get('unit', '')}".strip(), 36, PAL["muted"], bold=False,
                                  max_w_px=step - 10), cx_px=cx, top_px=cy + rmax + 96))
            self.play(*[GrowFromCenter(c) for c in circles], run_time=run, rate_func=smooth)
            self.play(*[FadeIn(t, shift=UP * 0.15) for t in texts], run_time=0.35)

        def _steps(self, s, run):
            items = [str(i) for i in s["items"][:4]]
            cur = int(s.get("current") or 0) if str(s.get("current", "0")).isdigit() else 0
            yy = BOX["y0"] + 80
            each = max(0.2, run / max(1, len(items)))
            for k, it in enumerate(items):
                on = k == cur
                dot = Circle(radius=45 / U, color=PAL["cyan"], stroke_width=4,
                             fill_color=PAL["amber"] if on else PAL["panel"], fill_opacity=1)
                dot.move_to([X(BOX["x0"] + 45), Y(yy + 45), 0])
                n = T(str(k + 1), 44, PAL["background"] if on else PAL["text"])
                n.move_to(dot.get_center())
                txt = at(T(it[:44], 48, PAL["text"] if on else PAL["muted"], bold=on, max_w_px=WIDTH - 130),
                         left_px=BOX["x0"] + 130, top_px=yy + 18)
                self.play(FadeIn(VGroup(dot, n), scale=0.6), FadeIn(txt, shift=RIGHT * 0.2), run_time=each)
                yy += 200

        def _headline(self, s, run):
            words = str(s.get("text") or "").split()
            lines, cur = [], []
            for w in words:                                        # about 3-4 words a line, like the still
                cur.append(w)
                if len(" ".join(cur)) > 16:
                    lines.append(" ".join(cur))
                    cur = []
            if cur:
                lines.append(" ".join(cur))
            lines = lines[:5]
            h = 96
            total = len(lines) * (h + 24)
            y = BOX["y0"] + max(80, (BOX["y1"] - BOX["y0"] - total) / 2 - 40)
            group = []
            for ln in lines:
                group.append(at(T(ln, h, PAL["text"]), cx_px=CX, top_px=y))
                y += h + 24
            self.play(*[FadeIn(m, shift=UP * 0.3) for m in group], run_time=run, lag_ratio=0.25)
            if s.get("label"):
                self.play(FadeIn(at(T(str(s["label"])[:40], 50, PAL["cyan"], bold=False), cx_px=CX, top_px=y + 30)),
                          run_time=0.3)

    config.pixel_width, config.pixel_height = W, H
    config.frame_height, config.frame_width = 16.0, 9.0
    config.frame_rate = int(job.get("fps", 30))
    config.disable_caching = True
    config.verbosity = "ERROR"
    config.progress_bar = "none"
    work = tempfile.mkdtemp(prefix="jv-manim-")
    config.media_dir = work
    out = []
    for i, sc in enumerate(job["scenes"]):
        try:
            Short.spec = sc["spec"]
            Short.seconds = float(sc.get("seconds", 3))
            Short.bg = sc.get("background") or job["background"]      # the scene's own AI picture, if it has one
            config.output_file = f"scene{i}"
            scene = Short()
            scene.render()
            src = scene.renderer.file_writer.movie_file_path
            shutil.move(str(src), sc["out"])
            hold = os.path.splitext(sc["out"])[0] + "_last.png"         # the final state, held for the rest
            from PIL import Image
            Image.fromarray(scene.renderer.get_frame()).convert("RGB").save(hold)
            out.append({"out": sc["out"], "hold": hold, "ok": True,
                        "seconds": round(scene.renderer.time, 3)})
        except Exception as e:
            out.append({"out": sc["out"], "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
    shutil.rmtree(work, ignore_errors=True)
    json.dump({"clips": out}, open(pout, "w"))
except Exception as e:
    json.dump({"error": f"{type(e).__name__}: {e}"}, open(pout, "w"))
