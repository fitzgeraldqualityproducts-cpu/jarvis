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
- Before and after installing an update, and again when he starts on the new version, he runs a
  self-check (selftest.py): his code and modules, his voice, his brain, the microphone and the speaker.
  If something that worked before is broken, he reinstalls his Python parts, and if that doesn't fix
  it he undoes the update and tells you next time you wake him. "self_test": false turns it off.
- "Check for updates" checks right now; "what's new?" repeats the last update;
  "run a self check" tells you whether everything's working.
- "auto_update": false in config.json turns it off; "update_repo" points it somewhere else.

## Cut him off
While he's talking, say **"stop"** or **"Jarvis"** (or "Hey Jarvis", or clap twice) and he stops
straight away and listens. "interrupt_words": false in config.json turns off the stop/Jarvis
words (claps and "Hey Jarvis" still work).

## Talk like a normal conversation
- **Quick answers:** his brain's reply is spoken while it's still being written, so he starts
  talking after the first sentence (a second or two) instead of waiting for the whole answer. If a
  web search is slow he says "one sec" first ("hold_on_after" in config.json, seconds; 0 turns it off).
- **Follow-ups:** after he answers, just keep talking. He listens for "follow_up_seconds"
  (default 8) without "Hey Jarvis". Say "thanks" or "that's all" to send him to sleep.
- **Talk over him:** in a spoken conversation, start talking while he's mid-answer and he stops and
  listens to what you're saying. "barge_in": false turns that off (stop/Jarvis still work).
  Group chat mode still checks who's talking.

## Learns from what he can't do
When a command fails or he has to say he can't do something, he notes it (lessons.json). While
idle he has Claude Code look over his code (read-only) for a fix, and later offers it: "Earlier you
asked me to ... I could add that. Want me to upgrade myself?" Say yes and he upgrades himself like
any voice edit ("undo that" still works); say no, or just carry on, and he drops it. At most two
offers a day. Ask **"any upgrade ideas?"** or **"what have you been struggling with?"** any time.
Only you get these, not friends in group chat mode. "self_improve": false turns it off.

**Self-improving:** he also keeps score: when you correct him ("no, that's not what I asked"),
whether you take up the routines he suggests, his team's project scores, and whether his upgrades
work. Once a week (while idle) he reviews it all and works out one improvement: either a code change
(offered as above) or a new **rule** for how he works, like "keep weather answers to one sentence".
Rules need no code change: after you say yes, they go into every answer and every team job.
Say **"review yourself"** for his report card and an idea, **"what rules have you learned"**, or
**"forget your rules"**.

## Predicts what you need
- **Routines:** he notes what you ask him and when (habits.json, on this computer only). When you've
  done the same thing around the same time on 3+ different days, he offers it around then: "You
  usually ask me to play Dead Fresh around this time. Want me to?" Say yes and he does it; say no
  twice and he stops offering that one. At most 3 a day. **"What have you noticed about me?"** lists them.
- **Heads-up:** a few minutes before each event on today's calendar he tells you, out loud, if he's
  not busy ("heads_up_minutes", default 10).
- **Looking ahead:** once a day he looks over your calendar, reminders, routines, Obsidian to-dos,
  what he knows about you and his team's projects, and works out what you'll need. It's in the
  morning briefing ("Looking ahead: ..."). Ask **"what do you think I need?"** any time (or "tomorrow").
"predictive": false turns it off; "speak_up": false stops him speaking up unasked.

## His AI team
Say **"get the team on ..."** (or "have your agents research ...", "start a project to ...") for
bigger jobs: research, planning, business ideas, writing or code. A team lead splits the job between
16 specialists (researcher, market analyst, fact checker, trend scout, planner, project manager,
business strategist, finance analyst, marketer, designer, coder, code reviewer, writer, editor, coach,
devil's advocate). Ones that don't need each other work at the same time; the rest get their
teammates' work passed along. A critic scores the result and sends weak parts back to be redone,
then the lead writes a report. It all happens in the background, so keep talking to him; he tells
you when they're done (not between 11pm and 8am, "quiet_hours").
- **"How's the team doing?"**, **"read me the report"**, **"who's on your team?"**, **"call off the team"**.
- If I restart while the team is working (an update, a crash), I now wait for them first. If a project still gets cut off, I say so, and **"restart the team project"** starts it again.
- **"Show me the team"** (or the AGENT NET link at the bottom of the display) opens the live agent
  network at http://localhost:8765/team: JARVIS in the middle, the team lead, critic and all 16
  specialists around him, glowing lines for who handed what to whom, dashed lines for which task needs
  which, a live activity feed, the mission's progress, and a task pipeline. Click any agent to see its
  task, tool calls, output and past missions. It's drawn only from the team's real events (saved as
  events.jsonl in each project folder); nothing on it is simulated.
- Reports are in the **team** folder here (and "Jarvis Team" in Obsidian if you use it).
- The agents can only search the web, think, and write files in their own project folder. They
  can't send, post, buy, delete or change your stuff or Jarvis. Anything like that is listed in the
  report under "Needs your OK", and a change to Jarvis himself is only made if you say yes.
- Each project uses your Claude plan like several questions do. "team": false turns it off.

### StoreForge (your Shopify store builder)
If StoreForge is on this Mac (~/storeforge), the team gets a **store builder** agent that uses it:
"get the team on building a store for <product link>". It can start a store project, run "generate
everything", follow progress, read the listing and checklist, redo a section, build the export ZIP
and dry-run a Shopify push. It talks to StoreForge on this computer only (storeforge_mcp.py), and
starts StoreForge itself if it isn't running.
- Two things always wait for you: confirming you have the right to use a product's photos and text,
  and pushing to your real Shopify store (as a draft). Jarvis asks "Should I go ahead?" when the
  team's done, or say **"what needs my OK"** any time.
- Setup once: double-click **Connect StoreForge.command**. It asks for an Anthropic API key
  (StoreForge's AI uses the Claude API, separate from your Claude plan) and, for pushes, your
  store's myshopify.com address and Admin API token. They're saved in StoreForge's own .env.local.

## Type to him
His display has a **Type to Jarvis** box under the conversation (press / to jump to it). Type
anything you'd say and he answers as usual. The menu bar / tray icon has **Type to Jarvis…** too.
Email summaries ("summarise my emails") are Mac-only (they read the Mail app).

## Group chat mode (knows your voice)
Double-click **Teach Jarvis My Voice** (Mac: .command, PC: .bat) and read the six sentences it shows
(about two minutes, one time). After that Jarvis checks who's talking, on this computer, with no internet.
- **You:** everything works as before.
- **Friends:** music, timers, weather, YouTube and questions work, but your notes, memory, calendar,
  reminders, emails, files, apps, Discord, stream mode and his settings are for you only.
- At the end you can add friends by name so he greets them ("who am I?").
- Say "group chat mode off" or "on" any time. Run the file again to re-teach your voice or remove a friend.
- Your voiceprint is saved in voiceprints.json, and gets a little better each time he's sure it's you.
