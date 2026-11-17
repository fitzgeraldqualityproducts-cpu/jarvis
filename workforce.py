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
}

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
they are. Never send, post, buy or delete anything: if something needs doing in the real world, write it up
under a heading "Needs {owner}'s OK"."""

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


def _run(prompt, tools, cwd, timeout, label):
    """One agent: a claude -p run. Returns its text ('' if it failed or was stopped)."""
    import brain
    import subprocess
    if _stop.is_set():
        return ""
    claude = brain.find_claude()
    if not claude:
        raise RuntimeError("I can't find Claude Code")
    start = time.time()
    try:
        out = brain.run_claude([claude, "-p", prompt] + list(tools), timeout=timeout, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        print(f"(team: {label} ran out of time)")
        return ""
    text = (out.stdout or "").strip()
    if not text:
        print(f"(team: {label} gave nothing: {(out.stderr or '')[-200:]})")
    print(f"(team: {label} done in {time.time() - start:.0f}s)")
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
    roster = "\n".join(f"- {name}: {about}" for name, (about, _) in ROLES.items())
    context = ""
    try:
        import memory
        fs = memory.facts()
        if fs:
            context = "\nWhat Jarvis knows about " + owner + ": " + "; ".join(fs[-12:]) + "\n"
    except Exception:
        pass
    plan = _json(_run(LEAD_PROMPT.format(owner=owner, goal=goal, roster=roster, context=context),
                      THINK, folder, 180, "team lead (plan)"))
    if not plan or not plan.get("tasks"):
        plan = {"title": goal[:40], "tasks": [{"id": "t1", "role": "researcher", "task": goal, "needs": []},
                                             {"id": "t2", "role": "planner", "task": f"Turn the research into a "
                                              f"practical plan for: {goal}", "needs": ["t1"]}]}
    tasks = []
    for i, t in enumerate(plan["tasks"][:6]):
        role = str(t.get("role", "")).lower()
        if role not in ROLES:
            role = "researcher" if re.search(r"research|find|search|look", str(t.get("task", "")), re.I) else "planner"
        tasks.append({"id": str(t.get("id") or f"t{i + 1}"), "role": role, "task": str(t.get("task") or goal),
                      "needs": [str(n) for n in (t.get("needs") or [])], "result": "", "status": "waiting"})
    ids = {t["id"] for t in tasks}
    for t in tasks:
        t["needs"] = [n for n in t["needs"] if n in ids and n != t["id"]]
    plan["tasks"] = tasks
    return plan


def _do_task(t, plan, goal, owner, folder, notes=""):
    about, tools = ROLES[t["role"]]
    inputs = ""
    for n in t["needs"]:
        dep = next((x for x in plan["tasks"] if x["id"] == n), None)
        if dep and dep["result"]:
            inputs += f"\nFrom the {dep['role']} ({dep['task'][:80]}):\n{dep['result'][:6000]}\n"
    if inputs:
        inputs = "\nWork your teammates have already done:" + inputs
    if notes:
        notes = f"\nThe critic reviewed your last attempt and wants this fixed: {notes}\nYour last attempt:\n" \
                f"{t['result'][:5000]}\n"
    if t["role"] in ("coder", "code reviewer"):
        notes += "\nWrite any code or files in the current folder (the project's own folder). Keep it working " \
                 "and explain how to run it.\n"
    prompt = WORKER_PROMPT.format(role=t["role"], owner=owner, about=about, goal=goal, task=t["task"],
                                  inputs=inputs, notes=notes, rules=_rules())
    t["status"] = "working"
    timeout = 900 if tools is BUILD else 600
    t["result"] = _run(prompt, tools, folder, timeout, t["role"]) or t["result"]
    t["status"] = "done" if t["result"] else "failed"


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
        plan = _plan(goal, owner, folder)
        _current["plan"] = plan
        _update(pid, title=plan.get("title") or goal[:40], status="working",
                tasks=[{"role": t["role"], "task": t["task"]} for t in plan["tasks"]])
        print(f"(team: {plan.get('title')}: " + ", ".join(f"{t['role']}" for t in plan["tasks"]) + ")")
        for wave in _waves(plan["tasks"]):
            if _stop.is_set():
                break
            _current["step"] = ", ".join(t["role"] for t in wave)
            _run_wave(wave, plan, goal, owner, folder)
        if _stop.is_set():
            _update(pid, status="cancelled")
            return
        _current["step"] = "critic"
        review = _json(_run(CRITIC_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan)), THINK, folder, 240,
                            "critic")) or {}
        score = review.get("score")
        redo = {str(r.get("id")): str(r.get("fix", "")) for r in (review.get("redo") or []) if r.get("id")}
        redo = {k: v for k, v in redo.items() if any(t["id"] == k for t in plan["tasks"])}
        if redo and isinstance(score, (int, float)) and score < 8 and not _stop.is_set():
            _current["step"] = "redoing " + ", ".join(t["role"] for t in plan["tasks"] if t["id"] in redo)
            print(f"(team: critic gave {score}/10, redoing {list(redo)})")
            _run_wave([t for t in plan["tasks"] if t["id"] in redo], plan, goal, owner, folder, redo)
            review2 = _json(_run(CRITIC_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan)), THINK, folder,
                                 240, "critic (second look)")) or {}
            if review2.get("score"):
                review, score = review2, review2.get("score")
        _current["step"] = "writing the report"
        report = _run(REPORT_PROMPT.format(owner=owner, goal=goal, work=_work_text(plan, 8000),
                                           verdict=review.get("verdict", "no verdict")),
                      THINK, folder, 300, "team lead (report)")
        spoken = ""
        m = re.search(r"\n\s*SPOKEN:\s*(.+)\s*$", report, re.S)
        if m:
            spoken, report = m.group(1).strip(), report[:m.start()].strip()
        if not report:
            report = _work_text(plan, 20000)
        if not spoken:
            spoken = "They've written it all up for you."
        needs_ok = [str(x) for x in (plan.get("needs_ok") or []) if str(x).strip()]
        full = (f"# {plan.get('title') or goal}\n\n_Asked: {goal}_  \n_Team: "
                + ", ".join(t["role"] for t in plan["tasks"])
                + (f"  \n_Critic's score: {score}/10_" if score else "") + "\n\n" + report + "\n\n---\n\n# The team's work\n\n"
                + "\n\n".join(f"## {t['role'].title()}: {t['task']}\n\n{t['result'] or '(nothing came back)'}"
                              for t in plan["tasks"]))
        (folder / "report.md").write_text(full, encoding="utf-8")
        _to_obsidian(plan.get("title") or goal, full)
        _update(pid, status="done", spoken=spoken, score=score, needs_ok=needs_ok,
                jarvis_change=str(plan.get("jarvis_change") or "").strip(), told=False,
                minutes=round((time.time() - started) / 60, 1), finished=time.time())
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
    return "Calling them off. They'll stop after the task they're on."


def roster():
    names = list(ROLES)
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

