"""Plug-in skills: everything Jarvis can do, as tools his brain can pick from.

Each file in this folder is a skill. Drop a new .py file in here and Jarvis picks it up the next
time he starts. A skill is just a function with the @tool decorator:

    from skills import tool

    @tool("Flip a coin and say heads or tails.")
    def flip_coin():
        import random
        return random.choice(["Heads.", "Tails."])

    @tool("Say hello to someone.", name="Who to greet")
    def greet(name: str, loud: bool = False):
        return f"Hello {name}!" + (" HELLO!" if loud else "")

The function name is the tool name. Arguments without a default are required. A type annotation
(str, int, float, bool) sets the type, and keyword arguments to @tool describe each argument.
Return a short sentence saying what happened (his brain turns it into his reply). Don't call
voice.say from a skill.

When Connor asks for something, his brain (Claude Code, through `claude -p`) sees these tools
over a tiny local MCP server (jarvis_mcp.py) and calls as many as it needs, in order. The calls
come back here and run inside Jarvis.

Add-ons: other people's free MCP servers can be added in addons.json (see addons.example.json),
and his brain can use their tools too."""
import importlib
import inspect
import json
import secrets
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOLS = {}            # name -> {"fn", "description", "schema", "owner_only", "skill"}
ERRORS = {}           # skill file -> why it didn't load
_loaded = False
_server = {}
TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def tool(description, owner_only=True, **arg_help):
    """Marks a function as a tool Jarvis's brain can use."""
    def wrap(fn):
        props, required = {}, []
        for name, p in inspect.signature(fn).parameters.items():
            kind = TYPES.get(p.annotation, "string")
            prop = {"type": kind}
            if kind == "array":
                prop["items"] = {"type": "string"}
            if name in arg_help:
                prop["description"] = arg_help[name]
            props[name] = prop
            if p.default is inspect.Parameter.empty:
                required.append(name)
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        TOOLS[fn.__name__] = {"fn": fn, "description": description, "schema": schema, "owner_only": owner_only,
                              "skill": fn.__module__.rsplit(".", 1)[-1]}
        return fn
    return wrap


def load():
    """Imports every skill file once. A broken skill is skipped (and noted), never fatal."""
    global _loaded
    if _loaded:
        return TOOLS
    _loaded = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for f in sorted(HERE.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"skills.{f.stem}")
        except Exception as e:
            ERRORS[f.name] = f"{type(e).__name__}: {e}"
            print(f"(skill {f.name} didn't load: {e})")
    print(f"(skills: {len(TOOLS)} tools from {len({t['skill'] for t in TOOLS.values()})} skill files)")
    return TOOLS


def listing():
    """The tools in MCP's format."""
    load()
    return [{"name": n, "description": t["description"], "inputSchema": t["schema"]} for n, t in sorted(TOOLS.items())]


def call(name, args):
    """Runs a tool. Always returns text."""
    load()
    t = TOOLS.get(name)
    if not t:
        return f"There's no tool called {name}."
    import voice
    voice.QUIET.add(threading.get_ident())       # a skill mustn't talk over his reply
    try:
        print(f"(tool: {name} {json.dumps(args)[:200]})")
        out = t["fn"](**{k: v for k, v in (args or {}).items() if k in t["schema"]["properties"]})
        out = "Done." if out is None or out is True else str(out)
        print(f"(tool result: {out[:200]})")
        return out
    except Exception as e:
        traceback.print_exc()
        return f"That didn't work: {type(e).__name__}: {e}"
    finally:
        voice.QUIET.discard(threading.get_ident())


class _Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("X-Jarvis-Token") != _server.get("token"):
            return self._reply(403, {})
        self._reply(200, {"tools": listing()})

    def do_POST(self):
        if self.headers.get("X-Jarvis-Token") != _server.get("token"):
            return self._reply(403, {})
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 100000)
            req = json.loads(self.rfile.read(n) or b"{}")
            self._reply(200, {"text": call(req.get("name"), req.get("arguments") or {})})
        except Exception as e:
            self._reply(200, {"text": f"That didn't work: {e}", "error": True})

    def log_message(self, *args):
        pass


def serve():
    """Starts the local tool server once (only this computer can reach it). Returns (port, token)."""
    if not _server:
        load()
        token = secrets.token_hex(16)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True, name="skills").start()
        _server.update(port=httpd.server_address[1], token=token, httpd=httpd)
    return _server["port"], _server["token"]


def addons():
    """Other people's MCP servers Connor added (addons.json): {name: {command, args, env}}."""
    path = ROOT / "addons.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"(addons.json isn't valid: {e})")
        return {}
    servers = data.get("mcpServers", data)
    return {n: s for n, s in servers.items() if isinstance(s, dict) and s.get("command") and not s.get("disabled")}


def mcp_args():
    """claude command-line options that give the brain his tools (and add-ons)."""
    port, token = serve()
    servers = {"jarvis": {"command": sys.executable, "args": [str(ROOT / "jarvis_mcp.py")],
                          "env": {"JARVIS_TOOLS_PORT": str(port), "JARVIS_TOOLS_TOKEN": token, "PYTHONUTF8": "1"}}}
    import os
    for name, s in addons().items():
        if name == "jarvis":
            continue
        entry = {k: v for k, v in s.items() if k in ("command", "args", "env", "type", "url")}
        entry["args"] = [os.path.expanduser(a) if isinstance(a, str) and a.startswith("~") else a
                         for a in entry.get("args", [])]
        if sys.platform == "win32" and entry.get("command") in ("npx", "npm"):     # Windows needs cmd for npx
            entry["args"] = ["/c", entry["command"]] + entry["args"]
            entry["command"] = "cmd"
        servers[name] = entry
    cfg = ROOT / ".jarvis-mcp.json"
    cfg.write_text(json.dumps({"mcpServers": servers}, indent=1))
    allowed = " ".join(f"mcp__{n}" for n in servers)
    return ["--mcp-config", str(cfg), "--strict-mcp-config"], allowed


def describe():
    load()
    files = sorted({t["skill"] for t in TOOLS.values()})
    extra = sorted(addons())
    said = f"I have {len(TOOLS)} abilities in {len(files)} skill files"
    said += (f", plus add-ons: {', '.join(extra)}." if extra else ", and no add-ons yet.")
    if ERRORS:
        said += f" {len(ERRORS)} skill file{'s' if len(ERRORS) > 1 else ''} didn't load: {', '.join(ERRORS)}."
    return said
