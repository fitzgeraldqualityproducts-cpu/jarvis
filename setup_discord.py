"""Connect Discord: installs what Jarvis needs for Discord and walks you through making his bot.
Run it with the "Connect Discord" file in this folder."""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
PERMISSIONS = 1024 + 2048 + 65536 + 1048576 + 2097152   # view, send, read history, connect, speak
PORTAL = "https://discord.com/developers/applications"


def say(text=""):
    print(text, flush=True)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def install():
    say("== Installing Jarvis's Discord parts (about a minute) ==")
    pkgs = ["discord.py[voice]>=2.7", "av"]
    uv = shutil.which("uv") or next((p for p in [Path.home() / ".local/bin/uv", Path.home() / ".local/bin/uv.exe"]
                                     if p.exists()), None)
    cmds = []
    if uv:
        cmds.append([str(uv), "pip", "install", "--python", sys.executable] + pkgs)
    cmds.append([sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs)
    for cmd in cmds:
        if subprocess.run(cmd).returncode == 0:
            break
    else:
        say("Installing failed. Copy everything above and send it to Claude.")
        return False
    try:
        sys.path.insert(0, str(HERE))
        import discord_bot
        import discord  # noqa: F401
        if not discord_bot._load_opus():
            say("(Warning: the voice part didn't load, so Jarvis can chat but may not speak in calls.)")
    except Exception as e:
        say(f"(Warning: {e})")
    say("Installed.\n")
    return True


def check_token(tok):
    req = urllib.request.Request("https://discord.com/api/v10/users/@me",
                                 headers={"Authorization": f"Bot {tok}", "User-Agent": "jarvis-assistant (setup, 1.0)"})
    try:
        import certifi
        import ssl
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.load(r)


def main():
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    say("Let's put Jarvis in your Discord.\n")
    if not install():
        return
    if cfg.get("discord_token"):
        if ask("Jarvis already has a Discord bot. Set up a new one? (y/N) ").lower() != "y":
            invite(cfg.get("discord_app_id"))
            return
    say("== Make Jarvis's bot (one time, about 3 minutes) ==")
    say("I'm opening the Discord Developer Portal in your browser. Log in if it asks, then:")
    say("  1. Click 'New Application' (top right), name it Jarvis, tick the box and click Create.")
    say("     (Optional: upload a picture for him on that page.)")
    say("  2. Click 'Bot' on the left.")
    say("  3. Scroll down to 'Privileged Gateway Intents' and switch ON 'Message Content Intent'.")
    say("     Click 'Save Changes' if it pops up.")
    say("  4. Scroll back up and click 'Reset Token', then 'Yes, do it!' (enter your password or code")
    say("     if asked), then 'Copy'.")
    say("  Keep that token secret: anyone with it can control the bot.\n")
    webbrowser.open(PORTAL)
    while True:
        tok = ask("Paste the token here and press Enter: ").strip().strip('"')
        if not tok:
            say("Nothing pasted. Run Connect Discord again when you're ready.")
            return
        try:
            me = check_token(tok)
            break
        except Exception as e:
            say(f"That token didn't work ({e}). Copy it again (Reset Token gives a fresh one) and paste it.\n")
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cfg["discord_token"] = tok
    cfg["discord_app_id"] = me["id"]
    cfg.setdefault("discord_guests", True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    say(f"\nConnected as {me.get('username', 'Jarvis')}.")
    invite(me["id"])


def invite(app_id):
    if not app_id:
        return
    url = f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions={PERMISSIONS}"
    say("\n== Add Jarvis to your server ==")
    say("I'm opening the invite page. Pick your server, click Continue, then Authorize.")
    say(f"(If it doesn't open, copy this link into your browser: {url})")
    webbrowser.open(url)
    say("\nAll done. Restart Jarvis, then:")
    say("  - In any channel, type 'Jarvis, what's up?' or @mention him. Friends can ask him things too.")
    say("  - Get in a voice channel and say 'Hey Jarvis, join Discord'. 'Leave Discord' sends him away.")


if __name__ == "__main__":
    main()
