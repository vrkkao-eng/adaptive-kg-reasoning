from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.recompute import recompute_window
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run adaptive-kg-reasoning v0.2.2 incremental maintenance check"
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

    state = IncrementalHighRecentState(threshold_watts=args.threshold)
    output = ROOT / "results" / "incremental_v0_2_2.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for current in windows:
        incremental = state.apply(current)
        oracle = recompute_window(current, threshold_watts=args.threshold)
        false_additions = incremental.facts - oracle.facts
        missed_facts = oracle.facts - incremental.facts
        symmetric_difference = incremental.facts ^ oracle.facts
        equivalent = not symmetric_difference

        rows.append(
            {
                "window_index": current.index,
                "start_timestamp": current.start_timestamp,
                "end_timestamp": current.end_timestamp,
                "events_added": incremental.events_added,
                "events_expired": incremental.events_expired,
                "affected_entities": incremental.affected_entities,
                "incremental_update_ms": f"{incremental.incremental_update_ms:.6f}",
                "triples_added": incremental.triples_added,
                "triples_retracted": incremental.triples_retracted,
                "materialised_fact_count": incremental.materialised_fact_count,
                "oracle_fact_count": len(oracle.facts),
                "result_equivalent": equivalent,
                "symmetric_difference_count": len(symmetric_difference),
                "false_additions": len(false_additions),
                "missed_facts": len(missed_facts),
            }
        )

        if not equivalent:
            raise AssertionError(
                f"Incremental/oracle divergence at window {current.index}: "
                f"false_additions={len(false_additions)} missed={len(missed_facts)}"
            )

    fields = list(rows[0].keys()) if rows else [
        "window_index",
        "start_timestamp",
        "end_timestamp",
        "events_added",
        "events_expired",
        "affected_entities",
        "incremental_update_ms",
        "triples_added",
        "triples_retracted",
        "materialised_fact_count",
        "oracle_fact_count",
        "result_equivalent",
        "symmetric_difference_count",
        "false_additions",
        "missed_facts",
    ]

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"events={len(events)} windows={len(rows)} width={args.width} "
        f"slide={args.slide} threshold={args.threshold} flush={args.flush}"
    )
    print(f"equivalent_windows={sum(row['result_equivalent'] for row in rows)}/{len(rows)}")
    print(
        f"triples_added_total={sum(row['triples_added'] for row in rows)} "
        f"triples_retracted_total={sum(row['triples_retracted'] for row in rows)}"
    )
    print(f"results={output}")


if __name__ == "__main__":
    main()
