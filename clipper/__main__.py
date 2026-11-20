"""Command line for the Shorts Clipper (Jarvis normally does all of this for you).

    python -m clipper add <link or file> [--clips N]   queue a video
    python -m clipper run                              run the worker + scheduler without Jarvis
    python -m clipper status | report | pending
    python -m clipper approve <clip> | reject <clip> | upload-approved
    python -m clipper grant <video id>                 you confirm you have permission for a video
    python -m clipper source <kind> <value> [--ok]     add an approved source (twitch_channel, youtube_channel,
                                                       youtube_search, folder, creator)
    python -m clipper discover                         check sources now
    python -m clipper stop | resume | pause            emergency stop / resume / pause
    python -m clipper connect-youtube [client.json]    Google sign-in (the Connect YouTube file runs this)
    python -m clipper job <id>                         (internal) run one job's stages
"""
import sys
import time


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    from clipper import db, jobs, publish, settings, sources, status
    if cmd == "job":
        from clipper import pipeline
        return pipeline.run_job(int(rest[0]))
    if cmd == "add":
        clips = None
        if "--clips" in rest:
            i = rest.index("--clips")
            clips = int(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        v, job, msg = sources.add(" ".join(rest), max_clips=clips)
        print(msg)
        return 0
    if cmd == "run":
        jobs.start()
        print("Clipper running. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(30)
                print(status.say_status())
        except KeyboardInterrupt:
            jobs.shutdown()
        return 0
    if cmd == "status":
        print(status.say_status())
        for j in status.jobs(20):
            print(f"  job {j['id']:>4}  {j['state']:<22} {int((j['progress'] or 0) * 100):>3}%  {str(j['title'])[:60]}")
        return 0
    if cmd == "report":
        print(status.today_report())
        return 0
    if cmd == "pending":
        for c in status.pending(50):
            print(f"  clip {c['id']:>4}  {c['score']:.1f}  {c['duration']:.0f}s  {c['title']}\n             {c['path']}")
        return 0
    if cmd == "approve":
        print(publish.approve(int(rest[0])))
        return 0
    if cmd == "reject":
        print(publish.reject(int(rest[0]), " ".join(rest[1:])))
        return 0
    if cmd == "upload-approved":
        print(publish.upload_approved())
        return 0
    if cmd == "grant":
        print(sources.grant(int(rest[0])))
        return 0
    if cmd == "source":
        s = sources.add_source(rest[0], rest[1], authorized="--ok" in rest)
        print(f"Added {s['kind']} {s['value']} ({'authorized' if s['authorized'] else 'needs your OK per video'}).")
        return 0
    if cmd == "discover":
        found = sources.discover("main")
        print(f"Found {len(found)} new video(s).")
        return 0
    if cmd == "stop":
        print(jobs.emergency_stop())
        return 0
    if cmd == "resume":
        print(jobs.resume())
        return 0
    if cmd == "pause":
        print(jobs.pause())
        return 0
    if cmd == "connect-youtube":
        from clipper import youtube
        if rest:
            youtube.save_client(rest[0])
        print("Connected:", youtube.sign_in())
        settings.update_channel("main", permissions={"upload_private": True})
        print("Private uploads are switched on. Public publishing stays off until you turn it on.")
        return 0
    print(f"Unknown command {cmd}. Try: python -m clipper --help")
    db.where_am_i()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
