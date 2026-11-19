"""A tiny MCP server (stdio) that hands Jarvis's skills to his brain.

brain.py starts Claude Code with --mcp-config pointing here. This process doesn't run the
skills itself: it passes each call to Jarvis (skills/__init__.py's local server, on 127.0.0.1
with a secret token), so they run inside Jarvis where Spotify, timers and the display live."""
import json
import os
import sys
import urllib.request

PORT = os.environ.get("JARVIS_TOOLS_PORT", "")
TOKEN = os.environ.get("JARVIS_TOOLS_TOKEN", "")


def _ask(method="GET", body=None, timeout=120):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Jarvis-Token": TOKEN, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # never through a proxy
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    tools = None
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "jarvis", "version": "1.0"}}
            elif method == "tools/list":
                if tools is None:
                    tools = _ask().get("tools", [])
                result = {"tools": tools}
            elif method == "tools/call":
                p = msg.get("params") or {}
                out = _ask("POST", {"name": p.get("name"), "arguments": p.get("arguments") or {}})
                result = {"content": [{"type": "text", "text": str(out.get("text", ""))[:20000]}],
                          "isError": bool(out.get("error"))}
            elif method == "ping":
                result = {}
            else:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})
                continue
        except Exception as e:
            if method == "tools/call":
                result = {"content": [{"type": "text", "text": f"Jarvis didn't answer: {e}"}], "isError": True}
            else:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})
                continue
        _send({"jsonrpc": "2.0", "id": mid, "result": result})


if __name__ == "__main__":
    main()
