"""Asking Claude (through Jarvis's brain: Claude Code on Connor's plan, no API key) for JSON.

Every call is read-only (no tools), has a timeout, and is retried once if the answer isn't valid
JSON. Tests swap RUNNER for a fake."""
import json
import re
import sys

from clipper import ROOT, settings


class LLMError(Exception):
    pass


def _claude(prompt, timeout):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import brain
    claude = brain.find_claude()
    if not claude:
        raise LLMError("Claude Code isn't installed or logged in")
    cmd = [claude, "-p", prompt, "--disallowedTools", "Edit Write NotebookEdit Bash WebSearch WebFetch Task"]
    model = settings.general().get("llm_model")
    if model:
        cmd += ["--model", model]
    r = brain.run_background(cmd, timeout, who="clipper")
    if r.returncode != 0:
        raise LLMError((r.stderr or r.stdout or "Claude Code failed").strip()[-300:])
    return r.stdout


RUNNER = _claude          # (prompt, timeout) -> text


def available():
    if RUNNER is not _claude:
        return True
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import brain
        return bool(brain.find_claude())
    except Exception:
        return False


def parse_json(text):
    """The first JSON object/array in text (Claude sometimes wraps it in prose or ```json)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    pairs = sorted((("[", "]"), ("{", "}")), key=lambda p: text.find(p[0]) if text.find(p[0]) >= 0 else len(text))
    for opener, closer in pairs:
        a = text.find(opener)
        if a < 0:
            continue
        depth, instr, esc = 0, False, False
        for i in range(a, len(text)):
            ch = text[i]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
            elif ch == '"':
                instr = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[a:i + 1])
                    except ValueError:
                        break
    raise LLMError("no valid JSON in Claude's answer")


def ask_json(prompt, timeout=300, tries=2):
    last = None
    for _ in range(tries):
        try:
            return parse_json(RUNNER(prompt, timeout))
        except LLMError as e:
            last = e
        except Exception as e:                  # timeouts etc.
            last = LLMError(f"{type(e).__name__}: {e}")
    raise last
