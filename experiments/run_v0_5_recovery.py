"""Compare fixed-site crash handling, cold rebuild and checkpoint replay."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning import __version__
from adaptive_kg_reasoning.adaptive import query_schedule
from adaptive_kg_reasoning.evidence import create_run_dir, new_manifest, save_manifest, write_csv
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.placement import load_config
from adaptive_kg_reasoning.recovery import FaultSpec, POLICIES, benchmark_recovery
from adaptive_kg_reasoning.recovery_acceptance import validate_recovery_matrix
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=ROOT / "configs/placement_profiles.json")
    parser.add_argument("--node", default="fog", choices=("edge", "fog", "cloud"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold", type=float, default=450.)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--slide", type=int, default=30)
    parser.add_argument("--workloads", default="none,sparse,dense,bursty")
    parser.add_argument("--faults", default="none,before_update,after_update,after_checkpoint,persistent_crash")
    parser.add_argument("--fault-window", type=int, default=2)
    parser.add_argument("--checkpoint-every", type=int, default=2)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--require-expected-outcomes", action="store_true",
                        help="Fail unless every feasible-fixture case meets the service/accounting contract")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.events <= 0 or not math.isfinite(args.threshold):
        parser.error("events must be positive and threshold must be finite")
    run_dir = create_run_dir(ROOT, args.output_dir)
    config = {**vars(args), "input": str(args.input) if args.input else None,
              "profiles": str(args.profiles), "output_dir": str(run_dir),
              "generated": args.input is None, "generator_seed": args.seed if args.input is None else None}
    manifest = new_manifest(ROOT, run_dir, benchmark=f"v{__version__}", config=config)
    manifest["acceptance_status"] = "pending" if args.require_expected_outcomes else "not_requested"
    try:
        run(args, run_dir, manifest)
        manifest["status"] = "passed"
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        save_manifest(run_dir, manifest)


def run(args, run_dir, manifest):
    profile_path, data_path = run_dir / "profiles.json", run_dir / "input.csv"
    shutil.copyfile(args.profiles, profile_path)
    config = load_config(profile_path)
    node = next(node for node in config.nodes if node.name == args.node)
    if args.input:
        shutil.copyfile(args.input, data_path)
    else:
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)
    events = load_events(data_path, property_filter=1)
    if not events:
        raise ValueError("Recovery benchmark requires at least one load event")
    manifest["loaded_load_events"] = len(events)
    windows = list(iter_sliding_windows(events, width_seconds=args.width,
                                       slide_seconds=args.slide, flush=args.flush))
    workloads, faults = args.workloads.split(","), args.faults.split(",")
    if len(set(workloads)) != len(workloads) or len(set(faults)) != len(faults):
        raise ValueError("Workloads and fault scenarios must be unique")
    specs = {}
    for name in faults:
        if name == "none":
            specs[name] = FaultSpec()
        elif name in ("before_update", "after_update", "after_checkpoint", "persistent_crash"):
            specs[name] = FaultSpec(args.fault_window, "after_update" if name == "persistent_crash" else name,
                                    args.max_retries + 1 if name == "persistent_crash" else 1)
        else:
            raise ValueError(f"Unknown fault scenario: {name}")
    details, summaries, audits, schedules = [], [], [], []
    for workload_index, workload in enumerate(workloads):
        queries = query_schedule(workload, len(windows))
        schedules.append({"workload": workload, "queries": queries})
        for fault_index, (name, spec) in enumerate(specs.items()):
            for policy in POLICIES:
                context = {"workload": workload, "fault": name, "policy": policy}
                path = run_dir / f"checkpoint-{workload_index}-{fault_index}-{policy}.json"
                rows, summary, audit = benchmark_recovery(
                    windows, queries, policy=policy, fault=spec, checkpoint_path=path,
                    node=node, memory=config.memory, threshold=args.threshold,
                    checkpoint_every=args.checkpoint_every, max_retries=args.max_retries)
                details.extend({**context, **row} for row in rows)
                summaries.append({**context, **summary})
                audits.extend({**context, **row} for row in audit)
    write_csv(run_dir / "detail.csv", details)
    write_csv(run_dir / "summary.csv", summaries)
    (run_dir / "audit.json").write_text(json.dumps(audits, indent=2) + "\n", encoding="utf-8")
    (run_dir / "workloads.json").write_text(json.dumps(schedules, indent=2) + "\n", encoding="utf-8")
    outcomes = {name: sum(row["status"] == name for row in summaries)
                for name in sorted({row["status"] for row in summaries})}
    manifest.update(recovery_outcomes=outcomes,
                    checked_states=sum(row["checked_states"] for row in summaries),
                    checked_queries=sum(row["checked_queries"] for row in summaries))
    if args.require_expected_outcomes:
        report = validate_recovery_matrix(summaries, details, audits, schedules=schedules,
                                          specs=specs, max_retries=args.max_retries)
        (run_dir / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        manifest["acceptance_status"] = report["status"]
        if report["status"] != "passed":
            raise AssertionError("Recovery acceptance failed; inspect acceptance.json and retained outcomes")
    print(f"passed: {len(summaries)} recovery summaries; outcomes={outcomes}; "
          f"{manifest['checked_queries']} served queries checked; evidence={run_dir}")


if __name__ == "__main__":
    main()
