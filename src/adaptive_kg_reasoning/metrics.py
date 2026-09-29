from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass
from typing import Iterable

from .incremental import IncrementalHighRecentState, IncrementalWindowResult
from .recompute import RecomputeWindowResult, recompute_window
from .windows import WindowTransition


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    window_index: int
    start_timestamp: int
    end_timestamp: int
    active_load_events: int
    events_added: int
    events_expired: int
    affected_entities: int
    recomputation_ms: float
    incremental_update_ms: float
    speedup_ratio: float
    triples_added: int
    triples_retracted: int
    materialised_fact_count: int
    state_size_proxy_kib: float
    fact_read_ms: float
    result_equivalent: bool
    symmetric_difference_count: int
    false_additions: int
    missed_facts: int


@dataclass(frozen=True, slots=True)
class ComparisonSummary:
    scenario: str
    windows: int
    equivalent_windows: int
    recomputation_ms_median: float
    recomputation_ms_p95: float
    recomputation_ms_mean: float
    recomputation_ms_total: float
    incremental_ms_median: float
    incremental_ms_p95: float
    incremental_ms_mean: float
    incremental_ms_total: float
    total_speedup_ratio: float
    median_speedup_ratio: float
    triples_added_total: int
    triples_retracted_total: int
    max_state_size_proxy_kib: float
    fact_read_ms_median: float


def percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be between 0 and 1")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def estimate_incremental_state_kib(state: IncrementalHighRecentState) -> float:
    """Return a documented Python object-size proxy, not process RSS.

    The proxy includes the top-level containers plus their direct keys/values.
    It is intended for within-experiment comparison only.
    """
    size = sys.getsizeof(state.active_events) + sys.getsizeof(state.aggregates) + sys.getsizeof(state.facts)
    for key, value in state.active_events.items():
        size += sys.getsizeof(key) + sys.getsizeof(value)
    for key, value in state.aggregates.items():
        size += sys.getsizeof(key) + sys.getsizeof(value)
    for fact in state.facts:
        size += sys.getsizeof(fact)
    return size / 1024.0


def measure_fact_read_ms(facts, repetitions: int = 5) -> float:
    """Measure a minimal materialised-fact readout proxy.

    This is not SPARQL query latency. It measures iterating the current fact set
    so v0.2 can keep query/read overhead visible without overclaiming an RSP engine.
    """
    times=[]
    for _ in range(repetitions):
        start=time.perf_counter()
        _ = sum(1 for _fact in facts)
        times.append((time.perf_counter()-start)*1000)
    return statistics.median(times)


def compare_window(
    window: WindowTransition,
    state: IncrementalHighRecentState,
    *,
    threshold_watts: float = 450.0,
) -> tuple[ComparisonRow, RecomputeWindowResult, IncrementalWindowResult]:
    oracle = recompute_window(window, threshold_watts=threshold_watts)
    incremental = state.apply(window)

    false_additions = incremental.facts - oracle.facts
    missed_facts = oracle.facts - incremental.facts
    symmetric_difference = incremental.facts ^ oracle.facts
    recompute_cost = oracle.recomputation_ms
    incremental_cost = incremental.incremental_update_ms
    speedup = recompute_cost / incremental_cost if incremental_cost > 0 else float("inf")

    row = ComparisonRow(
        window_index=window.index,
        start_timestamp=window.start_timestamp,
        end_timestamp=window.end_timestamp,
        active_load_events=oracle.active_load_events,
        events_added=incremental.events_added,
        events_expired=incremental.events_expired,
        affected_entities=incremental.affected_entities,
        recomputation_ms=recompute_cost,
        incremental_update_ms=incremental_cost,
        speedup_ratio=speedup,
        triples_added=incremental.triples_added,
        triples_retracted=incremental.triples_retracted,
        materialised_fact_count=incremental.materialised_fact_count,
        state_size_proxy_kib=estimate_incremental_state_kib(state),
        fact_read_ms=measure_fact_read_ms(incremental.facts),
        result_equivalent=not symmetric_difference,
        symmetric_difference_count=len(symmetric_difference),
        false_additions=len(false_additions),
        missed_facts=len(missed_facts),
    )
    return row, oracle, incremental


def summarise(rows: list[ComparisonRow], *, scenario: str) -> ComparisonSummary:
    recompute=[row.recomputation_ms for row in rows]
    incremental=[row.incremental_update_ms for row in rows]
    speedups=[row.speedup_ratio for row in rows if row.speedup_ratio != float("inf")]
    fact_reads=[row.fact_read_ms for row in rows]
    recompute_total=sum(recompute)
    incremental_total=sum(incremental)
    total_speedup = recompute_total / incremental_total if incremental_total > 0 else float("inf")

    return ComparisonSummary(
        scenario=scenario,
        windows=len(rows),
        equivalent_windows=sum(row.result_equivalent for row in rows),
        recomputation_ms_median=statistics.median(recompute) if recompute else 0.0,
        recomputation_ms_p95=percentile(recompute,0.95),
        recomputation_ms_mean=statistics.fmean(recompute) if recompute else 0.0,
        recomputation_ms_total=recompute_total,
        incremental_ms_median=statistics.median(incremental) if incremental else 0.0,
        incremental_ms_p95=percentile(incremental,0.95),
        incremental_ms_mean=statistics.fmean(incremental) if incremental else 0.0,
        incremental_ms_total=incremental_total,
        total_speedup_ratio=total_speedup,
        median_speedup_ratio=statistics.median(speedups) if speedups else 0.0,
        triples_added_total=sum(row.triples_added for row in rows),
        triples_retracted_total=sum(row.triples_retracted for row in rows),
        max_state_size_proxy_kib=max((row.state_size_proxy_kib for row in rows),default=0.0),
        fact_read_ms_median=statistics.median(fact_reads) if fact_reads else 0.0,
    )
