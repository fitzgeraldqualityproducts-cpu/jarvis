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

Setup: StoreForge's AI runs on Connor's Claude plan through the signed-in Claude Code CLI (no API
key needed; an ANTHROPIC_API_KEY in its .env.local is used instead if he adds one). Pushing needs
the store's myshopify.com address and Admin API token: double-click "Connect StoreForge.command". Config: "storeforge_path"
(default ~/storeforge), "storeforge_url" (default http://localhost:3000)."""
import json
import os
import re
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


def _env():
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env["PATH"] = env.get("PATH", "") + os.pathsep + os.pathsep.join(
        ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.npm-global/bin")])
    try:                                   # StoreForge's AI uses the same claude as Jarvis's brain
        import brain
        cli = brain.find_claude()
        if cli:
            env["CLAUDE_CLI_PATH"] = cli
    except Exception:
        pass
    return env


def _dev_mode():
    """StoreForge runs as a finished build (next start) unless "storeforge_mode" is "dev". The dev
    server live-reloads the page, and on Connor's Mac it got stuck reloading over and over, so the
    page flickered and never loaded."""
    return _cfg().get("storeforge_mode") == "dev"


SOURCES = ("src", "prisma", "public", "package.json", "package-lock.json", "next.config.ts", "tsconfig.json")


def needs_build(f=None):
    """True if StoreForge has no finished build, or its code changed since the last one."""
    f = f or folder()
    if not f or _dev_mode():
        return False
    try:
        built = (Path(f) / ".next" / "BUILD_ID").stat().st_mtime
    except OSError:
        return True
    for name in SOURCES:
        p = Path(f) / name
        files = [p] if p.is_file() else (x for x in p.rglob("*") if x.is_file() and "generated" not in x.parts)
        for x in files:
            try:
                if x.stat().st_mtime > built:
                    return True
            except OSError:
                pass
    return False


def _build(f, env):
    print("(StoreForge: building it, about a minute)")
    with open(HERE / "storeforge.log", "a") as log:
        try:
            r = subprocess.run([NPM, "run", "build"], cwd=str(f), stdout=log, stderr=log,
                               stdin=subprocess.DEVNULL, env=env, timeout=900)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def _start(f):
    env = _env()
    script = "dev"
    if not _dev_mode():
        if needs_build(f) and not _build(f, env):
            print("(StoreForge: the build failed, so starting the dev server instead; see storeforge.log)")
        else:
            script = "start"
    log = open(HERE / "storeforge.log", "a")
    return subprocess.Popen([NPM, "run", script], cwd=str(f), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            env=env, start_new_session=sys.platform != "win32"), env


def _dev_pid(f):
    """The process id of a StoreForge dev server that's running now, if there is one."""
    try:
        pid = int(json.loads((Path(f) / ".next" / "dev" / "lock").read_text())["pid"])
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                             timeout=5).stdout
        return pid if "next" in cmd else None           # a leftover lock can name some other program
    except Exception:
        return None


def _stop_dev(f):
    """Stops the dev server so the finished build can take over. Jobs it was running pick up again
    when StoreForge starts."""
    pid = _dev_pid(f)
    if not pid or sys.platform == "win32":
        return False
    import signal
    print("(StoreForge: switching from the dev server to the finished build)")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        for _ in range(20):
            time.sleep(0.5)
            if not running():
                return True
    return not running()


def _sqlite_packages(f):
    """Every copy of better-sqlite3 in StoreForge (Prisma's adapter keeps its own, older one)."""
    nm = Path(f) / "node_modules"
    pats = ["better-sqlite3", "*/node_modules/better-sqlite3", "@*/*/node_modules/better-sqlite3"]
    return [d for p in pats for d in nm.glob(p) if (d / "package.json").is_file()]


def _rebuild_sqlite(f, env):
    """StoreForge's database driver (better-sqlite3) is a compiled part that has to match this
    computer's Node.js. New npm versions block packages' install scripts, so `npm rebuild` can say
    it worked while doing nothing. So: for each copy that has no compiled file, run its own
    installer directly (it downloads the ready-made file for this Mac), or compile it."""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    print("(StoreForge: rebuilding its database driver for this Node.js)")
    ok = True
    with open(HERE / "storeforge.log", "a") as log:
        subprocess.run([npm, "rebuild", "better-sqlite3"], cwd=str(f), stdout=log, stderr=log,
                       stdin=subprocess.DEVNULL, env=env, timeout=600)
        pb = Path(f) / "node_modules" / "prebuild-install" / "bin.js"
        node = "node.exe" if sys.platform == "win32" else "node"
        for d in _sqlite_packages(f):
            try:
                scripts = json.loads((d / "package.json").read_text()).get("scripts") or {}
            except Exception:
                continue
            if "install" not in scripts or list(d.glob("build/Release/*.node")):
                continue                      # ships its own prebuilt files, or already built
            log.write(f"\n(Jarvis: installing the database driver in {d})\n")
            log.flush()
            r = None
            if pb.is_file():
                r = subprocess.run([node, str(pb)], cwd=str(d), stdout=log, stderr=log,
                                   stdin=subprocess.DEVNULL, env=env, timeout=300)
            if r is None or r.returncode != 0:
                r = subprocess.run([npx, "--yes", "node-gyp", "rebuild", "--release"], cwd=str(d), stdout=log,
                                   stderr=log, stdin=subprocess.DEVNULL, env=env, timeout=900)
            ok = ok and r.returncode == 0
    return ok


def ensure_running(wait=90):
    """Starts StoreForge (its finished build, built first if needed) if it isn't running, and fixes the usual start-up problems
    (a one-off Next.js notice that stops the first start, a database driver built for another
    Node.js). Returns None, or what went wrong, in plain words."""
    f = folder()
    if running() and not (f and not _dev_mode() and _dev_pid(f)):
        return None
    if not f:
        return "StoreForge isn't on this computer (set storeforge_path in Jarvis's config)."
    with _start_lock:
        if running() and _dev_pid(f) and not _dev_mode():
            _stop_dev(f)
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
                            "this Mac's Node.js, and the rebuild didn't work. See storeforge.log in Jarvis's folder.")
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


# ---------- product photos (real ones from the web, or placeholders) ----------

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")


def _get(url, timeout=20, limit=15 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit + 1)[:limit + 1], r.headers.get("Content-Type", "")


def _is_image(b):
    return (b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")
            or b[:6] in (b"GIF87a", b"GIF89a"))


def _ext(b):
    return "jpg" if b[:3] == b"\xff\xd8\xff" else "png" if b[:4] == b"\x89PNG" else "webp" if b[:4] == b"RIFF" else "gif"


def images_on_page(page_url, limit=8):
    """Product photo URLs from a product page: Shopify's product JSON, then og:image, JSON-LD and
    big <img> tags."""
    import html as _html
    import re
    from urllib.parse import urljoin, urlsplit
    found = []

    def add(u):
        u = _html.unescape(u or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        u = urljoin(page_url, u)
        if u.startswith("http") and u not in found and not re.search(r"(logo|icon|sprite|badge|payment|flag)", u, re.I):
            found.append(u)
    parts = urlsplit(page_url)
    if "/products/" in parts.path:                       # Shopify stores publish the product as JSON
        try:
            raw, _ = _get(f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}.json", limit=3_000_000)
            for im in (json.loads(raw).get("product") or {}).get("images") or []:
                add(im.get("src"))
        except Exception:
            pass
    if len(found) < limit:
        try:
            raw, _ = _get(page_url, limit=4_000_000)
            page = raw.decode("utf-8", "replace")
            for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\'][^>]*>', page, re.I):
                c = re.search(r'content=["\']([^"\']+)', m.group(0))
                if c:
                    add(c.group(1))
            for m in re.finditer(r'"image"\s*:\s*(\[[^\]]*\]|"[^"]+")', page):
                try:
                    v = json.loads(m.group(1))
                except ValueError:
                    continue
                for u in (v if isinstance(v, list) else [v]):
                    if isinstance(u, str):
                        add(u)
            for m in re.finditer(r'<img[^>]+(?:data-src|src)=["\']([^"\']+\.(?:jpe?g|png|webp)[^"\']*)', page, re.I):
                add(m.group(1))
        except Exception:
            pass
    return found[:limit]


def upload_photos(pid, files):
    """Adds photos to a store project. files: [(name, bytes)]. Returns StoreForge's answer."""
    import uuid
    boundary = "----jarvis" + uuid.uuid4().hex
    body = bytearray()
    for name, data in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                 f"Content-Type: image/{'jpeg' if name.endswith('jpg') else name.rsplit('.', 1)[-1]}\r\n\r\n").encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{base_url()}/api/projects/{pid}/upload", data=bytes(body), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            raise RuntimeError(f"StoreForge said ({e.code}): {e.reason}")


def add_photos(pid, image_urls=(), page_url=None):
    """Downloads product photos (from image links, or found on a product page) and adds them to
    the store project. Returns {"added": n, "problems": [...]}."""
    urls = list(image_urls or [])
    if page_url:
        urls += images_on_page(page_url)
    files, problems = [], []
    for u in urls[:12]:
        try:
            b, _ = _get(u)
            if len(b) > 15 * 1024 * 1024:
                problems.append(f"{u}: too big")
            elif not _is_image(b):
                problems.append(f"{u}: not an image")
            elif len(b) < 8000:
                problems.append(f"{u}: too small to use")
            else:
                files.append((f"photo-{len(files) + 1}.{_ext(b)}", b))
        except Exception as e:
            problems.append(f"{u}: {e}"[:200])
        if len(files) >= 8:
            break
    if not urls:
        problems.append("no photo links found" + (" on that page" if page_url else ""))
    added = 0
    if files:
        r = upload_photos(pid, files)
        added = len(r.get("assets") or [])
        problems += [f"{x.get('file')}: {x.get('error')}" for x in r.get("errors") or []]
    return {"added": added, "problems": problems[:10]}


FONT = {   # 5x7 capitals and digits for placeholder labels
    "A": "01110100011000111111100011000110001", "B": "11110100011000111110100011000111110",
    "C": "01110100011000010000100001000101110", "D": "11110100011000110001100011000111110",
    "E": "11111100001000011110100001000011111", "F": "11111100001000011110100001000010000",
    "G": "01110100011000010111100011000101111", "H": "10001100011000111111100011000110001",
    "I": "01110001000010000100001000010001110", "J": "00111000100001000010000101001001100",
    "K": "10001100101010011000101001001010001", "L": "10000100001000010000100001000011111",
    "M": "10001110111010110101100011000110001", "N": "10001110011010110011100011000110001",
    "O": "01110100011000110001100011000101110", "P": "11110100011000111110100001000010000",
    "Q": "01110100011000110001101011001001101", "R": "11110100011000111110101001001010001",
    "S": "01111100001000001110000010000111110", "T": "11111001000010000100001000010000100",
    "U": "10001100011000110001100011000101110", "V": "10001100011000110001100010101000100",
    "W": "10001100011000110101101011010101010", "X": "10001100010101000100010101000110001",
    "Y": "10001100010101000100001000010000100", "Z": "11111000010001000100010001000011111",
    "0": "01110100011001110101110011000101110", "1": "00100011000010000100001000010001110",
    "2": "01110100010000100010001000100011111", "3": "11110000010000101110000010000111110",
    "4": "00010001100101010010111110001000010", "5": "11111100001111000001000011000101110",
    "6": "00110010001000011110100011000101110", "7": "11111000010001000100010000100001000",
    "8": "01110100011000101110100011000101110", "9": "01110100011000101111000010001001100",
    "&": "01100100101010001000101011001001101", "-": "00000000000000011111000000000000000",
    "'": "00100001000100000000000000000000000", ".": "00000000000000000000000000110001100",
    " ": "0" * 35,
}


def placeholder_png(title, hue=200, size=800, note="PRODUCT PHOTO COMING SOON"):
    """A plain, clearly-a-placeholder product image (PNG, no extra libraries)."""
    import colorsys
    import struct
    import zlib
    W = H = size

    def rgb(h, s, v):
        return bytes(int(c * 255) for c in colorsys.hsv_to_rgb(h / 360 % 1, s, v))
    rows = [bytearray(rgb(hue, 0.18 + 0.10 * y / H, 0.96 - 0.10 * y / H) * W) for y in range(H)]

    def rect(x, y, w, h, col):
        x, y = max(0, int(x)), max(0, int(y))
        w, h = min(W - x, int(w)), min(H - y, int(h))
        if w <= 0:
            return
        for yy in range(y, min(H, y + h)):
            rows[yy][x * 3:(x + w) * 3] = col * w
    # a simple product box in the middle
    box, lid = rgb(hue, 0.45, 0.82), rgb(hue, 0.55, 0.70)
    rect(W * .30, H * .26, W * .40, H * .36, box)
    rect(W * .27, H * .22, W * .46, H * .07, lid)
    rect(W * .47, H * .22, W * .06, H * .40, rgb(hue, 0.25, 0.95))

    def text(s, cy, scale, col):
        s = "".join(ch if ch in FONT else " " for ch in s.upper())
        while len(s) * 6 * scale > W * .9 and scale > 2:
            scale -= 1
        s = s[:int(W * .9 // (6 * scale))]
        x0 = (W - len(s) * 6 * scale) // 2
        for i, ch in enumerate(s):
            g = FONT[ch]
            for j in range(35):
                if g[j] == "1":
                    rect(x0 + i * 6 * scale + (j % 5) * scale, cy + (j // 5) * scale, scale, scale, col)
    dark = rgb(hue, 0.6, 0.30)
    text(title or "Your product", int(H * .70), 6, dark)
    text(note, int(H * .82), 3, rgb(hue, 0.35, 0.50))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def add_placeholders(pid, title, count=3):
    """Adds clearly-marked placeholder photos, for when no real product photos can be found."""
    hue = (sum(map(ord, title or "x")) * 37) % 360
    notes = ["PRODUCT PHOTO COMING SOON", "PLACEHOLDER - ADD REAL PHOTO", "PLACEHOLDER - ADD REAL PHOTO",
             "PLACEHOLDER - ADD REAL PHOTO"]
    files = [(f"placeholder-{i + 1}.png", placeholder_png(title, (hue + i * 28) % 360, note=notes[i % 4]))
             for i in range(max(1, min(4, count)))]
    r = upload_photos(pid, files)
    return {"added": len(r.get("assets") or []), "placeholders": True,
            "problems": [f"{x.get('file')}: {x.get('error')}" for x in r.get("errors") or []]}


def photo_count(pid):
    return sum(1 for a in (view(pid).get("assets") or []) if not a.get("discarded"))


def _job_done(pid, job_id, wait=1500):
    """Waits for a StoreForge job; returns (status, error)."""
    end = time.time() + wait
    while time.time() < end:
        time.sleep(8)
        try:
            j = next((x for x in view(pid).get("jobs") or [] if x.get("id") == job_id), None)
        except Exception:
            continue
        if j and j.get("status") not in ("queued", "running"):
            return j.get("status"), j.get("error") or ""
    return "timeout", ""


def prepare_and_generate(pid, name=""):
    """Adds placeholder photos if the project has none, then starts 'Generate everything'."""
    if photo_count(pid) == 0:
        add_placeholders(pid, name or (view(pid).get("project") or {}).get("name") or "Product")
    return generate(pid)


def build_everything(pid, name=""):
    """Every StoreForge step except exporting: makes sure there are photos (placeholders if
    needed), runs 'Generate everything', and if it stops on missing photos, adds placeholders and
    runs it again once. Runs in the background; returns at once."""
    def run():
        try:
            for attempt in range(2):
                job = prepare_and_generate(pid, name) if attempt == 0 else generate(pid)
                status, err = _job_done(pid, job.get("id"))
                print(f"(StoreForge: generate {status} {err[:120]})")
                if status != "failed" or attempt:
                    break
                if re.search(r"photo|image|product page", err, re.I):
                    add_placeholders(pid, name or "Product")
                else:
                    break
        except Exception as e:
            print(f"(StoreForge: building {pid} failed: {e})")
    threading.Thread(target=run, daemon=True, name="storeforge-build").start()


# ---------- showing Connor his stores ----------

def remember(pid, name="", mission=None):
    """Note a store project the team made, so Jarvis can open it for Connor."""
    with _lock:
        data = _load()
        data.setdefault("projects", [])
        data["projects"] = [p for p in data["projects"] if p.get("pid") != pid][-19:]
        data["projects"].append({"pid": pid, "name": name, "mission": mission, "t": time.time()})
        _save(data)


def projects_for(mission):
    return [p for p in _load().get("projects", []) if p.get("mission") == str(mission)]


def last_project():
    ps = _load().get("projects") or []
    return ps[-1] if ps else None


def _open_url(url):
    if sys.platform == "darwin":
        subprocess.run(["open", url])
    else:
        import webbrowser
        webbrowser.open(url)


def open_app(pid=None):
    """Opens StoreForge in the browser, on a store project (the latest one the team made, unless
    told which). Starts StoreForge first if it isn't running. Returns what to say."""
    problem = ensure_running()
    if problem:
        return problem
    p = None
    if pid is None:
        p = last_project()
        if not p:
            try:
                ps = list_projects()
                if ps:
                    p = {"pid": ps[0].get("id"), "name": ps[0].get("name")}
            except Exception:
                pass
        pid = p and p.get("pid")
    _open_url(f"{base_url()}/projects/{pid}" if pid else base_url())
    if pid:
        return f"Opening {(p or {}).get('name') or 'your store'} in StoreForge."
    return "Opening StoreForge."


# ---------- things only Connor can OK ----------

KINDS = {
    "confirm_rights": "confirm you have the right to use the photos and product text for {name}",
    "push_to_shopify": "push {name} to your Shopify store as a draft",
}


def _load():
    try:
        data = json.loads(ACTIONS.read_text())
        data.setdefault("actions", [])
        return data
    except Exception:
        return {"actions": []}


def _save(data):
    data["actions"] = data.get("actions", [])[-50:]
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
            build_everything(a["pid"], a.get("name", ""))            # every step except exporting
            try:
                _open_url(f"{base_url()}/projects/{a['pid']}")      # so he can watch it being built
            except Exception:
                pass
            return f"Done. I've confirmed it and StoreForge is generating {a['name']} now. I've opened it so you can watch."
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
