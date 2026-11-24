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

## Phase 2: Protect: waiting

| Step | State |
|---|---|
| Handover-style backup on the PC (`D:\Jarvis\backups\2026-11-24\`) | Waiting on Connor's typed OK in the PC thread |
| 2.1 Automatic verified backups | Not started |
| 2.2 Guard hardening (S1, S2) | Not started |
| 2.3 YPP thresholds fixed | Not started |
| 2.4 Private uploads labelled honestly | Not started |
| 2.5 Regression tests | Not started |

## Phases 3-7: not started

These wait for Connor to agree the plan in IMPROVEMENT_ROADMAP.md.

## Earlier work still waiting on Connor

| Item | Waiting for |
|---|---|
| #82 visual upgrades (sharpen, motion, edit, sfx, footage) | A PC sample and Connor's typed OK in the PC thread |
| Pexels footage | A free Pexels key ("Connect Stock Footage") |
| Google Drive backup of finished Shorts | "Connect Google Drive" once on the PC |
| Channel verification and Canva logo/banner | Connor, in YouTube Studio |
