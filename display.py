"""Display: a live sci-fi HUD in your browser (holographic orb, time, weather, Spotify,
reminders, timers and the conversation).

Jarvis serves it at http://localhost:8765 and opens it when he starts. It's a real 3D scene
(three.js, in vendor/) that you can turn with the mouse, and it falls back to a flat 2D view on
computers where 3D runs slowly.
Other modules call display.update(...) to change what it shows. While the page is open,
this module also keeps the weather and the "now playing" panel fresh by itself."""
import json
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
HTML = Path(__file__).with_name("display.html")
TEAM_HTML = HTML.with_name("team.html")    # the live view of Jarvis's AI team at /team
CLIPPER_HTML = HTML.with_name("clipper.html")   # the Shorts clipper dashboard at /clipper
VENDOR = HTML.with_name("vendor")       # the 3D engine (three.js), served to the page
WINDOWS = sys.platform == "win32"

_lock = threading.Lock()
STATE = {
    "status": "sleeping",   # sleeping | listening | thinking | speaking | paused
    "level": 0.0,           # mic level 0..1 while listening
    "you": "",
    "jarvis": "",
    "weather": None,
    "apps": [],
    "log": [],
    "today": [],
    "timers": [],
    "music": None,          # {title, artist, album, art, state, pos, dur, at}
    "sky": None,            # {code, is_day, sunrise, sunset} for the live weather effects
    "boot": time.time(),    # changes when Jarvis restarts, so the page replays its boot-up
    "updated": time.time(),
}
TYPED = queue.Queue()      # messages typed into the display's "Type to Jarvis" box
TOKEN = secrets.token_hex(16)   # only the display page knows it, so other websites can't send him commands
_timer_starts = {}          # remembers when each timer began, for its progress ring
_last_view = [0.0]          # last time the page asked for /state


def update(**changes):
    with _lock:
        if "timers" in changes:
            now = time.time()
            timers = []
            for t in changes["timers"] or []:
                key = f'{t.get("label")}|{t.get("end")}'
                start = _timer_starts.setdefault(key, t.get("start") or now)
                timers.append(dict(t, start=start))
            live = {f'{t.get("label")}|{t.get("end")}' for t in timers}
            for k in list(_timer_starts):
                if k not in live:
                    del _timer_starts[k]
            changes["timers"] = timers
        STATE.update(changes)
        STATE["updated"] = time.time()


def log(who, text):
    with _lock:
        STATE["log"] = (STATE["log"] + [{"who": who, "text": text, "t": time.time()}])[-12:]
        STATE[who] = text
        STATE["updated"] = time.time()


# ---------------- Now playing (Spotify) ----------------

_MAC_SCRIPT = '''
if application "Spotify" is running then
  tell application "Spotify"
    if player state is stopped then return ""
    set t to current track
    set d to "|~|"
    return (name of t) & d & (artist of t) & d & (album of t) & d & (artwork url of t) & d & (duration of t) & d & (player position) & d & (player state as string)
  end tell
end if
return ""
'''
_art_cache = {}


def _itunes_art(title, artist):
    """Album art for a song from Apple's free search (no key). Used when Spotify doesn't say."""
    key = (title, artist)
    if key in _art_cache:
        return _art_cache[key]
    art = ""
    try:
        q = urllib.parse.quote(f"{artist} {title}")
        req = urllib.request.Request(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1",
                                     headers={"User-Agent": "jarvis-assistant"})
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.load(r).get("results") or []
        if res:
            art = res[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
    except Exception:
        pass
    _art_cache[key] = art
    return art


def _num(s, default=0.0):
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return default


def _mac_music():
    out = subprocess.run(["osascript", "-e", _MAC_SCRIPT], capture_output=True, text=True, timeout=5).stdout.strip()
    if not out:
        return None
    p = out.split("|~|")
    if len(p) < 7 or not p[0]:
        return None
    m = {"title": p[0], "artist": p[1], "album": p[2], "art": p[3],
         "dur": _num(p[4]) / 1000, "pos": _num(p[5]), "state": p[6]}
    if not m["art"].startswith("http"):
        m["art"] = _itunes_art(m["title"], m["artist"])
    return m


_win_api = {"sp": None, "checked": 0.0}
_last_win = {}


def _win_music():
    import spotify_control as sc
    if not sc.is_running():
        _last_win.clear()
        return None
    # With "Connect Spotify" done, the Web API gives album art and the song position.
    if time.time() - _win_api["checked"] > 300:
        _win_api["sp"] = sc._api()
        _win_api["checked"] = time.time()
    sp = _win_api["sp"]
    if sp:
        try:
            cur = sp.current_playback()
            item = (cur or {}).get("item")
            if item:
                imgs = (item.get("album") or {}).get("images") or [{}]
                return {"title": item.get("name", ""), "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                        "album": (item.get("album") or {}).get("name", ""), "art": imgs[0].get("url", ""),
                        "dur": item.get("duration_ms", 0) / 1000, "pos": (cur.get("progress_ms") or 0) / 1000,
                        "state": "playing" if cur.get("is_playing") else "paused"}
        except Exception:
            pass
    # Otherwise read Spotify's window title ("Artist - Song"); it changes to "Spotify ..." when paused.
    title = sc._win_title()
    if title and " - " in title:
        artist, name = title.split(" - ", 1)
        if _last_win.get("title") != name:
            _last_win.update(title=name, artist=artist, album="", art=_itunes_art(name, artist), dur=0, pos=0)
        return dict(_last_win, state="playing")
    if _last_win:
        return dict(_last_win, state="paused")
    return None


def _music_loop():
    while True:
        if time.time() - _last_view[0] < 15:      # only while the display is open
            try:
                m = _win_music() if WINDOWS else _mac_music()
                if m:
                    m["at"] = time.time()
                update(music=m)
            except Exception:
                pass
        time.sleep(2.5)


def _sky_info():
    """Weather code and today's sunrise/sunset, for the rain/snow effects and day/night colors."""
    import weather
    lat, lon, _ = weather.locate()
    d = weather._get("https://api.open-meteo.com/v1/forecast"
                     f"?latitude={lat}&longitude={lon}&current=weather_code,is_day"
                     "&daily=sunrise,sunset&timezone=auto&timeformat=unixtime&forecast_days=1")
    return {"code": d["current"]["weather_code"], "is_day": d["current"]["is_day"],
            "sunrise": d["daily"]["sunrise"][0], "sunset": d["daily"]["sunset"][0]}


def _weather_loop():
    time.sleep(3)
    while True:
        try:
            update(sky=_sky_info())
        except Exception:
            pass
        time.sleep(15)                            # Jarvis's greeting fetches the weather first
        try:
            import weather
            w = weather.current()
            if w:
                update(weather=w)
        except Exception:
            pass
        time.sleep(885)


# ---------------- Web server ----------------

def _page_version():
    """Changes when display.html changes (an update or a voice edit), so an open page reloads itself."""
    try:
        return int(HTML.stat().st_mtime)
    except OSError:
        return 0


def _team_version():
    try:
        return int(TEAM_HTML.stat().st_mtime)
    except OSError:
        return 0


class _Handler(BaseHTTPRequestHandler):
    def _clipper_get(self):
        """The Shorts clipper dashboard (clipper/api.py). True if it handled the request."""
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/clipper"):
            return False
        if path in ("/clipper", "/clipper/"):
            self._send(200, CLIPPER_HTML.read_bytes(), "text/html; charset=utf-8")
            return True
        try:
            from clipper import api
            if path == "/clipper/state":
                body = dict(api.state(), token=TOKEN, page=CLIPPER_HTML.stat().st_mtime)
                self._send(200, json.dumps(body, default=str).encode(), "application/json")
                return True
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            for kind, fn in (("job", api.job_detail), ("clip", api.clip_detail)):
                prefix = f"/clipper/{kind}/"
                if path.startswith(prefix) and path[len(prefix):].isdigit():
                    self._send(200, json.dumps(fn(int(path[len(prefix):])), default=str).encode(), "application/json")
                    return True
            if path.startswith("/clipper/frame/") and path.endswith(".jpg") and path[15:-4].isdigit():
                f = api.frame(int(path[15:-4]), float((query.get("t") or ["0"])[0]))
                if f:
                    self._send_file(f, "image/jpeg")
                    return True
            for kind, ext, ctype in (("media", ".mp4", "video/mp4"), ("thumb", ".jpg", "image/jpeg")):
                prefix = f"/clipper/{kind}/"
                if path.startswith(prefix) and path.endswith(ext) and path[len(prefix):-len(ext)].isdigit():
                    cid = int(path[len(prefix):-len(ext)])
                    f = api.media_path(kind, cid)
                    if not f:
                        break
                    name = api.download_name(cid) if kind == "media" and query.get("download") else None
                    self._send_file(f, ctype, download=name)
                    return True
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")
            return True
        self._send(404, b"not found", "text/plain")
        return True

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype, download=None):
        """Serves a clip with HTTP Range support, so the preview player can seek. download = a file
        name to save it as."""
        import os
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                start = int(a) if a else max(0, size - int(b))
                end = min(size - 1, int(b)) if a and b else end
            except ValueError:
                start, end = 0, size - 1
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(download))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        if self._clipper_get():
            return
        if self.path.startswith("/state"):
            _last_view[0] = time.time()
            with _lock:
                body = json.dumps(dict(STATE, now=time.time(), token=TOKEN, page=_page_version())).encode()
            ctype = "application/json"
        elif self.path.startswith("/team/state"):
            # The live agent network: real events from the AI team (workforce.py)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                import workforce
                state = workforce.live_state(int((q.get("since") or ["0"])[0]))
                if str(state.get("pid")) != (q.get("pid") or [""])[0] and state["since"]:
                    state = workforce.live_state(0)        # a new mission: start from its first event
                state["page"] = _team_version()
                body = json.dumps(state).encode()
            except Exception as e:
                body = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode()
            ctype = "application/json"
        elif self.path.split("?")[0] in ("/team", "/team/"):
            body = TEAM_HTML.read_bytes()
            ctype = "text/html; charset=utf-8"
        elif self.path.startswith("/vendor/"):
            name = urllib.parse.unquote(urllib.parse.urlparse(self.path).path[len("/vendor/"):])
            f = VENDOR / name
            if "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".js") or not f.is_file():
                self.send_response(404)
                self.end_headers()
                return
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            body = HTML.read_bytes()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """The display's text box: {"text": "..."} with the page's token (and the clipper's buttons)."""
        origin = self.headers.get("Origin", "")
        ok_origin = origin in ("", f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")
        if self.path.startswith("/clipper/upload"):
            if not ok_origin or self.headers.get("X-Jarvis-Token") != TOKEN:
                self.send_response(403)
                self.end_headers()
                return
            try:
                from clipper import api
                name = urllib.parse.unquote(self.headers.get("X-File-Name", "video.mp4"))
                res = api.save_upload(self.rfile, int(self.headers.get("Content-Length", 0)), name)
                if res.get("ok"):
                    count = self.headers.get("X-Clip-Count")
                    res = api.action({"action": "add", "url": res["path"], "count": int(count) if count and count.isdigit() else None,
                                      "length": self.headers.get("X-Clip-Length") or None})
            except Exception as e:
                res = {"ok": False, "message": f"{type(e).__name__}: {e}"}
            self._send(200, json.dumps(res, default=str).encode(), "application/json")
            return
        if self.path.startswith("/clipper/action"):
            if not ok_origin or self.headers.get("X-Jarvis-Token") != TOKEN:
                self.send_response(403)
                self.end_headers()
                return
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 200000)
                req = json.loads(self.rfile.read(n) or b"{}")
                from clipper import api
                res = api.action(req)
            except Exception as e:
                res = {"ok": False, "message": f"{type(e).__name__}: {e}"}
            self._send(200, json.dumps(res, default=str).encode(), "application/json")
            return
        if not self.path.startswith("/say") or not ok_origin or self.headers.get("X-Jarvis-Token") != TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 10000)
            text = str(json.loads(self.rfile.read(n) or b"{}").get("text", "")).strip()[:500]
        except Exception:
            text = ""
        if text:
            TYPED.put(text)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def start(open_browser=True):
    server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_music_loop, daemon=True).start()
    threading.Thread(target=_weather_loop, daemon=True).start()
    url = f"http://localhost:{PORT}"
    print(f"Display: {url}")
    if open_browser:
        if WINDOWS:
            webbrowser.open(url)
        else:
            subprocess.run(["open", url])


def open_page(path="/"):
    """Opens one of the display's pages (e.g. /team) in the browser."""
    url = f"http://localhost:{PORT}{path}"
    if WINDOWS:
        webbrowser.open(url)
    else:
        subprocess.run(["open", url])
