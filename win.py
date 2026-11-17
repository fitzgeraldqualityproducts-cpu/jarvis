"""Windows versions of the things Jarvis does with Mac-only tools.

On a Mac none of this is used. On Windows it replaces:
  afplay            -> play() (decodes mp3/wav with miniaudio, plays with sounddevice)
  open / open -a    -> open_url(), open_app() (Start menu apps, including Store apps)
  AppleScript       -> running_apps(), quit_app(), switch_to(), media keys, Spotify volume
  say               -> speak_to_file() (the built-in Windows voice)
  Login Items       -> set_login_item() (a small file in the Startup folder)
"""
import ctypes
import difflib
import json
import os
import subprocess
import sys
import threading
import time
import wave
import webbrowser
from pathlib import Path

WINDOWS = sys.platform == "win32"
HERE = Path(__file__).resolve().parent
NO_WINDOW = 0x08000000 if WINDOWS else 0          # CREATE_NO_WINDOW: no flashing console boxes
MEDIA = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Media"


def powershell(script, timeout=30, env=None):
    """Runs a PowerShell snippet and returns its output text ('' on failure)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
                             capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
                             env=dict(os.environ, **(env or {})))
        if out.returncode != 0 and out.stderr:
            print(f"(PowerShell: {out.stderr.decode('utf-8', 'replace').strip()[:200]})")
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception as e:
        print(f"(PowerShell failed: {e})")
        return ""


# ---------------- sound ----------------

def _decode(path):
    """Returns (float32 samples shaped (frames, channels), sample rate)."""
    import numpy as np
    try:
        import miniaudio
        d = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.FLOAT32)
        data = np.asarray(d.samples, dtype=np.float32).reshape(-1, d.nchannels)
        return data, d.sample_rate
    except ImportError:
        pass
    with wave.open(str(path)) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return raw.reshape(-1, w.getnchannels()), w.getframerate()


class Player:
    """Plays a sound file in the background. Works like the afplay process it replaces:
    poll() is None while playing, wait() blocks until done, terminate() stops it."""

    def __init__(self, path):
        import sounddevice as sd
        self._sd = sd
        self._data, rate = _decode(path)
        self._pos = 0
        self._done = threading.Event()
        self._closed = False
        if not len(self._data):
            self._done.set()
            self._stream = None
            return
        self._stream = sd.OutputStream(samplerate=rate, channels=self._data.shape[1], dtype="float32",
                                       device=output_device(), callback=self._fill,
                                       finished_callback=self._done.set)
        self._stream.start()

    def _fill(self, out, frames, _time, _status):
        chunk = self._data[self._pos:self._pos + frames]
        out[:len(chunk)] = chunk
        self._pos += frames
        if len(chunk) < frames:
            out[len(chunk):] = 0
            raise self._sd.CallbackStop

    def _close(self):
        if not self._closed and self._stream is not None:
            self._closed = True
            try:
                self._stream.close()
            except Exception:
                pass

    def poll(self):
        if self._done.is_set():
            self._close()
            return 0
        return None

    def wait(self, timeout=None):
        self._done.wait(timeout)
        self._close()
        return 0

    def terminate(self):
        if self._stream is not None and not self._closed:
            try:
                self._stream.abort()
            except Exception:
                pass
        self._done.set()
        self._close()


def play(path):
    """Start playing a file; returns a Player."""
    return Player(path)


def play_wait(path):
    play(path).wait()


def _cfg():
    try:
        return json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def output_devices():
    """Names of the speakers / outputs this PC has (one per name)."""
    import sounddevice as sd
    names = []
    for d in sd.query_devices():
        if d["max_output_channels"] > 0 and d["name"] not in names:
            names.append(d["name"])
    return names


def output_device():
    """The device Jarvis speaks through: config "audio_output" (part of its name), else the default."""
    want = _cfg().get("audio_output", "").strip().lower()
    if not want:
        return None
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and want in d["name"].lower():
            return i
    print(f"(speaker '{want}' not found, using the default)")
    return None


def system_sound(kind="chime"):
    """kind: 'chime' (Jarvis woke up) or 'alarm' (timer done)."""
    names = {"chime": ["Speech On.wav", "Windows Notify System Generic.wav", "chimes.wav"],
             "alarm": ["Alarm01.wav", "Windows Notify Calendar.wav", "notify.wav"]}[kind]
    try:
        import winsound
        for name in names:
            f = MEDIA / name
            if f.exists():
                if _cfg().get("audio_output"):        # keep all of Jarvis's sounds on his own channel
                    play(f)
                    return
                winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
        winsound.MessageBeep()
    except Exception as e:
        print(f"(sound failed: {e})")


def speak_to_file(text, path, voice_hint="George"):
    """Saves speech from the built-in Windows voice to a .wav file."""
    powershell(
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v=$s.GetInstalledVoices()|%{$_.VoiceInfo.Name}|?{$_ -match $env:JARVIS_HINT -or $_ -match 'GB'}|select -First 1;"
        "if($v){$s.SelectVoice($v)};"
        "$s.SetOutputToWaveFile($env:JARVIS_OUT);$s.Speak($env:JARVIS_TEXT);$s.Dispose()",
        timeout=60, env={"JARVIS_TEXT": text, "JARVIS_OUT": str(path), "JARVIS_HINT": voice_hint})
    if not os.path.exists(path) or os.path.getsize(path) < 100:
        raise RuntimeError("Windows voice didn't produce audio")


# ---------------- websites ----------------

BROWSER_EXE = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge",
               "firefox": "firefox", "brave browser": "brave", "brave": "brave", "opera": "opera"}


def open_url(url, browser=None):
    if browser:
        exe = BROWSER_EXE.get(browser.lower())
        if exe:
            r = subprocess.run(["cmd", "/c", "start", "", exe, url], capture_output=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                return True
        elif open_app(browser, url):
            return True
    try:
        os.startfile(url)
    except Exception:
        webbrowser.open(url)
    return True


# ---------------- apps ----------------

_apps_cache = {"t": 0, "apps": []}
JUNK_WORDS = ("uninstall", "readme", "read me", "help", "documentation", "website", "release notes",
              "license", "manual", "debug", "safe mode", "(x86)", "web site", "support")


def start_apps(fresh=False):
    """[(name, app_id)] for everything in the Start menu, including Microsoft Store apps."""
    if not WINDOWS:
        return []
    if fresh or time.time() - _apps_cache["t"] > 600 or not _apps_cache["apps"]:
        out = powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=40)
        try:
            data = json.loads(out) if out else []
            data = [data] if isinstance(data, dict) else data
            apps = [(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]
        except Exception:
            apps = []
        if apps:
            _apps_cache.update(t=time.time(), apps=apps)
    return _apps_cache["apps"]


def app_names():
    names = {n for n, _ in start_apps() if not any(w in n.lower() for w in JUNK_WORDS)}
    return sorted(names, key=str.lower)


def _find_start_app(name):
    apps = start_apps()
    lower = {}
    for n, aid in apps:
        lower.setdefault(n.lower(), aid)
    key = name.lower().strip()
    if key in lower:
        return lower[key]
    starts = [n for n in lower if n.startswith(key)]
    if starts:
        return lower[min(starts, key=len)]
    m = difflib.get_close_matches(key, list(lower), n=1, cutoff=0.75)
    return lower[m[0]] if m else None


ALIASES = {"file explorer": "explorer", "explorer": "explorer", "finder": "explorer", "notepad": "notepad",
           "calculator": "calc", "paint": "mspaint", "command prompt": "cmd", "task manager": "taskmgr",
           "settings": "ms-settings:", "system settings": "ms-settings:", "control panel": "control"}


def open_app(name, arg=None):
    """Opens a Start-menu app by name. Returns True if something launched."""
    aid = _find_start_app(name)
    try:
        if aid:
            if arg is None:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aid}"], creationflags=NO_WINDOW)
                return True
            # Shortcut-style IDs are file paths we can pass a web address to.
            if os.path.exists(aid):
                subprocess.Popen([aid, arg], creationflags=NO_WINDOW)
                return True
        alias = ALIASES.get(name.lower().strip())
        if alias:
            if alias.endswith(":"):
                os.startfile(alias)
            else:
                subprocess.Popen(["cmd", "/c", "start", "", alias] + ([arg] if arg else []), creationflags=NO_WINDOW)
            return True
        if aid:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aid}"], creationflags=NO_WINDOW)
            return True
    except Exception as e:
        print(f"(couldn't open {name}: {e})")
    return False


def _windows_list():
    """[{name, title, id, hwnd, exe}] for every app with a window on screen."""
    out = powershell(
        "Get-Process | ?{$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle} | "
        "Select-Object Id, ProcessName, MainWindowTitle, @{n='Hwnd';e={[int64]$_.MainWindowHandle}}, "
        "@{n='Desc';e={$_.Description}} | ConvertTo-Json -Compress", timeout=20)
    try:
        data = json.loads(out) if out else []
        data = [data] if isinstance(data, dict) else data
    except Exception:
        return []
    apps = []
    for d in data:
        friendly = (d.get("Desc") or "").strip() or d["ProcessName"]
        if d["ProcessName"].lower() == "applicationframehost":   # Store apps: name is in the window title
            friendly = d.get("MainWindowTitle") or friendly
        apps.append({"name": friendly, "exe": d["ProcessName"], "title": d.get("MainWindowTitle", ""),
                     "id": d["Id"], "hwnd": int(d.get("Hwnd") or 0)})
    return apps


def running_apps():
    return list(dict.fromkeys(a["name"] for a in _windows_list()))


def _match_window(spoken):
    spoken = spoken.lower().removeprefix("the ").removesuffix(" app").strip(" .?!")
    wins = _windows_list()
    for key in ("name", "exe"):
        for w in wins:
            if w[key].lower() == spoken:
                return w
    for w in wins:
        if spoken in w["name"].lower() or spoken in w["exe"].lower() or w["exe"].lower() in spoken:
            return w
    names = {w["name"].lower(): w for w in wins}
    m = difflib.get_close_matches(spoken, list(names), n=1, cutoff=0.6)
    return names[m[0]] if m else None


PROTECTED = {"windowsterminal", "cmd", "powershell", "python", "pythonw", "explorer", "conhost", "jarvis",
             "openconsole", "taskmgr"}


def quit_app(spoken):
    w = _match_window(spoken)
    if not w:
        return f"{spoken.strip().capitalize()} doesn't seem to be open."
    if w["exe"].lower() in PROTECTED or "jarvis" in w["title"].lower():
        return f"I'd better not close {w['name']}. I need it to keep running."
    powershell(f"Get-Process -Name '{w['exe']}' -EA SilentlyContinue | ?{{$_.MainWindowHandle -ne 0}} | "
               "%{ [void]$_.CloseMainWindow() }")
    return f"Closed {w['name']}."


def focus(hwnd):
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    user32.keybd_event(0x12, 0, 0, 0)          # tap Alt so Windows lets us change the front window
    user32.keybd_event(0x12, 0, 2, 0)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)             # SW_RESTORE
    return bool(user32.SetForegroundWindow(hwnd))


def switch_to(spoken):
    """Returns the app's name if it was open and brought to the front, else None."""
    w = _match_window(spoken)
    if w and focus(w["hwnd"]):
        return w["name"]
    return None


# ---------------- media keys and per-app volume (Spotify) ----------------

KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
        "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def media_key(name):
    user32 = ctypes.windll.user32
    vk = KEYS[name]
    user32.keybd_event(vk, 0, 1, 0)            # KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, 0, 3, 0)            # extended + key up


def process_running(exe):
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == exe.lower() for p in psutil.process_iter(["name"]))
    except Exception:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True, text=True,
                             creationflags=NO_WINDOW)
        return exe.lower() in out.stdout.lower()


def window_titles(exe):
    """Window titles belonging to a program, e.g. Spotify.exe -> ['Artist - Song']."""
    try:
        import psutil
    except ImportError:
        return []
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == exe.lower()}
    if not pids:
        return []
    user32 = ctypes.windll.user32
    titles = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(proto(each), 0)
    return titles


def _app_volume(exe):
    """The Windows volume-mixer control for a program, or None."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name().lower() == exe.lower():
                return s.SimpleAudioVolume
    except Exception as e:
        print(f"(volume mixer unavailable: {e})")
    return None


def get_app_volume(exe):
    v = _app_volume(exe)
    return None if v is None else round(v.GetMasterVolume() * 100)


def set_app_volume(exe, level):
    v = _app_volume(exe)
    if v is None:
        return False
    v.SetMasterVolume(max(0.0, min(1.0, level / 100)), None)
    return True


# ---------------- start at login ----------------

STARTUP = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft/Windows/Start Menu/Programs/Startup"
LOGIN_VBS = STARTUP / "Jarvis.vbs"


def set_login_item(enable):
    """Starts Jarvis quietly (tray icon only) when you sign in to Windows."""
    if not enable:
        LOGIN_VBS.unlink(missing_ok=True)
        return True
    bat = HERE / "Jarvis at Login.bat"
    STARTUP.mkdir(parents=True, exist_ok=True)
    LOGIN_VBS.write_text(f'CreateObject("WScript.Shell").Run """{bat}""", 0, False\r\n', encoding="utf-8")
    return True
