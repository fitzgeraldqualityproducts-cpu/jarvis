"""YouTube Shorts clipper (see clipper/__init__.py). These tools only report what really happened:
every answer comes from the clipper's database or the action's actual result."""
from skills import tool


@tool("Make YouTube Shorts from a long video: finds the best moments, edits vertical captioned clips. "
      "Use a link or file path the user gave; if they said 'this video' without one, leave source empty "
      "(the copied link is used). Only videos the user may clip are processed.",
      source="a YouTube/Twitch link or a video file path, or empty for the copied link",
      count="how many Shorts to make (default: the channel's setting)")
def clip_video(source: str = "", count: int = 0):
    from clipper import control
    return control.clip(source or None, count or None)


@tool("Make Shorts from the user's most recent Twitch stream (VOD).", count="how many Shorts (optional)")
def clip_last_stream(count: int = 0):
    from clipper import control
    return control.clip(None, count or None, twitch_latest=True)


@tool("What the Shorts clipper is doing right now: running jobs, queue, clips waiting for approval, failures.")
def clipper_status():
    from clipper import status
    return status.say_status()


@tool("How much disk space is free, what's in Jarvis's folder, and how much Jarvis could safely clear.")
def disk_space():
    from clipper import disk
    return disk.say()


@tool("Free up disk space: deletes old downloaded VODs/videos, half-finished downloads and renders, rejected "
      "clips and old setup backups, only inside Jarvis's folder. Never deletes clips waiting for review, approved "
      "or on YouTube. Use when the user asks to free up or clean up space.")
def free_up_disk_space():
    from clipper import disk
    return disk.say_clean(disk.clean(urgent=True, by="voice"))


@tool("The clipper's report for the last 24 hours (videos found, clips made, uploads, failures).")
def clipper_report():
    from clipper import status
    return status.today_report()


@tool("List the clips waiting for the user's approval, with their numbers, titles and scores.")
def clips_awaiting_approval():
    from clipper import status
    rows = status.pending(10)
    if not rows:
        return "No clips are waiting for approval."
    return " ".join(f"Clip {c['id']}: \"{c['title']}\" ({c['duration']:.0f}s, score {c['score']:.1f})." for c in rows)


@tool("Approve a clip (it then uploads if uploads are allowed). Only when the user clearly approves that clip.",
      clip_id="the clip number", title="optional new title")
def approve_clip(clip_id: int, title: str = ""):
    from clipper import publish
    return publish.approve(clip_id, title=title or None)


@tool("Reject a clip so it's never uploaded.", clip_id="the clip number", reason="optional why")
def reject_clip(clip_id: int, reason: str = ""):
    from clipper import publish
    return publish.reject(clip_id, reason)


@tool("Make a replacement for a clip the user didn't like (the next best moment).", clip_id="the clip number")
def regenerate_clip(clip_id: int):
    from clipper import pipeline
    return pipeline.regenerate(clip_id)


@tool("Change a clip's title or description before it's uploaded.", clip_id="the clip number",
      title="new title", description="new description")
def edit_clip(clip_id: int, title: str = "", description: str = ""):
    from clipper import publish
    return publish.edit(clip_id, title=title or None, description=description or None)


@tool("Upload all approved clips to YouTube under the channel's publishing permissions. Uploading is an important "
      "action: only call this after the user has explicitly said yes to uploading.",
      confirmed="true only if the user explicitly confirmed the upload in this conversation")
def upload_approved_clips(confirmed: bool = False):
    if not confirmed:
        return "Ask the user to confirm the upload first (how many clips, and that they'll go up as private unless public is allowed)."
    from clipper import publish
    return publish.upload_approved()


@tool("Emergency stop for the clipper: no new jobs and no uploads until resumed.")
def stop_clipping():
    from clipper import jobs
    return jobs.emergency_stop()


@tool("Pause or resume the clipper.", action="pause or resume")
def pause_clipping(action: str = "pause"):
    from clipper import jobs
    return jobs.resume() if action.strip().lower().startswith("res") else jobs.pause()


@tool("Analyse how the user's published Shorts performed (best clips, which openings, styles and lengths do best).",
      last="how many recent Shorts to look at")
def analyze_shorts(last: int = 20):
    from clipper import learning
    return learning.report("main", last or 20)


@tool("Change how clips are made: caption style (clean = Clean Professional, bold = Bold Viral, minimal = Minimal White, "
      "gaming, podcast, highlight = Highlighted Keywords, pop = Animated Pop, cinematic, or auto), clip lengths, "
      "clips per video, or what to prioritise (hook, story, payoff, clarity, entertainment, emotion, curiosity).",
      caption_style="clean, bold, minimal, gaming, podcast, highlight, pop, cinematic or auto", min_seconds="shortest clip",
      max_seconds="longest clip", clips_per_video="how many per video", prioritize="criterion to weigh double")
def clipper_settings(caption_style: str = "", min_seconds: int = 0, max_seconds: int = 0, clips_per_video: int = 0,
                     prioritize: str = ""):
    from clipper import settings
    ch = settings.channel("main")
    changes = {}
    if caption_style:
        changes["caption_style"] = caption_style.lower().strip()
    if min_seconds:
        changes["clip_min"] = min_seconds
    if max_seconds:
        changes["clip_max"] = max_seconds
        changes["clip_hard_max"] = max(max_seconds, ch["clip_hard_max"])
    if clips_per_video:
        changes["max_clips_per_video"] = clips_per_video
    if prioritize:
        pri = dict(ch.get("priorities") or {})
        pri[prioritize.lower().strip()] = 2.0
        changes["priorities"] = pri
    if not changes:
        return "Nothing to change."
    settings.update_channel("main", **changes)
    return "Clipper settings updated: " + ", ".join(f"{k} = {v}" for k, v in changes.items()) + "."



@tool("What the clipper has learned from the user's own edits, approvals and rejections (and its quality-check "
      "mistakes), or forget it all. Only reports real lessons with their evidence.",
      forget="true to forget everything it learned (only if the user asked)")
def clipper_lessons(forget: bool = False):
    from clipper import selftune
    return selftune.forget("main") if forget else selftune.report("main")
@tool("Check the user's approved sources (their Twitch VODs, approved channels, inbox folder) for new videos now.")
def find_new_videos():
    from clipper import control
    return control.run("discover", {})[0]


@tool("Open the Shorts clipper dashboard in the browser.", tab="overview, queue, library, approvals, analytics or settings")
def open_clipper(tab: str = "overview"):
    from clipper import control
    control._open(tab)
    return "Opened the clipper dashboard."
