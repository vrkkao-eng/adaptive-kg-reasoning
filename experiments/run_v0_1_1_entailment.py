from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.entailment import benchmark_owlrl_entailment
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.graph_builder import build_graph


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run adaptive-kg-reasoning v0.1.1 OWL-RL entailment baseline"
    )
    parser.add_argument("--events", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()

    data_path = ROOT / "data" / "raw" / "debs_sample_synthetic.csv"
    if args.regenerate or not data_path.exists():
        generate_debs_shaped_csv(data_path, n_events=args.events, seed=args.seed)

    graph = build_graph(data_path)
    result = benchmark_owlrl_entailment(graph)

    output = ROOT / "results" / "entailment_v0_1_1.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(result)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)

    print(result)
    print(f"results={output}")


if __name__ == "__main__":
    main()
