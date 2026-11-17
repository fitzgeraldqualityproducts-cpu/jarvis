"""Brain: answers questions using your Claude Code login (the `claude` command)."""
import glob
import json

import personality
import os
import shutil
import subprocess
import threading
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
# He starts speaking as soon as the first sentence arrives, so it should be a good one.
SPOKEN = ("Your words are spoken aloud the moment you write them: open with the answer itself in a "
          "short first sentence, keep it conversational, and never use lists, headings or markdown. "
          "Don't write anything before a web search.")


def run_claude(cmd, timeout, cwd=None):
    """Runs `claude -p <prompt> ...`. On Windows the prompt goes in through stdin, because
    long prompts with line breaks don't survive the Windows command line."""
    if WINDOWS:
        prompt = cmd.pop(cmd.index("-p") + 1)
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                          stdin=subprocess.DEVNULL)


def _drain(pipe, into):
    for line in pipe:
        into.append(line)


def _stream_claude(cmd, timeout, cwd=None, state=None):
    """Runs claude like run_claude, but yields the answer's text as Claude writes it.
    state gets "proc", "timed_out" and "error" so the caller can stop it or tell what went wrong."""
    state = {} if state is None else state
    cmd = list(cmd) + ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    prompt = cmd.pop(cmd.index("-p") + 1) if WINDOWS else None
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if WINDOWS else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd, bufsize=1)
    state["proc"] = proc
    err = []
    threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True).start()
    if prompt is not None:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass

    def too_long():
        state["timed_out"] = True
        proc.kill()
    timer = threading.Timer(timeout, too_long)
    timer.daemon = True
    timer.start()
    got, result = False, ""
    try:
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind = d.get("type")
            if kind == "stream_event" and not d.get("parent_tool_use_id"):
                ev = d.get("event") or {}
                delta = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    got = True
                    yield delta.get("text", "")
                elif ev.get("type") == "content_block_stop" and got:
                    yield "\n"                 # keeps text from before and after a web search apart
            elif kind == "result":
                result = "" if d.get("is_error") else (d.get("result") or "")
        if not got and result:
            yield result
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(5)
        except Exception:
            pass
        state["error"] = "".join(err).strip()[-300:]


class Answer:
    """The brain's answer as it arrives. Iterate over it for pieces of text, or call
    cancel() to stop Claude (e.g. when he's interrupted). str(answer) waits for all of it."""

    def __init__(self, make):
        self.state = {}
        self._gen = make(self.state)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._gen)

    def close(self):
        self._gen.close()

    def cancel(self):
        self.state["cancelled"] = True
        proc = self.state.get("proc")
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def __str__(self):
        return "".join(self).strip()


def _answer_stream(cmds, timeout, state, cwd=None, label="brain", fails=None):
    """Tries each command streaming, then (for older Claude Code) the plain way. Yields text."""
    import time
    fails = fails or {}
    start, first, any_text = time.time(), None, False
    for cmd in cmds:
        try:
            for piece in _stream_claude(cmd, timeout, cwd, state):
                if piece.strip() and first is None:
                    first = time.time() - start
                    print(f"({label}: first words after {first:.1f}s)")
                any_text = any_text or bool(piece.strip())
                yield piece
        except OSError as e:
            print(f"(couldn't run Claude: {e})")
            yield fails.get("oserror", "My brain won't start, I'm afraid.")
            return
        if any_text or state.get("cancelled"):
            break
        if state.get("timed_out"):
            yield fails.get("timeout", "That one is taking me too long, I'm afraid.")
            return
        print(f"({label}: streaming gave nothing, {state.get('error', '')[:200]})")
    if not any_text and not state.get("cancelled"):
        try:                                       # the old, non-streaming way
            out = run_claude(list(cmds[-1]), timeout=timeout, cwd=cwd)
            text = out.stdout.strip()
            if not text:
                print(f"(claude error: {out.stderr.strip()[:300]})")
            yield text or fails.get("empty", "I'm sorry, my brain didn't answer. You may need to log in to Claude Code.")
        except subprocess.TimeoutExpired:
            yield fails.get("timeout", "That one is taking me too long, I'm afraid.")
        except OSError as e:
            print(f"(couldn't run Claude: {e})")
            yield fails.get("oserror", "My brain won't start, I'm afraid.")
    print(f"({label} took {time.time() - start:.1f}s)")


_guest_chat = []        # what friends said this session (never saved, never mixed with Connor's memory)


def guest_think_stream(question, name=None, owner="Connor"):
    """Answer a friend in the room (group chat mode): no memory, notes, calendar or files.
    Returns an Answer that streams."""
    claude = find_claude()
    who = name or "a guest"
    if not claude:
        return Answer(lambda state: iter(["My brain's offline right now, sorry."]))
    import datetime
    import tempfile
    now = datetime.datetime.now().strftime("%A %B %d %Y, %I:%M %p")
    past = "\n".join(f"{n}: {q}\nJarvis: {a}" for n, q, a in _guest_chat[-6:])
    prompt = (f"{personality.persona()}\n\nYou are {owner}'s voice assistant, and right now you're talking out loud to "
              f"{who}, a friend of {owner}'s who is in the room. Be fun and friendly and keep answers short and spoken "
              f"(no lists, links or markdown). You can chat, joke, and answer general questions, and search the web. "
              f"This is not {owner}: never share anything private about {owner} (their notes, memories, calendar, "
              f"reminders, messages, files, accounts or personal details), and don't pretend to know it. If they ask "
              f"for that or to change your settings or code, say kindly that only {owner} can do that.\n\n"
              f"Current time: {now}\n\nRecent group chat:\n{past}\n\n{who}: {question}\nJarvis:")
    tools = ["--disallowedTools", "Read Glob Grep Edit Write NotebookEdit Bash Task"]
    try:
        if json.loads(CONFIG.read_text()).get("web_search", True):
            tools += ["--allowedTools", "WebSearch WebFetch"]
    except Exception:
        pass
    print(f"(thinking for guest {who})")

    def make(state):
        said = []
        cwd = tempfile.mkdtemp()                 # nothing of Connor's in reach
        try:
            for piece in _answer_stream([[claude, "-p", prompt] + tools], 120, state, cwd=cwd, label="guest brain",
                                        fails={"timeout": "That one's taking me too long, sorry.",
                                               "oserror": "My brain won't start right now, sorry.",
                                               "empty": "Sorry, my brain didn't answer that one."}):
                said.append(piece)
                yield piece
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
            _guest_chat.append((who, question, "".join(said).strip()))
            del _guest_chat[:-12]
    return Answer(make)


def guest_think(question, name=None, owner="Connor"):
    return str(guest_think_stream(question, name, owner))


def think_stream(question, context=""):
    """Answers a question, streaming: returns an Answer you can start speaking from
    as soon as the first sentence arrives."""
    claude = find_claude()
    if not claude:
        return Answer(lambda state: iter([
            "I can't find Claude Code on this computer, so I have no brain to think with. "
            "Run python3 brain.py in my folder for help."]))
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
    prompt = (f"{personality.persona()} {ABILITIES} {SPOKEN}\n\nCurrent time: {now}\n\n{known}\n\n{extra}"
              f"Recent conversation:\n{past}\n\nUser: {question}\nJarvis:")
    try:
        cfg = json.loads(CONFIG.read_text())
    except Exception:
        cfg = {}
    model = cfg.get("brain_model", "")
    # The brain only talks and looks things up; edits go through selfedit.py.
    tools = ["--disallowedTools", "Edit Write NotebookEdit Bash"]
    if cfg.get("web_search", True):
        tools += ["--allowedTools", "WebSearch WebFetch"]
    cmds = ([[claude, "-p", prompt] + tools + ["--model", model]] if model else []) + [[claude, "-p", prompt] + tools]
    print(f"(thinking with {claude})")

    def make(state):
        said = []
        try:
            for piece in _answer_stream(cmds, 150, state, fails={
                    "oserror": "My brain won't start, I'm afraid. Claude Code may need reinstalling."}):
                said.append(piece)
                yield piece
        finally:
            answer = "".join(said).strip()
            if state.get("cancelled") and answer:
                answer += " (cut off)"
            if answer:
                memory.add_exchange(question, answer)
                try:
                    import obsidian
                    obsidian.log_exchange(question, answer)
                except Exception as e:
                    print(f"(Obsidian log failed: {e})")
    return Answer(make)


def think(question, context=""):
    """Answers a question and returns the whole reply as text."""
    return str(think_stream(question, context))


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
