"""Compare four strategies on identical windows and request schedules."""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.adaptive import (
    STRATEGIES, CostModel, benchmark_strategy, query_schedule, summarise_strategy,
)
from adaptive_kg_reasoning.evidence import create_run_dir, new_manifest, save_manifest, write_csv
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--scenarios", default="3600:60,3600:300,3600:900")
    parser.add_argument("--workloads", default="none,sparse,dense,bursty")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=450.0)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--memory-price", type=float, default=.02,
                        help="Synthetic units per retained support-event/fact per window")
    args = parser.parse_args()
    if args.events <= 0 or args.repetitions <= 0 or not math.isfinite(args.threshold):
        parser.error("events/repetitions must be positive and threshold must be finite")
    model = CostModel(retain_item=args.memory_price)
    run_dir = create_run_dir(ROOT, args.output_dir)
    config = {**vars(args), "input": str(args.input) if args.input else None,
              "output_dir": str(run_dir), "generated": args.input is None,
              "generator_seed": args.seed if args.input is None else None,
              "cost_model": asdict(model)}
    manifest = new_manifest(ROOT, run_dir, benchmark="v0.3.0", config=config)
    try:
        run(args, model, run_dir, manifest)
        manifest["status"] = "passed"
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        save_manifest(run_dir, manifest)


def run(args, model, run_dir, manifest):
    source = run_dir / "input.csv"
    if args.input:
        shutil.copyfile(args.input, source)
    else:
        generate_debs_shaped_csv(source, n_events=args.events, seed=args.seed)
    events = load_events(source, property_filter=1)
    if not events:
        raise ValueError("Benchmark requires at least one load event")
    manifest["loaded_load_events"] = len(events)
    details, summaries, schedules, execution_order = [], [], [], []
    rng = random.Random(args.seed)
    for scenario in args.scenarios.split(","):
        width, slide = map(int, scenario.split(":"))
        windows = list(iter_sliding_windows(events, width_seconds=width,
                                           slide_seconds=slide, flush=args.flush))
        for workload in args.workloads.split(","):
            counts = query_schedule(workload, len(windows))
            schedules.append({"scenario": scenario, "workload": workload, "queries": counts})
            for repetition in range(args.repetitions):
                order = list(STRATEGIES)
                rng.shuffle(order)
                context = {"scenario": scenario, "workload": workload, "repetition": repetition}
                execution_order.append({**context, "strategies": order})
                for strategy in order:
                    rows = benchmark_strategy(windows, counts, strategy=strategy,
                                              threshold=args.threshold, model=model)
                    details.extend({**context, **row} for row in rows)
                    summaries.append({**context, **summarise_strategy(rows)})
    manifest["execution_order"] = execution_order
    write_csv(run_dir / "detail.csv", details)
    write_csv(run_dir / "summary.csv", summaries)
    (run_dir / "workloads.json").write_text(json.dumps(schedules, indent=2) + "\n", encoding="utf-8")
    print(f"passed: {len(summaries)} strategy runs; "
          f"{sum(r['checked_queries'] for r in summaries)} checked queries; "
          f"evidence={run_dir}")


if __name__ == "__main__":
    main()
