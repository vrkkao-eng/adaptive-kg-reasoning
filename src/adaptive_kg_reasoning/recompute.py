from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from rdflib import URIRef

from .namespaces import EX
from .windows import StreamEvent, WindowTransition

Triple = tuple


@dataclass(frozen=True, slots=True)
class PlugAggregate:
    count: int
    total_load: float
    average_load: float


@dataclass(frozen=True, slots=True)
class RecomputeWindowResult:
    window_index: int
    start_timestamp: int
    end_timestamp: int
    active_load_events: int
    distinct_plugs: int
    recomputation_ms: float
    high_recent_count: int
    facts: frozenset[Triple]


def plug_uri(event: StreamEvent) -> URIRef:
    return EX[
        f"house-{event.house_id}-household-{event.household_id}-plug-{event.plug_id}"
    ]


def aggregate_window_loads(
    events: Iterable[StreamEvent],
) -> dict[URIRef, PlugAggregate]:
    """Recompute per-plug load aggregates from the complete current window.

    Only DEBS property=1 (load) events contribute. No state from a previous
    window is consulted; this is intentionally the correctness oracle for
    later incremental maintenance.
    """
    counts: dict[URIRef, int] = defaultdict(int)
    totals: dict[URIRef, float] = defaultdict(float)

    for event in events:
        if event.property != 1:
            continue
        plug = plug_uri(event)
        counts[plug] += 1
        totals[plug] += event.value

    return {
        plug: PlugAggregate(
            count=counts[plug],
            total_load=totals[plug],
            average_load=totals[plug] / counts[plug],
        )
        for plug in counts
    }


def recompute_high_recent_consumption(
    events: Iterable[StreamEvent],
    *,
    threshold_watts: float = 450.0,
) -> set[Triple]:
    """Derive HighRecentConsumption solely from the current window."""
    aggregates = aggregate_window_loads(events)
    return {
        (plug, EX.hasState, EX.HighRecentConsumption)
        for plug, aggregate in aggregates.items()
        if aggregate.average_load >= threshold_watts
    }


def recompute_window(
    window: WindowTransition,
    *,
    threshold_watts: float = 450.0,
) -> RecomputeWindowResult:
    """Time one full-window recomputation for the reference baseline."""
    start = time.perf_counter()
    aggregates = aggregate_window_loads(window.events)
    facts = frozenset(
        (plug, EX.hasState, EX.HighRecentConsumption)
        for plug, aggregate in aggregates.items()
        if aggregate.average_load >= threshold_watts
    )
    recomputation_ms = (time.perf_counter() - start) * 1000

    active_load_events = sum(1 for event in window.events if event.property == 1)

    return RecomputeWindowResult(
        window_index=window.index,
        start_timestamp=window.start_timestamp,
        end_timestamp=window.end_timestamp,
        active_load_events=active_load_events,
        distinct_plugs=len(aggregates),
        recomputation_ms=recomputation_ms,
        high_recent_count=len(facts),
        facts=facts,
    )
