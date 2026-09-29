from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run adaptive-kg-reasoning v0.2.0 sliding-window trace"
    )
    parser.add_argument("--events", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--width", type=int, default=3_600)
    parser.add_argument("--slide", type=int, default=60)
    parser.add_argument("--property", dest="property_filter", type=int, default=1)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()

    data_path = ROOT / "data" / "raw" / "debs_sample_synthetic.csv"
    if args.regenerate or not data_path.exists():
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)

    events = load_events(data_path, property_filter=args.property_filter)
    windows = list(
        iter_sliding_windows(
            events,
            width_seconds=args.width,
            slide_seconds=args.slide,
            flush=args.flush,
        )
    )

    output = ROOT / "results" / "window_trace_v0_2_0.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "window_index",
            "start_timestamp",
            "end_timestamp",
            "active_events",
            "events_added",
            "events_expired",
            "added_ids",
            "expired_ids",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for window in windows:
            writer.writerow(
                {
                    "window_index": window.index,
                    "start_timestamp": window.start_timestamp,
                    "end_timestamp": window.end_timestamp,
                    "active_events": window.active_count,
                    "events_added": len(window.added),
                    "events_expired": len(window.expired),
                    "added_ids": ";".join(str(value) for value in window.added_ids),
                    "expired_ids": ";".join(str(value) for value in window.expired_ids),
                }
            )

    print(
        f"events={len(events)} windows={len(windows)} "
        f"width={args.width} slide={args.slide} flush={args.flush}"
    )
    print(f"results={output}")


if __name__ == "__main__":
    main()
