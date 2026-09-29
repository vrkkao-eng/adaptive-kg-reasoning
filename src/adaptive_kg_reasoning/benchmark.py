from __future__ import annotations

import csv
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

from rdflib import Graph

from .graph_builder import build_graph, clone_graph
from .namespaces import EX
from .reasoning import (
    derive_semi_dynamic_facts,
    derive_stable_facts,
    derive_volatile_facts,
    materialise,
)


@dataclass
class BenchmarkResult:
    strategy: str
    base_triples: int
    explicit_triples_after_materialisation: int
    derived_triples_materialised: int
    materialisation_ms: float
    peak_materialisation_kib: float
    stable_query_ms_median: float
    semi_query_ms_median: float
    volatile_query_ms_median: float
    stable_results: int
    semi_results: int
    volatile_results: int


def _timed(fn, repetitions: int) -> tuple[float, int]:
    times = []
    result_count = 0
    for _ in range(repetitions):
        start = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - start) * 1000)
        result_count = len(result)
    return median(times), result_count


def _query_materialised(graph: Graph, category: str) -> set:
    if category == "stable":
        return set(graph.triples((None, EX.deployedAt, None)))
    state = EX.HighRecentConsumption if category == "semi" else EX.CurrentOverload
    return set(graph.triples((None, EX.hasState, state)))


def run_strategy(base_graph: Graph, csv_path: str | Path, strategy: str, repetitions: int = 10) -> BenchmarkResult:
    graph = clone_graph(base_graph)
    base_triples = len(graph)

    tracemalloc.start()
    start = time.perf_counter()
    derived_count = 0
    if strategy == "full":
        derived_count += materialise(graph, derive_stable_facts(graph))
        derived_count += materialise(graph, derive_semi_dynamic_facts(csv_path))
        derived_count += materialise(graph, derive_volatile_facts(csv_path))
    elif strategy == "selective":
        derived_count += materialise(graph, derive_stable_facts(graph))
        derived_count += materialise(graph, derive_semi_dynamic_facts(csv_path))
    elif strategy != "query_time":
        raise ValueError(f"Unknown strategy: {strategy}")
    materialisation_ms = (time.perf_counter() - start) * 1000
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if strategy == "query_time":
        stable_fn = lambda: {t for t in derive_stable_facts(graph) if t[1] == EX.deployedAt}
        semi_fn = lambda: derive_semi_dynamic_facts(csv_path)
        volatile_fn = lambda: derive_volatile_facts(csv_path)
    elif strategy == "selective":
        stable_fn = lambda: _query_materialised(graph, "stable")
        semi_fn = lambda: _query_materialised(graph, "semi")
        volatile_fn = lambda: derive_volatile_facts(csv_path)
    else:
        stable_fn = lambda: _query_materialised(graph, "stable")
        semi_fn = lambda: _query_materialised(graph, "semi")
        volatile_fn = lambda: _query_materialised(graph, "volatile")

    stable_ms, stable_count = _timed(stable_fn, repetitions)
    semi_ms, semi_count = _timed(semi_fn, repetitions)
    volatile_ms, volatile_count = _timed(volatile_fn, repetitions)

    return BenchmarkResult(
        strategy=strategy,
        base_triples=base_triples,
        explicit_triples_after_materialisation=len(graph),
        derived_triples_materialised=derived_count,
        materialisation_ms=materialisation_ms,
        peak_materialisation_kib=peak_bytes / 1024.0,
        stable_query_ms_median=stable_ms,
        semi_query_ms_median=semi_ms,
        volatile_query_ms_median=volatile_ms,
        stable_results=stable_count,
        semi_results=semi_count,
        volatile_results=volatile_count,
    )


def run_benchmark(csv_path: str | Path, repetitions: int = 10) -> list[BenchmarkResult]:
    base_graph = build_graph(csv_path)
    return [
        run_strategy(base_graph, csv_path, strategy, repetitions)
        for strategy in ["full", "query_time", "selective"]
    ]


def write_results(results: list[BenchmarkResult], csv_output: str | Path, md_output: str | Path) -> None:
    csv_output = Path(csv_output)
    md_output = Path(md_output)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)

    rows = [asdict(r) for r in results]
    with csv_output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    headers = list(rows[0].keys())
    with md_output.open("w", encoding="utf-8") as fh:
        fh.write("# v0.1 benchmark result\n\n")
        fh.write("Synthetic DEBS-shaped stream; values depend on machine/runtime.\n\n")
        fh.write("| " + " | ".join(headers) + " |\n")
        fh.write("|" + "|".join(["---"] * len(headers)) + "|\n")
        for row in rows:
            vals = []
            for h in headers:
                v = row[h]
                vals.append(f"{v:.3f}" if isinstance(v, float) else str(v))
            fh.write("| " + " | ".join(vals) + " |\n")
