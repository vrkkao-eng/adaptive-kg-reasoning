"""Compare uninterrupted execution, stopped processes and bounded new-process resume."""
import argparse
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning import __version__
from adaptive_kg_reasoning.adaptive import query_schedule
from adaptive_kg_reasoning.evidence import create_run_dir, new_manifest, save_manifest, sha256, write_csv
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.process_resume import CONTRACT, POINTS, prepare_job
from adaptive_kg_reasoning.resume_experiment import execute_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--profiles", type=Path, default=ROOT / "configs/placement_profiles.json")
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--slide", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=450.)
    parser.add_argument("--node", choices=("edge", "fog", "cloud"), default="fog")
    parser.add_argument("--workloads", default="none,sparse,dense,bursty")
    parser.add_argument("--fault-window", type=int, default=2)
    parser.add_argument("--max-restarts", type=int, default=2)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.events <= 0 or not math.isfinite(args.threshold) or args.max_restarts < 0 or args.fault_window < 0:
        parser.error("Expected positive events, finite threshold and non-negative fault/restart bounds")
    run_dir = create_run_dir(ROOT, args.output_dir)
    config = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    manifest = new_manifest(ROOT, run_dir, benchmark="v0.6.0", config=config)
    manifest.update(package_version=__version__, contract=CONTRACT, acceptance_status="pending",
                    generated=args.input is None, generator_seed=args.seed if args.input is None else None)
    manifest["environment"]["sqlite"] = sqlite3.sqlite_version
    summaries = []
    try:
        data, profiles = run_dir / "input.csv", run_dir / "profiles.json"
        shutil.copyfile(args.profiles, profiles)
        if args.input:
            shutil.copyfile(args.input, data)
        else:
            generate_debs_shaped_csv(data, n_events=args.events, seed=args.seed)
        workloads = args.workloads.split(",")
        if len(set(workloads)) != len(workloads):
            raise ValueError("Workloads must be unique")
        for workload in workloads:
            query_schedule(workload, 1)  # Validate before using a workload in a directory name.
        scenarios = [("none", None, "resume", False)]
        scenarios += [(f"{point}-{policy}", point, policy, False) for point in POINTS for policy in ("stop", "resume")]
        scenarios.append(("persistent-before_commit", "before_commit", "resume", True))
        for workload in workloads:
            for name, point, policy, persistent in scenarios:
                job_dir = run_dir / f"{workload}-{name}"
                prepare_job(job_dir, input_path=data, profiles_path=profiles, width=args.width, slide=args.slide,
                            flush=args.flush, threshold=args.threshold, workload=workload, node=args.node)
                row = execute_case(ROOT / "experiments/resume_worker.py", job_dir, point=point, policy=policy,
                                   persistent=persistent, fault_window=args.fault_window, max_restarts=args.max_restarts)
                summaries.append({"workload": workload, "scenario": name, **row})
                write_csv(run_dir / "summary.csv", summaries)
        acceptance = {"schema_version": 1, "contract": CONTRACT,
                      "status": "passed" if all(row["acceptance"] == "passed" for row in summaries) else "failed",
                      "cases": [{key: row[key] for key in ("workload", "scenario", "acceptance", "mismatches")}
                                for row in summaries]}
        (run_dir / "acceptance.json").write_text(json.dumps(acceptance, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        manifest["acceptance_status"] = acceptance["status"]
        if acceptance["status"] != "passed":
            raise AssertionError("Process-resume acceptance failed")
        manifest["status"] = "passed"
        print(f"passed: {len(summaries)} process-resume cases; evidence={run_dir}")
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        manifest["completed_cases"] = len(summaries)
        save_manifest(run_dir, manifest)
        # Jobs are nested, and remain independently resumable/inspectable. Hash all
        # retained artifacts only after every child connection is closed.
        manifest["artifacts_sha256"] = {path.relative_to(run_dir).as_posix(): sha256(path)
                                       for path in sorted(run_dir.rglob("*"))
                                       if path.is_file() and path != run_dir / "manifest.json"}
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
