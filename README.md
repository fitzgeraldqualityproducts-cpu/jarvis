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
- **Menu bar icon:** shows his status, open the display, mute / unmute, quit
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
- The menu bar icon is a tray icon by the clock (click: display, right-click: mute / quit).
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
- His snapshots (the copies he keeps so an update or a voice edit can be undone) never include your
  settings, the YouTube business folder (its sign-in, brand and videos) or video and audio files,
  so undoing something can't delete or roll them back.

## Claude usage
Work Jarvis does on his own (the clipper, the YouTube studio, night school, routines, self-repair)
shares one daily limit of Claude calls, "claude_background_per_day" in config.json (150; 0 = no
limit), so it can never use up your plan. Answering you is never counted or blocked, and team
missions you ask for are counted but never blocked. When Claude says your plan's limit is reached,
background work waits an hour.

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

## Light on your Claude plan
Every question Jarvis sends to Claude Code now loads only the tools that question can use, instead of
Claude Code's whole toolbox, skills list and your own MCP servers. Same model, same prompt, same
answers, about 3 to 4 times fewer tokens per question. Simple things ("what time is it", "what's the
date", "thanks") he answers himself without asking Claude at all, and the self-improvement loop waits
for a quiet 15 minutes and thinks at most 3 times a day. Older Claude Code that doesn't know the new
options is spotted and gets the old way. "lean_claude": false in config.json turns it off.

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

## Mute

The **mic button** under his status on the display (or press **M** there, or **Mute Jarvis** in the menu
bar / tray) mutes him: he stops listening completely (no wake word, claps or "stop") and stops talking
out loud, cutting off mid-sentence if he's speaking. Anything he'd say shows up in the Comms panel, and
typing to him still works. Timer and reminder alarm sounds still ring. You can also say "mute yourself"
or "mute for an hour". To unmute, click the mic again, press M, use the menu, or type "unmute" (he can't
hear you while muted, on purpose). He stays muted through restarts and updates until you unmute.

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

**Dashboard:** http://localhost:8765/clipper (or the CLIPPER link on the HUD), a dark dashboard with:
- **New video:** paste a YouTube/Twitch link (checked as you type) or drag in a video file; pick a preset,
  a clip length (the preset's, 15–30, 30–45, 45–60, 60–90, 90–180 seconds) and how many clips.
- **Presets** (per video, or a default in Settings): Balanced, Viral potential, Educational, Entertainment,
  Storytelling, Podcast. Each one really changes things: what Claude looks for, how the score weighs hook,
  story, clarity and so on, which kinds of moments get a small bonus, the clip length, and the editing
  (pause tightening, zoom/punch-in strength, caption style). Your own settings always win over a preset.
- **Projects:** real progress through each step (getting the video, audio, transcript, understanding,
  ranking, framing, rendering, quality check), with Retry now / Cancel.
- **Moments, ranked:** every candidate with a 0–100 score (hook, entertainment and context shown
  separately), why it was picked, its kind of moment (funny, story, reaction...), sorting by viral,
  funniest, most emotional, storytelling, shortest, longest... and "Make selected" for any moment you
  want as a clip. Each moment also shows its opening line, its strongest line (and when it's said), who it's
  for, and its weak spots (the reviewer's note plus measured ones like a slow start or long pauses).
  Turned-down moments are listed with the reason. Scores are estimates, not view predictions.
- **Transcript:** the whole video's transcript on the project page, searchable. Click a sentence to start
  a clip there (the source video jumps there too while it's still on disk), Shift+click to end it, then
  "Make clip from this": it's measured, scored, titled and rendered like any other moment.
- **Clips:** preview, search (titles and what's said), download, multi-select export (to Movies/Videos ›
  Jarvis Shorts) as YouTube Shorts, TikTok or Reels (the same 1080p file, with a warning if a clip is too
  long for that app) or a smaller 720p/540p copy (re-encoded and checked before it counts as exported),
  approve, delete.
- **Batch:** "Add several videos at once" takes many links or file paths (one per line), and you can drop
  several files. Each becomes its own project in the queue; the same video is never added twice.
- **Editor:** trim on a timeline (snaps to words), caption style, font, size, colours, outline, shadow,
  position, animation, fix caption words, layout or hand framing, dead-air and "um" removal, noise
  reduction, music level, sound effects, zooms and punch-ins, editing intensity, opening text,
  titles/descriptions, re-render.
  If a re-render fails, the previous version is kept. Undo/redo (Ctrl/⌘+Z, Ctrl/⌘+Shift+Z), Space to
  play/pause, Ctrl/⌘+Enter to re-render, and a playback speed control.
- **Variations:** from any clip, make new clips with a stronger opening (starts on its strongest line),
  tighter pacing, different captions, different framing, a longer ending, or a plain copy. They sit next
  to the original ("Versions of this moment"); the original never changes.
- **Version history:** each re-render keeps the previous file (the last 2 versions per clip), so you can
  preview an earlier version and go back to it.
- **Approvals, Analytics** (predicted score vs. real results, kept separate) and **Settings**.
You can also drop video files into `clipper_data/inbox` and they're picked up automatically.

**Learns from you and from its mistakes:** it watches what you approve, reject (and why), regenerate,
delete and change in the editor. Once it sees the same thing a few times it adjusts: your caption style,
layout and editing switches, clip length, starting right on the hook, spellings you keep fixing (also given
to Whisper), and which kinds of moment you keep. Analytics › "What I've learned from you" lists every
lesson with its evidence and an Undo; changing a setting yourself always wins. A clip that fails a fixable
quality check is rendered once more with a safer setting, and the rest of that video's clips start with it.
Crashes in the clipper's own code go to Jarvis's self-repair mechanic (checked and reversible). It also
times every step: re-renders from the editor reuse the face analysis, and the encoder speed is picked from
how fast your computer really is (same quality setting). Say "what has the clipper learned" or "forget
what the clipper learned".

**Keeps your disk from filling up:** every 6 hours, and straight away when free space gets close to the
4 GB the clipper needs, Jarvis clears old downloaded VODs and audio (3 days after a project finishes),
half-finished downloads and renders, rejected clips (after 7 days), old setup backups that Upgrade Jarvis
leaves behind, and trims huge logs. It only ever deletes inside Jarvis's own folder. Clips waiting for your
review, approved clips, anything on YouTube and your own video files are never deleted, and projects you're
still reviewing keep their video so you can edit them. The dashboard's **Disk space** card shows what's
using space with a **Clean up now** button, or say "free up some disk space" / "how much disk space do I have".

**Caption styles:** Clean, Bold Viral, Minimal White, Gaming, Podcast, Highlighted Keywords, Animated
Pop, Cinematic, plus Kinetic, Educational and Premium. Auto picks one to suit the moment. Keywords
(numbers, emphasised words, names, reactions) are highlighted on purpose, never at random.

**What it will clip:** only videos you're allowed to use: your own Twitch VODs (your stream-mode channel
is an approved source automatically), your own YouTube channel, files you add, Creative Commons (CC BY)
videos (credited in the description), and creators you mark "I have permission". Anything else waits
for your OK. Protected, private or members-only videos are never downloaded.

**How it works (all on your computer, nothing to pay):** yt-dlp gets authorized videos (long VODs: just
the audio first, then only the clip sections), faster-whisper (or Parakeet) makes a word-timed transcript,
Claude (your plan, through Claude Code) picks moments with a setup and payoff, scores them on twelve
things (hook, emotion, story, replay value...), then one review call double-checks each clip's context
and writes its 5 titles, 3 descriptions, hashtags and pinned-comment ideas. Measured checks reject dead
air, repetition and mostly-silent picks. OpenCV follows whoever's talking, the action in gameplay, or a
facecam-over-gameplay layout, and FFmpeg renders with captions, jump cuts, optional noise reduction,
colour and sharpening (only when the picture needs it), music ducked under speech and -14 LUFS sound.
Transcripts are cached, so the same video is never transcribed twice. Every clip passes a quality check before it can be uploaded.
Jobs survive restarts and updates. Command line: `.venv/bin/python -m clipper --help`.

**Connect YouTube (one time, free):** double-click **Connect YouTube** and follow the steps it shows:
make a Google Cloud project, enable *YouTube Data API v3* and *YouTube Analytics API*, set up the consent
screen (add yourself as a test user, then "Publish app"), create a *Desktop app* OAuth client and download
its JSON. The script finds it in Downloads, opens Google's sign-in, and stores the tokens only on this
computer (`clipper_data/secrets`, never in git). Private uploads can then be switched on; public stays off.

**Good to know:**
- Google used to lock videos uploaded by apps it hadn't audited to private. Its upload page now says
  they aren't locked (checked 2026-11-23). Jarvis reads each video's status back, and if one did get
  locked it tells you; the fix is Google's free YouTube API audit
  (https://support.google.com/youtube/contact/yt_api_form).
- The free API quota allows 100 uploads and 100 searches a day, plus 10,000 units for everything
  else. Jarvis tracks each one.
- Settings: `config.json` "clipper" (worker, disk limits, whisper model, retries); per-channel settings
  live in the dashboard. Background music is only used from a folder of licensed tracks you choose.
- Tests: `.venv/bin/python -m pytest tests` (YouTube is tested against a fake Google server).

## YouTube Business Studio (in progress)

A separate module (`studio/`) that will run ONE new, dedicated YouTube Shorts channel: niche research,
original fact-checked scripts, narration, original visuals, a quality gate, publishing through
Google's official API, analytics and learning. It never touches your existing channel, never spends
money, and never posts comments. It's built in phases; the plan is in the Phase 0 audit.

- Off by default. Turn it on with `"studio": {"enabled": true}` in `config.json` (Mac only).
- Its data lives in `studio_data/` (database `studio.db`, backed up daily, never part of an update).
- Status: `.venv/bin/python -m studio status`. Tests: `.venv/bin/python -m pytest tests/test_studio_*.py`.
- Phase 1 (done): database with migrations, job queue with retries and restart recovery, the
  permission guard (allow-list, one-channel lock, $0 tool registry, audit log), health and disk limits.
- Phase 2 (done): narration with Kokoro in a US voice (config "studio" "voice", default am_michael, picked by Connor; Jarvis's own
  voice is unchanged), silence trimming, loudness to -14 LUFS, validation. `python -m studio toolchain`
  lists every tool with its licence; `python -m studio test-short` makes a real test Short and times it.
- Phase 3 (done): niche research. Double-click **Research YouTube Niche** (first time it walks you through
  a free, read-only YouTube API key). It measures recent Shorts and competitors through YouTube's
  official API, researches monetization, sustainability and production with Claude (sources required),
  scores 20 candidate niches with the fixed weights, picks one, defines 3-7 content pillars, and opens
  the Top 10 report (`studio_data/research/<run>/report.html`). Scores are reproducible from the saved
  evidence; once picked, the niche only changes with `--rechoose "<reason>"`.
- Phase 4 (done): the channel. Double-click **Set Up YouTube Channel**: Claude suggests names, each
  handle is looked up on YouTube ("taken" / "not found"), you pick one, and Jarvis draws the logo
  (800x800) and banner (2048x1152, text inside the 1235x338 safe area) and writes the description
  (with the AI-narration disclosure) and brand guide in `studio_data/brand/`. YouTube has no API to
  create channels, so you create it by hand in a NEW Google account, then double-click **Connect
  YouTube Business**: it protects your existing channel by handle, signs in with only upload,
  read-only and analytics-read permissions (own token in `studio_data/secrets/`), and locks the studio
  to that one channel. It refuses your existing channel. `python -m studio verify` re-checks it.
- Phase 5 (done): video ideas. Double-click **Find Video Ideas** (or `python -m studio ideas`, `--all`
  for every pillar). For the pillars with the fewest ideas, Claude (web search, on your plan) proposes
  evergreen topics, recent developments and real audience questions, each with 2+ sources; the top
  Shorts for the pillar (official API) are shown only as gaps to beat. Every source link is opened once
  and graded by publisher. Ideas are scored on 10 factors (fixed weights, reproducible), banned areas,
  copyright risk, weak sources and repeats are rejected, and everything is kept in the idea database so
  nothing is made twice. `python -m studio next` shows what Jarvis would make next: pillars take turns,
  trending topics stay under 30% and expire after 10 days. With the studio on, ideas top up by
  themselves when fewer than 12 are ready.
- Phase 6 (done): scripts and narration. Double-click **Write a Test Script** (or `python -m studio script`).
  Claude writes 5 hooks and a 60-105 word script (25-45 s) using ONLY the idea's checked sources, with
  every worked-out number given as a calculation. A separate Claude pass with web search fact-checks every
  line against its source and scores the hooks; Jarvis re-runs every calculation itself, picks the best
  hook that passes the hard rules (short, concrete, no clickbait) and refuses remakes of earlier scripts.
  One revision is allowed; if it still fails, the idea is dropped. The passed script is narrated in the
  channel voice and the audio is checked. The page `studio_data/videos/<id>/script.md` shows the script,
  calculations, verdicts and other hooks. With the studio on, the worker runs these as resumable stages
  (researching, scripting, fact_checking, generating_audio) and waits at rendering until Phase 7.
- Phase 7 (done): the video. Double-click **Make a Test Video** (or `python -m studio video`). Claude plans one
  scene per line from fixed templates (headline, big number, equation, comparison bars, scale circles,
  steps); Jarvis draws them itself from shapes and text (each recorded as an original asset with a
  checksum), times every word with Whisper matched to the known script so captions are exact, and renders
  1080x1920 at 30 fps with a slow push-in, crossfades, a progress bar, captions and the narration. It
  writes the upload details (title, description with the sources and the AI-narration note, tags,
  Education category) and runs the QA gate: format, length, loudness, blank frames, fact-checked script
  and sources, every caption word shown and in sync, visuals and captions in the safe area, licences,
  title rules and remake check. Only a full pass makes a Short "ready". Nothing is uploaded.
- Covers (2026-11-23): every Short also gets its own cover, `cover.jpg`, in the layout of the HussUniversity
  Canva template (big hook with one word in amber, a glowing panel with a checked calculation, the pillar
  pill and the channel tag; the bottom fifth stays clear for YouTube's buttons). After the upload Jarvis
  sends it as the video's thumbnail, once. If YouTube refuses (custom thumbnails need a phone-verified
  channel: youtube.com/verify), the upload still counts and the reason is saved with the video.
- Phase 8 (done): publishing, official YouTube Data API only. Double-click **Upload Test Video (Private)**
  to upload the newest finished Short as private after you confirm. Every upload first checks the signed-in
  channel is the locked business channel, needs a full QA pass, and is resumable: the session is saved
  before any bytes go, so a crash or restart continues the same upload. Each video carries a private tag,
  so an upload whose reply was lost is found on the channel instead of being sent twice. Afterwards Jarvis
  reads the video back (processing, privacy, channel). `python -m studio publish-mode` sets how finished
  Shorts go out: manual (default, they wait), private (uploaded privately for review) or public (only
  after typing PUBLIC: uploaded private with a publish time, about 3 a week at 5 PM Central, at least 40
  hours apart, then confirmed public). Note: Google may keep uploads from an unaudited API project
  private; the first private test shows what applies to this project.
- Phase 9 (done): analytics and learning, read-only. Every 6 hours (studio on) Jarvis reads each studio
  Short's numbers from YouTube itself: views and likes (Data API) and, once YouTube has them (1-3 days),
  watch time, average percentage viewed, shares, subscribers gained and how many were still watching at
  about 10% of the Short (the hook). Missing numbers are saved as missing, never guessed. After 3 days
  public each Short gets a performance index (50 = typical for this channel); a pillar with 3+ measured
  Shorts nudges idea picking by at most 6 points, and the idea finder hears what worked. A weekly report
  goes to studio_data/reports/ and Jarvis tells you about it. Double-click **Show YouTube Stats** or ask
  "how's the YouTube channel doing?". Revenue is never read.
- Phase 10 (done): Partner Program progress, read-only. Once a day after something is public, Jarvis
  compares subscribers, public uploads in 90 days and Shorts views in 90 days with YouTube's published
  thresholds (fan funding and ad revenue; the 2027-02-01 change applies by itself). Shorts views are an
  ESTIMATE from YouTube Analytics (YouTube's own count of qualifying views isn't available to apps), and
  projections are labelled. It's in the weekly report, Show YouTube Stats, and "how close are we to
  monetization?". If a tier is met Jarvis tells you; applying, terms, AdSense and money stay with you.
- Phase 11 (done): always on. With the studio on, the autopilot keeps the line stocked by itself: one
  Short in production at a time, at most 3 started a day, and only a small buffer of finished Shorts
  (1 in manual mode, 2 otherwise) so your Claude plan isn't spent on a pile. If 3 Shorts in a row fail
  their checks it stops and tells you (`python -m studio autopilot on` resumes, `off` turns it off).
  While a stage runs, the Mac is kept from idle sleep (macOS caffeinate; closing the lid still sleeps).
  Restart drills are tested: a crash mid-render resumes at rendering, and a stop mid-upload continues
  the same upload session (never a second upload). Starting Jarvis at login is your choice:
  run **Install Jarvis Extras** (it asks) or add "Jarvis at Login.command" in System Settings > Login Items.
- Learning from mistakes: every time a check stops something (a script too long or not original, a
  claim the sources don't back, an idea with thin sources, a failed video check), Jarvis records why in
  studio.db (lessons). The script writer, the idea finder and the visual planner are told the recent
  lessons (firmer once one happens 3 times), ideas from areas that keep failing are picked a little
  less (at most 8 points, fading after 30 days), and ideas sure to fail (fewer than 2 working sources)
  are skipped before any of your Claude plan is spent. `python -m studio lessons` and the weekly report
  show what was learned. No extra Claude calls.
- Google Drive backup: double-click **Connect Google Drive** once, pick the Google account whose Drive
  should hold the backups and press Allow. Jarvis asks for one permission only (drive.file): it can see
  and change only the files it puts there, nothing else in your Drive. If Google says the Drive API is
  off, the window opens the Enable page for Jarvis's own Google project; press Enable and it carries on.
  From then on, every 30 minutes, finished Shorts (video, narration, thumbnail, script, metadata) and the
  weekly reports are copied to "Jarvis YouTube Backups" in that Drive, and each copy is checked against
  the Mac's file (size + MD5). Mac copies of the big files are removed only when the Short is on YouTube,
  its Drive copy is verified again right before deleting, and it went up 7 days ago or more (sooner when
  disk space is low). Shorts waiting for upload are never removed. It stops before Drive gets within
  1 GB of full and tells you. **Back Up Shorts to Drive** runs it now; `python -m studio drive restore <id>`
  brings a Short's files back. Ask "are my Shorts backed up?". Free: Drive API + your free 15 GB.
- Live dashboard: say "show me the YouTube dashboard" or double-click **Open YouTube Dashboard**
  (http://localhost:8765/studio). An assembly line of nine stations (Idea, Script, Fact-check, Voice, Video,
  Quality check, Ready, Uploading, Live): every Short sits at the station it's on, with how long it's been
  there, retries and failures shown on the card. Channel numbers, Partner Program progress, recent uploads,
  what Jarvis did lately and health sit around it. It refreshes every 4 seconds and only reads; it never
  changes anything. If Jarvis isn't running, the double-click serves the page itself (port 8766).
