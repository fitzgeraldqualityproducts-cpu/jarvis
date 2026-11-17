"""Obsidian: Jarvis reads and writes your vault's Markdown files directly.
No plugin or API key needed. The vault is found automatically from Obsidian's own
settings, or set "obsidian_vault" in config.json (run setup_obsidian.py)."""
import datetime
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
OBSIDIAN_SETTINGS = (Path(os.environ.get("APPDATA", Path.home())) / "obsidian/obsidian.json" if os.name == "nt"
                     else Path.home() / "Library/Application Support/obsidian/obsidian.json")
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
STOP = set("""a an the and or but if of to in on at for with about from by is are was were be been am i me my
mine you your what which who whom when where why how did do does done have has had it its this that these those
there here any some all can could would should will shall may might must not no yes than then so as up out
note notes wrote write written say said tell told find look search check obsidian vault jarvis please""".split())


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def known_vaults():
    """Vaults Obsidian knows about, most recently used first."""
    try:
        data = json.loads(OBSIDIAN_SETTINGS.read_text())
    except Exception:
        return []
    vaults = [v for v in data.get("vaults", {}).values() if v.get("path") and Path(v["path"]).is_dir()]
    vaults.sort(key=lambda v: (not v.get("open", False), -v.get("ts", 0)))
    return [Path(v["path"]) for v in vaults]


def vault():
    """The vault folder, or None if Obsidian isn't set up."""
    path = _cfg().get("obsidian_vault", "").strip()
    if path:
        p = Path(os.path.expanduser(path))
        return p if p.is_dir() else None
    found = known_vaults()
    return found[0] if found else None


def enabled():
    return vault() is not None


# ---------------- daily note ----------------

MOMENT = [("YYYY", "%Y"), ("YY", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"),
          ("dddd", "%A"), ("ddd", "%a"), ("DD", "%d")]


def _moment_to_strftime(fmt):
    out, i = "", 0
    while i < len(fmt):
        if fmt[i] == "[":                         # [literal text]
            j = fmt.find("]", i)
            out += fmt[i + 1:j] if j > 0 else fmt[i + 1:]
            i = j + 1 if j > 0 else len(fmt)
            continue
        for token, code in MOMENT:
            if fmt.startswith(token, i):
                out += code
                i += len(token)
                break
        else:
            if fmt.startswith("Do", i):
                out += "{ORD}"
                i += 2
            elif fmt.startswith("D", i):
                out += "{DAY}"
                i += 1
            elif fmt.startswith("M", i):
                out += "{MON}"
                i += 1
            else:
                out += fmt[i].replace("%", "%%")
                i += 1
    return out


def _format_date(fmt, day):
    s = day.strftime(_moment_to_strftime(fmt))
    suffix = "th" if 11 <= day.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day.day % 10, "th")
    return s.replace("{ORD}", f"{day.day}{suffix}").replace("{DAY}", str(day.day)).replace("{MON}", str(day.month))


def _daily_settings(v):
    for name in ("daily-notes.json", "plugins/periodic-notes/data.json"):
        try:
            data = json.loads((v / ".obsidian" / name).read_text())
            if "daily" in data:
                data = data["daily"]
            return data.get("folder", "") or "", data.get("format", "") or "YYYY-MM-DD", data.get("template", "") or ""
        except Exception:
            continue
    return "", "YYYY-MM-DD", ""


def daily_note(day=None, create=True):
    v = vault()
    if not v:
        return None
    day = day or datetime.date.today()
    folder, fmt, template = _daily_settings(v)
    path = v / folder / (_format_date(fmt, day) + ".md")
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        body = ""
        if template:
            t = v / (template if template.endswith(".md") else template + ".md")
            if t.exists():
                body = t.read_text()
                body = body.replace("{{date}}", _format_date(fmt, day)).replace("{{title}}", path.stem)
                body = re.sub(r"\{\{date:([^}]+)\}\}", lambda m: _format_date(m.group(1), day), body)
                body = body.replace("{{time}}", datetime.datetime.now().strftime("%H:%M"))
        path.write_text(body)
    return path


def _append(path, line):
    text = path.read_text() if path.exists() else ""
    sep = "" if not text or text.endswith("\n") else "\n"
    with open(path, "a") as f:
        f.write(f"{sep}{line}\n")


def add_note(text):
    path = daily_note()
    if not path:
        return None
    text = text.strip().rstrip(".")
    _append(path, f"- {datetime.datetime.now().strftime('%H:%M')} {text[:1].upper() + text[1:]}")
    return "Noted in today's daily note."


def add_todo(text):
    path = daily_note()
    if not path:
        return None
    text = text.strip().rstrip(".")
    _append(path, f"- [ ] {text[:1].upper() + text[1:]}")
    return f"Added {text} to your to-do list."


# ---------------- to-dos ----------------

TASK = re.compile(r"^\s*[-*+]\s+\[ \]\s+(.+)$")
DUE = re.compile(r"(?:📅|due::?)\s*(\d{4}-\d{2}-\d{2})")


def _notes(v):
    for root, dirs, files in os.walk(v):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".md"):
                yield Path(root) / f


def _clean_task(t):
    t = re.sub(r"[📅⏳🛫✅➕🔁⏫🔼🔽]\s*\S*", "", t)
    t = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), t)
    t = re.sub(r"#\S+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def open_todos(limit=8):
    """Open to-dos: today's daily note and anything due by today first, then recent notes."""
    v = vault()
    if not v:
        return None
    today = datetime.date.today()
    daily = daily_note(create=False)
    found = []
    for path in _notes(v):
        try:
            mtime = path.stat().st_mtime
            lines = path.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines:
            m = TASK.match(line)
            if not m:
                continue
            due = DUE.search(line)
            due_date = datetime.date.fromisoformat(due.group(1)) if due else None
            if due_date and due_date > today:
                continue                           # not due yet
            rank = 0 if (daily and path == daily) else 1 if due_date else 2
            found.append((rank, -mtime, _clean_task(m.group(1)), due_date))
    found.sort(key=lambda x: (x[0], x[1]))
    out, seen = [], set()
    for rank, _, text, due_date in found:
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append((text, due_date))
    return out[:limit]


def todos_summary():
    todos = open_todos()
    if todos is None:
        return "I can't find your Obsidian vault. Run Connect Obsidian in my folder."
    if not todos:
        return "Your Obsidian to-do list is clear."
    today = datetime.date.today()
    items = [f"{t}, which is overdue" if d and d < today else t for t, d in todos[:6]]
    joined = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return f"From your Obsidian to-dos: {joined}."


# ---------------- answering questions from notes ----------------

def _terms(text):
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2]


def search(question, limit=5, chars=1500):
    """Most relevant notes for a question: list of (title, excerpt)."""
    v = vault()
    if not v:
        return None
    terms = _terms(question)
    if not terms:
        return []
    scored = []
    for path in _notes(v):
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        low, name = text.lower(), path.stem.lower()
        score = sum(low.count(t) + 5 * name.count(t) for t in terms)
        score += 3 * sum(1 for t in set(terms) if t in low)          # reward covering more words
        if score:
            scored.append((score, path, text))
    scored.sort(key=lambda x: -x[0])
    results = []
    for _, path, text in scored[:limit]:
        low = text.lower()
        first = min((low.find(t) for t in terms if t in low), default=0)
        start = max(0, first - chars // 3)
        results.append((str(path.relative_to(v))[:-3], text[start:start + chars]))
    return results


def ask(question):
    hits = search(question)
    if hits is None:
        return "I can't find your Obsidian vault. Run Connect Obsidian in my folder."
    if not hits:
        return "I couldn't find anything about that in your notes."
    import brain
    context = "\n\n".join(f"### Note: {title}\n{excerpt}" for title, excerpt in hits)
    return brain.think(question, context=(
        "Answer using ONLY these excerpts from the user's Obsidian notes. Mention which note it came "
        "from if helpful. If the notes don't contain the answer, say so.\n\n" + context))


# ---------------- Jarvis's long-term brain in Obsidian ----------------
# "About Connor.md" (vault root): Jarvis reads it before every answer; you edit it to teach him.
# Jarvis/Conversations/<date>.md: a running log of what you talked about each day.

PROFILE_NAME = "About Connor"
LOG_DIR = "Jarvis/Conversations"
LEARNED = "## Things Jarvis learned"
PROFILE_TEMPLATE = """# About Connor
Jarvis reads this note before every answer. Edit it any time to teach him about you:
add lines, change them, or delete what's wrong.

## Basics
- Name: Connor
- Computers: a MacBook Air and a Windows PC (Jarvis runs on both)

## Interests
- Streams on Twitch
- Listens to rap on Spotify

## Schedule and routines
- (for example: "I work 9 to 5 on weekdays", "I stream on Friday nights")

## People
- (for example: "Mum's birthday is March 3rd", "my best friend is Jake")

## How I like Jarvis to talk
- Hype-man personality, British voice

""" + LEARNED + """
(Jarvis adds to this list when you say "remember that...")
"""


def profile_path(create=True):
    v = vault()
    if not v:
        return None
    path = v / f"{PROFILE_NAME}.md"
    if create and not path.exists():
        try:
            path.write_text(PROFILE_TEMPLATE)
        except Exception as e:
            print(f"(couldn't create the About Connor note: {e})")
            return None
    return path


def profile(limit=4000):
    """The text of the About Connor note, without placeholder lines."""
    path = profile_path()
    if not path or not path.exists():
        return ""
    lines = [l for l in path.read_text(errors="ignore").splitlines()
             if l.strip() and not l.strip().startswith(("- (for example", "(Jarvis adds", "Jarvis reads this note",
                                                         "add lines, change them"))]
    return "\n".join(lines)[:limit]


def learn(fact):
    """Adds a fact to the 'Things Jarvis learned' list in the About Connor note."""
    path = profile_path()
    if not path:
        return
    text = path.read_text(errors="ignore")
    line = f"- {fact}"
    if line.lower() in text.lower():
        return
    if LEARNED not in text:
        text = text.rstrip("\n") + f"\n\n{LEARNED}\n"
    head, tail = text.split(LEARNED, 1)
    tail = tail.rstrip("\n") + "\n" + line + "\n"
    path.write_text(head + LEARNED + tail)


def unlearn(facts):
    """Removes forgotten facts from the About Connor note."""
    path = profile_path(create=False)
    if not path or not path.exists() or not facts:
        return
    drop = {f"- {f}".lower() for f in facts}
    lines = path.read_text(errors="ignore").splitlines()
    path.write_text("\n".join(l for l in lines if l.strip().lower() not in drop) + "\n")


def context_for(question, limit=3, chars=900):
    """Notes from your vault that might help answer a question (skips Jarvis's own files)."""
    hits = search(question, limit=limit + 4, chars=chars) or []
    hits = [(t, x) for t, x in hits if t != PROFILE_NAME and not t.replace("\\", "/").startswith("Jarvis/")]
    return "\n\n".join(f"### Note: {t}\n{x}" for t, x in hits[:limit])


def log_exchange(user, answer):
    """Appends one exchange to today's conversation log in the vault."""
    v = vault()
    if not v:
        return
    now = datetime.datetime.now()
    folder = v / LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now:%Y-%m-%d}.md"
    if not path.exists():
        path.write_text(f"# Conversations with Jarvis, {now:%A %B} {now.day}\n\n")
    short = lambda s, n: (s[:n].rsplit(" ", 1)[0] + "…") if len(s) > n else s
    with path.open("a") as f:
        f.write(f"- **{now.strftime('%I:%M %p').lstrip('0')}** You: {short(user, 200)}\n"
                f"  Jarvis: {short(answer, 400)}\n")
