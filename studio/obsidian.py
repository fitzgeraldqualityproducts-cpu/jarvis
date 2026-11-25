"""The weekly report, also written into Connor's Obsidian vault on the PC (a plain Markdown note).

vault()        the vault folder: config "obsidian_vault", else the first of the usual places that exists
               (OneDrive\\Documents\\Obsidian Vault, Documents\\Obsidian Vault). None when there's no vault.
write(text)    saves the note as <vault>/HussUniversity/Weekly reports/<date>.md. Returns the path or None.
review(idx)    the "what worked / what flopped / what changes next" part, from real results only.

Only this one folder inside the vault is ever written; nothing else in the vault is read or changed."""
import time
from pathlib import Path

FOLDER = ("HussUniversity", "Weekly reports")
WORKED_AT = 55.0          # index 50 = this channel's typical Short
FLOPPED_AT = 45.0


def on():
    try:
        from studio import config
        return config.general().get("obsidian_report", True) is not False
    except Exception:
        return True


def vault():
    try:
        from studio import config
        set_to = config.general().get("obsidian_vault")
    except Exception:
        set_to = None
    if set_to:
        p = Path(str(set_to)).expanduser()
        return p if p.is_dir() else None
    home = Path.home()
    for p in (home / "OneDrive" / "Documents" / "Obsidian Vault", home / "Documents" / "Obsidian Vault",
              home / "Obsidian Vault"):
        if p.is_dir():
            return p
    return None


def write(text, now=None):
    if not on():
        return None
    v = vault()
    if not v:
        return None
    folder = v.joinpath(*FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{time.strftime('%Y-%m-%d', time.localtime(now or time.time()))}.md"
    tags = "---\ntags: [youtube, weekly-report]\n---\n\n"
    out.write_text(tags + text, encoding="utf-8")
    return out


def _row(r):
    hold = "" if r.get("hook_hold") is None else f", {100 * r['hook_hold']:.0f}% still watching at 10%"
    return f"- {r.get('title') or 'Short'}: index {r['index']:.0f}, {int(r.get('views') or 0):,} views{hold}"


def review(idx):
    """Markdown lines: what worked, what flopped, what changes next."""
    rows = sorted((r for r in (idx or {}).values() if r.get("index") is not None), key=lambda r: -r["index"])
    worked = [r for r in rows if r["index"] >= WORKED_AT][:3]
    flopped = [r for r in reversed(rows) if r["index"] <= FLOPPED_AT][:3]
    lines = ["## What worked", ""]
    lines += [_row(r) for r in worked] or ["Nothing clearly above typical yet (index 55 or more)."]
    lines += ["", "## What flopped", ""]
    lines += [_row(r) for r in flopped] or ["Nothing clearly below typical (index 45 or less)."]
    lines += ["", "## What changes next", ""]
    nxt = []
    try:
        from studio import analytics, packaging, retention
        for t in (packaging.openings_advice(), packaging.advice(), retention.advice(), analytics.hint()):
            if t:
                nxt.append(t)
    except Exception:
        pass
    try:
        from studio import db
        nud = db.get("studio_pillar_nudges", {}) or {}
        if nud:
            nxt.append("Topic picking leans: " + ", ".join(f"{k} {v:+.1f} points" for k, v in sorted(nud.items())))
    except Exception:
        pass
    lines += [f"- {t}" for t in nxt] or ["No change yet: Jarvis waits for at least 3 measured Shorts that agree "
                                          "before it changes anything."]
    return lines
