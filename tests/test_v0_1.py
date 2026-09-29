from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.benchmark import run_benchmark
from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.graph_builder import build_graph
from adaptive_kg_reasoning.reasoning import derive_stable_facts


def test_generate_and_build(tmp_path):
    csv_path = generate_debs_shaped_csv(tmp_path / "sample.csv", n_events=200, seed=7)
    graph = build_graph(csv_path)
    assert len(graph) > 200
    assert len(derive_stable_facts(graph)) > 0


def test_strategies_return_same_answers(tmp_path):
    csv_path = generate_debs_shaped_csv(tmp_path / "sample.csv", n_events=600, seed=13)
    results = run_benchmark(csv_path, repetitions=2)
    by_strategy = {r.strategy: r for r in results}

    assert by_strategy["full"].stable_results == by_strategy["query_time"].stable_results
    assert by_strategy["full"].semi_results == by_strategy["query_time"].semi_results
    assert by_strategy["full"].volatile_results == by_strategy["query_time"].volatile_results
    assert by_strategy["selective"].stable_results == by_strategy["full"].stable_results
    assert by_strategy["selective"].semi_results == by_strategy["full"].semi_results
    assert by_strategy["selective"].volatile_results == by_strategy["full"].volatile_results


def test_materialisation_order(tmp_path):
    csv_path = generate_debs_shaped_csv(tmp_path / "sample.csv", n_events=500, seed=21)
    results = run_benchmark(csv_path, repetitions=1)
    by_strategy = {r.strategy: r for r in results}
    assert by_strategy["full"].derived_triples_materialised >= by_strategy["selective"].derived_triples_materialised
    assert by_strategy["query_time"].derived_triples_materialised == 0
