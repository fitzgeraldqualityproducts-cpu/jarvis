"""The production stages the worker runs for each Short (registered in jobs.STAGES).

researching       picks the best ready idea (topics.pick_next) unless the job already has one
scripting         writes the first draft (script.draft_step)
fact_checking     checks the newest draft; one revision, then it must pass or the job stops
generating_audio  narrates the passed script in the channel voice and validates the audio
Later phases add rendering and validating; until then a job waits at "rendering"."""
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


def register():
    """Adds the stages (a stage that's already registered, e.g. by a test, is kept)."""
    for name, fn in (("researching", researching), ("scripting", scripting), ("fact_checking", fact_checking),
                     ("generating_audio", generating_audio)):
        if name not in jobs.STAGES:
            jobs.register(name, fn)
