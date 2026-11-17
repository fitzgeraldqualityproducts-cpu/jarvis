"""Jarvis's AI team: a crew of specialist AI agents that work on a project together.

Say "get the team on <something>" (or "have your agents research...", "start a project to...").
A team lead (Claude) breaks the job into tasks and hands each to a specialist: researchers search the
web, the planner and strategists think it through, the coder writes code in the project's own folder,
the writer pulls it together. Specialists that depend on each other's work get it passed along, and
the ones that don't work at the same time. A critic then checks everything against what you asked;
if it isn't good enough, the specialists redo their part with the critic's notes. The lead writes the
final report and Jarvis tells you the gist.

It runs in the background, so you can keep talking to him. Reports are saved in the "team" folder
next to this file (and in your Obsidian vault, under "Jarvis Team", if you use one).

Safety: the agents can only search the web, think, and write files inside their own project folder.
They can't send, post, buy, delete or change anything of yours, and they can't change Jarvis. If the
job needs any of that, the report lists it as something that needs your OK, and if it's a change to
Jarvis himself he asks you first and does it through the normal "upgrade yourself" route.

  "get the team on ..."                      start a project
  "how's the team doing"                     progress
  "read me the report"                       the latest report's summary and next steps
  "who's on your team"                       the specialists
  "call off the team"                        stop the current project
  "show me the team"                         open the live network view

Live view: every real step the team takes (who hands what to whom, each web search, file write and
tool call, each agent's status, the critic's verdict, approvals) is recorded as an event, saved to
events.jsonl in the project's folder, and served to the display at http://localhost:8765/team, which
draws the network from those events. Nothing on that page is made up.
Set "team": false in config.json to turn it off."""
import datetime
import json
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDER = HERE / "team"
FILE = HERE / "team.json"
CONFIG = HERE / "config.json"
_lock = threading.RLock()
_stop = threading.Event()
_current = {}           # the project being worked on: id, title, steps, procs

THINK = ["--disallowedTools", "Read Glob Grep Edit Write NotebookEdit Bash WebSearch WebFetch Task"]
WEB = ["--allowedTools", "WebSearch WebFetch", "--disallowedTools", "Read Glob Grep Edit Write NotebookEdit Bash Task"]
BUILD = ["--allowedTools", "Read Glob Grep Edit Write", "--permission-mode", "acceptEdits",
         "--disallowedTools", "Bash NotebookEdit WebSearch WebFetch Task"]

# The specialists: name -> (what they do, their tools)
ROLES = {
    "researcher": ("Researcher: finds current facts, sources, prices and examples on the web.", WEB),
    "market analyst": ("Market analyst: sizes markets, studies competitors, customers and pricing, using the web.", WEB),
    "fact checker": ("Fact checker: verifies claims, numbers and dates on the web and flags anything wrong.", WEB),
    "trend scout": ("Trend scout: finds what's new and rising in a field (products, tech, culture), using the web.", WEB),
    "planner": ("Planner: turns a goal into a clear step-by-step plan with a timeline and milestones.", THINK),
    "project manager": ("Project manager: tasks, owners, order, risks and what to do first this week.", THINK),
    "business strategist": ("Business strategist: business models, positioning, offers and how to make money.", THINK),
    "finance analyst": ("Finance analyst: budgets, costs, pricing maths, break-even and simple forecasts.", THINK),
    "marketer": ("Marketer: audience, messaging, content ideas, channels and launch plans.", THINK),
    "designer": ("Designer: look and feel, branding, layouts and user experience ideas, described in words.", THINK),
    "coder": ("Coder: writes working code and files for the project in its project folder.", BUILD),
    "code reviewer": ("Code reviewer: reads the code written in the project folder and finds bugs and fixes.", BUILD),
    "writer": ("Writer: clear documents, scripts, emails, posts and summaries (drafts only, nothing is sent).", THINK),
    "editor": ("Editor: tightens and corrects writing, and makes it sound like the owner.", THINK),
    "coach": ("Coach: habits, health, training and personal routines, practical and encouraging.", THINK),
    "devil's advocate": ("Devil's advocate: finds the weak spots, risks and better alternatives.", THINK),
    "store builder": ("Store builder: uses the owner's own StoreForge app to turn product links into a "
                      "ready-to-launch Shopify store (analysis, brand, listing, content, export ZIP). Pushing "
                      "to the real store waits for the owner's OK.", "STORE"),
}


def _roles():
    """The specialists available on this computer (the store builder only where StoreForge is)."""
    try:
        import storeforge
        store = storeforge.available()
    except Exception:
        store = False
    return {k: v for k, v in ROLES.items() if v[1] != "STORE" or store}


def _store_tools(folder, pid):
    """Tools for the store builder: StoreForge (through storeforge_mcp.py) and the web, nothing else."""
    import sys
    cfg = Path(folder) / "storeforge-mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"storeforge": {
        "command": sys.executable, "args": [str(HERE / "storeforge_mcp.py")],
        "env": {"JARVIS_TEAM_PID": str(pid), "PYTHONUTF8": "1"}}}}))
    return ["--mcp-config", str(cfg), "--allowedTools", "mcp__storeforge WebSearch WebFetch",
            "--disallowedTools", "Read Glob Grep Edit Write NotebookEdit Bash Task"]

LEAD_PROMPT = """You are the team lead of Jarvis's AI team, working for {owner}. {owner} asked out loud:
"{goal}"

Your specialists (use only these names):
{roster}

Break the job into 2 to 6 tasks for specialists. Tasks that need another task's result list it in "needs";
tasks that don't will be done at the same time. Use web specialists for anything current or factual, and
the coder only if code or files are genuinely wanted. Keep it to what's useful: a small job gets 2 tasks.
{context}
Nobody on the team can send messages, post, buy, book, delete, or change {owner}'s accounts or Jarvis
himself. If the job needs any of that, plan to prepare it (drafts, steps, code) and list the real-world
step in "needs_ok". If {owner} wants Jarvis himself changed, put the precise change request in
"jarvis_change" (and no coder task for it).

Reply with ONLY one line of JSON, no markdown:
{{"title": "short project name (3-6 words)",
  "tasks": [{{"id": "t1", "role": "researcher", "task": "exactly what to do and deliver", "needs": []}}],
  "needs_ok": ["real-world steps only {owner} can approve"],
  "jarvis_change": ""}}"""

WORKER_PROMPT = """You are the {role} on Jarvis's AI team, working for {owner}. {about}

The team's project: "{goal}"
Your task: {task}
{inputs}{notes}{rules}
Do the task well and completely, then write your result as clear, well organised markdown for the rest of
the team. Be concrete (names, numbers, steps). Don't ask questions: make sensible assumptions and say what
they are. Never send, post, buy or delete anything: only if something actually needs doing in the real
world, write it up under a heading "Needs {owner}'s OK" (leave that heading out when nothing does)."""

CRITIC_PROMPT = """You are the critic on Jarvis's AI team. {owner} asked: "{goal}"

Here is the team's work:
{work}

Judge it strictly against what {owner} asked: is it correct, complete, specific and useful? Reply with ONLY
one line of JSON, no markdown:
{{"score": 1-10, "verdict": "one sentence", "redo": [{{"id": "task id", "fix": "what to improve"}}]}}
Only ask for redos that would clearly make it better (none if score is 8 or more)."""

REPORT_PROMPT = """You are the team lead of Jarvis's AI team. {owner} asked: "{goal}"

The team's work:
{work}

Critic's verdict: {verdict}

Write the final report for {owner} in markdown: a short summary first, then the findings or deliverables,
then "Next steps", then (only if there are any) "Needs your OK" for real-world actions only {owner} can
approve. Keep everything the team found that matters; cut repetition.

After the report, on its own last line, write SPOKEN: followed by 2 short sentences Jarvis will say out loud
to {owner} with the most important result (no markdown, links or lists)."""


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def enabled():
    return _cfg().get("team", True)


def _load():
    try:
        data = json.loads(FILE.read_text())
    except Exception:
        data = {}
    data.setdefault("projects", [])
    return data


def _save(data):
    data["projects"] = data["projects"][-40:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(FILE)


def _update(pid, **changes):
    with _lock:
        data = _load()
        for p in data["projects"]:
            if p["id"] == pid:
                p.update(changes)
        _save(data)


def _owner():
    try:
        import speaker
        return speaker.owner_name()
    except Exception:
        return "the owner"


def _json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# ---------- live events (for the network view) ----------

_live_lock = threading.RLock()
LIVE = {"pid": None, "events": [], "folder": None}


def _emit(kind, **data):
    """Record one real thing that just happened in the team. The live view is drawn only from these."""
    with _live_lock:
        ev = dict(data, type=kind, seq=len(LIVE["events"]) + 1, t=round(time.time(), 2))
        LIVE["events"].append(ev)
        folder = LIVE.get("folder")
    if folder:
        try:
            with open(Path(folder) / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev) + "\n")
        except Exception:
            pass
    return ev


def _begin_live(pid, folder):
    with _live_lock:
        LIVE.update(pid=pid, events=[], folder=str(folder))


def _tool_detail(name, inp):
    inp = inp or {}
    for k in ("query", "url", "file_path", "pattern", "path", "command", "description", "prompt", "name",
              "project_id", "section"):
        if inp.get(k):
            v = str(inp[k])
            if k == "file_path":
                v = Path(v).name
            return v[:160]
    return ""


def _stream(cmd, timeout, cwd, node, task):
    """Runs claude, turning its stream into live events. Returns (text, ok_stream)."""
    import subprocess
    import brain
    cmd = list(cmd) + ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    prompt = cmd.pop(cmd.index("-p") + 1) if brain.WINDOWS else None
    kw = {"creationflags": 0x08000000} if brain.WINDOWS else {}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd, bufsize=1, **kw)
    _procs.add(proc)
    err = []
    threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True).start()
    if prompt is not None:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
    timer = threading.Timer(timeout, proc.kill)
    timer.daemon = True
    timer.start()
    text, result, got, last_tick, writing, names = [], None, False, 0.0, False, {}
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
                    text.append(delta.get("text", ""))
                    if not writing:
                        writing = True
                        _emit("agent", node=node, task=task, status="working", activity="writing")
                    if time.time() - last_tick > 1.5:
                        last_tick = time.time()
                        _emit("writing", node=node, task=task, chars=len("".join(text)),
                              tail="".join(text)[-240:])
            elif kind == "assistant" and not d.get("parent_tool_use_id"):
                for block in (d.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        writing = False
                        names[block.get("id")] = block.get("name", "tool")
                        _emit("tool", node=node, task=task, tool=block.get("name", "tool"),
                              detail=_tool_detail(block.get("name"), block.get("input")))
                        _emit("agent", node=node, task=task, status="working", activity=f"using {block.get('name')}")
            elif kind == "user" and not d.get("parent_tool_use_id"):
                for block in (d.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("is_error"):
                            body = block.get("content")
                            body = " ".join(x.get("text", "") for x in body if isinstance(x, dict)) \
                                if isinstance(body, list) else str(body or "")
                            _emit("blocked", node=node, task=task, tool=names.get(block.get("tool_use_id"), "tool"),
                                  reason=body[:160])
                        _emit("agent", node=node, task=task, status="analyzing", activity="reading tool results")
            elif kind == "result":
                got = True
                result = "" if d.get("is_error") else (d.get("result") or "")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(5)
        except Exception:
            pass
        _procs.discard(proc)
    out = (result if result else "".join(text)).strip()
    if not got:
        print(f"(team: streaming gave nothing for {node}: {''.join(err)[-200:]})")
    return out, got


_procs = set()


def _run(prompt, tools, cwd, timeout, label, node=None, task=None):
    """One agent: a claude -p run. Returns its text ('' if it failed or was stopped).
    Its tool use and writing are reported live as it goes."""
    import brain
    import subprocess
    if _stop.is_set():
        return ""
    claude = brain.find_claude()
    if not claude:
        raise RuntimeError("I can't find Claude Code")
    node = node or label
    start = time.time()
    _emit("agent", node=node, task=task, status="analyzing", activity="thinking")
    text, streamed = "", False
    try:
        text, streamed = _stream([claude, "-p", prompt] + list(tools), timeout, str(cwd), node, task)
    except OSError as e:
        print(f"(team: couldn't stream {label}: {e})")
    if not streamed and not _stop.is_set():          # older Claude Code: the plain way
        try:
            out = brain.run_claude([claude, "-p", prompt] + list(tools), timeout=timeout, cwd=str(cwd))
            text = (out.stdout or "").strip()
            if not text:
                print(f"(team: {label} gave nothing: {(out.stderr or '')[-200:]})")
        except subprocess.TimeoutExpired:
            print(f"(team: {label} ran out of time)")
    secs = round(time.time() - start, 1)
    print(f"(team: {label} done in {secs:.0f}s)")
    _emit("finished", node=node, task=task, ok=bool(text), seconds=secs, chars=len(text))
    return text


def _rules():
    try:
        import learn
        rules = learn.rules()
        return ("\nHow the owner likes things done (lessons Jarvis has learned):\n- " + "\n- ".join(rules) + "\n") \
            if rules else ""
    except Exception:
        return ""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


def _plan(goal, owner, folder):
    roster = "\n".join(f"- {name}: {about}" for name, (about, _) in _roles().items())
    context = ""
    try:
        import memory
        fs = memory.facts()
        if fs:
            context = "\nWhat Jarvis knows about " + owner + ": " + "; ".join(fs[-12:]) + "\n"
    except Exception:
        pass
    plan = _json(_run(LEAD_PROMPT.format(owner=owner, goal=goal, roster=roster, context=context),
                      THINK, folder, 180, "team lead (plan)", node="lead"))
    if not plan or not plan.get("tasks"):
        plan = {"title": goal[:40], "tasks": [{"id": "t1", "role": "researcher", "task": goal, "needs": []},
                                             {"id": "t2", "role": "planner", "task": f"Turn the research into a "
                                              f"practical plan for: {goal}", "needs": ["t1"]}]}
    tasks = []
    for i, t in enumerate(plan["tasks"][:6]):
        role = str(t.get("role", "")).lower()
        if role not in _roles():
            role = "researcher" if re.search(r"research|find|search|look", str(t.get("task", "")), re.I) else "planner"
        tasks.append({"id": str(t.get("id") or f"t{i + 1}"), "role": role, "task": str(t.get("task") or goal),
                      "needs": [str(n) for n in (t.get("needs") or [])], "result": "", "status": "waiting"})
    ids = {t["id"] for t in tasks}
    for t in tasks:
        t["needs"] = [n for n in t["needs"] if n in ids and n != t["id"]]
    plan["tasks"] = tasks
    _emit("plan", title=plan.get("title") or goal[:40],
          tasks=[{k: t[k] for k in ("id", "role", "task", "needs")} for t in tasks],
          needs_ok=[str(x) for x in (plan.get("needs_ok") or [])], jarvis_change=str(plan.get("jarvis_change") or ""))
    for t in tasks:
        _emit("delegate", **{"from": "lead", "to": t["role"], "task": t["id"], "text": t["task"][:200]})
        _emit("agent", node=t["role"], task=t["id"], status="waiting",
              activity=("waiting for " + ", ".join(t["needs"])) if t["needs"] else "queued")
    return plan


def _do_task(t, plan, goal, owner, folder, notes=""):
    about, tools = ROLES[t["role"]]
    if tools == "STORE":
        tools = _store_tools(folder, LIVE.get("pid"))
    inputs = ""
    for n in t["needs"]:
        dep = next((x for x in plan["tasks"] if x["id"] == n), None)
        if dep and dep["result"]:
            inputs += f"\nFrom the {dep['role']} ({dep['task'][:80]}):\n{dep['result'][:6000]}\n"
    for n in t["needs"]:
        dep = next((x for x in plan["tasks"] if x["id"] == n), None)
        if dep and dep["result"]:
            _emit("handoff", **{"from": dep["role"], "to": t["role"], "from_task": dep["id"], "task": t["id"],
                                "chars": len(dep["result"][:6000])})
    if inputs:
        inputs = "\nWork your teammates have already done:" + inputs
    if notes:
        _emit("delegate", **{"from": "critic", "to": t["role"], "task": t["id"], "text": "Redo: " + notes[:200]})
        notes = f"\nThe critic reviewed your last attempt and wants this fixed: {notes}\nYour last attempt:\n" \
                f"{t['result'][:5000]}\n"
    if t["role"] in ("coder", "code reviewer"):
        notes += "\nWrite any code or files in the current folder (the project's own folder). Keep it working " \
                 "and explain how to run it.\n"
    prompt = WORKER_PROMPT.format(role=t["role"], owner=owner, about=about, goal=goal, task=t["task"],
                                  inputs=inputs, notes=notes, rules=_rules())
    t["status"] = "working"
    timeout = 1500 if t["role"] == "store builder" else 900 if tools is BUILD else 600
    t["result"] = _run(prompt, tools, folder, timeout, t["role"], node=t["role"], task=t["id"]) or t["result"]
    t["status"] = "done" if t["result"] else "failed"
    m = re.search(r"^#+\s*needs .{0,20}ok\W*\n+\s*(?:[-*]\s*)?(.{0,40})", t["result"], re.I | re.M)
    needs_ok = bool(m) and not re.match(r"(nothing|none|no\b|n/a)", m.group(1).strip(), re.I)
    _emit("result", node=t["role"], task=t["id"], ok=bool(t["result"]), preview=t["result"][:4000],
          needs_ok=needs_ok)
    _emit("agent", node=t["role"], task=t["id"],
          status=("approval" if needs_ok else "completed") if t["result"] else "failed",
          activity="needs your OK on something" if needs_ok else "done" if t["result"] else "failed")
    if t["result"]:
        try:
            (folder / f"{t['id']} {t['role']}.md").write_text(t["result"], encoding="utf-8")
        except Exception:
            pass


def _waves(tasks):
    """Groups tasks so each group only needs earlier groups."""
    done, waves, left = set(), [], list(tasks)
    while left:
        ready = [t for t in left if all(n in done for n in t["needs"])] or left[:1]
        waves.append(ready)
        done |= {t["id"] for t in ready}
        left = [t for t in left if t not in ready]
    return waves


def _work_text(plan, limit=5000):
    return "\n\n".join(f"## {t['id']} ({t['role']}): {t['task']}\n{(t['result'] or '(no result)')[:limit]}"
                       for t in plan["tasks"])


def _run_wave(wave, plan, goal, owner, folder, notes=None):
    threads = []
    for t in wave:
        th = threading.Thread(target=_do_task, args=(t, plan, goal, owner, folder, (notes or {}).get(t["id"], "")),
                              daemon=True, name=f"team-{t['role']}")
        th.start()
        threads.append(th)
        if len(threads) >= 3:            # at most 3 agents at once
            threads.pop(0).join()
    for th in threads:
        th.join()


def _project(pid, goal):
    owner = _owner()
    folder = Path(_load_project(pid)["folder"])
    started = time.time()
    try:
        _current.update(id=pid, step="planning")
        _emit("phase", phase="planning")
        _emit("delegate", **{"from": "jarvis", "to": "lead", "task": None, "text": goal[:200]})
        plan = _plan(goal, owner, folder)
        _emit("agent", node="lead", status="waiting", activity="waiting for the specialists")
        _current["plan"] = plan
        _update(pid, title=plan.get("title") or goal[:40], status="working",
                tasks=[{"role": t["role"], "task": t["task"]} for t in plan["tasks"]])
        print(f"(team: {plan.get('title')}: " + ", ".join(f"{t['role']}" for t in plan["tasks"]) + ")")
        _emit("phase", phase="working")
        for wave in _waves(plan["tasks"]):
            if _stop.is_set():
                break
            _current["step"] = ", ".join(t["role"] for t in wave)
            _run_wave(wave, plan, goal, owner, folder)
        if _stop.is_set():
            _update(pid, status="cancelled")
            _emit("phase", phase="cancelled")
            return
        _current["step"] = "critic"
        _emit("phase", phase="reviewing")
        for t in plan["tasks"]:
            _emit("handoff", **{"from": t["role"], "to": "critic", "from_task": t["id"], "task": None,
                                "chars": len(t["result"][:5000])})
        review = _json(_run(CRITIC_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan)), THINK, folder, 240,
                            "critic", node="critic")) or {}
        score = review.get("score")
        redo = {str(r.get("id")): str(r.get("fix", "")) for r in (review.get("redo") or []) if r.get("id")}
        redo = {k: v for k, v in redo.items() if any(t["id"] == k for t in plan["tasks"])}
        _emit("review", score=score, verdict=str(review.get("verdict", ""))[:300], redo=list(redo),
              round=1)
        _emit("agent", node="critic", status="completed", activity=f"scored it {score}/10" if score else "reviewed")
        if redo and isinstance(score, (int, float)) and score < 8 and not _stop.is_set():
            _emit("phase", phase="redoing")
            _current["step"] = "redoing " + ", ".join(t["role"] for t in plan["tasks"] if t["id"] in redo)
            print(f"(team: critic gave {score}/10, redoing {list(redo)})")
            _run_wave([t for t in plan["tasks"] if t["id"] in redo], plan, goal, owner, folder, redo)
            _emit("phase", phase="reviewing")
            review2 = _json(_run(CRITIC_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan)), THINK, folder,
                                 240, "critic (second look)", node="critic")) or {}
            if review2.get("score"):
                review, score = review2, review2.get("score")
                _emit("review", score=score, verdict=str(review.get("verdict", ""))[:300], redo=[], round=2)
            _emit("agent", node="critic", status="completed", activity=f"scored it {score}/10" if score else "reviewed")
        _current["step"] = "writing the report"
        _emit("phase", phase="reporting")
        _emit("handoff", **{"from": "critic", "to": "lead", "from_task": None, "task": None, "chars": 0})
        report = _run(REPORT_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan, 8000),
                                           verdict=review.get("verdict", "no verdict")),
                      THINK, folder, 300, "team lead (report)", node="lead")
        spoken = ""
        m = re.search(r"\n\s*SPOKEN:\s*(.+)\s*$", report, re.S)
        if m:
            spoken, report = m.group(1).strip(), report[:m.start()].strip()
        if not report:
            report = _work_text(plan, 20000)
        if not spoken:
            spoken = "They've written it all up for you."
        needs_ok = [str(x) for x in (plan.get("needs_ok") or []) if str(x).strip()]
        try:
            import storeforge
            store_asks = storeforge.waiting(str(pid))
        except Exception:
            store_asks = []
        needs_ok += [f"StoreForge: {a['text']}" for a in store_asks]
        full = (f"# {plan.get('title') or goal}\n\n_Asked: {goal}_  \n_Team: "
                + ", ".join(t["role"] for t in plan["tasks"])
                + (f"  \n_Critic's score: {score}/10_" if score else "") + "\n\n" + report + "\n\n---\n\n# The team's work\n\n"
                + "\n\n".join(f"## {t['role'].title()}: {t['task']}\n\n{t['result'] or '(nothing came back)'}"
                              for t in plan["tasks"]))
        (folder / "report.md").write_text(full, encoding="utf-8")
        _emit("agent", node="lead", status="completed", activity="report written")
        _emit("handoff", **{"from": "lead", "to": "jarvis", "from_task": None, "task": None, "chars": len(report)})
        if needs_ok or plan.get("jarvis_change"):
            _emit("approval", items=needs_ok, change=str(plan.get("jarvis_change") or ""))
        _to_obsidian(plan.get("title") or goal, full)
        _update(pid, status="done", spoken=spoken, score=score, needs_ok=needs_ok,
                jarvis_change=str(plan.get("jarvis_change") or "").strip(), told=False,
                minutes=round((time.time() - started) / 60, 1), finished=time.time(),
                tasks=[{"id": t["id"], "role": t["role"], "task": t["task"], "ok": bool(t["result"])}
                       for t in plan["tasks"]])
        _emit("phase", phase="done", score=score, minutes=round((time.time() - started) / 60, 1),
              spoken=spoken)
        try:
            import learn
            learn.record_result("team", plan.get("title") or goal, (score or 0) >= 7, score=score,
                                detail=review.get("verdict", ""))
        except Exception:
            pass
        print(f"(team: finished {plan.get('title')!r} in {(time.time() - started) / 60:.1f} min, score {score})")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _update(pid, status="failed", error=f"{type(e).__name__}: {e}"[:300], told=False)
        _emit("phase", phase="failed", error=f"{type(e).__name__}: {e}"[:300])
        try:
            import learn
            learn.note(goal, "My team couldn't finish that project.", "error", f"workforce: {type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        _current.clear()
        _stop.clear()


def _to_obsidian(title, text):
    try:
        import obsidian
        v = obsidian.vault() if obsidian.enabled() else None
        if v:
            d = v / "Jarvis Team"
            d.mkdir(exist_ok=True)
            name = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", title).strip()[:60] or "Project"
            (d / f"{datetime.date.today().isoformat()} {name}.md").write_text(text, encoding="utf-8")
    except Exception as e:
        print(f"(team: couldn't save to Obsidian: {e})")


def _load_project(pid):
    return next((p for p in _load()["projects"] if p["id"] == pid), {})


def busy():
    return bool(_current)


def start(goal):
    """Start a project in the background. Returns what to say."""
    if not enabled():
        return "My team is switched off. Set team to true in my config to bring them back."
    import brain
    if not brain.find_claude():
        return "I can't find Claude Code, so my team has no brains to work with."
    with _lock:
        if _current:
            p = _load_project(_current.get("id"))
            return f"The team's still busy with {p.get('title') or 'the last project'}. Say call off the team to stop it."
        pid = int(time.time())
        folder = FOLDER / f"{datetime.date.today().isoformat()} {_slug(goal)}"
        folder.mkdir(parents=True, exist_ok=True)
        data = _load()
        data["projects"].append({"id": pid, "goal": goal, "title": goal[:40], "status": "planning",
                                 "folder": str(folder), "started": time.time()})
        _save(data)
        _current.update(id=pid, step="planning")
        _begin_live(pid, folder)
        _emit("mission", pid=pid, goal=goal)
    threading.Thread(target=_project, args=(pid, goal), daemon=True, name="team").start()
    return ("On it. I've got the team lead splitting that up between my specialists. They'll work in the "
            "background, so keep talking to me, and I'll tell you when they're done.")


def status():
    if _current:
        p = _load_project(_current.get("id"))
        plan = _current.get("plan") or {}
        tasks = plan.get("tasks") or []
        done = sum(1 for t in tasks if t["status"] == "done")
        mins = (time.time() - p.get("started", time.time())) / 60
        step = _current.get("step", "")
        if step == "planning":
            return "The team lead is still planning who does what."
        return (f"The team's on {p.get('title')}: {done} of {len(tasks)} tasks done after {mins:.0f} minutes. "
                f"Right now: {step}.")
    last = latest()
    if not last:
        return "The team's free. Say get the team on, then what you want, and they'll get to work."
    return f"The team's free. Their last project was {last.get('title')}, which is {last.get('status')}."


def latest():
    ps = _load()["projects"]
    return ps[-1] if ps else None


def report():
    """The latest finished project, to say out loud."""
    done = [p for p in _load()["projects"] if p.get("status") == "done"]
    if not done:
        return "My team hasn't finished a project yet."
    p = done[-1]
    folder = Path(p["folder"])
    text = p.get("spoken", "")
    try:
        md = (folder / "report.md").read_text(encoding="utf-8")
        steps = re.search(r"#+\s*Next steps\s*\n(.+?)(?:\n#|\Z)", md, re.S | re.I)
        if steps:
            items = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*|\*\*", "", l).strip() for l in steps.group(1).splitlines()]
            items = [i.replace("`", "").rstrip(" .;") for i in items if re.search(r"\w", i)][:3]
            if items:
                text += " Next steps: " + "; ".join(items) + "."
    except Exception:
        pass
    if p.get("needs_ok"):
        text += " They need your OK for: " + "; ".join(p["needs_ok"][:3]) + ". I won't do any of that without you."
    return f"On {p.get('title')}: {text} The full report is in my team folder."


def cancel():
    if not _current:
        return "The team isn't working on anything."
    _stop.set()
    for proc in list(_procs):             # stop the agents that are running right now
        try:
            proc.kill()
        except Exception:
            pass
    _emit("phase", phase="cancelling")
    return "Calling them off now."


def roster():
    names = list(_roles())
    return ("My team: " + ", ".join(names[:-1]) + " and " + names[-1] + ". Plus a team lead who hands out the work "
            "and a critic who checks it. Say get the team on, then what you want.")


def news():
    """A finished project he hasn't told Connor about yet: (id, words, project) or None."""
    for p in reversed(_load()["projects"]):
        if p.get("told") is False:
            if p.get("status") == "done":
                words = f"Your team finished {p.get('title')}. {p.get('spoken', '')}"
                if p.get("needs_ok"):
                    n = len(p["needs_ok"])
                    words += (f" There are {n} things that need your OK." if n > 1 else
                              " There's one thing that needs your OK.") + " Say read me the report."
                return p["id"], words, p
            return p["id"], f"Sorry, my team couldn't finish {p.get('title')}. Something went wrong on their end.", p
    return None


def told(pid):
    _update(pid, told=True)


def projects_text():
    ps = _load()["projects"][-6:]
    return "; ".join(f"{p.get('title')} ({p.get('status')})" for p in ps)



# ---------- the live view's data (display.py serves it at /team/state) ----------

RANK = {"working": 6, "analyzing": 5, "waiting": 4, "approval": 3, "failed": 2, "completed": 1, "idle": 0}


def _load_last_live():
    """After a restart: the last project's recorded events, so the view still shows what really happened."""
    ps = _load()["projects"]
    for p in reversed(ps):
        f = Path(p.get("folder", "")) / "events.jsonl"
        if f.is_file():
            evs = []
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    evs.append(json.loads(line))
                except ValueError:
                    pass
            LIVE.update(pid=p["id"], events=evs, folder=None)
            return
    LIVE["pid"] = 0


def _reduce(events):
    """Works out the current picture (mission, agents, tasks, progress) from the events alone."""
    mission = {"phase": "idle", "approvals": [], "change": ""}
    tasks, nodes = {}, {}

    def node(n):
        return nodes.setdefault(n, {"status": "idle", "activity": "", "tools": 0, "chars": 0, "tasks": []})
    for e in events:
        k = e["type"]
        if k == "mission":
            mission.update(pid=e.get("pid"), goal=e.get("goal"), started=e["t"], title=e.get("goal", "")[:40])
            node("jarvis").update(status="supervising", activity="handing the mission to the team lead")
        elif k == "phase":
            mission["phase"] = e["phase"]
            if e["phase"] in ("done", "failed", "cancelled"):
                mission["finished"] = e["t"]
                mission.update({x: e[x] for x in ("score", "minutes", "spoken", "error") if x in e})
                node("jarvis").update(status="approval" if mission["approvals"] or mission["change"] else
                                      "completed" if e["phase"] == "done" else "failed",
                                      activity={"done": "mission complete", "failed": "mission failed",
                                                "cancelled": "mission called off"}[e["phase"]])
                for t in tasks.values():
                    if t["status"] in ("working", "analyzing", "waiting"):
                        t["status"] = "failed" if e["phase"] == "failed" else "idle"
                        t["activity"] = "stopped"
            elif e["phase"] != "cancelling":
                node("jarvis").update(status="supervising", activity="the team is on it")
        elif k == "plan":
            mission["title"] = e.get("title") or mission.get("title")
            mission["planned_ok"] = e.get("needs_ok") or []
            for t in e["tasks"]:
                tasks[t["id"]] = dict(t, status="waiting", activity="queued", tools=[], chars=0, attempts=0,
                                      delegated_by="lead", started=None, finished=None, seconds=None,
                                      needs_ok=False, ok=None)
                node(t["role"])["tasks"].append(t["id"])
        elif k == "delegate":
            if e.get("task") in tasks and e.get("from") == "critic":
                tasks[e["task"]]["delegated_by"] = "critic"
                tasks[e["task"]]["redo"] = e.get("text", "")
        elif k == "agent":
            t = tasks.get(e.get("task"))
            if t is not None:
                if e["status"] in ("analyzing", "working") and t["status"] not in ("analyzing", "working"):
                    t["attempts"] += 1
                    t["started"] = t["started"] or e["t"]
                t.update(status=e["status"], activity=e.get("activity", ""))
            else:
                node(e["node"]).update(status=e["status"], activity=e.get("activity", ""))
        elif k == "tool":
            t = tasks.get(e.get("task"))
            if t is not None:
                t["tools"].append({"tool": e.get("tool"), "detail": e.get("detail", ""), "t": e["t"]})
            node(e["node"])["tools"] += 1
        elif k == "blocked":
            t = tasks.get(e.get("task"))
            if t is not None:
                for x in reversed(t["tools"]):
                    if x["tool"] == e.get("tool") and not x.get("blocked"):
                        x.update(blocked=True, reason=e.get("reason", ""))
                        break
        elif k == "writing":
            t = tasks.get(e.get("task"))
            if t is not None:
                t["chars"] = e.get("chars", 0)
        elif k == "finished":
            t = tasks.get(e.get("task"))
            if t is not None:
                t.update(finished=e["t"], seconds=e.get("seconds"), chars=e.get("chars", t["chars"]))
            node(e["node"])["chars"] += e.get("chars", 0)
        elif k == "result":
            t = tasks.get(e.get("task"))
            if t is not None:
                t.update(ok=e.get("ok"), needs_ok=e.get("needs_ok", False))
        elif k == "review":
            mission.update(score=e.get("score"), verdict=e.get("verdict"), reviewed=True)
            for tid in e.get("redo") or []:
                if tid in tasks:
                    tasks[tid].update(status="waiting", activity="sent back by the critic")
        elif k == "approval":
            mission.update(approvals=e.get("items") or [], change=e.get("change") or "")
    for n in nodes.values():
        mine = [tasks[i] for i in n["tasks"] if i in tasks]
        if mine:
            best = max(mine, key=lambda t: RANK.get(t["status"], 0))
            n.update(status=best["status"], activity=best["activity"], task=best["id"])
    # Overall progress: planning, each task, the critic's review and the report.
    units = 3 + len(tasks)
    done = (1 if tasks else 0) + sum(1 for t in tasks.values() if t["status"] in ("completed", "approval")) \
        + (1 if mission.get("reviewed") else 0) + (1 if mission["phase"] == "done" else 0)
    mission["progress"] = {"done": done, "total": units}
    return {"mission": mission, "tasks": tasks, "nodes": nodes}


def live_state(since=0):
    """What the live network view needs: the events after `since`, and the picture they add up to."""
    with _live_lock:
        if LIVE["pid"] is None:
            _load_last_live()
        events = list(LIVE["events"])
        pid = LIVE["pid"]
    history = {}
    for p in _load()["projects"]:
        for t in p.get("tasks") or []:
            history.setdefault(t.get("role"), []).append(
                {"title": p.get("title"), "task": t.get("task"), "when": p.get("started"),
                 "status": p.get("status"), "ok": t.get("ok"), "pid": p["id"]})
    since = since if 0 <= since <= len(events) else 0
    return {"pid": pid, "seq": len(events), "since": since, "events": events[since:], "running": busy(),
            "snapshot": _reduce(events), "now": time.time(),
            "roster": [{"id": n, "about": a.split(":", 1)[-1].strip(),
                        "tools": "web" if t is WEB else "files" if t is BUILD else "StoreForge + web"
                        if t == "STORE" else "thinking"}
                       for n, (a, t) in _roles().items()],
            "history": {k: v[-8:] for k, v in history.items()}}
