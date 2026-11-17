"""Brain: answers questions using your Claude Code login (the `claude` command)."""
import glob
import json

import personality
import os
import shutil
import subprocess
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
HOME = os.path.expanduser("~")
# Places Claude Code is commonly installed (Mac and Windows), in case the PATH misses them.
WINDOWS = os.name == "nt"
CANDIDATES = [
    f"{HOME}/.local/bin/claude",
    f"{HOME}/.claude/local/claude",
    f"{HOME}/.npm-global/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    f"{HOME}/.bun/bin/claude",
    f"{HOME}/.volta/bin/claude",
]
if WINDOWS:
    APPDATA = os.environ.get("APPDATA", f"{HOME}/AppData/Roaming")
    LOCAL = os.environ.get("LOCALAPPDATA", f"{HOME}/AppData/Local")
    CANDIDATES = [
        f"{HOME}/.local/bin/claude.exe",
        f"{APPDATA}/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
        f"{LOCAL}/Programs/claude/claude.exe",
        f"{HOME}/.bun/bin/claude.exe",
        f"{APPDATA}/npm/claude.cmd",
    ]
GLOBS = [] if os.name == "nt" else [
    f"{HOME}/.npm-global/lib/node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-darwin-*/claude",
    "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-darwin-*/claude",
    "/usr/local/lib/node_modules/@anthropic-ai/claude-code/node_modules/@anthropic-ai/claude-code-darwin-*/claude",
    f"{HOME}/.nvm/versions/node/*/bin/claude",
    f"{HOME}/Library/Application Support/Claude/claude-code/*/claude",
    f"{HOME}/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude",
]


def _usable(path):
    """True if path is a real program, not npm's 'binary not installed' stub."""
    if not path or not os.access(path, os.X_OK):
        return False
    if WINDOWS:
        low = path.lower()
        if low.endswith((".cmd", ".bat")):
            return os.path.isfile(path)
        try:
            with open(path, "rb") as f:
                return f.read(2) == b"MZ"
        except OSError:
            return False
    try:
        with open(os.path.realpath(path), "rb") as f:
            head = f.read(4)
        return head.startswith(b"#!") or head in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\x7fELF")
    except OSError:
        return False


def find_claude():
    """Returns the path to the claude command, or None."""
    try:
        custom = json.loads(CONFIG.read_text()).get("claude_path")
        if custom and _usable(os.path.expanduser(custom)):
            return os.path.expanduser(custom)
    except Exception:
        pass
    found = shutil.which("claude")
    if _usable(found):
        return found
    for path in CANDIDATES:
        if _usable(path):
            return path
    for pattern in GLOBS:
        for path in sorted(glob.glob(pattern), reverse=True):
            if _usable(path):
                return path
    if WINDOWS:
        return None
    # Last resort: ask a login shell, which loads ~/.zshrc and its PATH.
    try:
        out = subprocess.run(["/bin/zsh", "-lic", "command -v claude"],
                             capture_output=True, text=True, timeout=10)
        path = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        if _usable(path):
            return path
    except Exception:
        pass
    return None

ABILITIES = (
    "You can already do these things through voice commands handled elsewhere: open apps "
    "and websites, Google and YouTube searches, Spotify, calendar, reminders, timers, "
    "alarms, notes, Shortcuts, Oura ring vitals ('give me a vitals analysis'), Twitch stream mode that reads chat aloud ('stream mode' / 'stop stream mode'), and remembering facts. If the user asks for something like "
    "that in a way that reached you, tell them the phrase to use, e.g. 'remind me to ...', "
    "'add dentist to my calendar tomorrow at 3', 'play Dead Fresh by Lil Baby', 'open Spotify'. "
    "You cannot take actions yourself from here: never claim something is waiting for approval, "
    "and never pretend you did something. If the user wants to change your own code, look, layout, "
    "voice or behaviour, tell them to say 'upgrade yourself' followed by what they want, and you'll "
    "rebuild yourself. "
    "You can search the web and read web pages. For anything current or factual you aren't sure "
    "of (news, scores, prices, weather elsewhere, opening hours, recent events, people, products), "
    "search first, then give the answer itself in plain spoken words. Don't read out links or "
    "sources unless asked, and don't narrate that you searched."
)


def run_claude(cmd, timeout, cwd=None):
    """Runs `claude -p <prompt> ...`. On Windows the prompt goes in through stdin, because
    long prompts with line breaks don't survive the Windows command line."""
    if WINDOWS:
        prompt = cmd.pop(cmd.index("-p") + 1)
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                          stdin=subprocess.DEVNULL)


def think(question, context=""):
    claude = find_claude()
    if not claude:
        return ("I can't find Claude Code on this computer, so I have no brain to think with. "
                "Run python3 brain.py in my folder for help.")
    import datetime
    import memory
    now = datetime.datetime.now().strftime("%A %B %d %Y, %I:%M %p")
    facts = memory.facts()
    known = ("Things the user asked you to remember:\n- " + "\n- ".join(facts)) if facts else ""
    past = "\n".join(f"[{h['t']}] User: {h['user']}\nJarvis: {h['jarvis']}" for h in memory.history(8))
    extra = f"{context}\n\n" if context else ""
    try:                                            # his long-term brain in Obsidian
        import obsidian
        if obsidian.enabled():
            about = obsidian.profile()
            if about:
                known += "\n\nAbout the user, from their Obsidian note (use it naturally, don't recite it):\n" + about
            if not context:
                notes = obsidian.context_for(question)
                if notes:
                    extra = ("Possibly relevant notes from the user's Obsidian vault. Use them only if they "
                             "actually help with the question:\n" + notes + "\n\n")
    except Exception as e:
        print(f"(Obsidian context failed: {e})")
    prompt = (f"{personality.persona()} {ABILITIES}\n\nCurrent time: {now}\n\n{known}\n\n{extra}"
              f"Recent conversation:\n{past}\n\nUser: {question}\nJarvis:")
    import time
    print(f"(thinking with {claude})")
    start = time.time()
    try:
        model = json.loads(CONFIG.read_text()).get("brain_model", "")
        # The brain only talks and looks things up; edits go through selfedit.py.
        tools = ["--disallowedTools", "Edit Write NotebookEdit Bash"]
        if json.loads(CONFIG.read_text()).get("web_search", True):
            tools += ["--allowedTools", "WebSearch WebFetch"]
        cmd = [claude, "-p", prompt] + tools + (["--model", model] if model else [])
        out = run_claude(cmd, timeout=150)
        if out.returncode != 0 and model:
            print(f"(model {model} failed, retrying with the default: {out.stderr.strip()[:150]})")
            out = run_claude([claude, "-p", prompt] + tools, timeout=150)
        answer = out.stdout.strip()
        print(f"(brain took {time.time() - start:.1f}s, exit code {out.returncode})")
        if not answer:
            print(f"(claude error: {out.stderr.strip()[:300]})")
            answer = "I'm sorry, my brain didn't answer. You may need to log in to Claude Code."
    except subprocess.TimeoutExpired:
        answer = "That one is taking me too long, I'm afraid."
    except OSError as e:
        print(f"(couldn't run {claude}: {e})")
        answer = "My brain won't start, I'm afraid. Claude Code may need reinstalling."
    memory.add_exchange(question, answer)
    try:
        import obsidian
        obsidian.log_exchange(question, answer)
    except Exception as e:
        print(f"(Obsidian log failed: {e})")
    return answer


if __name__ == "__main__":
    path = find_claude()
    if path:
        print(f"Found Claude Code at: {path}")
        print("Test answer:", think("Say hello in one short sentence."))
    else:
        print("Claude Code isn't installed (or I can't find it). Install it with:")
        print("  irm https://claude.ai/install.ps1 | iex      (Windows PowerShell)" if WINDOWS else
              "  curl -fsSL https://claude.ai/install.sh | bash")
        print("then run `claude` once and log in. If it's installed somewhere unusual,")
        print('add  "claude_path": "/full/path/to/claude"  to config.json.')
