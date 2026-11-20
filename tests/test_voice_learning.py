"""Voice commands map to real actions; learning only acts on solid evidence."""
import time

from conftest import make_video

COMMANDS = {
    "find the best moments from this podcast": "clip",
    "process the latest videos from my approved sources": "discover",
    "generate five shorts from this video": "clip",
    "prioritize clips with strong opening hooks": "prioritize",
    "use my premium podcast caption style": "style",
    "show me the clips awaiting approval": "show",
    "upload the approved clips": "upload_approved",
    "pause all clipping jobs": "pause",
    "show me today's processing report": "report",
    "analyze which of my last 20 shorts performed best": "analyze",
    "use the highest-performing editing style for future clips": "best_style",
    "clip my last stream": "clip",
    "make 3 clips from my latest twitch vod": "clip",
    "emergency stop": "stop",
    "resume clipping": "resume",
    "turn on public publishing": "permission",
}
NOT_CLIPPER = ["play some music", "what's the weather", "stop the music", "open safari", "get the team on a business plan",
               "how was my last stream", "stop talking"]


def test_intents():
    from clipper import intents
    for text, want in COMMANDS.items():
        got = intents.parse(text.lower(), text)
        assert got and got[0] == want, (text, got)
    assert intents.parse("generate five shorts from this video")[1]["count"] == 5
    assert intents.parse("clip https://youtu.be/AbCdEfGhIjK", "clip https://youtu.be/AbCdEfGhIjK")[1]["url"] == "https://youtu.be/AbCdEfGhIjK"
    for text in NOT_CLIPPER:
        assert intents.parse(text) is None, text


def test_control_really_acts(tmp_path, monkeypatch):
    from clipper import control, db, settings
    settings.ensure_default_channel()
    say, confirm = control.run("stop", {})
    assert settings.stopped() and confirm is None
    control.run("resume", {})
    assert not settings.stopped()
    control.run("style", {"style": "premium"})
    assert settings.channel()["caption_style"] == "premium"
    say, confirm = control.run("permission", {"which": "publish_public", "on": True})
    assert confirm and not settings.channel()["permissions"]["publish_public"]        # asks first
    control.confirm(confirm, True)
    assert settings.channel()["permissions"]["publish_public"]
    monkeypatch.setattr(control, "clipboard", lambda: str(make_video(tmp_path / "copied.mp4", 5)))
    say, _ = control.run("clip", {"url": None, "count": 2})
    assert "queued" in say and db.one("SELECT max_clips FROM jobs")["max_clips"] == 2
    say, _ = control.run("upload_approved", {})
    assert "no approved clips" in say
    assert "Nothing's running" in control.run("status", {})[0] or "queue" in control.run("status", {})[0]


def _published(n, style, pct, views):
    from clipper import db
    for i in range(n):
        cid = db.add_clip(job_id=1, video_id=1, channel="main", rank=1, start=0, end=30, duration=30, title=f"{style} {i}",
                          caption_style=style, hook_type="question", layout="track", state="published", youtube_id=f"y{style}{i}")
        db.run("INSERT INTO metrics(clip_id, youtube_id, fetched, views, avg_view_pct, likes, shares, subs_gained) VALUES(?,?,?,?,?,?,?,?)",
               cid, f"y{style}{i}", time.time(), views + i, pct + (i % 3), 10, 1, 0)


def test_learning_needs_enough_data():
    from clipper import learning, settings
    settings.ensure_default_channel()
    _published(3, "bold", 90, 5000)
    _published(3, "clean", 40, 100)
    assert learning.update("main") == {}                       # 3 clips each: not enough to act on
    assert "not proof" in learning.report("main")


def test_learning_acts_on_clear_signal():
    from clipper import learning, moments, settings
    settings.ensure_default_channel()
    _published(10, "bold", 90, 5000)
    _published(10, "clean", 35, 80)
    prefs = learning.update("main")
    assert prefs.get("caption_style") == "bold"
    assert "bold" in learning.use_best_style("main") and settings.channel()["caption_style"] == "bold"
    assert all(abs(v) <= 0.5 for t in prefs.values() if isinstance(t, dict) for v in t.values())
    assert moments.duration_bucket(30) == "30_45"
