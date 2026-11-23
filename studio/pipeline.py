"""The production stages the worker runs for each Short (registered in jobs.STAGES).

researching       picks the best ready idea (topics.pick_next) unless the job already has one
scripting         writes the first draft (script.draft_step)
fact_checking     checks the newest draft; one revision, then it must pass or the job stops
generating_audio  narrates the passed script in the channel voice and validates the audio
rendering         visual plan, word timings, original scenes, captions -> short.mp4
validating        upload metadata + the QA gate; a pass makes it "ready"
ready             waits for its publishing slot (publish_mode "manual" = waits for Connor)
uploading         resumable, idempotent upload to the locked channel (publish.upload)
processing        until YouTube finishes processing; then scheduled / published
scheduled         until the publish time, then confirms it really went public"""
import json
import time

from studio import data_dir, db, jobs, script, topics


def _topic(v):
    t = topics.topic(v["topic_id"]) if v.get("topic_id") else None
    if not t:
        raise jobs.Permanent("the job has no topic")
    return t


def researching(v, ctx):
    if not v.get("topic_id"):
        t = topics.pick_next()
        if not t:
            raise jobs.QuotaWait("no ideas are ready yet; topic discovery will add more")
        db.set_video(v["id"], topic_id=t["id"], title=t["title"])
        topics.set_status(t["id"], "in_production", video_id=v["id"], script_status="writing")
    return "scripting"


def scripting(v, ctx):
    t = _topic(v)
    if not script.latest(v["id"], "draft"):
        script.draft_step(t, v["id"])
    ctx.beat("draft written")
    return "fact_checking"


def fact_checking(v, ctx):
    t = _topic(v)
    if script.latest(v["id"], "passed"):
        return "generating_audio"
    if not script.latest(v["id"], "draft"):
        script.revise_step(t, v["id"])
        ctx.beat("revised")
    try:
        result, _ = script.check_step(t, v["id"])
    except script.ScriptRejected:
        topics.set_status(t["id"], "rejected", reject_reason="script failed the fact-check")
        raise
    if result == "revise":
        script.revise_step(t, v["id"])
        return "fact_checking"
    return "generating_audio"


def generating_audio(v, ctx):
    from studio import narration
    row = script.latest(v["id"], "passed")
    if not row:
        raise jobs.Permanent("there's no fact-checked script to narrate")
    out = data_dir("videos", str(v["id"])) / "voice.wav"
    try:
        rec = narration.narrate(row["body"], out, v["id"])
    except narration.NarrationError as e:
        raise jobs.Transient(str(e))
    if rec["seconds"] > 60:
        raise jobs.Permanent(f"the narration is {rec['seconds']:.0f} s; Shorts here must stay under a minute")
    db.set_video(v["id"], duration=rec["seconds"])
    return "rendering"


def rendering(v, ctx):
    from studio import video
    video.render(v["id"], progress=lambda s: ctx.beat(s))
    return "validating"


def validating(v, ctx):
    from studio import qa, video
    video.metadata(v["id"])
    r = qa.check(v["id"])
    if not r["ok"]:
        if v.get("topic_id"):
            topics.set_status(v["topic_id"], script_status="passed", publish_status="qa_failed")
        raise jobs.Permanent("failed QA: " + "; ".join(f"{c['name']} ({c['detail']})" for c in r["failed"][:5]))
    if v.get("topic_id"):
        topics.set_status(v["topic_id"], publish_status="ready")
    return "ready"


def ready(v, ctx):
    from studio import publish
    m = publish.mode()
    if m == "manual":
        raise jobs.Wait("ready; waiting for Connor (publish mode is manual)", time.time() + 6 * 3600)
    meta = json.loads(v.get("meta") or "{}")
    if m == "private":
        meta["publish_plan"] = {"privacy": "private", "publish_at": None}
    else:
        slot = publish.next_slot()
        if slot - time.time() > 36 * 3600:
            raise jobs.Wait("waiting for its publishing slot", slot - 30 * 3600)
        meta["publish_plan"] = {"privacy": "private", "publish_at": slot}
    db.set_video(v["id"], meta=json.dumps(meta))
    return "uploading"


def uploading(v, ctx):
    from studio import publish
    plan = json.loads(v.get("meta") or "{}").get("publish_plan") or {"privacy": "private", "publish_at": None}
    publish.upload(v["id"], plan["privacy"], plan.get("publish_at"), progress=lambda f: ctx.beat(f"uploaded {f:.0%}"),
                   stop=ctx.stopping)
    return "processing"


def processing(v, ctx):
    from studio import publish
    r = publish.verify(v["id"])
    if r["state"] in ("processing", "missing"):
        raise jobs.Wait("YouTube is still processing it", time.time() + 300)
    if r["state"] == "failed":
        raise jobs.Permanent(f"YouTube rejected the upload: {r.get('problem')}")
    if r["state"] == "scheduled":
        return "scheduled"
    if v.get("topic_id"):
        topics.set_status(v["topic_id"], "published" if r["state"] == "published" else "uploaded_private",
                          publish_status=r["state"])
    return "published"


def scheduled(v, ctx):
    from studio import publish
    r = publish.verify(v["id"])
    if r["state"] == "scheduled":
        at = (publish.job(v["id"]) or {}).get("publish_at")
        raise jobs.Wait("scheduled on YouTube", (float(at) if at else time.time()) + 600)
    if r["state"] == "failed":
        raise jobs.Permanent(f"YouTube rejected the video: {r.get('problem')}")
    if r["state"] != "published":
        raise jobs.Wait(f"expected it to be public by now; YouTube says {r['state']}", time.time() + 1800)
    if v.get("topic_id"):
        topics.set_status(v["topic_id"], "published", publish_status="published")
    return "published"


def register():
    """Adds the stages (a stage that's already registered, e.g. by a test, is kept)."""
    for name, fn in (("researching", researching), ("scripting", scripting), ("fact_checking", fact_checking),
                     ("generating_audio", generating_audio), ("rendering", rendering), ("validating", validating), ("ready", ready), ("uploading", uploading),
                     ("processing", processing), ("scheduled", scheduled)):
        if name not in jobs.STAGES:
            jobs.register(name, fn)
