"""Compare fixed edge/fog/cloud placement with an estimate-based selection."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.adaptive import query_schedule
from adaptive_kg_reasoning.evidence import create_run_dir, new_manifest, save_manifest, write_csv
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.placement import (
    build_reference_trace, evaluate_fixed, evaluate_selection, load_config, plan_placement,
)
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=ROOT / "configs/placement_profiles.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold", type=float, default=450.0)
    parser.add_argument("--scenarios", default="3600:60,3600:300,3600:900",
                        help="Window width:slide pairs, independent of placement profile scenarios")
    parser.add_argument("--workloads", default="none,sparse,dense,bursty")
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.events <= 0 or not math.isfinite(args.threshold):
        parser.error("events must be positive and threshold must be finite")
    run_dir = create_run_dir(ROOT, args.output_dir)
    config = {**vars(args), "input": str(args.input) if args.input else None,
              "profiles": str(args.profiles), "output_dir": str(run_dir),
              "generated": args.input is None, "generator_seed": args.seed if args.input is None else None}
    manifest = new_manifest(ROOT, run_dir, benchmark="v0.4.0", config=config)
    try:
        run(args, run_dir, manifest)
        manifest["status"] = "passed"
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        save_manifest(run_dir, manifest)


def run(args, run_dir: Path, manifest: dict) -> None:
    # Make and persist every planning decision before loading the evaluation stream.
    profile_path = run_dir / "profiles.json"
    shutil.copyfile(args.profiles, profile_path)
    config = load_config(profile_path)
    decisions = {scenario.name: plan_placement(config, scenario) for scenario in config.scenarios}
    write_csv(run_dir / "planning.csv", [row for _, candidates in decisions.values() for row in candidates])
    manifest["selected_nodes"] = {name: node for name, (node, _) in decisions.items()}
    data_path = run_dir / "input.csv"
    if args.input:
        shutil.copyfile(args.input, data_path)
    else:
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)
    events = load_events(data_path, property_filter=1)
    if not events:
        raise ValueError("Placement benchmark requires at least one load event")
    manifest["loaded_load_events"] = len(events)
    reference, details, summaries, schedules = [], [], [], []
    for window_scenario in args.scenarios.split(","):
        width, slide = map(int, window_scenario.split(":"))
        windows = list(iter_sliding_windows(events, width_seconds=width,
                                           slide_seconds=slide, flush=args.flush))
        for workload in args.workloads.split(","):
            context = {"window_scenario": window_scenario, "workload": workload}
            counts = query_schedule(workload, len(windows))
            schedules.append({**context, "queries": counts})
            trace = build_reference_trace(windows, counts, threshold=args.threshold)
            reference.extend({**context, **row} for row in trace)
            for scenario in config.scenarios:
                fixed_summaries = []
                for node in config.nodes:
                    rows, summary = evaluate_fixed(trace, node=node, config=config, scenario=scenario)
                    details.extend({**context, **row} for row in rows)
                    fixed_summaries.append(summary)
                summaries.extend({**context, **row} for row in fixed_summaries)
                summaries.append({**context, **evaluate_selection(decisions[scenario.name][0], fixed_summaries)})
    write_csv(run_dir / "reference.csv", reference)
    write_csv(run_dir / "detail.csv", details)
    write_csv(run_dir / "summary.csv", summaries)
    (run_dir / "workloads.json").write_text(json.dumps(schedules, indent=2) + "\n", encoding="utf-8")
    outcomes = {name: sum(r["status"] == name for r in summaries)
                for name in ("feasible", "infeasible", "no_feasible_estimate")}
    manifest.update(reference_checked_queries=sum(r["reference_checked_queries"] for r in reference),
                    reference_checked_states=len(reference), placement_outcomes=outcomes)
    print(f"passed: {len(summaries)} placement summaries; outcomes={outcomes}; "
          f"{manifest['reference_checked_queries']} unique reference queries checked; evidence={run_dir}")


if __name__ == "__main__":
    main()
