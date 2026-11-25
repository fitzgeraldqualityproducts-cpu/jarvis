# Implementation status

Last updated: 2026-11-25. This file is updated with every PR in the roadmap. "Done" means merged and
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

## Five more upgrades (asked for 2026-11-24: "1,2,3,4,6")

| Upgrade | What it does | Switch | State |
|---|---|---|---|
| 3. More facts | Before the first draft, adds up to 8 sentences with new numbers, copied word for word from Wikipedia (its API), the NASA/agency pages the topic already cites, and arXiv abstracts (marked as preprints). Their text is saved for the fact-check | `enrich_facts` (on) | Built and tested with fakes. The cloud can't reach these sites |
| 2. Voice check | Whisper listens back to the Chatterbox voiceover. Unclear sentences are re-recorded (2 more seeds) and the clearest take is spliced in. Still garbled: Kokoro. Pronunciation list for units and science words (`pronounce` adds more) | `voice_check` (on) | Built and tested with fakes |
| 1. Final-cut review | Claude looks at a contact sheet (the first frame plus the middle of each line) next to what is said, and reports problems as minor or major | `final_review`: `advise` (default, never stops a Short), `block`, `off` | Built and tested. A real CLI image read was checked |
| 4. Smarter topics | Shows Claude yesterday's most-read Wikipedia articles as hints. Blends Wikipedia page views into interest. Catches repeats by meaning with Jarvis's local embedding model | `wiki_trends` (on), `duplicate_meaning_at` (0.9) | Built and tested. Tune the threshold on the PC with `python -m studio similar` |
| 6. Packaging that learns | Saves each Short's title style and first-frame look. Once a style is clearly ahead on the index (titles) or hook hold (first frames), over 3+ Shorts each side, the writer is told and that title style is picked first | none (does nothing until there is evidence) | Built and tested. Shorts have no CTR data, so it uses index and hook hold |

## Shorts that make sense (Connor's picks 1, 2, 5; then 5, 6; then 1, 2, 4)

| Upgrade | What it does | Switch | State |
|---|---|---|---|
| Footage relevance | A stock clip must match every word of its search (tags, title, description). Space searches ask NASA first | none | Built and tested with fakes |
| Storyboard | The visual plan picks one real, filmable main subject and carries it through every scene, so the pictures tell one story | `storyboard` (on) | Built and tested |
| Picture check (OpenCLIP) | OpenCLIP ViT-B/32 (MIT, free, runs in tools/sdxl) scores each AI picture against its line. Under `picture_fit_min` (0.22): 2 more takes, best kept. Still under `picture_fit_floor` (0.17): a plain background so the diagram carries the scene (never the hook). Footage whose middle frame scores under `footage_fit_floor` is dropped and the AI picture stays | `picture_fit` (on) | Built and tested with fakes. Thresholds to calibrate on the PC |
| Explain it back + confused viewer | The local brain (Ollama) plays a 14-year-old new to the topic: it retells the passed script, rates 1-5 how well that matches the intended point, and names lines it really couldn't follow (a skipped step, an unexplained word). A rating of 2 or lower, or a named line, sends it back once with what the viewer said. Never on the last allowed try, never twice | `explain_back` (on) | Built and tested with fakes. Skipped when Ollama isn't running |
| One question per Short | The writer builds the whole Short around one question the hook raises; the payoff answers it. The fact-checker names the question and rates "answered" 0-10: under 4 sends the script back, under 6 is an editor note | none | Built and tested |
| Plain comparisons | Every surprising number gets a familiar comparison the moment it appears ("127 C, hotter than boiling water"), from the facts or everyday knowledge only. The fact-checker lists bare numbers as an editor note | none | Built and tested |
| Hook check | Once 3+ Shorts have a measured hook hold, the best-holding openings are shown to the writer, and the fact-checker rates every hook "proven" against them. That rating counts in picking the hook; a weak one gets an editor note | none (does nothing until there is evidence) | Built and tested |
| Obsidian weekly report | The weekly report (now with what worked, what flopped, what changes next) is also saved to `<vault>/HussUniversity/Weekly reports/<date>.md`. Vault: `obsidian_vault`, else OneDrive\Documents\Obsidian Vault | `obsidian_report` (on) | Built and tested. Writes only that folder |

## Audit follow-up (Connor's picks 1, 2, 3, 6 on 2026-11-25)

| Step | What it does | Switch | State |
|---|---|---|---|
| 1. Measure the script stage | `python -m studio stops [--since YYYY-MM-DD]`: Shorts that passed (first try, after a rewrite, by cutting lines) or stopped, why, and fact-checks per Short. Read only | none | Built and tested. First reading (PC, by hand, 2026-11-25): before the fixes 4 of 13 passed, 9 stopped (originality 6, unsupported lines 6, word count 2; 2.6 checks per stopped Short). No Short has been written since the fixes |
| 2. Free number check | Before any fact-check: a line that says a number but lists no fact gets the fact whose words have that number. A number found in no fact or calculation (and not marked textbook science) becomes a "needs" lookup: a page that states it is found, or the line is dropped. The hook, payoff and loop lines are left for the fact-check | `number_check` (on) | Built and tested |
| 3. Look up before cutting | When the fact-check flags one or two lines as unsupported, one search looks for a reputable page that states each line's number (Jarvis downloads it and checks the number is really on it). Lines it sources get that page and only those lines are checked again. Nothing found: the usual cut or rewrite. Once per draft, never for a "wrong" line, and never when it could cost the Short a try | `lookup_before_cut` (on) | Built and tested with fakes |
| 6. Visual upgrades sample | The PC test Short of the story work is made with every #82 upgrade (sharpen, depth motion, lively edit, sound effects, footage). Connor's typed OK in the PC thread switches them on | n/a | Waiting for the sample |

## After the first new test Short (video 15, 2026-11-25)

Final-cut review 4/10 found: line cutting left "So the real question is simple:" hanging; the review's
storytelling notes never stopped anything; the read-back was skipped on the cut path; a label was cut off and
another stayed up after its scene; the review frame caught the counter mid-count.

| Fix | Switch | State |
|---|---|---|
| A line is never cut when the line before it leads into it (ends with ":" or a dash), when it would remove the payoff, or when the Short would end mid-thought | none | Built and tested |
| The newcomer read-back also runs after lines are cut | `explain_back` | Built and tested |
| One "polish" rewrite (Connor's choice) when a script passes the facts but the loop or re-hook is under 5/10, the payoff only half answers the question (under 6/10), numbers have no comparison, or an editor note says something is wrong, misleading, confusing or hanging. Never twice, never on the last try | `polish_rewrite` (on) | Built and tested |
| Final-cut review blocks by default (Connor's choice): a major problem stops the Short before upload and Connor is told | `final_review` = `block` | Built and tested |
| A carried counter stays up for one scene only; long labels wrap; review frames are taken 80% into each line (after the counter lands) | none | Built and tested |
| Script page written as UTF-8 (Windows crashed on "λ") | none | Built |

## Upgrade brief (2026-11-25): story first, visuals that show what is said, bold text, better QC

Built on #82-#96; reusable for any topic (nothing is written for one Short).

| Change | Switch | State |
|---|---|---|
| Story first: the planner writes the story (subject, beats) before the scenes, then a storyboard per scene: purpose, 1-3 shots with camera moves, transition, sound | `storyboard` (on since #93) | Built and tested |
| Honesty rule: AI pictures never fake an experiment result or measurement; demonstrations are diagrams | none (prompt) | Built |
| The fact-check also checks the explanation (e.g. hot spots are half a wavelength apart, not hot-to-cold); every line must earn its place | none (prompt) | Built |
| A new shot every 1.5-2 s: scenes with longer lines get 2-3 pictures, each with its own camera move from the storyboard; shots OpenCLIP says don't fit are dropped | `shots` (OFF) | Built and tested with fakes |
| New reusable diagrams: a standing wave with its peaks (hot spots) and the gap or wavelength labelled; a ruler between two things with the distance | `shots` (OFF) | Built and tested; rendered stills checked |
| The storyboard's sound (whoosh, pop, tick, rise, none) and transition (cut, fade, zoom) on each cut | `shots` (OFF) | Built and tested |
| Bold type system: heavy sans-serif hook, counter and captions; 1-2 short caption lines timed to the voice, numbers and key words in the accent colour, the payoff line bigger; all inside the phone-safe area | `type_style` = `serif` (bold is OFF) | Built and tested; rendered stills checked |
| QA: the picture must not stand still for over 3.5 s and no two scenes may reuse a picture | `visual_qc` = `advise` | Built and tested |
| Learning: likes, comments and shares (counts only, comments never read) join the performance index at 10% and the weekly report | none | Built and tested |
| Windows: still pictures now find Arial on Windows (on Windows they could fall back to a tiny default font) | none | Built |

## Phases 6-7: validate and activate

Each change goes live on the PC by update. New styles and tools stay off until a sample is approved.

## Earlier work still waiting on Connor

| Item | Waiting for |
|---|---|
| #82 visual upgrades (sharpen, motion, edit, sfx, footage) | A PC sample and Connor's typed OK in the PC thread |
| Stock footage | NASA works with no key; a free Pixabay key ("Connect Stock Footage") adds Pixabay. Pexels has paused new keys. Footage stays off until Connor approves a sample |
| Google Drive backup of finished Shorts | "Connect Google Drive" once on the PC |
| Channel verification and Canva logo/banner | Connor, in YouTube Studio |
