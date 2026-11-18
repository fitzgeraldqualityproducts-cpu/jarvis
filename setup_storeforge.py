"""Connect Jarvis's AI team to StoreForge (your Shopify store builder). Run: .venv/bin/python setup_storeforge.py

Asks for what StoreForge needs and saves it in StoreForge's own .env.local file (that file is
git-ignored and stays on this computer):
  (No API key: StoreForge's AI runs on your Claude plan through the Claude Code login.)
  - your store's myshopify.com address and its Admin API access token (shpat_...), so pushes can
    go to your store. Pushes still always wait for your yes."""
import getpass
import json
import os
import secrets
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"


def _env(path):
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _write_env(path, updates):
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _check_shopify(domain, token, version):
    req = urllib.request.Request(f"https://{domain}/admin/api/{version}/graphql.json",
                                 data=json.dumps({"query": "{ shop { name } }"}).encode(),
                                 headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["data"]["shop"]["name"]


def main():
    print("== Connect Jarvis's team to StoreForge ==\n")
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    default = cfg.get("storeforge_path", "~/storeforge")
    folder = Path(os.path.expanduser(default))
    if not (folder / "package.json").is_file():
        typed = input(f"Where is StoreForge? (folder path, e.g. ~/storeforge): ").strip().strip("'\"")
        folder = Path(os.path.expanduser(typed))
        if not (folder / "package.json").is_file():
            print("That doesn't look like StoreForge (no package.json there).")
            return
    print(f"StoreForge: {folder}")
    if not (folder / "node_modules").is_dir():
        print("Note: StoreForge's packages aren't installed. Open Terminal in that folder and run: npm install")
    envf = folder / ".env.local"
    env = _env(envf)
    updates = {}

    print("\nStoreForge's AI runs on your Claude plan through Claude Code (the same login as Jarvis's")
    print("brain), so there's no API key and nothing extra to pay.")

    print("\nShopify (optional, only needed to push to your store). In Shopify admin: Settings > Apps and")
    print("sales channels > Develop apps > your app > API credentials > Admin API access token (shpat_...).")
    domain = input(f"Store address (e.g. my-store.myshopify.com){' [' + env['SHOPIFY_STORE_DOMAIN'] + ']' if env.get('SHOPIFY_STORE_DOMAIN') else ''}: ").strip()
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    if domain:
        if not domain.endswith(".myshopify.com"):
            domain = domain.split(".")[0] + ".myshopify.com"
        token = getpass.getpass("Admin API access token (hidden): ").strip()
        if token:
            try:
                name = _check_shopify(domain, token, env.get("SHOPIFY_API_VERSION", "2026-10"))
                print(f"Connected to your store: {name}")
            except Exception as e:
                print(f"Couldn't check that token ({e}). Saving it anyway; double-check it in Shopify.")
            updates["SHOPIFY_STORE_DOMAIN"] = domain
            updates["SHOPIFY_ADMIN_ACCESS_TOKEN"] = token
    if not env.get("SESSION_SECRET"):
        updates["SESSION_SECRET"] = secrets.token_hex(24)
    if updates:
        _write_env(envf, updates)
        print(f"\nSaved to {envf} (only on this computer).")
    cfg["storeforge_path"] = str(folder)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    print("\nDone. If StoreForge is already running, stop it and Jarvis will start it again when the team needs it.")
    print('Then say: "Get the team on building a store for <product link>".')


if __name__ == "__main__":
    main()
