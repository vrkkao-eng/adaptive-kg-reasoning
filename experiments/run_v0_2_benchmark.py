from __future__ import annotations

import argparse
import math
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.evidence import create_run_dir, new_manifest, save_manifest, write_csv
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.metrics import compare_window, summarise
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def parse_scenarios(text: str) -> list[tuple[int,int]]:
    scenarios=[]
    for item in text.split(","):
        width,slide=item.split(":",1)
        scenarios.append((int(width),int(slide)))
    return scenarios


def main() -> None:
    parser=argparse.ArgumentParser(description="Run v0.2.3 recomputation vs incremental benchmark")
    parser.add_argument("--events",type=int,default=5_000)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--threshold",type=float,default=450.0)
    parser.add_argument("--scenarios",default="3600:60,3600:300,3600:900")
    parser.add_argument("--flush",action="store_true")
    parser.add_argument("--regenerate",action="store_true")
    parser.add_argument("--input", type=Path, help="Existing CSV; mutually exclusive with --regenerate")
    parser.add_argument("--output-dir", type=Path, help="New directory; existing directories are rejected")
    args=parser.parse_args()
    if args.input and args.regenerate:
        parser.error("--input and --regenerate are mutually exclusive")
    if args.events <= 0:
        parser.error("--events must be positive")
    if not math.isfinite(args.threshold):
        parser.error("--threshold must be finite")
    run_dir = create_run_dir(ROOT, args.output_dir)
    generated = args.regenerate or (not args.input and not (ROOT/"data/raw/debs_sample_synthetic.csv").exists())
    config = {**vars(args), "input": str(args.input) if args.input else None,
              "output_dir": str(run_dir), "generated": generated,
              "generator_seed": args.seed if generated else None}
    manifest = new_manifest(ROOT, run_dir, benchmark="v0.2.4", config=config)
    try:
        run(args, run_dir, manifest, generated)
        manifest["status"] = "passed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save_manifest(run_dir, manifest)


def run(args, run_dir: Path, manifest: dict, generated: bool) -> None:
    data_path = run_dir / "input.csv"
    if generated:
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)
    else:
        shutil.copyfile(args.input or ROOT/"data/raw/debs_sample_synthetic.csv", data_path)
    events=load_events(data_path,property_filter=1)
    if not events:
        raise ValueError("Benchmark requires at least one load event")
    manifest["loaded_load_events"] = len(events)

    detail_rows=[]
    summaries=[]
    for width,slide in parse_scenarios(args.scenarios):
        state=IncrementalHighRecentState(threshold_watts=args.threshold)
        rows=[]
        for window in iter_sliding_windows(events,width_seconds=width,slide_seconds=slide,flush=args.flush):
            row,_,_=compare_window(window,state,threshold_watts=args.threshold)
            if not row.result_equivalent:
                raise AssertionError(
                    f"divergence scenario={width}/{slide} window={window.index} "
                    f"symmetric_difference={row.symmetric_difference_count}"
                )
            rows.append(row)
            record=asdict(row)
            record={"scenario":f"{width}/{slide}",**record}
            detail_rows.append(record)
        summaries.append(summarise(rows,scenario=f"{width}/{slide}"))

    detail_path=run_dir/"detail.csv"
    summary_path=run_dir/"summary.csv"
    write_csv(detail_path,detail_rows)
    summary_rows=[asdict(item) for item in summaries]
    write_csv(summary_path,summary_rows)

    print(f"events={len(events)} threshold={args.threshold} flush={args.flush}")
    for summary in summaries:
        print(
            f"scenario={summary.scenario} windows={summary.windows} "
            f"equivalent={summary.equivalent_windows}/{summary.windows} "
            f"recompute_total_ms={summary.recomputation_ms_total:.6f} "
            f"incremental_total_ms={summary.incremental_ms_total:.6f} "
            f"total_speedup={summary.total_speedup_ratio:.3f}x"
        )
    print(f"detail={detail_path}")
    print(f"summary={summary_path}")


if __name__=="__main__":
    main()
