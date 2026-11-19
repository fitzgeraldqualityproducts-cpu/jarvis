"""Computer skills: websites and search, quitting and switching apps, Shortcuts.
On Windows the Mac tools are swapped for the ones in win.py."""
import difflib
import subprocess
import sys
import urllib.parse

import hands

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import win

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com", "google drive": "https://drive.google.com",
    "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.com",
    "reddit": "https://www.reddit.com", "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com", "twitter": "https://x.com", "x": "https://x.com",
    "tiktok": "https://www.tiktok.com", "linkedin": "https://www.linkedin.com",
    "github": "https://github.com", "wikipedia": "https://en.wikipedia.org",
    "claude": "https://claude.ai", "chat gpt": "https://chatgpt.com", "chatgpt": "https://chatgpt.com",
    "espn": "https://www.espn.com", "weather": "https://weather.com", "twitch": "https://www.twitch.tv",
    "shopify": "https://admin.shopify.com", "canva": "https://www.canva.com", "pinterest": "https://www.pinterest.com",
}
BROWSERS = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "safari": "Safari", "firefox": "Firefox",
            "brave": "Brave Browser", "arc": "Arc", "edge": "Microsoft Edge", "opera": "Opera"}
PROTECTED = {"terminal", "finder", "jarvis", "python", "python3", "loginwindow", "system settings"}


def _open_url(url, browser=None):
    if WINDOWS:
        win.open_url(url, browser)
        return
    if browser:
        r = subprocess.run(["open", "-a", browser, url], capture_output=True)
        if r.returncode == 0:
            return
    subprocess.run(["open", url])


def site_url(spoken):
    """URL for a site name like 'youtube' or 'amazon dot com', else None."""
    spoken = spoken.lower().strip(" .?!").removeprefix("the ").removesuffix(" website").strip()
    if spoken in SITES:
        return SITES[spoken]
    domain = spoken.replace(" dot ", ".").replace(" ", "")
    if "." in domain and len(domain) > 3:
        return "https://" + domain
    m = difflib.get_close_matches(spoken, list(SITES), n=1, cutoff=0.8)
    return SITES[m[0]] if m else None


def open_in_browser(site, browser_word):
    browser = BROWSERS.get(browser_word.lower().strip(), None)
    url = site_url(site) or ("https://www.google.com/search?q=" + urllib.parse.quote_plus(site))
    _open_url(url, browser)
    return f"Opening {site.strip()} in {browser or 'your browser'}."


def new_tab(browser_word):
    browser = BROWSERS.get(browser_word.lower().strip(), None if WINDOWS else "Safari")
    if WINDOWS:
        win.open_url("https://www.google.com", browser)
        return f"New tab open in {browser or 'your browser'}."
    subprocess.run(["open", "-a", browser, "https://www.google.com"])
    return f"New tab open in {browser}."


def open_thing(name):
    """Open an allowed app, otherwise a known website or a typed web address."""
    spoken = name.lower().strip(" .?!")
    spoken = spoken.removeprefix("the ").removesuffix(" website").removesuffix(" app").strip()
    domain = spoken.replace(" dot ", ".").replace(" ", "")
    if spoken not in SITES and "." not in domain and \
            not difflib.get_close_matches(spoken, list(SITES), n=1, cutoff=0.8):
        import clarify
        options = clarify.choices(spoken, hands.allowed_apps())
        if options:                                  # "Open Discord or Spotify?"
            return clarify.ask_choice("open", options)
    if hands.find_app(spoken):
        return hands.open_app(spoken)[1]
    if spoken in SITES:
        _open_url(SITES[spoken])
        return f"Opening {name.strip()}."
    domain = spoken.replace(" dot ", ".").replace(" ", "")
    if "." in domain and len(domain) > 3:
        _open_url("https://" + domain)
        return f"Opening {domain}."
    match = difflib.get_close_matches(spoken, list(SITES), n=1, cutoff=0.8)
    if match:
        _open_url(SITES[match[0]])
        return f"Opening {match[0]}."
    return hands.open_app(spoken)[1]


def google(query):
    _open_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Here's what Google has on {query}."


def youtube(query):
    _open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
    return f"Searching YouTube for {query}."


def _osa(script):
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    return out.stdout.strip()


def running_apps():
    if WINDOWS:
        return win.running_apps()
    names = _osa('tell application "System Events" to get name of every application process whose background only is false')
    return [n.strip() for n in names.split(",") if n.strip()]


def _match_running(spoken):
    spoken = spoken.lower().removeprefix("the ").removesuffix(" app").strip(" .?!")
    apps = running_apps()
    lower = {a.lower(): a for a in apps}
    if spoken in lower:
        return lower[spoken]
    for low, real in lower.items():
        if spoken in low or low in spoken:
            return real
    m = difflib.get_close_matches(spoken, list(lower), n=1, cutoff=0.6)
    return lower[m[0]] if m else None


def quit_app(spoken):
    if WINDOWS:
        return win.quit_app(spoken)
    import clarify
    options = clarify.choices(spoken.lower().removeprefix("the ").removesuffix(" app"), running_apps())
    if options:                                      # "Close Discord or Spotify?"
        return clarify.ask_choice("close", options)
    app = _match_running(spoken)
    if not app:
        return f"{spoken.strip().capitalize()} doesn't seem to be open."
    if app.lower() in PROTECTED:
        return f"I'd better not close {app}. I need it to keep running."
    _osa(f'tell application "{app}" to quit')
    return f"Closed {app}."


def switch_to(spoken):
    if WINDOWS:
        app = win.switch_to(spoken)
        return f"Here's {app}." if app else open_thing(spoken)
    app = _match_running(spoken)
    if app:
        subprocess.run(["open", "-a", app])
        return f"Here's {app}."
    return open_thing(spoken)


# ---- Shortcuts app ----

def shortcuts():
    if WINDOWS:
        return []
    out = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=20)
    return [s.strip() for s in out.stdout.splitlines() if s.strip()]


def run_shortcut(name):
    if WINDOWS:
        return "Shortcuts are a Mac thing, so I can't run them on this PC."
    names = shortcuts()
    if not names:
        return "I couldn't find any shortcuts in the Shortcuts app."
    lower = {n.lower(): n for n in names}
    spoken = name.lower().strip(" .?!").removeprefix("my ").removeprefix("the ").removesuffix(" shortcut").strip()
    target = lower.get(spoken)
    if not target:
        hits = [real for low, real in lower.items() if spoken in low or low in spoken]
        target = hits[0] if hits else None
    if not target:
        m = difflib.get_close_matches(spoken, list(lower), n=1, cutoff=0.5)
        target = lower[m[0]] if m else None
    if not target:
        return f"I couldn't find a shortcut called {name}."
    out = subprocess.run(["shortcuts", "run", target], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        return f"The {target} shortcut didn't finish properly."
    result = out.stdout.strip()
    return f"Done. {result}" if result and len(result) < 200 else f"Ran {target}."


def list_shortcuts():
    if WINDOWS:
        return "Shortcuts are a Mac thing, so there aren't any on this PC."
    names = shortcuts()
    if not names:
        return "You don't have any shortcuts yet. You can make them in the Shortcuts app."
    shown = names[:10]
    more = f", and {len(names) - 10} more" if len(names) > 10 else ""
    return "Your shortcuts are: " + ", ".join(shown) + more + "."
