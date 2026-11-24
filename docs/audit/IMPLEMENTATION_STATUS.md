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
| 4.1 Retention intelligence (beat-level) | Built and tested (Connor asked for it 2026-11-24). `studio/retention.py`: YouTube's retention curve laid over each Short's line timings (saved with every render from now on), fast-leaving lines become lessons once per Short, the writer is told once 3+ Shorts agree on a kind of line. Shorts without a curve (too few views) or without line timings are listed, never guessed. Studio's "swiped away" share isn't in the API, so hook hold (still watching at 10%) is the closest real number. `python -m studio retention` and the weekly report |
| 4.2 Hardware monitor: GPU memory, temperature and use, Windows CPU, C: and D: free space, backup age on the dashboard | Built and tested (GPU read with nvidia-smi, read-only) |
| 4.3 Full software registry, `python -m studio registry`: 21 tools with version, licence, source, local or external, $0, hardware, fallback. All registered in the $0 table at start-up | Built and tested |
| 4.4 Weighted quality score (0-100) next to the 21 hard checks. It never passes or stops a Short | Built and tested |
| 4.5 Studio stage progress and problems in jarvis.log | Built |
| 5.1 Experiment engine: one variable, 6 Shorts per arm, a result only when clear, a suggestion only. Runs only after `python -m studio experiment start <name>` | Built and tested. None running |
| 5.2 Weekly policy watch of 7 YouTube rule and API pages. Tells Connor what changed and never changes a setting | Built and tested |

## Speed (asked for 2026-11-24)

| Step | State |
|---|---|
| A writing lane: the next Short is researched, written and fact-checked while this one is being built (voice, pictures, render, checks). At most one being written and one being built. The one being built counts towards the buffer, and the daily limit holds. Config `studio.overlap` (on by default) | Built and tested |
| Encode on the RTX 2060 (NVENC) instead of the processor: the classic compose, camera-move clips, footage cuts, the fallback slideshow, and Remotion (`--hardware-acceleration=if-possible`, 12 Mbit/s, since Remotion refuses crf with the card). One test encode decides; a failed GPU encode is redone on the processor and the rest of the run stays there. The encoder used is saved in the render report. Off with `JARVIS_GPU_ENCODE=0` | Built and tested in the cloud (no NVIDIA card there, so the GPU path was tested with fakes and the fallback for real). Speed and look on the PC not measured yet |
| Remotion prepared once per update: the composition is built once per code version (`tools/remotion-bundle/<hash>`) and each render points the build's picture folder at the Short's folder (a junction on Windows) instead of rebuilding and copying the folder. Any problem falls back to the old way. Config `studio.remotion_prepared` (on by default) | Built and tested. A real Remotion render in the cloud came out frame-for-frame identical (same frame checksums). Time saved there was about 1 s, because the test Short's folder was only 7 MB; on the PC it saves the copy of the Short's whole folder on D:, not measured yet |
| AI models loaded once per Short | Checked, no change needed: SDXL-Turbo, ESRGAN plus depth, Chatterbox, whisperX, ACE-Step and Manim each already load once per Short and do all its scenes in one job |
| TensorRT for SDXL-Turbo | Skipped. TensorRT pictures are close but not identical to today's, building the SDXL engine needs more than the 2060's 6 GB, and the 4-step pictures already take only seconds a Short |
| Timing report: every stage of every Short, each heavy job inside it (pictures, sharpening and depth, Manim, voice, captions, music, Remotion render), each Claude call, and time spent waiting for the graphics card. `python -m studio timings [id]` and "Where the minutes go" on the dashboard (average of the last 10 Shorts, slowest first) | Built and tested. Real numbers start with the first Short made on the PC after this update |
| Saved source pages: each page the fact-check reads is kept (gzip, `D:\Jarvis\studio\pages` on the PC) for 7 days, so fix rounds and re-checks don't download it again. Deleted after 30 days. Empty or blocked pages are never saved | Built and tested |

## Tokens (asked for 2026-11-24)

Measured with real Claude in the cloud (Sonnet, the lean studio calls) on two test topics, and on the PC from
`python -m studio usage`: a Short that passes first time uses about 40,000 tokens, of which about 9,000-10,000 are
written by Claude. The 3-draft writer is most of that (about 7,000-9,600 output tokens, about 60% of them its own
thinking), then the fact-check (about 2,000-3,000 each).

| Step | State |
|---|---|
| Fact-check answers: no entries for opinion or transition lines, and no "note" on supported claims (the quote already says it) | Built and tested. Same script checked twice with each prompt: output tokens 2,426 and 2,470 before, 1,972 and 2,000 after (about 19% fewer); the same verdict on every checkable line, the same originality, re-hook and best hook |
| After "fix lines", only the changed lines are checked again (the whole script is shown for context); kept lines keep their verdicts, and the hook, loop, re-hook and originality ratings carry over. A full check still runs if a hook, re-hook, payoff or loop line changed, the hooks changed, the length was refitted, or the earlier failure wasn't just flagged lines. Config `studio.partial_recheck` (on) | Built and tested with fakes (a still-unsupported line fails again as before). Saves most of one full fact-check each time a line fix happens |
| Writer's thinking | Not changed: it is where the drafts get their quality |

## Self-review score (asked for 2026-11-24)

| Step | State |
|---|---|
| After each measured Short: the quality score made before upload next to the real performance index. With 5+ Shorts: rank correlation for the score and each part, in the weekly report. Suggestions only; weights change only by an approved update | Built and tested |

## Phases 6-7: validate and activate

Each change goes live on the PC by update. New styles and tools stay off until a sample is approved.

## Earlier work still waiting on Connor

| Item | Waiting for |
|---|---|
| #82 visual upgrades (sharpen, motion, edit, sfx, footage) | A PC sample and Connor's typed OK in the PC thread |
| Pexels footage | A free Pexels key ("Connect Stock Footage") |
| Google Drive backup of finished Shorts | "Connect Google Drive" once on the PC |
| Channel verification and Canva logo/banner | Connor, in YouTube Studio |
