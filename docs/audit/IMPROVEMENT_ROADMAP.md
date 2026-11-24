# Improvement roadmap

Date: 2026-11-24. Based on the three audits in this folder. The order is set by what limits the channel
today. The visuals are fine. **The script stage stops 10 of every 14 videos** (YOUTUBE_MODULE_AUDIT.md, Y1).

Rules for every step:
- $0 extra.
- Nothing existing is removed. Every new tool or style ships **off**, until Connor approves a sample.
- Protected channel never touched.
- Daily max stays 3, as a ceiling.
- Each step is a separate PR with tests.

## Phase 2: Protect (first, small, no change to what the Shorts look like)

| Step | What | Fixes | Size |
|---|---|---|---|
| 2.1 | `python -m studio backup` and a daily automatic run. SQLite backup-API copies of `studio.db`, `clipper.db` and `memory.db`, plus `config.json` and the secrets folder, to `D:\Jarvis\backups\<date>\`. Each copy gets `integrity_check` and a SHA-256 manifest. Keeps 14 daily and 8 weekly copies. `backup verify` and `backup restore --dry-run` show exactly what a restore would change | Y13, S7 | M |
| 2.2 | Guard hardening. The protected channel id is a constant in code. Publishing is refused if the protected set is empty. Self-editors (night school, voice self-edit, repair) get a protected-path list (`studio/guard.py`, `publish.py`, `config.py`, `ytauth.py`, `config.json`), and an edit touching one is reverted. A start-up hash check of the guard files: if they differ from git, publishing pauses and Connor is told | S1, S2 | M |
| 2.3 | Fix the YPP tracker to YouTube's real thresholds, in force today and from Feb 1, 2027, including the watch-hours routes and the 2027 "active channel" rule. Each number cites its page | Y8 | S |
| 2.4 | Honest states. An upload that is still private shows "Uploaded (private)", not "Live" | Y5 | S |
| 2.5 | Baseline regression tests. Upload refused for the protected id, an empty protected set and a self-edited guard. Any comment endpoint refused at the HTTP layer. Thresholds match the cited pages | S6 | S |

## Phase 3: Optimize the script stage (the biggest win)

| Step | What | Why |
|---|---|---|
| 3.1 | **Every factual line carries its fact id** (F1..Fn) from the writer. Code rejects a draft with an untagged factual line *before* the paid review call, and the judge prefers fully tagged drafts | 14 of 30 stops were "unsupported" claims. This catches them for free and saves 2-4 calls each |
| 3.2 | **Fill a gap instead of failing.** When review finds 1-2 unsupported but plausible lines, one cheap targeted lookup tries to source exactly that claim. If it finds a source, the claim is added as a new fact. If not, the line is cut, not the whole Short | Most stops were one or two lines in an otherwise good script |
| 3.3 | **Originality at the topic stage.** Each candidate angle is scored for surprise before any script is written. Weak ones are dropped or re-angled | 10 stops at "originality 5/10" came after full script, review and fix spending |
| 3.4 | **Semantic duplicate detection** with local embeddings (the Chroma and Ollama already installed for the local brain, $0). Word overlap stays as a second check | Y4 |
| 3.5 | **Per-claim fact records:** the source passage (a short quote), confidence, and the source's date. Code checks that units match and that numbers keep their significant figures | Y6 |
| 3.6 | Three drafts with **genuinely different approaches** (for example a question, a scale comparison, a counter-intuitive claim), plus opening-frame concepts and a curiosity check. This builds on #82 | Directive section on hooks |

## Phase 4: Analytics, hardware, registry

| Step | What |
|---|---|
| 4.1 | Retention intelligence. Where YouTube gives a retention curve, map the drops onto script beats (hook, setup, math, re-hook, payoff) and save beat-level lessons. Where it doesn't (private, too few views), say "not enough data" and never estimate |
| 4.2 | Hardware monitor: GPU VRAM, temperature and use (`nvidia-smi`), Windows CPU load and commit charge, disk on C: and D:. Shown on the dashboard. Heavy jobs wait when the PC is busy |
| 4.3 | Complete software registry: every engine with version, purpose, licence, source URL, local or external, cost (always 0), hardware need and fallback. `python -m studio registry` |
| 4.4 | QA: a weighted quality score next to the 21 hard fails (the fails stay absolute) |
| 4.5 | Autopilot lines in `jarvis.log` |

## Phase 5: Automation that learns

| Step | What |
|---|---|
| 5.1 | Experiment engine using the existing `experiments` table: one variable at a time (hook style, length, posting hour), a fixed sample size, and a result only when the difference is clear. No change goes live on its own: a finished experiment becomes a suggestion |
| 5.2 | Policy monitor. Once a week it checks YouTube's Shorts and monetization policy pages and the API terms for changes, and tells Connor what changed. It never changes settings by itself |
| 5.3 | The Niche report stays a report. It is re-run monthly, but never rebrands on its own |

## Phases 6-7: Validate and activate

- Each phase's PR is tested in the cloud. The PC then renders a private sample.
- Connor reviews it and types OK in the PC thread before any new setting goes on.
- The #82 visual upgrades (sharpen, motion, edit, sfx, footage) follow the same path. Their sample is
  still waiting.

## Not proposed

- Auto-rebranding.
- Paid tools.
- A comment bot.
- Any change to the protected channel.
- Raising the 3-a-day limit.
