"""Asking Claude for the studio: Claude Code on Connor's plan (no API key, nothing extra to pay).

Every call goes through guard.allow("claude.ask"), has no file or shell tools, and counts against
a daily cap (config "studio" "claude_calls_per_day") because Connor's plan is shared with Jarvis's
own answers. web=True lets Claude use WebSearch/WebFetch (read-only) for research and fact-checks.
When the plan is busy or the cap is reached, work waits; there is never a paid fallback."""
import datetime
import json
import sys
import time

from studio import ROOT, config, db, guard
from studio.jobs import Permanent, QuotaWait, Transient

NO_TOOLS = "Edit Write NotebookEdit Bash Task"


def _today():
    return datetime.date.today().isoformat()


def used_today():
    return int(db.get(f"claude_calls_{_today()}", 0) or 0)


SYSTEM = """You are the research and writing engine of a science YouTube Shorts studio. Today is {date}.
Do exactly the task in the user's message, carefully and completely. Be accurate: never invent facts,
numbers, names, quotes, sources or URLs. When the task asks for JSON, answer with only that JSON:
no prose before or after it and no code fences."""
WEB = ("\nYou have WebSearch and WebFetch. Use them to find and open real pages; only cite URLs you "
       "actually opened, and check claims against what the pages really say.")


def _room():
    """An empty working folder, so Claude Code adds no project files or git status to every call."""
    from studio import data_dir
    return str(data_dir("claude_room"))


def lean_on():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import brain
    return brain.lean_on() and config.general().get("lean_claude", True) is not False


def command(exe, prompt, web, lean=True):
    """The claude command. Lean (the default): only the tools the job needs (none, or the two read-only
    web tools), a short system prompt written for this studio instead of Claude Code's coding one, no
    skills, no MCP servers and no saved session. The model and its effort are left exactly as they are."""
    if not lean:
        cmd = [exe, "-p", prompt]
        if web:
            return cmd + ["--allowedTools", "WebSearch WebFetch", "--disallowedTools", NO_TOOLS]
        return cmd + ["--disallowedTools", NO_TOOLS + " WebSearch WebFetch"]
    system = SYSTEM.format(date=datetime.date.today().strftime("%B %d, %Y")) + (WEB if web else "")
    cmd = [exe, "-p", prompt, "--tools", "WebSearch,WebFetch" if web else "", "--system-prompt", system,
           "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    if web:
        cmd += ["--allowedTools", "WebSearch WebFetch"]
    return cmd


def _result(r):
    """(ok, text, usage) from a claude run; understands --output-format json and plain text."""
    out = (r.stdout or "").strip()
    try:
        j = json.loads(out)
    except ValueError:
        j = None
    if isinstance(j, dict) and "result" in j:
        bad = r.returncode != 0 or j.get("is_error")
        return not bad, str(j.get("result") or ""), j
    return r.returncode == 0, r.stdout, None


def _note(usage, seconds, purpose):
    """Adds one call's tokens and time to today's totals (python -m studio usage shows them)."""
    try:
        u = (usage or {}).get("usage") or {}
        key = f"claude_usage_{_today()}"
        d = db.get(key, {}) or {}
        d["calls"] = d.get("calls", 0) + 1
        d["seconds"] = round(d.get("seconds", 0) + seconds, 1)
        d["input"] = d.get("input", 0) + int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
        d["cached"] = d.get("cached", 0) + int(u.get("cache_read_input_tokens", 0))
        d["output"] = d.get("output", 0) + int(u.get("output_tokens", 0))
        d["searches"] = d.get("searches", 0) + int((u.get("server_tool_use") or {}).get("web_search_requests", 0))
        tag = (purpose or "other").split(":")[0].strip()[:40] or "other"
        d.setdefault("by", {})[tag] = d.get("by", {}).get(tag, 0) + 1
        db.put(key, d)
        kind = LOCAL_KINDS.get(tag)
        if kind:                                  # so the local brain's "tokens saved" uses real sizes
            local = _local()
            if local:
                local.claude_used(kind, sum(int(u.get(k) or 0) for k in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")))
    except Exception:
        pass


LOCAL_KINDS = {"json repair": "studio_json", "script length": "studio_length", "script": "studio_draft"}


def _local():
    """local_brain (Ollama on this computer), or None."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import local_brain
        return local_brain
    except Exception:
        return None


def _words(text):
    import re
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", str(text).lower())


def _values(obj):
    if isinstance(obj, dict):
        return " ".join(f"{k} {_values(v)}" for k, v in obj.items())
    if isinstance(obj, list):
        return " ".join(_values(v) for v in obj)
    if obj is None or isinstance(obj, bool):
        return json.dumps(obj)
    return str(obj)


def local_json_repair(prompt, text):
    """The local model turns a broken JSON answer into valid JSON, or None. Used only if it kept the answer
    word for word: every word it gives back was in the answer, and it dropped almost nothing."""
    local = _local()
    if not local or not local.ready("studio_json")[0]:
        return None
    from clipper import llm
    size = len(text)
    if size > 16000:
        return None
    ans = local.chat([{"role": "system", "content": "You fix broken JSON. Rewrite the answer as valid JSON in the shape "
                                                    "asked for. Keep every value and word exactly as it is; add nothing, "
                                                    "drop nothing. Answer with ONLY the JSON."},
                      {"role": "user", "content": "The shape asked for:\n" + _shape(prompt) + "\n\nThe answer:\n" + text.strip()}],
                     json_mode=True, max_tokens=int(size / 2.5) + 300, timeout=180, first_timeout=60,
                     num_ctx=min(16384, 2048 + int(size / 2)), temperature=0)
    if not ans:
        return None
    try:
        fixed = llm.parse_json(ans)
    except llm.LLMError:
        return None
    raw = text.strip()
    a, b = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0] or [0]), max(raw.rfind("}"), raw.rfind("]"))
    before, after = _words(raw[a:b + 1] if b > a else raw), _words(_values(fixed))
    have = set(_words(raw))
    if not after or any(w not in have for w in after) or len(after) < 0.9 * len(before):
        print("(local brain: its JSON fix changed the answer, asking Claude instead)")
        return None
    local.saved("studio_json")
    return fixed


_OLD = []


def _too_old(error):
    """True (and the old way from now on) if this Claude Code doesn't know one of the lean options."""
    low = (error or "").lower()
    if any(w in low for w in ("unknown option", "unknown argument", "error: option")):
        _OLD.append(True)
        return True
    return False


def _claude(prompt, timeout, web, purpose=""):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import brain
    exe = brain.find_claude()
    if not exe:
        raise Permanent("Claude Code isn't installed or logged in on this computer")
    lean = lean_on() and not _OLD
    t0 = time.time()
    r = brain._run_claude(command(exe, prompt, web, lean), timeout, _room()) if lean else \
        brain.run_claude(command(exe, prompt, web, False), timeout)
    ok, text, usage = _result(r)
    if lean and not ok and not text.strip() and _too_old(r.stderr):
        r = brain.run_claude(command(exe, prompt, web, False), timeout)     # older Claude Code: the old way
        ok, text, usage = _result(r)
    _note(usage, time.time() - t0, purpose)
    if not ok:
        err = (text if usage else "") or r.stderr or r.stdout or "Claude Code failed"
        err = err.strip()[-300:]
        low = err.lower()
        if "limit" in low or "usage" in low or "overloaded" in low or "rate" in low:
            try:
                import budget
                budget.saw(err)
            except Exception:
                pass
            raise QuotaWait(f"Claude is busy or the plan's limit is reached: {err}", time.time() + 3600)
        raise Transient(err)
    return text


RUNNER = _claude          # tests swap this: (prompt, timeout, web) -> text


def _shared_budget():
    """The studio also counts toward Jarvis's one daily limit for background Claude calls (budget.py)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import budget
    ok, why = budget.check("studio")
    if not ok:
        until = budget.paused_until()
        raise QuotaWait(f"waiting: {why}", until if until > time.time() else _tomorrow())
    budget.spend("studio")


def ask(prompt, timeout=300, web=False, purpose=""):
    """Claude's answer as text."""
    guard.allow("claude.ask", purpose or prompt[:120], record=False)
    cap = config.general()["claude_calls_per_day"]
    if used_today() >= cap:
        raise QuotaWait(f"the studio has used today's {cap} Claude calls", _tomorrow())
    if RUNNER is _claude:
        _shared_budget()
    db.put(f"claude_calls_{_today()}", used_today() + 1)
    if RUNNER is _claude:
        return _claude(prompt, timeout, web, purpose)
    return RUNNER(prompt, timeout, web)


def _shape(prompt):
    """The part of a prompt that describes the JSON answer (its last paragraph, normally "JSON only: ...")."""
    i = max(prompt.rfind("\nJSON"), prompt.rfind("\n\nJSON"))
    return prompt[i:].strip()[:1500] if i >= 0 else prompt[-800:]


def ask_json(prompt, timeout=300, web=False, purpose=""):
    """Claude's answer parsed as JSON. If it isn't valid JSON, a small no-web call first turns that same
    answer into JSON (so a web research job isn't redone); only if that fails is the whole question asked again.
    Every call, the repair too, counts against the daily cap."""
    from clipper import llm
    text = ask(prompt, timeout, web, purpose)
    try:
        return llm.parse_json(text)
    except llm.LLMError:
        pass
    if text.strip():
        fixed = local_json_repair(prompt, text)          # on this computer first: no Claude call
        if fixed is not None:
            return fixed
        fixed = ask("Rewrite the answer below as valid JSON in exactly the shape asked for. Keep every value and "
                    "word as it is; add nothing, drop nothing. Answer with ONLY the JSON.\n\nThe shape asked for:\n"
                    + _shape(prompt) + "\n\nThe answer:\n" + text.strip()[-20000:], 180, False, "json repair")
        try:
            return llm.parse_json(fixed)
        except llm.LLMError:
            pass
    text = ask(prompt + "\n\nYour last answer wasn't valid JSON. Answer with ONLY the JSON, nothing else.",
               timeout, web, purpose)
    try:
        return llm.parse_json(text)
    except llm.LLMError:
        raise Transient("Claude's answer wasn't valid JSON twice")


def usage(days=7):
    """The studio's Claude use for the last few days: calls, tokens, seconds, web searches, by job."""
    out = {}
    for n in range(days):
        d = (datetime.date.today() - datetime.timedelta(days=n)).isoformat()
        u = db.get(f"claude_usage_{d}")
        if u:
            out[d] = u
    return out


def _tomorrow():
    t = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time(0, 5))
    return t.timestamp()
