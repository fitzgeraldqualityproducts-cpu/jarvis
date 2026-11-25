"""The live YouTube business dashboard: every Short sits at the right station, read-only, and the
page is served by Jarvis's display server and on its own."""
import json
import time

from test_studio_foundation import studio_home  # noqa: F401


def _line():
    from studio import config, db, guard
    config.set_values(enabled=True, publish_mode="private")
    guard.lock_channel("UC_BIZ", "HussUniversity")
    now = time.time()
    db.insert("INSERT INTO topics(title, fingerprint, status, discovered) VALUES(?,?,?,?)", "Spare idea", "fp1", "idea", now)

    def vid(title, state, **more):
        v = db.insert("INSERT INTO videos(idem_key, state, title, created, updated) VALUES(?,?,?,?,?)",
                      f"k-{title}", state, title, now - 600, now - 120)
        if more:
            db.set_video(v, **more)
        return v

    making = vid("How hot is lightning?", "generating_audio", stage_done="fact_checking")
    ready = vid("Mars sunset is blue", "ready", stage_done="validating")
    sched = vid("Why ice floats", "scheduled", stage_done="processing")
    db.insert("INSERT INTO publish_jobs(video_id, channel_id, status, youtube_id, privacy, publish_at) VALUES(?,?,?,?,?,?)",
              sched, "UC_BIZ", "done", "yt_ice", "private", str(now + 86400))
    live = vid("Scale of the Sun", "published", stage_done="scheduled", finished=now - 3600)
    db.insert("INSERT INTO published(video_id, youtube_id, channel_id, title, privacy, published_at) VALUES(?,?,?,?,?,?)",
              live, "yt_sun", "UC_BIZ", "Scale of the Sun", "public", "2026-11-20T17:00:00Z")
    db.insert("INSERT INTO analytics(youtube_id, video_id, fetched, period, source, metrics) VALUES(?,?,?,?,?,?)",
              "yt_sun", live, now, "lifetime", "data_api", json.dumps({"viewCount": 31400}))
    db.insert("INSERT INTO analytics(youtube_id, fetched, period, source, metrics) VALUES(?,?,?,?,?)",
              "UC_BIZ", now, "lifetime", "channel", json.dumps({"subscriberCount": 1284, "viewCount": 412880, "videoCount": 27}))
    failed = vid("Broken one", "failed", stage_done="generating_audio", error="RuntimeError: render crashed",
                 finished=now - 60)
    db.insert("INSERT INTO errors(t, kind, video_id, stage, detail) VALUES(?,?,?,?,?)", now - 60, "permanent", failed,
              "rendering", "RuntimeError: render crashed")
    old = vid("Old failure", "failed", stage_done="scripting", finished=now - 3 * 86400)
    return {"making": making, "ready": ready, "sched": sched, "live": live, "failed": failed, "old": old}


def test_every_short_sits_at_its_station():
    from studio import dashboard
    ids = _line()
    d = dashboard.state()
    by = {c["id"]: c for c in d["cards"]}
    labels = [s["label"] for s in d["stations"]]
    assert labels == ["Idea", "Script", "Fact-check", "Voice", "Video", "Quality check", "Ready", "Uploading", "Live"]
    assert by[ids["making"]]["station"] == 3                       # recording the narration
    assert by[ids["ready"]]["station"] == 6
    assert by[ids["sched"]]["station"] == 7 and by[ids["sched"]]["publish_at"]
    assert by[ids["failed"]]["station"] == 4 and by[ids["failed"]]["failed"]   # failed while rendering
    assert ids["old"] not in by and ids["live"] not in by            # old failures and live Shorts leave the line
    assert d["live_count"] == 1 and d["published"][0]["views"] == 31400
    assert d["stats"]["subscribers"] == 1284 and d["ideas"] == 1
    assert d["run"]["level"] == "good" and d["channel"]["title"] == "HussUniversity"
    assert any("Broken one" in e["text"] for e in d["activity"])
    assert d["next_upload"]["t"]


def test_status_shows_halts_and_off():
    from studio import autopilot, config, dashboard
    _line()
    autopilot.halt("the last 3 Shorts failed their checks")
    d = dashboard.state()
    assert d["run"]["level"] == "bad" and "3 Shorts failed" in d["run"]["text"]
    config.set_values(enabled=False)
    assert dashboard.state()["run"]["level"] == "off"


def test_reading_changes_nothing():
    from studio import dashboard, db
    _line()
    before = [dict(r) for r in db.q("SELECT * FROM videos ORDER BY id")]
    dashboard.state()
    assert [dict(r) for r in db.q("SELECT * FROM videos ORDER BY id")] == before


def test_http_routes_and_thumbnail(tmp_path):
    from studio import dashboard, data_dir
    ids = _line()
    code, body, ctype = dashboard.handle("/studio")
    assert code == 200 and b"Where each Short is right now" in body and ctype.startswith("text/html")
    code, body, _ = dashboard.handle("/studio/state")
    assert code == 200 and json.loads(body)["cards"]
    folder = data_dir("videos", str(ids["making"]))
    (folder / "scene_01.png").write_bytes(b"\x89PNG fake")
    assert dashboard.handle(f"/studio/thumb/{ids['making']}.img")[:3:2] == (200, "image/png")
    assert dashboard.handle("/studio/thumb/../../x.img")[0] == 404
    assert dashboard.handle("/clipper") is None


def test_display_server_serves_it():
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    import display
    _line()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), display._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/studio/state", timeout=5).read())
        assert d["channel"]["title"] == "HussUniversity"
    finally:
        srv.shutdown()


def test_voice_phrases_open_it():
    import re
    from conftest import ROOT
    src = (ROOT / "jarvis.py").read_text()
    ns = {"re": re}
    exec(src[src.index("STUDIO_VIEW = "):src.index("def studio_dashboard_command")], ns)
    p = ns["STUDIO_VIEW"]
    for t in ("show me the youtube dashboard", "open the studio dashboard", "pull up the channel dashboard",
              "i want to watch the channel run", "where are the youtube shorts at"):
        assert p.search(t), t
    for t in ("show me the shorts dashboard", "open the clipper", "show me my clips", "show me youtube stats"):
        assert not p.search(t), t


def test_stage_clock_only_restarts_on_a_new_stage():
    from studio import db
    v = db.insert("INSERT INTO videos(idem_key, state, created, updated) VALUES(?,?,?,?)", "k", "rendering", 1, 1)
    db.set_video(v, state="rendering")
    first = db.video(v)["state_since"]
    db.set_video(v, meta="{}")
    db.set_video(v, state="rendering")
    assert db.video(v)["state_since"] == first
    db.set_video(v, state="validating")
    assert db.video(v)["state_since"] >= first


def test_charts_and_fonts_are_served_locally():
    """Tabler, ECharts and the fonts ship inside Jarvis (no internet); nothing outside vendor/studio leaks out."""
    import re
    from studio import dashboard
    page = dashboard.PAGE.read_text(encoding="utf-8")
    assert "https://" not in re.sub(r"https://(youtube\.com|tabler\.io|github\.com)[^\s\"'`]*", "", page.split("<script>", 1)[0])
    for name in re.findall(r"/studio/v/([\w.-]+)", page):
        code, body, ctype = dashboard.handle("/studio/v/" + name)
        assert code == 200 and len(body) > 1000, name
    assert dashboard.handle("/studio/v/tabler.min.css")[2].startswith("text/css")
    assert dashboard.handle("/studio/v/echarts.min.js")[2].startswith("text/javascript")
    for bad in ("/studio/v/../../studio/config.py", "/studio/v/%2e%2e%2fconfig.json", "/studio/v/LICENSES.txt",
                "/studio/v/nope.js", "/studio/v/.hidden.css"):
        assert dashboard.handle(bad)[0] == 404, bad


def test_history_gives_one_reading_a_day():
    from studio import dashboard, db
    _line()
    now = time.time()
    for days_ago, subs, views in ((3, 1000, 300000), (2, 1100, 350000), (2.0001, 1050, 340000), (1, 1200, 400000), (40, 5, 50)):
        db.insert("INSERT INTO analytics(youtube_id, fetched, period, source, metrics) VALUES(?,?,?,?,?)",
                  "UC_BIZ", now - days_ago * 86400, "lifetime", "channel", json.dumps({"subscriberCount": subs, "viewCount": views}))
    h = dashboard.state(now)["history"]
    assert [x["subs"] for x in h] == [1000, 1100, 1200, 1284]          # last reading of each day; 40 days ago left out
    assert h[-1]["views"] == 412880 and all(x["live"] >= 0 for x in h)
    assert h[-1]["live"] == 1
