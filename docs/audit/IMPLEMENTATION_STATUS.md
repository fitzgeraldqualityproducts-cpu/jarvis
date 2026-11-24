# Implementation status

Last updated: 2026-11-24. This file is updated with every PR in the roadmap. "Done" means merged and
tested. "Live" means switched on on the PC.

## Phase 1: Inspect: done

- [x] Repo inspected at 4c985d0 (every module's purpose, size, tests, docs).
- [x] PC state read, read-only: hardware, config, engines, database counts, errors, lessons, analytics,
      schedules, storage, backups, log.
- [x] YPP thresholds checked against YouTube Help 72851 and 12843009 and the YouTube blog post of
      2026-08-10.
- [x] The five audit documents in `docs/audit/`.
- [x] Baseline test run (below).

### Baseline tests (cloud, 2026-11-24)

397 tests: **396 passed, 1 failed** (21 minutes). The one failure, `test_selftune.py::test_section_falls_back_to_the_whole_video`, is the cloud machine missing `yt-dlp`, not a code fault. GitHub CI installs `yt-dlp` and was green on #82.

## Phase 2: Protect: done (PR #83, merged 2026-11-24)

| Step | State |
|---|---|
| One-off backup on the PC (`D:\Jarvis\backups\2026-11-24\`) | **Done** 2026-11-24 on Connor's typed OK. studio.db integrity ok, 25 tables, row counts match. config and secrets hashes match |
| 2.1 Automatic verified backups (`studio/backup.py`, `python -m studio backup ...`) | Built and tested. Runs daily in the worker's spare time. 14 daily + 8 weekly copies on D:. Restore only with Jarvis closed, after a safety copy |
| 2.2 Guard hardening: Connor's channel id in code, an empty or protected lock refused, guard files checked against GitHub before each upload, self-edits can't touch guard files or guarded settings | Built and tested |
| 2.3 YPP tracker: all routes (watch hours or Shorts views), uploads rule, the Feb 2027 new-applicant numbers and notes, each cited | Built and tested |
| 2.4 Dashboard: "Shorts live" counts public only, private uploads are shown as private | Built and tested |
| 2.5 Regression tests (`tests/test_studio_protect.py`, 17 tests) | Passing |

## Phases 3-5: built, in review (PR #84)

| Step | State |
|---|---|
| 3.1 One or two unsourced or wrong lines are cut instead of failing the Short, with no extra Claude call. Only when the line isn't the hook, payoff, loop or last math step, and the next line doesn't lean on it. Every line that stays was checked word for word | Built and tested |
| 3.2 Ideas rated under 6/10 for originality are dropped at the idea stage, including ones already in the pool. The rater is told the reviewer's bar | Built and tested |
| 3.3 Per-claim records: the line, the source's own words (quote), the checker's confidence, the source's date (migration 5). Shown in the script report | Built and tested |
| 3.4 Semantic duplicate detection (local embeddings) | Not started |
| 3.5 Genuinely different draft approaches, opening-frame concepts | Partly there since #82 (3 drafts around different images); more later |
| 4.1 Retention intelligence (beat-level) | Not started. No retention data exists yet (the only upload is private). Connor skipped retention learning on 2026-11-24 in the visuals thread, so this will ask him first |
| 4.2 Hardware monitor: GPU memory, temperature and use, Windows CPU, C: and D: free space, backup age on the dashboard | Built and tested (GPU read with nvidia-smi, read-only) |
| 4.3 Full software registry, `python -m studio registry`: 21 tools with version, licence, source, local or external, $0, hardware, fallback. All registered in the $0 table at start-up | Built and tested |
| 4.4 Weighted quality score (0-100) next to the 21 hard checks. It never passes or stops a Short | Built and tested |
| 4.5 Studio stage progress and problems in jarvis.log | Built |
| 5.1 Experiment engine: one variable, 6 Shorts per arm, a result only when clear, a suggestion only. Runs only after `python -m studio experiment start <name>` | Built and tested. None running |
| 5.2 Weekly policy watch of 7 YouTube rule and API pages. Tells Connor what changed and never changes a setting | Built and tested |

## Phases 6-7: validate and activate

Each change goes live on the PC by update. New styles and tools stay off until a sample is approved.

## Earlier work still waiting on Connor

| Item | Waiting for |
|---|---|
| #82 visual upgrades (sharpen, motion, edit, sfx, footage) | A PC sample and Connor's typed OK in the PC thread |
| Pexels footage | A free Pexels key ("Connect Stock Footage") |
| Google Drive backup of finished Shorts | "Connect Google Drive" once on the PC |
| Channel verification and Canva logo/banner | Connor, in YouTube Studio |
