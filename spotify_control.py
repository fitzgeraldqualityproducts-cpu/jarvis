"""Spotify skills for Jarvis.

Works in two layers:
  * Built in, no setup: play, pause, skip, previous, what's playing, volume
    (AppleScript on a Mac; media keys and the volume mixer on Windows).
  * With a free Spotify developer app (one-time setup, run
    `python spotify_control.py`): play any song, album, artist or playlist by
    name, and add songs to your queue. Adding to the queue needs Spotify Premium.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"

CONFIG = Path(__file__).with_name("config.json")
TOKEN_CACHE = Path(__file__).with_name(".spotify_token")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-private playlist-read-private user-read-playback-state user-modify-playback-state user-read-currently-playing"


# ---------- AppleScript layer ----------

def _osa(script):
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return out.stdout.strip()


def is_running():
    if WINDOWS:
        import win
        return win.process_running("Spotify.exe")
    return _osa('application "Spotify" is running') == "true"


def _tell(cmd):
    return _osa(f'tell application "Spotify" to {cmd}')


def play():
    if WINDOWS:
        if not _win_playing():
            _win_key("play_pause", "start_playback")
        return "Resuming the music."
    _tell("play")
    return "Resuming the music."


def pause():
    if WINDOWS:
        if _win_playing():
            _win_key("play_pause", "pause_playback")
        return "Paused."
    if is_running():
        _tell("pause")
    return "Paused."


def next_track():
    if WINDOWS:
        _win_key("next", "next_track")
        return "Skipping ahead."
    _tell("next track")
    return "Skipping ahead."


def previous_track():
    if WINDOWS:
        _win_key("previous", "previous_track")
        return "Going back one."
    _tell("previous track")
    return "Going back one."


def now_playing():
    if not is_running():
        return "Spotify isn't open at the moment."
    if WINDOWS:
        title = _win_title()
        if title and " - " in title:
            artist, name = title.split(" - ", 1)
            return f"This is {name} by {artist}."
        return "Spotify is paused right now."
    name = _tell("name of current track")
    artist = _tell("artist of current track")
    state = _tell("player state as string")
    if not name:
        return "Nothing is playing right now."
    verb = "This is" if state == "playing" else "Paused on"
    return f"{verb} {name} by {artist}."


def set_volume(level):
    level = max(0, min(100, int(level)))
    if WINDOWS:
        import win
        if not win.set_app_volume("Spotify.exe", level):
            try:
                _api().volume(level)
            except Exception:
                return "I couldn't change Spotify's volume. Start playing something first."
        return f"Volume set to {level}."
    _tell(f"set sound volume to {level}")
    return f"Volume set to {level}."


def change_volume(delta):
    if WINDOWS:
        import win
        return set_volume((win.get_app_volume("Spotify.exe") or 50) + delta)
    current = int(_tell("sound volume") or 50)
    return set_volume(current + delta)


_saved_volume = None


def duck():
    """Turn the music down while Jarvis listens, so he can hear you."""
    global _saved_volume
    if WINDOWS:
        try:
            import win
            if _win_playing():
                _saved_volume = win.get_app_volume("Spotify.exe")
                if _saved_volume is not None:
                    win.set_app_volume("Spotify.exe", max(10, _saved_volume // 4))
        except Exception as e:
            print(f"(couldn't turn the music down: {e})")
        return
    if is_running() and _tell("player state as string") == "playing":
        _saved_volume = int(_tell("sound volume") or 50)
        _tell(f"set sound volume to {max(10, _saved_volume // 4)}")


def unduck():
    global _saved_volume
    if WINDOWS:
        try:
            import win
            if _saved_volume is not None:
                win.set_app_volume("Spotify.exe", _saved_volume)
        except Exception:
            pass
        _saved_volume = None
        return
    if _saved_volume is not None and is_running():
        _tell(f"set sound volume to {_saved_volume}")
    _saved_volume = None


def play_uri(uri):
    if WINDOWS:
        return _win_play_uri(uri)
    _tell(f'play track "{uri}"')
    return True


# ---------- Windows layer (media keys, window title, Web API) ----------

def _win_title():
    import win
    for t in win.window_titles("Spotify.exe"):
        if t and t not in ("GDI+ Window", "Default IME", "MSCTFIME UI") and not t.startswith("Spotify"):
            return t
    return ""


def _win_playing():
    """Spotify's window shows 'Artist - Song' while playing and 'Spotify ...' when paused."""
    return is_running() and bool(_win_title())


def _win_key(key, api_call):
    import win
    if is_running():
        win.media_key(key)
        return
    sp = _api()
    if sp:
        try:
            getattr(sp, api_call)()
        except Exception as e:
            print(f"(Spotify: {e})")


def _win_play_uri(uri):
    """Plays a track/album/playlist/artist. Uses the Web API (needs Premium); otherwise opens it
    in the app and presses play."""
    sp = _api()
    if sp:
        try:
            devices = sp.devices().get("devices", [])
            dev = next((d for d in devices if d.get("is_active")), None) or \
                next((d for d in devices if d.get("type") == "Computer"), None) or (devices[0] if devices else None)
            kwargs = {"device_id": dev["id"] if dev else None}
            if ":track:" in uri:
                kwargs["uris"] = [uri]
            else:
                kwargs["context_uri"] = uri
            sp.start_playback(**kwargs)
            return True
        except Exception as e:
            print(f"(Spotify Web API couldn't play, opening it instead: {e})")
    os.startfile(uri)
    time.sleep(2.5)
    import win
    if not _win_playing():
        win.media_key("play_pause")
    return False


# ---------- Web API layer (search + queue) ----------

def _client_id():
    try:
        return json.loads(CONFIG.read_text()).get("spotify_client_id", "").strip()
    except Exception:
        return ""


def _api(interactive=False):
    """Returns a logged-in spotipy client, or None if not set up."""
    cid = _client_id()
    if not cid:
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyPKCE
    except ImportError:
        return None
    auth = SpotifyPKCE(client_id=cid, redirect_uri=REDIRECT_URI, scope=SCOPES,
                       cache_path=str(TOKEN_CACHE), open_browser=interactive)
    if not interactive and not auth.get_cached_token():
        return None
    return spotipy.Spotify(auth_manager=auth)


def _my_playlist(sp, name):
    """Look through your own playlists first, so 'my workout playlist' finds yours."""
    name = name.lower().removeprefix("my ").strip()
    try:
        results = sp.current_user_playlists(limit=50)
        while results:
            for pl in results["items"]:
                if pl and name in pl["name"].lower():
                    return pl
            results = sp.next(results) if results.get("next") else None
    except Exception:
        pass
    return None


def _search(query, kind="track"):
    sp = _api()
    if not sp:
        return None
    if kind == "playlist":
        mine = _my_playlist(sp, query)
        if mine:
            return mine
        query = query.removeprefix("my ")
    q = query
    if kind in ("track", "album") and " by " in query:
        title, artist = query.rsplit(" by ", 1)
        q = f"{kind}:{title} artist:{artist}"
    res = sp.search(q=q, type=kind, limit=1)
    if not res.get(kind + "s", {}).get("items") and q != query:
        res = sp.search(q=query, type=kind, limit=1)
    items = res.get(kind + "s", {}).get("items", [])
    return items[0] if items else {}


def _describe(item, kind):
    if kind == "track":
        return f"{item['name']} by {item['artists'][0]['name']}"
    if kind == "album":
        return f"the album {item['name']} by {item['artists'][0]['name']}"
    if kind == "artist":
        return item["name"]
    return f"the playlist {item['name']}"


NOT_SET_UP = ("I can't search Spotify yet. Run python spotify_control.py in my folder "
              "once to connect your account.")


def play_query(query, kind="track"):
    item = _search(query, kind)
    if item is None:
        if WINDOWS:
            os.startfile(f"spotify:search:{query}")
        else:
            subprocess.run(["open", f"spotify:search:{query}"])
        return f"I've opened a Spotify search for {query}. " + NOT_SET_UP
    if not item:
        return f"I couldn't find {query} on Spotify."
    if not is_running():
        if WINDOWS:
            import win
            win.open_app("Spotify") or os.startfile("spotify:")
        else:
            subprocess.run(["open", "-a", "Spotify"])
        time.sleep(4 if WINDOWS else 3)
    play_uri(item["uri"])
    return f"Playing {_describe(item, kind)}."


def queue_query(query):
    item = _search(query, "track")
    if item is None:
        return NOT_SET_UP
    if not item:
        return f"I couldn't find {query} on Spotify."
    try:
        _api().add_to_queue(item["uri"])
    except Exception as e:
        msg = str(e)
        if "PREMIUM" in msg.upper():
            return "Spotify only allows adding to the queue with a Premium account, I'm afraid."
        if "NO_ACTIVE_DEVICE" in msg.upper() or "404" in msg:
            return "Start playing something in Spotify first, then I can add to the queue."
        return "Spotify wouldn't let me add that to the queue."
    return f"Added {_describe(item, 'track')} to your queue."


# ---------- one-time setup ----------

if __name__ == "__main__":
    print(__doc__)
    cfg = json.loads(CONFIG.read_text())
    if not cfg.get("spotify_client_id"):
        print("Step 1. Go to https://developer.spotify.com/dashboard and log in.")
        print("Step 2. Click 'Create app'. Any name/description is fine.")
        print(f"        Redirect URI:  {REDIRECT_URI}   (click Add)")
        print("        Tick 'Web API', agree, Save.")
        print("Step 3. Open the app's Settings and copy the Client ID.\n")
        cid = input("Paste the Client ID here: ").strip()
        cfg["spotify_client_id"] = cid
        CONFIG.write_text(json.dumps(cfg, indent=2))
    print("\nA browser window will open. Log in to Spotify and click Agree.")
    sp = _api(interactive=True)
    me = sp.current_user()
    print(f"\nConnected as {me.get('display_name') or me['id']} "
          f"({me.get('product', 'unknown')} account).")
    print("Test search:", _describe(_search("Back in Black"), "track"))
    print("All set. Restart Jarvis and try: 'play Bohemian Rhapsody', "
          "'add Yellow by Coldplay to the queue', 'pause', 'next song'.")
