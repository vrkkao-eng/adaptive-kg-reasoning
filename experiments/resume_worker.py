"""Resume one existing v0.6 fixed-trace job in a new operating-system process."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.process_resume import POINTS, run_worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--pause-point", choices=POINTS)
    parser.add_argument("--pause-window", type=int, default=2)
    args = parser.parse_args()

    def hook(point, index):
        if point == args.pause_point and index == args.pause_window:
            print(json.dumps({"event": "kill_ready", "point": point, "window_index": index}), flush=True)
            # Test rendezvous only. Parent kills this process without graceful cleanup.
            if sys.stdin.readline() != "continue\n":
                raise RuntimeError("Fault rendezvous lost its supervisor")
    print(json.dumps(run_worker(args.job_dir, hook=hook)), flush=True)


if __name__ == "__main__":
    main()
