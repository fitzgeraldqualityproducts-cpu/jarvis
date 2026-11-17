# Jarvis

A voice assistant for your Mac. Clap twice, he greets you with the local weather,
then answers out loud in a calm British voice and opens the apps you allow.

## Use
    cd ~/Desktop/jarvis
    source .venv/bin/activate
    python3 jarvis.py          # clap twice, then talk
    python3 settings.py        # toggle which apps he may open

When he starts, a display opens in your browser at http://localhost:8765 with a glowing
orb that reacts as he listens, thinks and speaks, plus the weather, time, the apps he can
open and your conversation. Use `--no-display` to skip it.

Say "open Spotify", "what's the weather", "what can you open", or ask anything.
Say "goodbye" to send him back to sleep. Ctrl+C quits.

- Brain: your Claude Code login (`claude -p`), so no API key.
- Weather: found from your internet location, no key needed.
- If claps aren't detected, lower `clap_threshold` in config.json (e.g. 0.2); raise it if he wakes by accident.
- macOS will ask once for microphone access for your Terminal app.

## Spotify
Works straight away (no setup): "pause", "play", "next song", "previous song",
"what's playing", "turn it up/down", "volume 40". Music is turned down while Jarvis
is awake so he can hear you.

One-time setup for "play Bohemian Rhapsody", "play my workout playlist",
"play the album Thriller", "add Yellow by Coldplay to the queue":

    .venv/bin/pip install spotipy
    .venv/bin/python spotify_control.py

It walks you through making a free Spotify developer app and logging in.
Adding to the queue needs Spotify Premium.

## New abilities (install once: double-click "Install Jarvis Extras.command" in this folder)
- Wake him with **"Hey Jarvis"** (or clap twice). Say "Hey Jarvis" or clap while he's talking to cut him off.
- **Calendar:** "what's on today?", "what do I have tomorrow?"
- **Reminders:** "remind me to call Mum at 5pm", "remind me to take the bins out tomorrow morning", "what are my reminders?"
- **Timers and alarms:** "set a 10 minute timer", "how long is left?", "cancel the timer", "wake me up at 7am" (while Jarvis is running)
- **Notes:** "take a note: buy batteries"
- **Morning briefing:** automatic on your first clap before 2pm, or say "brief me"
- **Websites and search:** "open YouTube", "google best pizza near me", "play lofi on YouTube"
- **Apps:** "close Safari", "switch to Messages"
- **Shortcuts:** "run my Good Night shortcut", "what shortcuts do I have?"
- **Memory:** "remember that my sister's birthday is June 3rd", "what do you remember about me?", "forget my sister's birthday"
- **Menu bar icon:** shows his status, open the display, pause listening, quit
- **Start at login:** the installer asks; or add "Jarvis at Login.command" in System Settings > Login Items

## Hearing and voice upgrade (run once: double-click "Upgrade Jarvis.command")
- **Hearing:** Whisper speech recognition runs on your Mac. It's more accurate and private, keeps the start of your sentence, and allows longer commands. Add hard words or names to "extra_words" in config.json to help it.
- **Voice:** a natural British voice (Kokoro, offline). Pick another with "kokoro_voice": bm_george, bm_fable, bm_lewis, bm_daniel or bf_emma.
- **Most lifelike:** add an ElevenLabs API key when the upgrade asks. It's used first, and Kokoro takes over if it fails or runs out.
- He now starts speaking after the first sentence is ready, instead of waiting for the whole reply.

## Edit Jarvis by voice
Ask him to change himself and he'll confirm first, then use Claude Code to edit his own files:
- "Upgrade yourself so that you call me boss"
- "Add a new command that flips a coin"
- "From now on, give the weather in Celsius"
- "Change your voice to bm_fable"
He checks that the new code works and undoes it himself if it doesn't, then restarts to load it.
Say **"undo that"** to reverse the last change, or **"what did you change?"** to hear recent edits.
Every change is saved with git in this folder, so nothing is lost.

## Obsidian
Double-click **Connect Obsidian.command** to pick your vault (Jarvis also finds it automatically).
Then say "note that…" (goes to today's daily note), "add X to my to-do list", "what's on my to-do list",
or "what did I write about…". Open to-dos are read out in the morning briefing.
The vault path is saved as `obsidian_vault` in config.json.

## Windows PC
Jarvis also runs on Windows 10 and 11. Unzip **Jarvis-Windows.zip**, open the folder and
double-click **Install Jarvis**. It copies Jarvis to `C:\Users\<you>\Jarvis`, installs Git,
Python (via uv), his parts and Claude Code, logs you in, downloads his voice and hearing, and
puts a **Jarvis** shortcut on your Desktop. Run it again any time to update; settings and memory are kept.

In the Jarvis folder on Windows:
- **Start Jarvis** (or the Desktop shortcut). Self-edits restart him automatically.
- **Choose Jarvis Apps**, **Choose Jarvis Speaker** (own audio channel for OBS via VB-CABLE), **Connect Spotify / Obsidian / Oura / Twitch**, **Fix Jarvis Brain**.

What's different on Windows (all in `win.py`, `wincal.py`, `menubar_win.py`):
- The menu bar icon is a tray icon by the clock (click: display, right-click: pause / quit).
- Calendar and reminders are Jarvis's own (kept in `calendar.json`), not Apple's. Reminders are
  spoken when due while he's running. Notes go to "Jarvis Notes.txt" in Documents, or Obsidian.
- Spotify uses media keys and the Windows volume mixer; "play <song>" needs Connect Spotify.
- Apps are opened from the Start menu (Store apps too). Shortcuts are Mac-only.
- Mic access: Settings > Privacy & security > Microphone > let desktop apps use it.

## Discord
Double-click **Connect Discord** (Mac: Connect Discord.command, PC: Connect Discord.bat). It installs
what Jarvis needs and walks you through making his bot and adding it to your server.
- **Chat:** start a message with "Jarvis", @mention him, reply to him or DM him. Friends can ask him
  things too, but he never shares your notes, memory, calendar or files with them.
- **Voice:** get in a voice channel and say "Hey Jarvis, join Discord" (or type "jarvis join").
  He speaks his answers in the call. "Leave Discord" sends him away; he also leaves when you do.
  Discord doesn't let bots hear people in encrypted calls, so friends type to him.
- Settings in config.json: "discord_guests": false stops friends using him; "discord_guest_limit"
  (default 40) caps friends' questions per hour.

## Keeps himself running
Jarvis starts through a small watchdog (keeper.py). If he crashes, he's back a few seconds later
(after 5 crashes in 5 minutes he stops and says so in jarvis.log). Only one copy can run: starting
him again just opens the display of the one already running. "Quit Jarvis" in the menu bar / tray
icon, or Ctrl+C, stops him for good.

## Updates himself
New versions are published to github.com/fitzgeraldqualityproducts-cpu/jarvis. A few minutes after
he starts, and every 3 hours, Jarvis checks it (updater.py). He merges the new version with your own
voice edits, installs any new parts, checks that he still works (and undoes the update if not), then
restarts when you're not talking to him and tells you what's new the next time you wake him.
Your settings, memory, calendar, tokens and notes are never touched.
- "Check for updates" checks right now; "what's new?" repeats the last update.
- "auto_update": false in config.json turns it off; "update_repo" points it somewhere else.

## Cut him off
While he's talking, say **"stop"** or **"Jarvis"** (or "Hey Jarvis", or clap twice) and he stops
straight away and listens. "interrupt_words": false in config.json turns off the stop/Jarvis
words (claps and "Hey Jarvis" still work).

## Type to him
His display has a **Type to Jarvis** box under the conversation (press / to jump to it). Type
anything you'd say and he answers as usual. The menu bar / tray icon has **Type to Jarvis…** too.
Email summaries ("summarise my emails") are Mac-only (they read the Mail app).
