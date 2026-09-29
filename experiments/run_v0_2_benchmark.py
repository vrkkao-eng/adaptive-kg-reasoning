from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.metrics import compare_window, summarise
from adaptive_kg_reasoning.windows import iter_sliding_windows, load_events


def parse_scenarios(text: str) -> list[tuple[int,int]]:
    scenarios=[]
    for item in text.split(","):
        width,slide=item.split(":",1)
        scenarios.append((int(width),int(slide)))
    return scenarios


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as fh:
        writer=csv.DictWriter(fh,fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser=argparse.ArgumentParser(description="Run v0.2.3 recomputation vs incremental benchmark")
    parser.add_argument("--events",type=int,default=5_000)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--threshold",type=float,default=450.0)
    parser.add_argument("--scenarios",default="3600:60,3600:300,3600:900")
    parser.add_argument("--flush",action="store_true")
    parser.add_argument("--regenerate",action="store_true")
    args=parser.parse_args()

    data_path=ROOT/"data"/"raw"/"debs_sample_synthetic.csv"
    if args.regenerate or not data_path.exists():
        generate_debs_shaped_csv(data_path,n_events=args.events,seed=args.seed)
    events=load_events(data_path,property_filter=1)

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

    detail_path=ROOT/"results"/"benchmark_v0_2_3_detail.csv"
    summary_path=ROOT/"results"/"benchmark_v0_2_3_summary.csv"
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
