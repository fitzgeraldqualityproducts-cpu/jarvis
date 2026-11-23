"""Channel branding (Phase 4): names, handles, description, logo, banner, style guide.

names()            Claude suggests names from the chosen niche; each handle is looked up through
                   YouTube's official API (channels.list forHandle). "taken" means a channel has it;
                   "not found" means YouTube's API has no channel with it, which is NOT a promise
                   that YouTube will let you claim it: only YouTube Studio can confirm that.
make(name, handle) writes everything to studio_data/brand/: logo.png (800x800, also the profile
                   picture), banner.png (2048x1152, text inside YouTube's 1235x338 safe area),
                   description.txt, brand.md (colours, fonts, voice, style rules) and brand.json.
All artwork is drawn here from shapes and text (original, no outside images)."""
import json
import re
import time

from studio import config, data_dir, db, guard

PALETTE = {"background": "#0B1020", "panel": "#141B33", "cyan": "#36D1FF", "amber": "#FFB020", "text": "#F2F5FF",
           "muted": "#9AA6C8"}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def decision():
    d = db.get("niche_decision")
    if not d:
        raise RuntimeError("no niche has been chosen yet (run Research YouTube Niche first)")
    return json.loads(d) if isinstance(d, str) else d


def clean_handle(h):
    h = re.sub(r"[^A-Za-z0-9._-]", "", str(h).lstrip("@"))[:30]
    return h


def check_handle(handle):
    """'taken', 'not found' (claim it in YouTube Studio to be sure) or 'unknown' (no API access)."""
    from studio import ytpublic
    try:
        r = ytpublic.get("channels", {"part": "id", "forHandle": "@" + handle})
    except Exception:
        return "unknown"
    return "taken" if r.get("items") else "not found"


def names(n=12):
    from studio import claude
    d = decision()
    plan = d.get("plan") or {}
    ans = claude.ask_json(
        f"Suggest {n} names for a NEW YouTube Shorts channel. Niche: {d['name']}. Positioning: {plan.get('positioning')}. "
        f"Pillars: {', '.join(p.get('name', '') for p in plan.get('pillars') or [])}. Audience: US English.\n"
        "Rules: short (1-3 words), easy to say aloud, not a trademark or an existing big channel's name, no 'AI' or "
        "'Facts' clichés, not misleading. For each give a handle (letters/numbers/dots/underscores, 3-30 chars, no @) "
        "and one sentence on why it fits. JSON array of {\"name\", \"handle\", \"why\"}.",
        timeout=300, web=False, purpose="channel names")
    out, seen = [], set()
    for item in ans if isinstance(ans, list) else []:
        h = clean_handle(item.get("handle") or item.get("name", ""))
        if len(h) < 3 or h.lower() in seen:
            continue
        seen.add(h.lower())
        out.append({"name": str(item.get("name", ""))[:50], "handle": h, "why": str(item.get("why", ""))[:200],
                    "handle_status": check_handle(h)})
    (data_dir("brand") / "names.json").write_text(json.dumps({"t": time.time(), "names": out}, indent=2))
    return out


# ---------- artwork ----------

def _font(size, bold=True):
    from studio.toolchain import _font as f
    return f(size, bold)


def _mark(draw, cx, cy, r):
    """The channel mark: an orbit (ellipse), a measured arc and a point: 'show the math'."""
    cyan, amber, text = _rgb(PALETTE["cyan"]), _rgb(PALETTE["amber"]), _rgb(PALETTE["text"])
    w = max(4, r // 14)
    draw.ellipse([cx - r, cy - r * 0.42, cx + r, cy + r * 0.42], outline=cyan, width=w)
    draw.arc([cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62], 200, 340, fill=amber, width=w)
    draw.line([cx - r * 0.62, cy, cx + r * 0.62, cy], fill=text, width=max(2, w // 2))
    for k in range(-3, 4):
        x = cx + k * r * 0.62 / 3
        draw.line([x, cy - w * 1.2, x, cy + w * 1.2], fill=text, width=max(2, w // 2))
    d = r * 0.14
    px, py = cx + r * 0.86, cy - r * 0.21
    draw.ellipse([px - d, py - d, px + d, py + d], fill=amber)


def logo(path, initials=""):
    from PIL import Image, ImageDraw
    S = 800
    img = Image.new("RGB", (S, S), _rgb(PALETTE["background"]))
    d = ImageDraw.Draw(img)
    d.ellipse([40, 40, S - 40, S - 40], fill=_rgb(PALETTE["panel"]))
    _mark(d, S // 2, S // 2 - (40 if initials else 0), 260)
    if initials:
        f = _font(120)
        w = d.textlength(initials, font=f)
        d.text(((S - w) / 2, S // 2 + 140), initials, font=f, fill=_rgb(PALETTE["text"]))
    img.save(path)
    return path


def banner(path, name, tagline):
    """2048x1152; everything important inside the centred 1235x338 area that every device shows."""
    from PIL import Image, ImageDraw
    W, H = 2048, 1152
    img = Image.new("RGB", (W, H), _rgb(PALETTE["background"]))
    d = ImageDraw.Draw(img)
    grid = _rgb(PALETTE["panel"])
    for x in range(0, W, 64):
        d.line([x, 0, x, H], fill=grid, width=1)
    for y in range(0, H, 64):
        d.line([0, y, W, y], fill=grid, width=1)
    sx, sy = (W - 1235) // 2, (H - 338) // 2
    _mark(d, sx + 150, H // 2, 120)
    size = 96
    f1, f2 = _font(size), _font(40, bold=False)
    while d.textlength(name, font=f1) > 1235 - 340 and size > 48:
        size -= 6
        f1 = _font(size)
    while d.textlength(name, font=f1) > 1235 - 340 and len(name) > 4:     # very long names: shorten, never spill
        name = name[:-2].rstrip() + "…"
    d.text((sx + 320, sy + 70 + (96 - size) // 2), name, font=f1, fill=_rgb(PALETTE["text"]))
    words, line, lines = tagline.split(), "", []
    for w in words:
        t = (line + " " + w).strip()
        if d.textlength(t, font=f2) > 1235 - 340:
            lines.append(line)
            line = w
        else:
            line = t
    lines.append(line)
    for i, ln in enumerate(lines[:2]):
        d.text((sx + 322, sy + 190 + i * 52), ln, font=f2, fill=_rgb(PALETTE["muted"]))
    img.save(path)
    return path


def description(name):
    plan = decision().get("plan") or {}
    pillars = "\n".join(f"- {p.get('name')}: {p.get('description', '').split('.')[0]}." for p in plan.get("pillars") or [])
    text = (f"{name}: {plan.get('positioning', '')}\n\n{plan.get('value', '')}\n\nWhat you'll find here:\n{pillars}\n\n"
            "Every Short lists its sources in the description. Narration is AI-generated (a synthetic voice); "
            "the research, numbers and drawings are checked before anything is published.")
    return text[:1000]


def make(name, handle):
    guard.allow("media.local", f"branding for {name}", record=False)
    folder = data_dir("brand")
    plan = decision().get("plan") or {}
    tagline = plan.get("positioning") or decision()["name"]
    words = [w for w in re.findall(r"[A-Za-z0-9]+", name) if w.lower() not in ("the", "a", "an", "of", "and")]
    initials = "".join(w[0] for w in words[:2]).upper()
    logo(folder / "logo.png", initials)
    banner(folder / "banner.png", name, tagline)
    desc = description(name)
    (folder / "description.txt").write_text(desc)
    g = config.general()
    guide = f"""# {name} brand guide

Handle: @{handle}   Niche: {decision()['name']}

## Positioning
{tagline}

## Colours
""" + "\n".join(f"- {k}: {v}" for k, v in PALETTE.items()) + f"""

## Type
Headlines bold sans-serif (Arial Bold / Helvetica Bold), body regular. Captions large, high contrast, inside safe margins.

## Voice
Kokoro {g['voice']} at speed {g['voice_speed']} (US English), calm and conversational. Disclosed as AI narration.

## Style
{plan.get('style', '')}

## Avoid
""" + "\n".join(f"- {a}" for a in plan.get("avoid") or []) + "\n"
    (folder / "brand.md").write_text(guide)
    brand = {"name": name, "handle": handle, "tagline": tagline, "palette": PALETTE, "voice": g["voice"], "made": time.time()}
    (folder / "brand.json").write_text(json.dumps(brand, indent=2))
    db.run("INSERT INTO channel(id, handle, voice, updated) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
           "handle=excluded.handle, voice=excluded.voice, updated=excluded.updated", handle, g["voice"], time.time())
    db.put("brand", brand)
    return folder
