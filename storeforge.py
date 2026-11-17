"""StoreForge: Connor's own Shopify store builder (a Next.js app on this Mac, ~/storeforge).

Jarvis's AI team gets a "store builder" agent that drives StoreForge through its local API at
http://localhost:3000 (via storeforge_mcp.py). It can look things up and do anything that stays
inside StoreForge on this computer: start a store project from product links, run "generate
everything", follow progress, read the listing and preflight checklist, build the export ZIP and
dry-run a Shopify push (which only checks the connection and lists what would happen).

Anything that changes your real Shopify store, or that you have to vouch for, is never done by an
agent. The agent files a request (storeforge_actions.json) and Jarvis asks you:
  - confirming you have the right to use a product's photos and text (StoreForge requires it)
  - pushing a store to Shopify (it goes in as a draft product and an unpublished theme)
Say yes and Jarvis does it; say no and nothing happens.

Setup: double-click "Connect StoreForge.command" (StoreForge's AI needs an Anthropic API key, and
pushing needs your store's myshopify.com address and Admin API token). Config: "storeforge_path"
(default ~/storeforge), "storeforge_url" (default http://localhost:3000)."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
ACTIONS = HERE / "storeforge_actions.json"
_lock = threading.RLock()


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def folder():
    p = Path(os.path.expanduser(_cfg().get("storeforge_path", "~/storeforge")))
    return p if (p / "package.json").is_file() else None


def base_url():
    return _cfg().get("storeforge_url", "http://localhost:3000").rstrip("/")


def available():
    """True if StoreForge is on this computer (or a URL was set for it)."""
    return bool(folder() or _cfg().get("storeforge_url"))


# ---------- talking to its API ----------

def _call(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error") or e.reason
        except Exception:
            msg = e.reason
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg)
        raise RuntimeError(f"StoreForge said ({e.code}): {msg}")


def running():
    try:
        _call("GET", "/api/capabilities", timeout=4)
        return True
    except Exception:
        return False


_start_lock = threading.Lock()


def _log_problem(since):
    """The real error from storeforge.log, written after `since` (a byte offset)."""
    try:
        with open(HERE / "storeforge.log", "rb") as f:
            f.seek(since)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for pat in ("Could not locate the bindings file", "was compiled against a different Node.js version",
                "EADDRINUSE", "Cannot find module", "command not found", "Error:"):
        k = text.find(pat)
        if k >= 0:
            return text[k:k + 200].splitlines()[0].strip()
    return ""


def _start(f):
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + os.pathsep + os.pathsep.join(
        ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.npm-global/bin")])
    log = open(HERE / "storeforge.log", "a")
    return subprocess.Popen([npm, "run", "dev"], cwd=str(f), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            env=env, start_new_session=sys.platform != "win32"), env


def _rebuild_sqlite(f, env):
    """StoreForge's database driver (better-sqlite3) is a compiled part that has to match this
    computer's Node.js. If it's missing, rebuild it in StoreForge's own folder (npm rebuild)."""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print("(StoreForge: rebuilding its database driver for this Node.js)")
    with open(HERE / "storeforge.log", "a") as log:
        r = subprocess.run([npm, "rebuild", "better-sqlite3"], cwd=str(f), stdout=log, stderr=log,
                           stdin=subprocess.DEVNULL, env=env, timeout=600)
    return r.returncode == 0


def ensure_running(wait=90):
    """Starts StoreForge (npm run dev) if it isn't running, and fixes the usual start-up problems
    (a one-off Next.js notice that stops the first start, a database driver built for another
    Node.js). Returns None, or what went wrong, in plain words."""
    if running():
        return None
    f = folder()
    if not f:
        return "StoreForge isn't on this computer (set storeforge_path in Jarvis's config)."
    with _start_lock:
        if running():
            return None
        rebuilt, problem = False, ""
        for attempt in range(3):
            try:
                since = (HERE / "storeforge.log").stat().st_size
            except OSError:
                since = 0
            try:
                proc, env = _start(f)
            except OSError as e:
                return f"Couldn't start StoreForge: {e}"
            print(f"(started StoreForge, try {attempt + 1})")
            end = time.time() + wait
            while time.time() < end:
                time.sleep(3)
                if running():
                    return None
                if proc.poll() is not None:          # it stopped by itself
                    break
            problem = _log_problem(since)
            if proc.poll() is None:
                proc.terminate()
            if "bindings file" in problem or "different Node.js version" in problem:
                if rebuilt or not _rebuild_sqlite(f, env):
                    return ("StoreForge won't start: its database driver (better-sqlite3) needs rebuilding for "
                            "this Mac's Node.js, and the rebuild didn't work. In Terminal: cd ~/storeforge && "
                            "npm rebuild better-sqlite3")
                rebuilt = True
        return "StoreForge didn't start" + (f": {problem}" if problem else ". Check storeforge.log in Jarvis's folder.")


def capabilities():
    return _call("GET", "/api/capabilities")


def list_projects():
    return _call("GET", "/api/projects").get("projects", [])


def create_project(name, urls=(), context=None):
    p = _call("POST", "/api/projects", {"name": name[:120]})["project"]
    body = {"inputs": {"urls": list(urls)[:3], "context": context or {}}}
    _call("PATCH", f"/api/projects/{p['id']}", body)
    return p["id"]


def view(pid):
    return _call("GET", f"/api/projects/{pid}")


def summary(pid):
    """A compact, readable picture of a project for an agent."""
    v = view(pid)
    p = v.get("project") or {}
    listing = p.get("listing") or {}
    brand = p.get("brandKit") or {}
    jobs = [{k: j.get(k) for k in ("type", "status", "step", "percent", "message", "error")}
            for j in (v.get("jobs") or [])[:6]]
    return {
        "id": p.get("id"), "name": p.get("name"), "status": p.get("status"),
        "rights_confirmed": (p.get("rightsAck") or {}).get("accepted", False),
        "inputs": p.get("inputs"),
        "steps": {k: s.get("status") for k, s in (p.get("steps") or {}).items()},
        "risk_flags": [f.get("label") or f.get("title") or f for f in ((p.get("analysis") or {}).get("riskFlags") or [])][:8],
        "brand_name": brand.get("selectedName"),
        "listing": {k: listing.get(k) for k in ("title", "price", "compareAtPrice", "productType", "tags",
                                                  "seoTitle", "seoDescription") if k in listing},
        "preflight": v.get("preflight"),
        "jobs": jobs,
        "exports": [{k: e.get(k) for k in ("id", "type", "createdAt", "artifactPath")} for e in (v.get("exports") or [])[:5]],
        "app_link": f"{base_url()}/projects/{p.get('id')}",
    }


def generate(pid):
    return _call("POST", f"/api/projects/{pid}/pipeline", {"forceScrape": False})["job"]


def export_zip(pid):
    return _call("POST", f"/api/projects/{pid}/export/bundle", {})["job"]


def dry_run(pid):
    return _call("POST", f"/api/projects/{pid}/export/shopify", {"dryRun": True})


def regenerate(pid, section, instruction=""):
    body = {"section": section}
    if instruction:
        body["instruction"] = instruction[:1000]
    return _call("POST", f"/api/projects/{pid}/regenerate", body)


# ---------- things only Connor can OK ----------

KINDS = {
    "confirm_rights": "confirm you have the right to use the photos and product text for {name}",
    "push_to_shopify": "push {name} to your Shopify store as a draft",
}


def _load():
    try:
        return json.loads(ACTIONS.read_text())
    except Exception:
        return {"actions": []}


def _save(data):
    data["actions"] = data["actions"][-50:]
    tmp = ACTIONS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(ACTIONS)


def request(kind, pid, why="", mission=None):
    """An agent asks for something only Connor can approve. Nothing happens until he says yes."""
    if kind not in KINDS:
        raise ValueError(f"unknown action {kind}")
    try:
        name = (view(pid).get("project") or {}).get("name") or pid
    except Exception:
        name = pid
    with _lock:
        data = _load()
        for a in data["actions"]:
            if a["kind"] == kind and a["pid"] == pid and a["status"] == "waiting":
                return a
        a = {"id": f"{kind}-{pid}-{int(time.time())}", "kind": kind, "pid": pid, "name": name,
             "why": why[:300], "mission": mission, "status": "waiting", "t": time.time(),
             "text": KINDS[kind].format(name=name)}
        data["actions"].append(a)
        _save(data)
    return a


def waiting(mission=None):
    acts = [a for a in _load()["actions"] if a["status"] == "waiting"]
    return [a for a in acts if mission is None or a.get("mission") == mission]


def answer(action_id, yes):
    """Connor's answer. On yes, does it. Returns what to say."""
    with _lock:
        data = _load()
        a = next((x for x in data["actions"] if x["id"] == action_id), None)
        if not a or a["status"] != "waiting":
            return "That request isn't waiting any more."
        a["status"] = "approved" if yes else "declined"
        a["answered"] = time.time()
        _save(data)
    if not yes:
        return "Okay, I won't."
    problem = ensure_running()
    if problem:
        return problem
    try:
        if a["kind"] == "confirm_rights":
            _call("PATCH", f"/api/projects/{a['pid']}", {"rightsAccepted": True})
            generate(a["pid"])
            return f"Done. I've confirmed it and StoreForge is generating {a['name']} now."
        if a["kind"] == "push_to_shopify":
            r = _call("POST", f"/api/projects/{a['pid']}/export/shopify", {"dryRun": False, "confirm": True})
            return (f"Pushing {a['name']} to {r.get('storeDomain', 'your store')} now. It goes in as a draft, so "
                    "nothing is live until you publish it in Shopify.")
    except Exception as e:
        _mark(action_id, "failed", str(e))
        return f"That didn't work: {e}"
    return "Done."


def _mark(action_id, status, note=""):
    with _lock:
        data = _load()
        for a in data["actions"]:
            if a["id"] == action_id:
                a.update(status=status, note=note[:300])
        _save(data)
