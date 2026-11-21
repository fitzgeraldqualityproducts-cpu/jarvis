"""Carries out clipper commands for Jarvis (voice, the type box, or his brain's tools) and says what
actually happened. Important actions (uploading, switching public publishing on) come back as a
question first; Jarvis asks, and only a yes runs them (confirm())."""
import os
import subprocess
import sys
import threading

from clipper import db, jobs, publish, settings, sources, status


def clipboard():
    """A link or file path on the clipboard (so "clip this podcast" works after copying a link)."""
    try:
        if sys.platform == "darwin":
            text = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
        elif sys.platform == "win32":
            text = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True,
                                  text=True, timeout=8, creationflags=0x08000000).stdout
        else:
            return None
    except Exception:
        return None
    text = (text or "").strip().strip('"')
    if not text or len(text) > 2000:
        return None
    if text.startswith(("http://", "https://")) or os.path.isfile(os.path.expanduser(text)):
        return text
    return None


def run(intent, args):
    """-> (what to say, confirmation action or None)."""
    try:
        return _run(intent, args)
    except Exception as e:
        db.log(f"Command {intent} failed: {e}", "warn")
        return f"That didn't work: {e}", None


def _run(intent, a):
    if intent == "stop":
        return jobs.emergency_stop(), None
    if intent == "pause":
        return jobs.pause(), None
    if intent == "resume":
        return jobs.resume(), None
    if intent == "status":
        return status.say_status(), None
    if intent == "disk":
        from clipper import disk
        return disk.say(), None
    if intent == "disk_clean":
        from clipper import disk
        return disk.say_clean(disk.clean(urgent=True, by="voice")), None
    if intent == "lessons":
        from clipper import selftune
        return selftune.report("main"), None
    if intent == "unlearn":
        from clipper import selftune
        return selftune.forget("main"), None
    if intent == "report":
        return status.today_report(), None
    if intent == "show":
        _open(a.get("tab", "overview"))
        n = len(status.pending(100))
        if a.get("tab") == "approvals":
            return (f"Here are the {n} clip{'s' if n != 1 else ''} waiting for your OK." if n else
                    "Nothing's waiting for approval right now, but here's the clipper."), None
        return "Here's the clipper dashboard.", None
    if intent == "approve":
        return publish.approve(a["clip"]), None
    if intent == "reject":
        return publish.reject(a["clip"]), None
    if intent == "regenerate":
        from clipper import pipeline
        return pipeline.regenerate(a["clip"]), None
    if intent == "upload_approved":
        rows = db.clips("state='approved' AND youtube_id IS NULL", limit=100)
        if not rows:
            return "There are no approved clips waiting to upload.", None
        ch = settings.channel(rows[0]["channel"])
        if not ch["permissions"].get("upload_private"):
            return "Uploads are switched off. Connect YouTube first, or say turn on uploads.", None
        mode = "public" if a.get("public") else ch.get("privacy", "private")
        priv, _, note = publish.decide(dict(rows[0], approved=1), ch, mode)
        return (f"I'll upload {len(rows)} approved clip{'s' if len(rows) != 1 else ''} to YouTube as {priv}"
                + (f" ({note})" if note else "") + ". Go ahead?"), {"do": "upload_approved", "mode": mode}
    if intent == "permission":
        which, on = a["which"], a["on"]
        if not on:
            settings.update_channel("main", permissions={which: False})
            return {"publish_public": "Public publishing is off.", "schedule_public": "Scheduled publishing is off.",
                    "upload_private": "Uploads are off."}[which], None
        words = {"publish_public": "clips go public on YouTube automatically, after you've approved each one",
                 "schedule_public": "approved clips get scheduled to go public at your posting times",
                 "upload_private": "approved clips upload to YouTube as private"}[which]
        return f"Just to check: with this on, {words}. Turn it on?", {"do": "permission", "which": which}
    if intent == "analyze":
        from clipper import learning
        return learning.report("main", a.get("last", 20)), None
    if intent == "best_style":
        from clipper import learning
        return learning.use_best_style("main"), None
    if intent == "style":
        settings.update_channel("main", caption_style=a["style"])
        return f"Done. New clips use the {a['style']} caption style.", None
    if intent == "prioritize":
        ch = settings.channel("main")
        pri = dict(ch.get("priorities") or {})
        pri[a["criterion"]] = 2.0
        settings.update_channel("main", priorities=pri)
        return f"Got it. I'll weigh {a['criterion'].replace('hook', 'strong opening hooks')} twice as much when picking clips.", None
    if intent == "discover":
        def go():
            try:
                found = sources.discover("main")
                db.log(f"Checked your sources: {len(found)} new video{'s' if len(found) != 1 else ''}"
                       + (", queued the ones that are yours to clip." if found else "."), tell=True)
            except Exception as e:
                db.log(f"Checking sources failed: {e}", "warn", tell=True)
        threading.Thread(target=go, daemon=True, name="clipper-discover").start()
        return "Checking your approved sources now. I'll tell you what I find.", None
    if intent == "grant":
        j = db.job(a["job"])
        if not j:
            return "There's no job with that number.", None
        return sources.grant(j["video_id"]), None
    if intent == "clip":
        return clip(a.get("url"), a.get("count"), a.get("twitch_latest")), None
    return "I'm not sure what to do with that.", None


def clip(url=None, count=None, twitch_latest=False):
    if twitch_latest:
        url = sources.latest_twitch_vod("main")
        if not url:
            ch = settings.jarvis_config().get("twitch_channel")
            return ("I couldn't find a VOD on your Twitch channel. Make sure Store past broadcasts is on in Twitch."
                    if ch else "I don't know your Twitch channel yet. Double-click Connect Twitch first.")
    url = url or clipboard()
    if not url:
        return ("Copy the video's link (or the file) first and say that again, or paste the link into my type box "
                "with the word clip in front.")
    v, job, msg = sources.add(url, max_clips=count, priority=8)
    if job and job["state"] == "queued":
        msg += f" I'll make {job['max_clips']} Short{'s' if job['max_clips'] != 1 else ''} and tell you when they're ready."
    return msg


def confirm(action, yes):
    if not yes:
        return "Okay, I won't."
    if action.get("do") == "upload_approved":
        return publish.upload_approved(privacy=action.get("mode"))
    if action.get("do") == "permission":
        settings.update_channel("main", permissions={action["which"]: True})
        if action["which"] == "publish_public":
            from clipper import youtube
            return "Public publishing is on for approved clips. " + youtube.lock_note()
        return "Done, it's on."
    return "Done."


def _open(tab):
    try:
        from clipper import ROOT
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import display
        display.open_page("/clipper" + (f"#{tab}" if tab else ""))
    except Exception as e:
        print(f"(clipper: couldn't open the dashboard: {e})")
