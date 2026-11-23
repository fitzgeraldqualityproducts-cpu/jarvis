"""The studio's real state, for Jarvis's answers, the CLI and (later) the /studio dashboard.
Nothing here is decorative: every value is read from the database or the machine."""
import time

from studio import config, db, guard, health, jobs


def overview():
    g = config.general()
    counts = {r["state"]: r["n"] for r in db.q("SELECT state, COUNT(*) n FROM videos GROUP BY state")}
    return {
        "enabled": config.enabled(), "configured_on": g["enabled"], "paused": jobs.paused(), "working": jobs.busy(),
        "schema": db.version(), "channel": guard.locked_channel(), "jobs": counts,
        "stages_ready": sorted(jobs.STAGES), "resources": health.resources(),
        "errors": db.q("SELECT t, kind, video_id, stage, detail, recovery, remaining FROM errors ORDER BY id DESC LIMIT 10"),
        "tools": db.q("SELECT name, purpose, license, local, needs_key, cost_usd, limits, status, fails, open_until FROM tools ORDER BY name"),
        "operating_cost_usd": sum(t["cost_usd"] or 0 for t in db.q("SELECT cost_usd FROM tools")),
        "t": time.time(),
    }


def say():
    """One or two plain sentences for Jarvis to speak."""
    o = overview()
    if not o["configured_on"]:
        return "The YouTube business module is installed but switched off."
    ch = o["channel"]
    parts = [("The YouTube business is paused" if o["paused"] else "The YouTube business is running")
             + (f" for {ch['title']}." if ch else ", but no business channel is connected yet.")]
    active = sum(n for s, n in o["jobs"].items() if s not in db.FINAL)
    parts.append(f"{active} Short{'s' if active != 1 else ''} in progress, {o['jobs'].get('published', 0)} published. "
                 f"Running cost: ${o['operating_cost_usd']:.0f}.")
    return " ".join(parts)
