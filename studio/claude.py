"""Asking Claude for the studio: Claude Code on Connor's plan (no API key, nothing extra to pay).

Every call goes through guard.allow("claude.ask"), has no file or shell tools, and counts against
a daily cap (config "studio" "claude_calls_per_day") because Connor's plan is shared with Jarvis's
own answers. web=True lets Claude use WebSearch/WebFetch (read-only) for research and fact-checks.
When the plan is busy or the cap is reached, work waits; there is never a paid fallback."""
import datetime
import sys
import time

from studio import ROOT, config, db, guard
from studio.jobs import Permanent, QuotaWait, Transient

NO_TOOLS = "Edit Write NotebookEdit Bash Task"


def _today():
    return datetime.date.today().isoformat()


def used_today():
    return int(db.get(f"claude_calls_{_today()}", 0) or 0)


def _claude(prompt, timeout, web):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import brain
    exe = brain.find_claude()
    if not exe:
        raise Permanent("Claude Code isn't installed or logged in on this computer")
    cmd = [exe, "-p", prompt]
    if web:
        cmd += ["--allowedTools", "WebSearch WebFetch", "--disallowedTools", NO_TOOLS]
    else:
        cmd += ["--disallowedTools", NO_TOOLS + " WebSearch WebFetch"]
    r = brain.run_claude(cmd, timeout)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "Claude Code failed").strip()[-300:]
        low = err.lower()
        if "limit" in low or "usage" in low or "overloaded" in low or "rate" in low:
            raise QuotaWait(f"Claude is busy or the plan's limit is reached: {err}", time.time() + 3600)
        raise Transient(err)
    return r.stdout


RUNNER = _claude          # tests swap this: (prompt, timeout, web) -> text


def ask(prompt, timeout=300, web=False, purpose=""):
    """Claude's answer as text."""
    guard.allow("claude.ask", purpose or prompt[:120], record=False)
    cap = config.general()["claude_calls_per_day"]
    if used_today() >= cap:
        raise QuotaWait(f"the studio has used today's {cap} Claude calls", _tomorrow())
    db.put(f"claude_calls_{_today()}", used_today() + 1)
    return RUNNER(prompt, timeout, web)


def ask_json(prompt, timeout=300, web=False, purpose=""):
    """Claude's answer parsed as JSON (asked once more if it isn't valid JSON)."""
    from clipper import llm
    text = ask(prompt, timeout, web, purpose)
    try:
        return llm.parse_json(text)
    except llm.LLMError:
        text = ask(prompt + "\n\nYour last answer wasn't valid JSON. Answer with ONLY the JSON, nothing else.",
                   timeout, web, purpose)
        try:
            return llm.parse_json(text)
        except llm.LLMError:
            raise Transient("Claude's answer wasn't valid JSON twice")


def _tomorrow():
    t = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time(0, 5))
    return t.timestamp()
