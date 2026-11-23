"""python -m studio status | check | run | toolchain | test-short [folder]
  run         the worker in the foreground (Ctrl+C stops it safely)
  toolchain   checks every free tool is installed, with versions and licences
  test-short  makes one real 1080x1920 test Short with narration and times each step (never uploads)"""
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
    elif cmd == "toolchain":
        from studio import toolchain
        print(json.dumps(toolchain.check(), indent=2, default=str))
    elif cmd == "test-short":
        from studio import ROOT, toolchain
        folder = argv[1] if len(argv) > 1 else str(ROOT / "studio_data" / "test-short")
        print(json.dumps(toolchain.benchmark(folder), indent=2, default=str))
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
