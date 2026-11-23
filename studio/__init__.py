"""YouTube Business Studio: Jarvis's module for running one dedicated YouTube Shorts channel.

It is separate from the Shorts clipper (clipper/), which clips Connor's own videos. The studio makes
original Shorts for a NEW channel and must never touch Connor's existing channel.

    research -> topic -> script -> fact-check -> voice -> visuals -> render -> captions
    -> quality gate -> publish (official API) -> analytics -> learning -> repeat

Off by default: config.json "studio": {"enabled": true} turns it on (Mac only unless
"allow_windows": true). Turning it off stops the worker; nothing else in Jarvis changes.

Modules:
    config    settings, validation and the permanent rules (no money, no comments, $0)
    db        studio_data/studio.db (SQLite, versioned migrations, backups, integrity checks)
    guard     the permission layer every outside action goes through: allowed actions,
              the channel lock, the $0 tool registry, the audit log, secret redaction
    jobs      persistent production jobs, the worker thread, retries, circuit breakers, recovery
    health    start-up checks, disk/memory/CPU limits, alerts for Connor

Data lives in studio_data/ (JARVIS_STUDIO_DATA overrides it, which the tests use). Updates never
touch it: updater.py untracks it."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # the Jarvis folder


def home():
    return Path(os.environ.get("JARVIS_STUDIO_DATA") or ROOT / "studio_data")


def data_dir(*parts):
    p = home().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
