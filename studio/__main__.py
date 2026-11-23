"""python -m studio status | check | run | toolchain | test-short [folder]
  run         the worker in the foreground (Ctrl+C stops it safely)
  usage       the studio's Claude use per day (calls, tokens, seconds, web searches, by job)
  toolchain   checks every free tool is installed, with versions and licences
  test-short  makes one real 1080x1920 test Short with narration and times each step (never uploads)
  add-key     saves a read-only YouTube Data API key for research (reads it from the keyboard)
  research    researches the candidate niches, scores them, picks one and writes the Top 10 report
              (--no-youtube skips YouTube data; --run <id> resumes a run; --rechoose "<reason>" replaces the niche)
  report      rewrites the last run's report
  brand       channel names (handles checked), logo, banner, description, brand guide
  connect     signs in to the NEW business channel and locks the studio to it
  verify      checks the connection: right channel, upload and analytics permission (changes nothing)
  protect     adds a channel (@handle or UC id) that the studio must never publish to
  ideas       finds new Short topics for the 2 pillars with the fewest ideas, scores them, lists the best
              (--all every pillar; --list only lists; --pillar "<name>")
  next        shows the idea Jarvis would make next (--take marks it as picked)
  script      makes one real script for the next idea: hooks, fact-check, narration (never uploads)
              (--topic <id> picks the idea; --no-voice skips narration)
  video       makes a finished test Short: the waiting narrated script (or a new one), visuals, captions,
              render, metadata and the QA gate (--video <id>); never uploads
  upload      uploads the newest finished Short as PRIVATE to the business channel, after you confirm
              (--video <id>); then checks YouTube processed it
  dashboard   opens the live dashboard (Jarvis's own page if Jarvis is running, else serves it itself)
  publish-mode  shows or sets how finished Shorts go out: manual (default), private, public
  drive       Google Drive backup: drive connect | drive (status) | drive backup | drive restore <video id>
              backup copies finished Shorts and reports, checks each copy, then clears Mac copies that are
              on YouTube and safe on Drive (after drive_keep_local_days; --now skips the wait)"""
import json
import sys
import time

from studio import health, jobs, status


def _script(argv):
    import subprocess
    from studio import data_dir, db, narration, script, topics
    health.startup()
    if "--topic" in argv:
        t = topics.topic(int(argv[argv.index("--topic") + 1]))
    else:
        nxt = topics.pick_next()
        t = topics.topic(nxt["id"]) if nxt else None
    if not t:
        print("No ideas ready. Double-click Find Video Ideas first.")
        return 1
    vid = jobs.create(t["id"], title=t["title"])
    db.set_video(vid, topic_id=t["id"], title=t["title"], state="scripting", stage_done="researching")
    topics.set_status(t["id"], "in_production", video_id=vid, script_status="writing")
    print(f"Idea #{t['id']}: {t['title']}  (video {vid})")
    try:
        script.make(t, vid)
    except script.ScriptRejected as e:
        db.set_video(vid, state="rejected", error=str(e), error_kind="permanent", finished=time.time())
        topics.set_status(t["id"], "rejected", reject_reason="script failed the fact-check")
        print(f"\nRejected: {e}\nThat's the safety net working: nothing unchecked goes further.")
        return 1
    folder = data_dir("videos", str(vid))
    page = folder / "script.md"
    page.write_text(script.report(vid))
    db.set_video(vid, state="generating_audio", stage_done="fact_checking")
    print("\n" + page.read_text())
    if "--no-voice" not in argv:
        print("Recording the narration...")
        rec = narration.narrate(script.latest(vid)["body"], folder / "voice.wav", vid)
        db.set_video(vid, state="rendering", stage_done="generating_audio", duration=rec["seconds"])
        print(f"Narration: {rec['seconds']} s, {rec['checks']['wpm']} words a minute, {rec['loudness']} LUFS. Checks passed.")
        if sys.platform == "darwin":
            subprocess.run(["open", str(folder / "voice.wav")])
    if sys.platform == "darwin":
        subprocess.run(["open", str(page)])
    print(f"\nSaved in {folder}. Nothing was uploaded.")
    return 0


def _video(argv):
    import subprocess
    from studio import db, qa, video
    if "--video" in argv:
        vid = int(argv[argv.index("--video") + 1])
    else:
        r = db.one("SELECT id FROM videos WHERE state='rendering' ORDER BY id DESC LIMIT 1")
        if not r:
            print("No narrated script is waiting, so writing one first.\n")
            if _script([]) != 0:
                return 1
            r = db.one("SELECT id FROM videos WHERE state='rendering' ORDER BY id DESC LIMIT 1")
        vid = r["id"]
    print(f"\nMaking video {vid}: {db.video(vid)['title']}")
    t0 = time.time()
    rep = video.render(vid, progress=lambda s: print("  " + s))
    print(f"  Rendered {rep['duration']} s in {time.time() - t0:.0f} s ({rep['timing']['method']}, "
          f"{rep['timing']['matched']:.0%} of words matched)")
    up = video.metadata(vid)
    res = qa.check(vid)
    print(f"\nTitle: {up['title']}\n\nQuality checks:")
    for c in res["checks"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}  {c['detail']}")
    db.set_video(vid, state="ready" if res["ok"] else "validating", stage_done="validating" if res["ok"] else "rendering")
    print("\nReady to publish (Phase 8 will do that)." if res["ok"] else "\nNot ready: it failed the checks above.")
    if sys.platform == "darwin":
        subprocess.run(["open", rep["path"]])
    print(f"Video: {rep['path']}\nNothing was uploaded.")
    return 0 if res["ok"] else 1


def _upload(argv):
    from studio import db, guard, publish
    health.startup()
    if "--video" in argv:
        vid = int(argv[argv.index("--video") + 1])
    else:
        r = db.one("SELECT id FROM videos WHERE state IN ('ready','uploading','processing') ORDER BY id DESC LIMIT 1")
        if not r:
            print("No finished Short is waiting. Double-click Make a Test Video first.")
            return 1
        vid = r["id"]
    v = db.video(vid)
    ch = guard.locked_channel()
    if not ch:
        print("The business channel isn't connected. Double-click Connect YouTube Business first.")
        return 1
    up = json.loads(v["meta"] or "{}").get("upload") or {}
    print(f"Video {vid}: {up.get('title') or v['title']}\nChannel: {ch['title']} ({ch['youtube_channel_id']})")
    if input("Upload it as PRIVATE (only you can see it)? (y/n) ").strip().lower() != "y":
        print("Nothing uploaded.")
        return 0
    db.set_video(vid, state="uploading")
    yid = publish.upload(vid, "private", progress=lambda f: print(f"  uploaded {f:.0%}"))
    print(f"Uploaded: https://youtu.be/{yid}\nWaiting for YouTube to process it...")
    db.set_video(vid, state="processing")
    for _ in range(40):
        r = publish.verify(vid)
        if r["state"] not in ("processing", "missing"):
            break
        time.sleep(15)
    print(f"YouTube says: {r['state']} (privacy {r.get('privacy')}, processing {r.get('processing')})")
    if r["state"] == "private":
        db.set_video(vid, state="published", stage_done="processing", finished=time.time())
        print(f"\nCheck it in YouTube Studio: https://studio.youtube.com/video/{yid}/edit")
    elif r["state"] == "failed":
        db.set_video(vid, state="failed", error=f"YouTube: {r.get('problem')}", finished=time.time())
    return 0


def _publish_mode(argv):
    from studio import config, publish
    if len(argv) < 2:
        print(f"Publish mode: {publish.mode()}  (manual | private | public)")
        return 0
    m = argv[1]
    if m not in publish.MODES:
        print("Use manual, private or public.")
        return 1
    if m == "public":
        s = publish.settings()
        print(f"Public means Jarvis publishes finished Shorts by itself, about {s['posts_per_week']} a week at "
              f"{s['publish_hour']}:00 Central, each only after passing every check.")
        if input('Type PUBLIC to allow that: ').strip() != "PUBLIC":
            print("Unchanged.")
            return 0
    config.set_values(publish_mode=m)
    from studio import guard
    guard.audit("publish.mode", True, {"mode": m})
    print(f"Publish mode is now {m}.")
    return 0


def _drive(argv):
    from studio import drive
    health.startup()
    sub = argv[1] if len(argv) > 1 else "status"
    try:
        if sub == "connect":
            print("Pick the Google account whose Drive should keep the backups, then press Allow.\n"
                  "Jarvis only gets permission for files it creates itself; it can't see anything else in your Drive.")
            me = drive.connect()
            print(f"\nConnected to Google Drive{' (' + me['email'] + ')' if me.get('email') else ''}.")
            if me.get("limit"):
                print(f"Drive space used: {me['usage'] / (1 << 30):.1f} of {me['limit'] / (1 << 30):.0f} GB.")
            sub = "backup"
        if sub == "backup":
            print("Backing up finished Shorts to Google Drive...")
            res = drive.back_up(limit=1000, say=print)
            if res["problem"] == "full":
                print("Stopped: your Google Drive is nearly full. Nothing on the Mac was removed.")
            freed = drive.free_local(urgent="--now" in argv, say=print)
            print(f"\nCopied {res['copied']} file(s), {res['bytes'] / (1 << 20):.1f} MB. "
                  f"Cleared {freed / (1 << 20):.1f} MB from the Mac.")
        elif sub == "restore":
            n = drive.restore(int(argv[2]))
            print(f"Restored {n} file(s).")
        elif sub != "status":
            print(__doc__)
            return 2
        print(drive.say())
    except drive.DriveOff as e:
        print(f"\nGoogle Drive isn't switched on for Jarvis's Google project yet. Open {e.link}, press Enable, "
              "then double-click Connect Google Drive again.")
        return 1
    except Exception as e:
        from studio import guard
        print("\nThat didn't work: " + guard.redact(str(e)))
        return 1
    return 0


def _dashboard(argv):
    """Opens Jarvis's /studio page when Jarvis is running; otherwise serves the dashboard here."""
    import subprocess
    import urllib.request
    import webbrowser
    from studio import dashboard
    url = "http://localhost:8765/studio"
    try:
        json.loads(urllib.request.urlopen(url + "/state", timeout=3).read())
        print(f"Opening {url}")
        if sys.platform == "darwin":
            subprocess.run(["open", url])
        else:
            webbrowser.open(url)
        return 0
    except Exception:
        pass
    health.startup()
    dashboard.serve(open_browser="--no-open" not in argv)
    return 0


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(status.overview(), indent=2, default=str))
    elif cmd == "usage":
        from studio import claude
        print(json.dumps(claude.usage(int(argv[1]) if len(argv) > 1 else 7), indent=2))
    elif cmd == "check":
        print(json.dumps(health.startup(), indent=2, default=str))
    elif cmd == "toolchain":
        from studio import toolchain
        print(json.dumps(toolchain.check(), indent=2, default=str))
    elif cmd == "test-short":
        from studio import ROOT, toolchain
        folder = argv[1] if len(argv) > 1 else str(ROOT / "studio_data" / "test-short")
        print(json.dumps(toolchain.benchmark(folder), indent=2, default=str))
    elif cmd == "add-key":
        import getpass
        from studio import ytpublic
        key = getpass.getpass("Paste the API key (it won't show) and press Return: ")
        ytpublic.save_key(key)
        print("Saved. Only your user can read it.")
    elif cmd in ("research", "report"):
        from studio import db, niche
        run = argv[argv.index("--run") + 1] if "--run" in argv else None
        if cmd == "report":
            print(niche.report(run or db.get("last_niche_run")))
            return 0
        health.startup()
        print("Researching niches. This takes a while (it uses YouTube's free API and your Claude plan).")
        run = niche.research(run, progress=print, skip_youtube="--no-youtube" in argv)
        reason = argv[argv.index("--rechoose") + 1] if "--rechoose" in argv else ""
        d = niche.choose(run, force=bool(reason), reason=reason)
        print(f"\nNiche: {d.get('name') or d.get('niche')}")
        print(f"Report: {niche.run_dir(run) / 'report.html'}")
    elif cmd == "ideas":
        from studio import topics
        health.startup()
        if "--list" not in argv:
            names = None
            if "--all" in argv:
                names = [p["name"] for p in topics.pillars()]
            elif "--pillar" in argv:
                names = [argv[argv.index("--pillar") + 1]]
            topics.expire()
            res = topics.discover(names)
            print(f"\nKept {len(res['added'])} new ideas, rejected {len(res['rejected'])}, dropped {res['duplicates']} repeats.")
            if res.get("revived"):
                print(f"Brought back {len(res['revived'])} earlier ideas that now have enough sources.")
            for r in res["rejected"]:
                print(f"  rejected: {r['title']} ({'; '.join(r['why'])})")
        print("\n" + topics.summary_text())
    elif cmd == "next":
        from studio import topics
        t = topics.pick_next(mark="--take" in argv)
        if not t:
            print("No ideas ready. Run: .venv/bin/python -m studio ideas")
        else:
            full = topics.topic(t["id"])
            print(f"#{t['id']} {t['title']}  (score {t['score']}, {t['pillar']}, {t['kind']})\nAngle: {t['angle']}\nHook: {t['hook']}")
            for s_ in full["sources"]:
                print(f"  [{s_['verdict']}, tier {s_['tier']}] {s_['claim']} <{s_['url']}>")
    elif cmd == "script":
        return _script(argv)
    elif cmd == "video":
        return _video(argv)
    elif cmd == "upload":
        return _upload(argv)
    elif cmd == "dashboard":
        return _dashboard(argv)
    elif cmd == "publish-mode":
        return _publish_mode(argv)
    elif cmd == "autopilot":
        from studio import autopilot, config as scfg
        health.startup()
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "off":
            scfg.set_values(autopilot=False)
            print("The autopilot is off: Jarvis won't start new Shorts by himself.")
        elif sub == "on":
            scfg.set_values(autopilot=True)
            autopilot.resume()
            print("The autopilot is on: one Short at a time, a small buffer ahead, only Shorts that pass every check.")
        print(json.dumps(autopilot.status(), indent=2, default=str))
    elif cmd == "lessons":
        from studio import lessons
        health.startup()
        print(lessons.summary())
        for area in ("ideas", "script", "video"):
            a = lessons.advice(area)
            if a:
                print(f"\n[{area}]\n{a}")
    elif cmd == "stats":
        from studio import analytics
        health.startup()
        if "--offline" not in argv:
            try:
                n = analytics.fetch()
                analytics.channel()
                from studio import eligibility
                eligibility.check()
                print(f"(read the latest numbers for {n} Short(s) from YouTube)")
            except Exception as e:
                print(f"(couldn't reach YouTube just now, showing saved numbers: {e})")
        analytics.learn()
        print(analytics.report())
    elif cmd == "brand":
        from studio import setup_channel
        return setup_channel.brand_flow()
    elif cmd == "connect":
        from studio import setup_channel
        return setup_channel.connect_flow()
    elif cmd == "verify":
        from studio import ytauth
        print(json.dumps(ytauth.verify(), indent=2, default=str))
    elif cmd == "protect":
        from studio import ytauth
        print(ytauth.protect_existing(argv[1]))
    elif cmd == "drive":
        return _drive(argv)
    elif cmd == "run":
        jobs.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("stopping safely...")
            jobs.shutdown()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
