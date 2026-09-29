from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.benchmark import run_benchmark, write_results
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run adaptive-kg-reasoning v0.1 benchmark")
    parser.add_argument("--events", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()

    data_path = ROOT / "data" / "raw" / "debs_sample_synthetic.csv"
    if args.regenerate or not data_path.exists():
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)

    results = run_benchmark(data_path, repetitions=args.repetitions)
    out_csv = ROOT / "results" / "benchmark_v0_1.csv"
    out_md = ROOT / "results" / "benchmark_v0_1.md"
    write_results(results, out_csv, out_md)

    print(f"data={data_path}")
    for result in results:
        print(result)
    print(f"results={out_csv}")


if __name__ == "__main__":
    main()
