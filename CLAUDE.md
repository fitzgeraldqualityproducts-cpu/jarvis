# Jarvis: notes for coding sessions

Jarvis is Connor's voice assistant plus a YouTube Shorts studio (`studio/`) and a Twitch/Shorts
clipper (`clipper/`). It runs on his Mac (M1, 8 GB) and his Windows PC (RTX 2060, 16 GB).
Jarvis's own Claude Code calls run from an empty folder (`brain.quiet_room()`, `studio/claude.py _room()`),
so this file only reaches coding sessions and Jarvis's self-edits.

## Shipping
- Anything merged to `main` installs itself on both computers (`updater.py`, with a self-check and
  rollback). Treat every merge as a release. Work on a branch and open a PR; never push to `main`.
- Before a PR: `python -m compileall -q .` and `python -m pytest -q -p no:cacheprovider`.
  GitHub CI runs the same on every PR. A few tests need ffmpeg/yt-dlp and may fail in a bare cloud box.
- Every change must work on macOS and Windows. Launchers come in pairs (`X.command` and `X.bat`);
  Windows code paths check `WINDOWS`/`sys.platform == "win32"`. Use `pathlib`, never hard-coded `/`.

## Never
- Touch Connor's main channel @sauce2010 (`studio/guard.PERMANENT_PROTECTED`). Only the locked
  HussUniversity channel may get uploads.
- Change the channel-safety files without Connor's OK: `studio/guard.py`, `studio/publish.py`,
  `studio/ytauth.py`, `studio/config.py`, `studio/backup.py`, `selfedit.py`, `nightschool.py`,
  `repair.py` (`studio/guard.GUARDED_FILES`). `.claude/settings.json` asks before editing them.
- Commit `config.json`, `*.db`, tokens, media (`*.mp4`, `*.wav`...) or `studio_data/` / `clipper_data/`.
- Add anything that costs money: no API keys, no paid services. Claude use goes through Connor's
  Claude plan via the `claude` CLI (`brain.find_claude()`), kept lean (see `brain.lean`).

## Habits
- New studio engines and styles ship switched OFF in config and are turned on only after Connor
  approves a sample.
- PC storage: app code and venvs on C:, big things (clips, Shorts, model downloads) on D:\Jarvis.
- Connor isn't a programmer. Explain results in plain words, not code names.
