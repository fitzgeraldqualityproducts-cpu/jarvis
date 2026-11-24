# Security audit

Date: 2026-11-24. Scope: the YouTube business (`studio/`), plus every part of Jarvis that can change it.
No secrets were read or printed while making this audit. On the PC, the secret files were only listed by name.

## 1. Controls that are in place and work

| Control | Where | Verified |
|---|---|---|
| Action allow-list. Anything else is refused, and so is anything containing a "never" word (money, purchase, ads, AdSense, sponsor, affiliate, comment...) | `guard.allow`, `config.RULES["never"]` | Tests in `test_studio_foundation.py` |
| Google scopes pinned to 4. Any other scope is refused before sign-in | `guard.check_scopes` | Tests |
| One locked channel. The signed-in channel is checked right before every upload and every cover change. A protected or different channel stops the upload, and there is no fallback | `guard.verify_destination`, `publish._destination` | Tests. Live lock is HussUniversity |
| Protected list = config `protected_channels` plus every channel the clipper knows | `guard.protected_channels` | PC holds `UCamLvdxlcfg3YqISoj4jlcA` |
| $0 registry: banned names (ElevenLabs, OpenAI, Anthropic API, Runway...) and any cost above 0 are refused | `guard.register_tool` | Tests |
| Claude runs with **no file or shell tools**: none, or read-only WebSearch/WebFetch | `studio/claude.py:48-60` | Tests |
| Audit table for sensitive actions, with OAuth tokens redacted | `guard.audit`, `guard.redact` | Tests |
| Secrets are not tracked: `config.json`, `studio_data/`, `clipper_data/`, `*.db` are gitignored | `.gitignore` | `git ls-files` shows none |
| Upload idempotency: one publish job per video, tag reconcile, check on YouTube afterwards | `publish.py` | Tests |

## 2. Weaknesses (S = security), most serious first

| # | Weakness | Risk | Proposed fix |
|---|---|---|---|
| S1 | **Jarvis's self-editing agents can change the guard.** `nightschool.py` (every night at 01:00), `selfedit.py` (by voice) and `repair.py` run Claude Code with Edit/Write and `acceptEdits` on the whole Jarvis folder. Nothing stops them from editing `studio/guard.py`, `studio/publish.py`, `studio/config.py` or `config.json` (the protected list). An honest mistake while "improving" something is enough | High | A list of protected paths. Every self-edit is checked after it runs and reverted if it touched one. A start-up hash check of the guard files against the last version from GitHub. The guard refuses to publish if they differ |
| S2 | **The protected list can be emptied.** It lives only in `config.json` and the clipper database. If both lose it (a config rewrite, a clipper reset), the protection is silently gone. The channel lock still stops an upload elsewhere, but one layer of the two is lost | Medium | Add Connor's protected channel id as a constant in code, which a setting can't remove. Refuse to publish when the merged protected set is empty |
| S3 | Research text from the web (pages, API answers, competitor titles) goes into Claude prompts with no "this is data, not instructions" marking | Low-Medium (Claude has no file or shell tools, so the worst case is a bad script, which the reviewer and QA then stop) | Wrap research in clearly marked data blocks, and tell the model to ignore any instructions inside them |
| S4 | Updates install from `main` with no signature check. Anyone who can push to the repository can run code on both computers | Medium (needs a GitHub account compromise) | Keep branch protection on `main`, 2FA on the GitHub account, and the self-check rollback. A signature check would need keys on both computers. Proposed for later, not now |
| S5 | `save_key` uses `chmod 600` on the secrets, which does nothing on Windows | Low (the user profile's permissions already protect them) | Document it. Optionally set an ACL with `icacls` |
| S6 | The studio's playlist permission (`youtube` scope) also technically allows comment actions. Only the word list refuses them | Low (Connor approved this on 2026-11-23) | Keep it. Add a test that every `commentThreads` or `comments` URL is refused at the HTTP layer too |
| S7 | Backups: one `.bak` next to the database, on the same disk, and none newer than 11-23 | High for recovery (not an attack, but loss) | Phase 2: verified, rotated backups on D: with integrity checks |
| S8 | `studio_data/secrets` would be copied into a backup | Low | Backups stay local (D:), are never uploaded, and are never printed |

## 3. The never list, checked against the directive

| Directive prohibition | Enforced by |
|---|---|
| Spending, purchases, subscriptions, paid APIs | never-words + $0 registry + banned tools |
| Ads, revenue or monetization settings, AdSense | never-words. No such API is called. Revenue scopes are refused |
| Sponsors, affiliates | never-words |
| Reading, replying to or deleting comments | never-words. No comment endpoint exists in the code (add the HTTP-layer test, S6) |
| Unrelated channels; protected channel settings | lock + protected list (S2 hardens it) |
| Credentials in AI prompts | Prompts are built from database text only. Tokens are never read into prompts. Redaction in the audit |
| Claiming untested results | Honest status texts. Remaining doc gaps are listed in JARVIS_ARCHITECTURE_AUDIT.md section 8 |

## 4. Not tested here

- Real Windows ACLs on `studio_data\secrets`.
- What the YouTube API answers for a channel that was switched in the Google account chooser. This is covered by
  unit tests with fakes, not by a real second account, on purpose: the protected channel is never signed in.
