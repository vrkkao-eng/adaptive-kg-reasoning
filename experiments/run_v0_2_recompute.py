from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.recompute import recompute_window
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def _local_name(value) -> str:
    text = str(value)
    return text.rsplit("/", 1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run adaptive-kg-reasoning v0.2.1 full-window recomputation baseline"
    )
    parser.add_argument("--events", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--width", type=int, default=3_600)
    parser.add_argument("--slide", type=int, default=60)
    parser.add_argument("--threshold", type=float, default=450.0)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()

    data_path = ROOT / "data" / "raw" / "debs_sample_synthetic.csv"
    if args.regenerate or not data_path.exists():
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)

    events = load_events(data_path, property_filter=1)
    windows = iter_sliding_windows(
        events,
        width_seconds=args.width,
        slide_seconds=args.slide,
        flush=args.flush,
    )
    results = [
        recompute_window(window, threshold_watts=args.threshold)
        for window in windows
    ]

    output = ROOT / "results" / "recompute_v0_2_1.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8") as fh:
        fields = [
            "window_index",
            "start_timestamp",
            "end_timestamp",
            "active_load_events",
            "distinct_plugs",
            "recomputation_ms",
            "high_recent_count",
            "high_recent_plugs",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for result in results:
            plugs = sorted(_local_name(subject) for subject, _, _ in result.facts)
            writer.writerow(
                {
                    "window_index": result.window_index,
                    "start_timestamp": result.start_timestamp,
                    "end_timestamp": result.end_timestamp,
                    "active_load_events": result.active_load_events,
                    "distinct_plugs": result.distinct_plugs,
                    "recomputation_ms": f"{result.recomputation_ms:.6f}",
                    "high_recent_count": result.high_recent_count,
                    "high_recent_plugs": ";".join(plugs),
                }
            )

    timings = [result.recomputation_ms for result in results]
    print(
        f"events={len(events)} windows={len(results)} width={args.width} "
        f"slide={args.slide} threshold={args.threshold} flush={args.flush}"
    )
    if timings:
        print(
            f"recomputation_ms_median={statistics.median(timings):.6f} "
            f"recomputation_ms_mean={statistics.fmean(timings):.6f} "
            f"recomputation_ms_total={sum(timings):.6f}"
        )
    print(f"results={output}")


if __name__ == "__main__":
    main()
