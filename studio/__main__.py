"""python -m studio status | check | run   (run = the worker in the foreground, Ctrl+C stops it)"""
import json
import sys
import time

from studio import health, jobs, status


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(status.overview(), indent=2, default=str))
    elif cmd == "check":
        print(json.dumps(health.startup(), indent=2, default=str))
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
