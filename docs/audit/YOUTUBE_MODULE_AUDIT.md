# YouTube module audit (`studio/`)

Date: 2026-11-24. Code at 4c985d0. The live data was read from the PC's `studio.db`, opened read-only.

## 1. The live channel right now

| What | Value (PC, 2026-11-24 about 09:30 CT) |
|---|---|
| Channel | HussUniversity, `UCIfBUEhiNl78bB_MoeE225A`, locked |
| Protected (never publish) | `UCamLvdxlcfg3YqISoj4jlcA` (Connor's existing channel), from config |
| Mode | publish_mode **public**, autopilot on, max 3 new a day, Claude cap 60 calls a day |
| Engines on | voice chatterbox, align whisperx, visuals manim, compose remotion, images sdxl, music acestep, look hud |
| Engines off (from #82, waiting on a sample) | sharpen, motion, edit, sfx, footage |
| Videos (14) | 1 published (#7, **still private** on YouTube, 2 views), 1 scheduled (#12, public at 12:00 CT today), 2 ready (#13, #14), 5 failed, 5 rejected |
| QA gate | 82 check rows in 14 days, all passed. Every stop happened **before** render |
| Lessons, last 30 days | 30 stops: unsupported claim 14, originality 10, wrong fact 4, missing value 1, thin sources 1 |
| Claude use | 11-23: 39 calls (17 fact-check, 14 script). 11-24 so far: 4 calls |
| Analytics | Data API counts every 6 h. No Analytics API (retention) rows yet |
| Eligibility | 0 rows. Never run, because it waits for the first public video |
| Backups | Only the handover copies from 11-23 18:07. Nothing newer. Drive backup never connected |

**The headline finding:** the line is limited by the script stage, not by visuals. 10 of 14 videos never
reached render. 14 of 30 stops were claims the reviewer could not match to a listed fact. Another 10 were
"originality 5/10" rejections, which means the topic angle was too ordinary rather than wrong. The QA gate has
never failed a render.

## 2. How one Short flows through the system

```
autopilot.tick (inside Jarvis, every few minutes, if not paused and under 3/day)
  1 topics.discover      Claude (web) proposes ideas inside the chosen pillars, 3-4 facts each
                         from different reputable sites -> topics.score -> is_duplicate (word overlap)
  2 jobs.create          videos row, idem_key, state "queued"
  3 script.write         ONE Claude call returns 3 drafts (JSON), each line tagged with a beat
    script.pick_draft    code screens drafts (calc, hook, length, stock phrases), then a no-web judge
                         call picks one; losing drafts become lessons
    script.review        Claude WITH web search checks each line against the listed facts F1..Fn
                         -> verdicts saved in `sources`; originality score; rehook score
    script.revise        up to N fix rounds ("fix lines"); still failing -> Permanent -> lessons
  4 narration            Chatterbox (or Kokoro fallback), per-beat delivery, loudness
  5 align                whisperX word times (Whisper fallback)
  6 video.plan           Claude visual plan per scene (type, numbers, picture prompt, footage search)
    art / music          SDXL-Turbo picture per scene, ACE-Step music (GPU, one heavy job at a time)
    render               Remotion (Manim / Pillow fallbacks) -> 1080x1920 H.264/AAC
  7 qa.check             21 hard checks (file, loop, loudness, captions, safe area, licences, sources...)
  8 cover                per-Short cover
  9 publish.schedule     timing.best_slot -> state "ready" / "scheduled"
 10 publish.upload       guard.allow -> qa passed -> ytauth.whoami -> guard.verify_destination
                         -> resumable upload -> reconcile on lost answers -> verify on YouTube
 11 playlists, drive     add to the series playlist; Drive copy (not connected)
 12 analytics            every 6 h; timing and lessons learn from it
```

Every stage saves its result before the next one starts. `jobs.recover()` picks up stale jobs after a
restart, using heartbeats.

## 3. Findings (Y = YouTube module)

| # | Finding | Severity | Evidence |
|---|---|---|---|
| Y1 | The script stage throws away most of the work. 10 of 14 videos stopped there, and each one spent 4-8 Claude calls | High | PC errors #1, #8-#11; lessons counts |
| Y2 | Facts are gathered **before** the angle is written. The writer then adds natural claims ("Saturn is oblate", "averages a circular orbit") that are true but not in the fact list, so the reviewer marks them unsupported | High | Errors #1, #10 |
| Y3 | Originality is judged only after the script is written. An ordinary angle should be caught at the topic stage, before any script call | Medium | 10 originality stops |
| Y4 | Duplicate detection is word overlap (threshold 0.5-0.75). "Why is space cold" and "If the Sun is hot, why is space cold" can slip either way | Medium | `topics.py:73-90` |
| Y5 | A private upload is stored as state `published`. The dashboard then shows it as "Live on YouTube" | Low | #7, `dashboard.py:37` |
| Y6 | Fact records have a claim, URL and verdict, but **no source passage, confidence or source date**. Units and significant figures are not checked in code | Medium | `sources` table schema |
| Y7 | Retention: there is only the share of viewers still watching at about 10%. There are no beat-level diagnostics. YouTube's audience-retention report is per video and only for public videos with enough views, so most Shorts will show "not enough data". That limit must be reported, not guessed around | Medium | `analytics.py:97` |
| Y8 | **Eligibility thresholds are wrong.** The code checks 500 subs + 3 public uploads + 3M Shorts views for the lower tier. YouTube's page says 500 subs + **3,000 watch hours (12 months) or 3M Shorts views (90 days)**, with no upload rule. The 4,000-hour route for the full tier is missing. The 2027 row says 20M for everyone, but the 2027 change (from Feb 1, 2027) is for **new applicants**: 1,000 subs + 8,000 watch hours or 20M Shorts views. There is also a new "active channel" rule (1,000 hours, or 1M Shorts views, or 5 Shorts per 90 days) | High (wrong facts shown to Connor) | `eligibility.py:23-25`; YouTube Help 72851, 12843009; YouTube blog of 2026-08-10 |
| Y9 | The `experiments` table exists, but nothing uses it. There is no A/B testing | Low | 0 rows, no writers |
| Y10 | QA is pass/fail only: there is no weighted quality score next to the hard fails | Low | `qa.py` |
| Y11 | No policy monitoring (YouTube policy and API terms changes) | Low | missing |
| Y12 | Autopilot lines are not in `jarvis.log`, so a stall can only be seen from the database | Low | PC log check |
| Y13 | There is no backup on a different disk from `studio.db`. `dbrescue.backup` keeps one `.bak` next to the file, and on the PC that is the only copy. The newest one is from 11-23 | High | PC storage check |
| Y14 | Hardware monitoring has no GPU or VRAM reading, and CPU load is unknown on Windows | Low | `health.py:70-78` |
| Y15 | Software registry is incomplete: 5 engines are missing, and there are no version, source, fallback or hardware fields | Low | `guard.BUILT_IN_TOOLS` |

## 4. What already meets the directive

- Channel lock, the protected list and a check of the destination channel right before every upload (and
  before setting the cover). A wrong channel is refused, and there is no fallback to another channel.
- Allow-list of actions. Money, comments, ads, AdSense and sponsors are refused by word. Google scopes are pinned.
- $0 rule: paid tool names are banned, and a tool that costs money can't be registered.
- Uploads are resumable and never happen twice: there is one publish job per video, a tag-based reconcile
  check, and a check on YouTube afterwards.
- Every number in a script must come from a listed, sourced fact. Every asset has a licence and SHA-256.
- Niche research has a reproducible Top-10 report, and the niche can only be changed with `force` and a
  written reason.
- 3 drafts, a judge and lessons (PR #82). The daily limit of 3 is a ceiling, not a target.
