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
- **He doesn't hear himself:** the mic picks up his own voice from the speakers, so he ignores it
  (echo.py). While he talks, talking over him only counts if it's words he isn't saying, a few
  checks in a row, and louder than his own echo. After he stops he waits half a second, and anything
  heard that's just a bit of his own reply is ignored (or cut off the front of what you said).
  "Stop", "Jarvis" and claps always interrupt. Tuning: "echo_gap" (0.5), "barge_in_checks" (3),
  "barge_in_louder" (1.2, lower it if talking over him stopped working), "echo_guard": false to turn
  the after-speech checks off.

## One brain for everything (skills and add-ons)
Everything Jarvis can do is also a **skill** his brain (Claude) can use as a tool, so any wording
works, and one sentence can do several things: "play my gym playlist, remind me in 20 to stretch,
and tell the squad I'm off at 9". Quick everyday commands still go straight through (instant);
anything else, or several things at once, goes to his brain, which picks the skills and chains them.
- Say **"use your brain for everything"** to send every command through his brain (a second or two
  slower, but smartest), or **"use quick commands"** to go back. ("tool_brain": false turns skills off.)
- **Skills** live in the skills folder, one file per area (music, organize, computer, me, social). A
  new file there is a new ability: see skills/__init__.py for the 5-line format. "Upgrade yourself to
  ..." adds new skills too. Say **"what skills do you have"**.
- **Add-ons:** free MCP servers other people built. Copy addons.example.json to addons.json, switch
  on the ones you want, and restart him. His brain can then use their tools.
- Discord: his brain can post for you ("tell the squad ..."). It posts in the channel you name, or
  "discord_channel" in config.json, or where you last talked to him.

## Perfect memory
Every conversation you have with him (spoken or typed) is saved on this computer in memory.db and
searchable by meaning, using a tiny free model (about 30 MB) that runs on your computer. Months later
"that pizza place we talked about" still finds "Tony's on 5th has the best slice". When you ask him
anything, the most relevant old conversations are given to his brain, and it can search further
itself. Friends in group chat mode are never saved. Say "forget what we said about X" to delete
those conversations. "perfect_memory": false turns it off.

## A more human voice
His brain can mark how a line should sound, and he plays it in his own (Kokoro) voice: he can
**whisper** a secret, **laugh**, **chuckle** or **sigh**, get **hyped** for a win, or go **sarcastic**
for a roast. It all runs on this computer and needs no downloads. He only does it now and then, when
the moment calls for it. Other voice engines just skip it. "expressive": false turns it off.

## Hears you better
A while after he's started (when he's idle and has heard you once), he tries a bigger free speech
model, Whisper large-v3-turbo, that runs on this computer. On an Apple Silicon Mac it runs on the
graphics chip (MLX); on Windows it runs on the processor. He times it on something you actually said
and keeps it only if it's still quick (about 1.6 seconds or less); otherwise he stays on small.en and
deletes the download. It needs about 6 GB free on a Mac (5 GB on a PC) before it downloads anything.
Say **"how's your hearing"**, **"upgrade your hearing"** (try again, e.g. after freeing space) or
**"use your old hearing"**. "hearing_upgrade": false in config.json turns this off.

## "Did you mean?"
If he isn't confident he heard a command right, he checks first: "Sorry, did you say close Discord?"
If two apps fit what you said, he asks "Open Discord or Spotify?"; a weak match gets "Did you mean
Safari?". Answer yes, no, the name, or "the second one", or just say something else. Questions go to
his brain, which asks a quick question back when it can't tell what you meant. "did_you_mean": false
turns it off; "unsure_below" (default -0.8) sets how unsure he must be (lower = asks less).

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

**Night school:** between 1 and 5 AM, if he's idle, he picks one thing he struggled with that day
(from his log and his missed requests) and builds the fix on a separate copy of himself, checks
it, and leaves your real Jarvis untouched. In the morning he asks: "Overnight I taught myself to
... Want to keep it?" Say yes and he adds it and restarts ("undo that" still works); say no and he
throws it away. Nothing is kept without your yes. Ask **"what did you learn last night?"** any
time, or say **"night school off"** / **"night school on"**.

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
teammates' work passed along. A critic scores each part on its own (for a store: photos, writing,
price, look and facts; otherwise accuracy, completeness, specificity and usefulness) and sends only
the parts under 8 back, up to 3 rounds. For a store, a picky shopper first reads it like a real buyer
and the store builder fixes whatever would stop them buying. Before you see anything, the fact
checker double-checks the facts, numbers and prices (on the web) and the team fixes what's wrong.
If a job needs know-how nobody has, the lead hires up to 2 new specialists just for that mission
(they get a NEW HIRE desk in the pixel office). After every mission each worker writes one thing to
do differently next time (team_lessons.json) and reads it before its next job. Then the lead writes
a report. It all happens in the background, so keep talking to him; he tells
you when they're done (not between 11pm and 8am, "quiet_hours").
- **"How's the team doing?"**, **"read me the report"**, **"who's on your team?"**, **"call off the team"**.
- If I restart while the team is working (an update, a crash), I now wait for them first. If a project still gets cut off, I say so, and **"restart the team project"** starts it again.
- **"Show me the team"** (or the AGENT NET link at the bottom of the display) opens the live agent
  network at http://localhost:8765/team: a little pixel office with JARVIS (a pixel AI core) in the
  middle and the team lead, critic and all 16 specialists as pixel people at their desks. They type
  while really working, walk the work over when they hand it on, walk to the web terminal, file
  cabinet, toolbox or StoreForge kiosk when they really use those tools, and the critic walks round
  the finished desks while it reviews. Their faces show how it's really going: narrowed eyes while
  working, a sweat drop when a tool fails or the critic sends their part back, a smile when done, and
  arms up with confetti when the critic scores the mission 8 or more. Also glowing lines for who handed what to whom, dashed lines for which task needs
  which, a live activity feed, the mission's progress, and a task pipeline. Click any agent to see its
  task, tool calls, output and past missions. It's drawn only from the team's real events (saved as
  events.jsonl in each project folder); nothing on it is simulated.
- Store builds use only real product facts: the store builder reads the product page's specs first
  (size, material, capacity...), and anything it couldn't confirm is taken out or marked [confirm].
- The team learns from your own edits: what you change in a store in StoreForge (price, title,
  wording, colours) is noted, and the next store is done your way.
- Reports are in the **team** folder here (and "Jarvis Team" in Obsidian if you use it).
- The agents can only search the web, think, and write files in their own project folder. They
  can't send, post, buy, delete or change your stuff or Jarvis. Anything like that is listed in the
  report under "Needs your OK", and a change to Jarvis himself is only made if you say yes.
- Each project uses your Claude plan like several questions do. "team": false turns it off.

**Fixes his team's errors:** when an agent hits an error (a StoreForge step fails, StoreForge won't
start, an agent comes back empty, or a whole mission crashes), Jarvis tells you, has Claude Code find
the cause and fix it, then tries that step again. Fixes to his own code are checked like a voice edit
and rolled back if they break anything, so "undo that" works. In the StoreForge folder he can only run
npm install/rebuild/build and prisma generate. Things only you can do, like confirming photo rights,
aren't retried over and over: he just asks you. At most 2 repairs per mission. Say **"what did you
fix?"**, or **"self-repair off"** / **"self-repair on"**.

### StoreForge (your Shopify store builder)
If StoreForge is on this Mac (~/storeforge), the team gets a **store builder** agent that uses it:
"get the team on building a store for <product link>". It can start a store project, run "generate
everything", follow progress, read the listing and checklist, redo a section, build the export ZIP
and dry-run a Shopify push. It talks to StoreForge on this computer only (storeforge_mcp.py), and
starts StoreForge itself if it isn't running.
- Two things always wait for you: confirming you have the right to use a product's photos and text,
  and pushing to your real Shopify store (as a draft). Jarvis asks "Should I go ahead?" when the
  team's done, or say **"what needs my OK"** any time.
- Say **"open StoreForge"** or **"show me the store"** to see the latest store project in StoreForge.
  Jarvis also opens it himself when you say yes to the rights question, and when the team finishes a store.
- StoreForge's AI runs on your Claude plan through Claude Code, so no API key and nothing extra to pay.
- Pushing to Shopify needs setup once: double-click **Connect StoreForge.command** and give it
  your store's myshopify.com address and Admin API token. That part is optional and free.

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

## Shorts Clipper (turns long videos into YouTube Shorts)

Jarvis finds the best moments in a long video, edits them into vertical 1080x1920 Shorts with
captions, writes titles and descriptions, checks the quality, and (once you allow it) uploads them.

**Say:**
- "Clip my last stream" (your newest Twitch VOD)
- Copy a link (or a video file), then "find the best moments from this podcast" or "generate five Shorts from this video"
- "Show me the clips awaiting approval" (opens the dashboard), "approve clip 12", "redo clip 12", "reject clip 12"
- "Upload the approved clips" (he asks first), "turn on public publishing" (he asks first)
- "Process the latest videos from my approved sources", "how's the clipper doing", "show me today's processing report"
- "Use my premium caption style", "prioritize clips with strong opening hooks"
- "Analyze which of my last 20 Shorts performed best", "use the highest performing editing style"
- "Pause clipping", "resume clipping", and **"emergency stop"** (no new jobs, no uploads, until "resume clipping")

**Dashboard:** http://localhost:8765/clipper (or the CLIPPER link on the HUD): overview, queue with
retry/cancel, clip library with previews, approval center (edit titles, approve, reject, make another),
analytics, and settings (permissions, caption style, lengths, posting times, sources, brand colours).
You can also drop video files into `clipper_data/inbox` and they're picked up automatically.

**What it will clip:** only videos you're allowed to use: your own Twitch VODs (your stream-mode channel
is an approved source automatically), your own YouTube channel, files you add, Creative Commons (CC BY)
videos (credited in the description), and creators you mark "I have permission". Anything else waits
for your OK. Protected, private or members-only videos are never downloaded.

**How it works (all on your computer, nothing to pay):** yt-dlp gets authorized videos (long VODs: just
the audio first, then only the clip sections), faster-whisper (or Parakeet) makes a word-timed transcript,
Claude (your plan, through Claude Code) picks and scores moments and double-checks each clip's context,
OpenCV follows whoever's talking (or uses a streamer facecam-over-gameplay layout), and FFmpeg renders
with captions, jump cuts and -14 LUFS sound. Every clip passes a quality check before it can be uploaded.
Jobs survive restarts and updates. Command line: `.venv/bin/python -m clipper --help`.

**Connect YouTube (one time, free):** double-click **Connect YouTube** and follow the steps it shows:
make a Google Cloud project, enable *YouTube Data API v3* and *YouTube Analytics API*, set up the consent
screen (add yourself as a test user, then "Publish app"), create a *Desktop app* OAuth client and download
its JSON. The script finds it in Downloads, opens Google's sign-in, and stores the tokens only on this
computer (`clipper_data/secrets`, never in git). Private uploads can then be switched on; public stays off.

**Good to know:**
- Google keeps videos uploaded by an app it hasn't audited locked to private. Private and unlisted
  work right away; to publish publicly from Jarvis, request Google's free YouTube API audit for your
  Cloud project (https://support.google.com/youtube/contact/yt_api_form). Jarvis tells you if a clip got locked.
- The free API quota allows about 6 uploads a day (each costs 1,600 of 10,000 units). Jarvis tracks it.
- Settings: `config.json` "clipper" (worker, disk limits, whisper model, retries); per-channel settings
  live in the dashboard. Background music is only used from a folder of licensed tracks you choose.
- Tests: `.venv/bin/python -m pytest tests` (YouTube is tested against a fake Google server).
