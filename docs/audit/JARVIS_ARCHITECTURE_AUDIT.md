# Jarvis architecture audit

Date: 2026-11-24. Code inspected at `main` 4c985d0 (PR #82). The PC's live state was read the same morning,
read-only (Windows PC, HEAD 2eae4a4, which is the updater's own commit over 4c985d0, with a clean git status).
Nothing was changed for this audit.

## 1. What Jarvis is

Jarvis is one Python program (`jarvis.py`, about 1,700 lines) with about 60 helper modules and three larger
packages. It runs on two computers.

| Computer | Role today | Hardware |
|---|---|---|
| Windows PC (DESKTOP-TB86SLS) | Voice assistant, plus the **only** computer running the YouTube business (`studio/`) and the clipper | i7-7700K 4c/8t, 15.9 GB RAM, RTX 2060 6 GB (driver 617.14), C: SSD 19 GB free of 118, D: HDD 1,666 GB free of 1,862, page file on C: plus 8-16 GB on D: |
| MacBook Air (M1, 8 GB) | Voice assistant. The studio was handed over to the PC on 2026-11-23 | M1, 8 GB RAM, about 26 GB free |

Both computers install updates by pulling `main` from GitHub (`updater.py`). After each update, a self-check
(`selftest.py`) runs and rolls the update back if it fails.

## 2. Layers

```
                 +-------------------------------------------------------------+
  you  --voice-->| ears.py (clap / "Hey Jarvis", Whisper) -> clarify.py, echo.py|
  type box ----->| jarvis.py  main loop, intents, modes, personality.py          |
                 |   brain.py (Claude Code CLI on Connor's plan, streaming)     |
                 |   local_brain.py (Ollama qwen2.5:3b) + vecstore.py (Chroma)  |
                 |   skills/ (tool brain: plug-in tools) + jarvis_mcp.py        |
                 |   voice.py / expressive.py (Kokoro, voice tags)              |
                 |   memory.py, recall.py (memory.db), learn.py, predict.py     |
                 |   display.py (HUD web server :8765, /clipper, /studio)       |
                 |   keeper.py (single copy + watchdog), updater.py, selftest   |
                 +--------+----------------------+-----------------------------+
                          |                      |
                 +--------v-------+     +--------v--------------------------------+
                 | clipper/       |     | studio/  (YouTube Shorts business)       |
                 | Twitch VOD ->  |     | jobs worker thread inside the Jarvis     |
                 | Shorts         |     | process; tools run as subprocesses in    |
                 | clipper.db     |     | tools/<env>; studio_data/studio.db       |
                 +----------------+     +------------------------------------------+
                 workforce.py (AI agent team), storeforge*.py, nightschool.py,
                 repair.py, discord_bot.py, twitch.py, spotify_control.py, oura.py, obsidian.py
```

## 3. Operating environment (verified on the PC)

- **OS:** Windows 10 Home 19045. Python venv in `%USERPROFILE%\Jarvis\.venv`.
- **Starting Jarvis:** only from the Desktop shortcut. There is no Task Scheduler entry and the Startup
  folder is empty, which is what Connor asked for. So the autopilot runs only while Jarvis is open.
- **Storage split:** app code, venvs and `tools/` stay on C:. Videos, models and outputs go to D:\Jarvis.
  `studio_data\videos` is a junction to D:. Models use 16 GB on D:.
- **Logs:** `jarvis.log` in the Jarvis folder. The studio worker's autopilot lines are **not** written there
  (gap G7).

## 4. External services and how each is reached

| Service | Module | Auth | Cost |
|---|---|---|---|
| Claude | `brain.py`, `studio/claude.py`, `clipper/llm.py` | Claude Code CLI login (Connor's plan), no API key | $0 extra. Plan limits are shared. Studio cap is 60 calls a day |
| YouTube Data API v3 | `studio/publish.py`, `studio/ytpublic.py`, `clipper/youtube.py` | OAuth (business channel) + API key | Free quota |
| YouTube Analytics API v2 | `studio/analytics.py` | Same OAuth, read-only scope | Free |
| Google Drive | `studio/drive.py` | `drive.file` scope only | Free 15 GB. **Never connected on the PC** |
| Wikipedia / Wikimedia | `studio/script.py`, `studio/art.py` | none | Free |
| Pexels | `studio/footage.py` | Free key (not set) | Free. Off |
| Ollama | `local_brain.py` | local | Free |

## 5. Dependency graph (studio)

```
__main__ / skills/studio.py / display.py(/studio)
        |
        v
   autopilot.py --> topics.py --> niche.py (pillars) --> ytpublic.py (public data)
        |                |--> claude.py --> guard.allow("claude.ask") + budget
        v                v
     jobs.py (STAGES, claim/heartbeat/retry/recover) --> pipeline.py (stage handlers)
                                  |
   script.py (write 3 drafts -> screen -> judge -> fact-check review -> revise) --> lessons.py
   narration.py (Kokoro | Chatterbox via engines.run) --> clipper/media.py (ffmpeg)
   video.py (timings, visual plan, scenes) --> art.py (SDXL) -> enhance (ESRGAN, depth)
                                           --> motion.py, edit.py, sound.py, footage.py, music.py
                                           --> engines.render_remotion | Manim | classic Pillow
   qa.py (21 checks) --> cover.py --> publish.py --> ytauth.py --> guard.verify_destination
                                                \--> playlists.py, drive.py, timing.py
   analytics.py --> eligibility.py, timing.py, lessons.py, dashboard.py
   guard.py (allow-list, scopes, channel lock, $0 tool registry, audit)   db.py (SQLite WAL)
   health.py (start-up checks, resources, notices)   handover.py (Mac -> PC move)
```

## 6. Component classification

Legend: **Full** = implemented, tested and in use; **Partial** = works but parts are missing; **Broken** =
does not do what it says; **Duplicate** = overlaps another part; **Unused** = present but never runs.

### Core assistant

| Component | Status | Notes |
|---|---|---|
| jarvis.py, ears, voice, brain, display, keeper, updater, selftest | Full | Working on both computers |
| local_brain.py + vecstore.py | Full | Needs "Install Local Brain" once |
| workforce.py, repair.py, storeforge*.py | Full | Not part of the YouTube business |
| oura.py, setup_oura.py | Unused | Connor cancelled connecting it |
| predict.py | Partial | `habits.json` on the PC is empty, so no routines have been learned yet |
| nightschool.py | Full | The last run reported "0 misses, 6 errors, 1 ideas" |
| clipper/llm.py vs studio/claude.py | Duplicate (deliberate) | Two wrappers around the same CLI with separate budgets. Acceptable, but a shared core would remove drift |
| learn.py / clipper/selftune.py / studio/lessons.py | Duplicate pattern (deliberate) | Three learning stores, one per area. Fine, kept separate on purpose |

### YouTube business: see YOUTUBE_MODULE_AUDIT.md for detail

| Component | Status |
|---|---|
| guard.py (allow-list, scopes, lock, audit) | Full. See SECURITY_AUDIT.md for hardening |
| niche.py (Top-10 report, choose once) | Full |
| topics.py (scoring, dedup) | Partial: duplicate detection is word overlap only |
| script.py (3 drafts, judge, fact-check) | Full, but it is **the main failure point**: 10 of 14 videos stopped at the script stage |
| narration.py, video.py, art.py, music.py, Remotion, Manim | Full |
| motion.py, edit.py, sound.py, footage.py, ESRGAN | Partial: built and tested in the cloud, switched **off** on the PC until a sample is approved |
| qa.py | Full (21 render checks). It does not produce a weighted score |
| publish.py | Full for upload and resume. Partial for verifying the result (see Y5) |
| analytics.py | Partial: only Data API counts so far. No retention rows yet (the only video is private) |
| eligibility.py | **Broken thresholds** (see Y8). Has never run (0 rows), because it waits for a public video |
| experiments table | Unused: the table exists, but no code writes to it |
| drive.py | Unused on the PC: never connected |
| dashboard.py | Full, with one misleading label (see Y6) |
| health.py | Partial: no GPU or VRAM reading, and CPU load is always unknown on Windows (`os.getloadavg` does not exist there) |
| handover.py | Full: used once, Mac to PC |
| policy monitoring | Missing |
| software registry | Partial: `tools` table has 15 rows. The engines added since (SDXL, ACE-Step, ESRGAN, Depth Anything, Pexels) are not in it, and there are no version, source or fallback fields |

## 7. Tests

- 41 test files, 368 test functions. Baseline run on 2026-11-24 in the cloud: see IMPLEMENTATION_STATUS.md.
- GitHub Actions runs `compileall` and the whole suite on every push (`.github/workflows/tests.yml`).
- **No tests** for: `jarvis.py` main loop, ears and voice hardware paths, `display.py` HTTP handlers (except the
  dashboard data), `workforce.py`, the Windows-only parts of `engines.py` (commit-limit memory, junctions), and
  real GPU workers. These need real hardware and are covered by the PC's self-check and samples instead.

## 8. Documentation vs code

| Doc says | Code does |
|---|---|
| `studio/narration.py`, `guard.ACTIONS` and others say "on this Mac" | The studio runs on the Windows PC since 2026-11-23 |
| `eligibility.py` says it compares with "YouTube's published thresholds" | The thresholds in it do not match YouTube's pages (Y8) |
| Memory note says "#7 first private test Short uploaded" | The database marks it `published` and the dashboard says "Live on YouTube" |
| `guard.py` docstring: "every tool the studio uses is registered" | Five newer engines are not registered |
