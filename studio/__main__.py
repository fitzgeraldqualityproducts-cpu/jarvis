"""python -m studio status | check | run | toolchain | test-short [folder]
  run         the worker in the foreground (Ctrl+C stops it safely)
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
  publish-mode  shows or sets how finished Shorts go out: manual (default), private, public"""
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


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(status.overview(), indent=2, default=str))
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
    elif cmd == "publish-mode":
        return _publish_mode(argv)
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
