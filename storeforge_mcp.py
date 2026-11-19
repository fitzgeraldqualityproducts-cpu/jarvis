"""A tiny MCP server (stdio) that lets the team's "store builder" agent use StoreForge.

workforce.py starts it for that agent only (claude --mcp-config). Every tool here either reads
from StoreForge or works inside it on this computer. The two things that need Connor (vouching
for the rights to a product's photos and text, and pushing to his real Shopify store) are only
*requests*: they're queued in storeforge_actions.json and Jarvis asks him."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storeforge  # noqa: E402

MISSION = os.environ.get("JARVIS_TEAM_PID")
SECTIONS = ["analysis", "risk", "brand_names", "brand_details", "listing", "content", "seo", "alt_text"]

TOOLS = [
    {"name": "storeforge_status",
     "description": "Check StoreForge is running (starts it if needed) and what it can do: whether its AI is "
                    "configured and whether a Shopify store is connected.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_stores", "description": "List the store projects in StoreForge.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "create_store",
     "description": "Start a new store project from up to 3 product page URLs (supplier or competitor) plus "
                    "context. Doesn't generate anything yet.",
     "inputSchema": {"type": "object", "required": ["name", "product_urls"], "properties": {
         "name": {"type": "string"}, "product_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
         "niche": {"type": "string"}, "target_customer": {"type": "string"},
         "tone": {"type": "string", "description": "e.g. Minimal, Playful, Premium, Bold"},
         "brand_name": {"type": "string"}, "target_price": {"type": "number"}, "unit_cost": {"type": "number"},
         "target_margin_pct": {"type": "number"}, "notes": {"type": "string"}}}},
    {"name": "product_facts",
     "description": "What a product page really says about the product: specs like size, material, "
                    "capacity and weight from the page's product data and spec lines. Use it before "
                    "create_store, and put the confirmed facts in create_store's notes, so the listing only "
                    "states real facts. Anything not listed is unconfirmed.",
     "inputSchema": {"type": "object", "required": ["page_url"], "properties": {"page_url": {"type": "string"}}}},
    {"name": "store_details",
     "description": "Everything about a store project: steps, risk flags, brand, listing, preflight checklist, "
                    "running jobs and exports. wait_seconds (up to 150) waits for running jobs to move on first.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {
         "project_id": {"type": "string"}, "wait_seconds": {"type": "number"}}}},
    {"name": "add_product_photos",
     "description": "Add real product photos to a store project. Give the product page link (page_url: it finds the "
                    "photos on the page itself, best for Shopify stores) and/or direct image links (image_urls). "
                    "Says how many were added. Do this right after create_store.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {
         "project_id": {"type": "string"}, "page_url": {"type": "string"},
         "image_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 12}}}},
    {"name": "add_placeholder_photos",
     "description": "Only if no real product photos could be added: adds clearly-marked placeholder photos "
                    "(the product name on a plain box picture) so StoreForge can still build the whole store. "
                    "The owner swaps in real photos later.",
     "inputSchema": {"type": "object", "required": ["project_id", "product_name"], "properties": {
         "project_id": {"type": "string"}, "product_name": {"type": "string"},
         "count": {"type": "integer", "minimum": 1, "maximum": 4}}}},
    {"name": "request_rights_confirmation",
     "description": "StoreForge needs the owner to confirm he has the right to use the product's photos and "
                    "text before it can generate. You can't confirm it yourself: this asks him, and StoreForge "
                    "starts generating once he says yes.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {
         "project_id": {"type": "string"}, "why": {"type": "string"}}}},
    {"name": "generate_store",
     "description": "Run every StoreForge step except exporting (analysis, brand kit, listing, images, store "
                    "content), adding placeholder photos first if the project has none. Needs the rights "
                    "confirmed first (once the owner says yes, Jarvis runs this for you). Runs in the "
                    "background: follow it with store_details and wait_seconds.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}}},
    {"name": "regenerate_section",
     "description": "Redo one part of a store with an instruction, e.g. section=listing, instruction='shorter, "
                    "more premium title'. Sections: " + ", ".join(SECTIONS),
     "inputSchema": {"type": "object", "required": ["project_id", "section"], "properties": {
         "project_id": {"type": "string"}, "section": {"type": "string", "enum": SECTIONS},
         "instruction": {"type": "string"}}}},
    {"name": "build_export_zip",
     "description": "Build the ZIP bundle for Shopify's importers (CSV, images, pages, theme, brand files). "
                    "Stays on this computer.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}}},
    {"name": "dry_run_shopify_push",
     "description": "Check the Shopify connection and list every operation a push would do, without changing "
                    "the store.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}}},
    {"name": "request_shopify_push",
     "description": "Ask the owner to approve pushing the store to his Shopify (as a draft product and "
                    "unpublished theme). You can't push yourself; this queues the request and he decides.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {
         "project_id": {"type": "string"}, "why": {"type": "string"}}}},
]


def _wait(pid, secs):
    end = time.time() + min(float(secs or 0), 150)
    while time.time() < end:
        s = storeforge.summary(pid)
        if not any(j.get("status") in ("queued", "running") for j in s["jobs"]):
            return s
        time.sleep(5)
    return storeforge.summary(pid)


CHANGES = ("add_product_photos", "add_placeholder_photos", "generate_store", "regenerate_section")


def _fill_project(name, a):
    """Agents sometimes leave out project_id. Use this mission's latest store, or say what's missing."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    need = (tool or {}).get("inputSchema", {}).get("required", [])
    if "project_id" in need and not a.get("project_id"):
        mine = storeforge.projects_for(MISSION) if MISSION else []
        if mine:
            a["project_id"] = mine[-1]["pid"]
    missing = [k for k in need if a.get(k) in (None, "")]
    if missing:
        props = (tool or {}).get("inputSchema", {}).get("properties", {})
        return {"error": f"{name} needs {', '.join(missing)}. Its inputs are: {', '.join(props)}. "
                         "create_store returns the project_id; list_stores shows existing ones."}
    return None


def call(name, a):
    problem = storeforge.ensure_running()
    if problem:
        return {"error": problem}
    a = dict(a)
    problem = _fill_project(name, a)
    if problem:
        return problem
    if name == "storeforge_status":
        return storeforge.capabilities()
    if name == "list_stores":
        return storeforge.list_projects()
    if name == "create_store":
        ctx = {k: v for k, v in {
            "niche": a.get("niche"), "targetCustomer": a.get("target_customer"), "tone": a.get("tone"),
            "brandName": a.get("brand_name"), "targetPrice": a.get("target_price"), "unitCost": a.get("unit_cost"),
            "targetMarginPct": a.get("target_margin_pct"), "notes": a.get("notes")}.items() if v not in (None, "")}
        pid = storeforge.create_project(a["name"], a.get("product_urls") or [], ctx)
        storeforge.remember(pid, a["name"], MISSION)
        return {"project_id": pid, "next": "Ask for the rights confirmation (request_rights_confirmation); "
                                          "StoreForge can't generate until the owner confirms."}
    if name == "product_facts":
        return storeforge.product_facts(a["page_url"])
    if name in CHANGES:
        storeforge.team_changed(a["project_id"])
    if name == "store_details":
        return _wait(a["project_id"], a.get("wait_seconds"))
    if name == "add_product_photos":
        r = storeforge.add_photos(a["project_id"], a.get("image_urls") or [], a.get("page_url"))
        r["photos_in_project"] = storeforge.photo_count(a["project_id"])
        if not r["photos_in_project"]:
            r["next"] = "No photos yet. Try another product page, or use add_placeholder_photos."
        return r
    if name == "add_placeholder_photos":
        r = storeforge.add_placeholders(a["project_id"], a["product_name"], int(a.get("count") or 3))
        r["photos_in_project"] = storeforge.photo_count(a["project_id"])
        return r
    if name == "request_rights_confirmation":
        act = storeforge.request("confirm_rights", a["project_id"], a.get("why", ""), MISSION)
        return {"queued_for_owner": act["text"], "note": "Jarvis will ask him. Nothing happens until he says yes."}
    if name == "generate_store":
        s = storeforge.summary(a["project_id"])
        if not s["rights_confirmed"]:
            return {"error": "The owner hasn't confirmed the rights yet. Use request_rights_confirmation; once he "
                             "says yes, Jarvis starts generation for you."}
        job = storeforge.prepare_and_generate(a["project_id"], s.get("name") or "")
        return {"job": job, "note": "Generating every step (not exporting). Follow it with store_details and "
                                    "wait_seconds. If it fails on photos, add photos or placeholders and run it again."}
    if name == "regenerate_section":
        return storeforge.regenerate(a["project_id"], a["section"], a.get("instruction", ""))
    if name == "build_export_zip":
        return {"job": storeforge.export_zip(a["project_id"])}
    if name == "dry_run_shopify_push":
        return storeforge.dry_run(a["project_id"])
    if name == "request_shopify_push":
        act = storeforge.request("push_to_shopify", a["project_id"], a.get("why", ""), MISSION)
        return {"queued_for_owner": act["text"], "note": "Jarvis will ask him. Nothing is pushed until he says yes."}
    return {"error": f"unknown tool {name}"}


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:                       # a notification
            continue
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "storeforge", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                out = call(p.get("name"), p.get("arguments") or {})
                err = isinstance(out, dict) and "error" in out and len(out) == 1
            except Exception as e:
                out, err = {"error": str(e)}, True
            result = {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)[:20000]}],
                      "isError": bool(err)}
        elif method == "ping":
            result = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                         "error": {"code": -32601, "message": "method not found"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
