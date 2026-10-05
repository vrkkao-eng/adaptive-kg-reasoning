"""Cost-aware selection for one window-derived state; no future-query oracle."""
from __future__ import annotations

import math
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from .incremental import IncrementalHighRecentState
from .metrics import estimate_incremental_state_kib, percentile
from .recompute import recompute_window
from .windows import WindowTransition

STRATEGIES = ("recompute", "query_time", "incremental", "adaptive")


@dataclass(frozen=True)
class CostModel:
    """Explicit synthetic cost units, not milliseconds or fitted measurements.

    Memory is priced per retained support-event/fact per window. The separate
    measured state-size proxy is not used as a prediction of process memory.
    """
    event: float = 1.0
    read_fact: float = 0.05
    retain_item: float = 0.02
    switch: float = 1.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError("Cost coefficients must be finite and non-negative")


@dataclass(frozen=True)
class Decision:
    mode: str
    predicted_queries: float
    estimated_query_time_units: float
    estimated_incremental_units: float
    reason: str


def choose_mode(*, active: int, changed: int, retained: bool, previous_facts: int,
                previous_queries: int, model: CostModel) -> Decision:
    # Only the preceding window's observed demand is available to the policy.
    q = previous_queries
    query_time = q * (active * model.event + previous_facts * model.read_fact)
    query_time += model.switch if retained else 0.0
    incremental = (changed if retained else active) * model.event
    incremental += q * previous_facts * model.read_fact
    incremental += (active + previous_facts) * model.retain_item
    incremental += 0.0 if retained else model.switch
    mode = "incremental" if incremental < query_time else "query_time"
    return Decision(mode, q, query_time, incremental,
                    "lower_estimated_cost" if incremental != query_time else "tie_query_time")


def query_schedule(name: str, windows: int) -> list[int]:
    """Known to the harness, hidden from policy until each decision is made."""
    if name == "none":
        return [0] * windows
    if name == "sparse":
        return [int(i % 8 == 0) for i in range(windows)]
    if name == "dense":
        return [8] * windows
    if name == "bursty":
        return [8 if (i // 8) % 2 == 0 else 0 for i in range(windows)]
    raise ValueError(f"Unknown workload: {name}")


def benchmark_strategy(windows: Iterable[WindowTransition], queries: Iterable[int], *,
                       strategy: str, threshold: float = 450.0,
                       model: CostModel | None = None) -> list[dict]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    if not math.isfinite(threshold):
        raise ValueError("Threshold must be finite")
    model = model or CostModel()
    rows = []
    state = None
    previous_queries = previous_facts = 0
    previous_mode = None
    for window, query_count in zip(windows, queries, strict=True):
        if type(query_count) is not int or query_count < 0:
            raise ValueError("Query counts must be non-negative integers")
        if any(e.property != 1 or not math.isfinite(e.value)
               for e in (*window.events, *window.added, *window.expired)):
            raise ValueError("v0.3 benchmark requires finite, load-only events")
        active = len(window.events)
        changed = len(window.added) + len(window.expired)
        decision_ms = 0.0
        decision = None
        if strategy == "adaptive":
            start = time.perf_counter()
            decision = choose_mode(active=active, changed=changed, retained=state is not None,
                                   previous_facts=previous_facts, previous_queries=previous_queries,
                                   model=model)
            decision_ms = (time.perf_counter() - start) * 1000
            mode = decision.mode
        else:
            mode = strategy
        # Independent validation work is outside strategy cost and never informs selection.
        oracle = recompute_window(window, threshold_watts=threshold).facts
        bootstrap_ms = maintenance_ms = release_ms = query_ms = 0.0
        event_work = 0
        was_retained = state is not None
        facts = frozenset()
        if mode == "incremental":
            start = time.perf_counter()
            if state is None:
                state = IncrementalHighRecentState(threshold_watts=threshold)
                # A switch back must rebuild from the full current window, not its delta.
                initial = WindowTransition(window.index, window.start_timestamp,
                                           window.end_timestamp, window.events, window.events, ())
                facts = state.apply(initial).facts
                bootstrap_ms = (time.perf_counter() - start) * 1000
                event_work = active
            else:
                facts = state.apply(window).facts
                maintenance_ms = (time.perf_counter() - start) * 1000
                event_work = changed
        elif mode == "recompute":
            start = time.perf_counter()
            facts = recompute_window(window, threshold_watts=threshold).facts
            maintenance_ms = (time.perf_counter() - start) * 1000
            event_work = active
        elif state is not None:
            start = time.perf_counter()
            state = None
            release_ms = (time.perf_counter() - start) * 1000
        state_checked = mode != "query_time"
        if state_checked and facts != oracle:
            raise AssertionError(f"state divergence strategy={strategy} window={window.index}")
        result_count = len(facts) if state_checked else None
        for _ in range(query_count):
            start = time.perf_counter()
            answer_facts = (recompute_window(window, threshold_watts=threshold).facts
                            if mode == "query_time" else facts)
            # Same full-result consumption for every strategy and every request.
            answer = tuple(answer_facts)
            query_ms += (time.perf_counter() - start) * 1000
            if frozenset(answer) != oracle:
                raise AssertionError(f"query divergence strategy={strategy} window={window.index}")
            result_count = len(answer)
        if mode == "query_time":
            event_work = active * query_count
        retained_items = active + len(facts) if mode == "incremental" else len(facts)
        # No retained derived state or support in query-time mode; shared input excluded.
        state_kib = estimate_incremental_state_kib(state) if state is not None else 0.0
        if mode == "recompute":
            state_kib = (sys.getsizeof(facts) + sum(sys.getsizeof(f) for f in facts)) / 1024
        read_work = query_count * (result_count or 0)
        allocation_changed = (mode == "incremental") != was_retained
        modeled_units = event_work * model.event + read_work * model.read_fact
        modeled_units += retained_items * model.retain_item
        modeled_units += model.switch if allocation_changed else 0.0
        total_ms = decision_ms + bootstrap_ms + maintenance_ms + release_ms + query_ms
        rows.append({
            "strategy": strategy, "window_index": window.index,
            "start_timestamp": window.start_timestamp, "end_timestamp": window.end_timestamp,
            "active_events": active, "delta_events": changed, "queries": query_count,
            "selected_mode": mode, "switched": previous_mode is not None and mode != previous_mode,
            "predicted_queries": decision.predicted_queries if decision else None,
            "estimated_query_time_units": decision.estimated_query_time_units if decision else None,
            "estimated_incremental_units": decision.estimated_incremental_units if decision else None,
            "estimated_utility_units": (decision.estimated_query_time_units -
                                        decision.estimated_incremental_units) if decision else None,
            "decision_reason": decision.reason if decision else "fixed_baseline",
            "bootstrap_ms": bootstrap_ms, "maintenance_ms": maintenance_ms,
            "release_ms": release_ms, "query_ms": query_ms, "decision_ms": decision_ms,
            "total_ms": total_ms, "realized_model_units": modeled_units,
            "event_work": event_work, "state_size_proxy_kib": state_kib,
            "retained_items": retained_items, "result_count": result_count,
            "checked_queries": query_count, "state_checked": state_checked,
            "symmetric_difference_count": 0,
        })
        previous_queries = query_count
        if result_count is not None:
            previous_facts = result_count
        previous_mode = mode
    if not rows:
        raise ValueError("Benchmark requires at least one window")
    return rows


def summarise_strategy(rows: list[dict]) -> dict:
    return {
        "strategy": rows[0]["strategy"], "windows": len(rows),
        "queries": sum(r["queries"] for r in rows),
        "checked_queries": sum(r["checked_queries"] for r in rows),
        "checked_states": sum(r["state_checked"] for r in rows),
        "switches": sum(r["switched"] for r in rows),
        **{name: sum(r[name] for r in rows) for name in (
            "bootstrap_ms", "maintenance_ms", "release_ms", "query_ms", "decision_ms", "total_ms",
            "realized_model_units", "event_work")},
        "window_ms_p95": percentile([r["total_ms"] for r in rows], .95),
        "max_state_size_proxy_kib": max(r["state_size_proxy_kib"] for r in rows),
        "symmetric_difference_count": sum(r["symmetric_difference_count"] for r in rows),
    }
