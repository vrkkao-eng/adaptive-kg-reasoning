from __future__ import annotations

import time
from dataclasses import dataclass

from rdflib import URIRef

from .namespaces import EX
from .recompute import plug_uri
from .windows import StreamEvent, WindowTransition

Triple = tuple


@dataclass(slots=True)
class MutablePlugState:
    count: int = 0
    total_load: float = 0.0

    @property
    def average_load(self) -> float:
        if self.count <= 0:
            raise ValueError("average_load is undefined for an empty support state")
        return self.total_load / self.count


@dataclass(frozen=True, slots=True)
class IncrementalWindowResult:
    window_index: int
    start_timestamp: int
    end_timestamp: int
    incremental_update_ms: float
    events_added: int
    events_expired: int
    affected_entities: int
    triples_added: int
    triples_retracted: int
    materialised_fact_count: int
    facts: frozenset[Triple]


class IncrementalHighRecentState:
    """Maintain HighRecentConsumption using window deltas only.

    The state tracks active load events and per-plug count/sum aggregates.
    Replaying a transition is idempotent: already-active additions and already-
    removed expirations are ignored.
    """

    def __init__(self, *, threshold_watts: float = 450.0) -> None:
        self.threshold_watts = threshold_watts
        self.aggregates: dict[URIRef, MutablePlugState] = {}
        self.active_events: dict[int, tuple[URIRef, float]] = {}
        self.facts: set[Triple] = set()

    def _expire(self, event: StreamEvent, affected: set[URIRef]) -> None:
        if event.property != 1:
            return

        active = self.active_events.pop(event.id, None)
        if active is None:
            return

        plug, value = active
        state = self.aggregates.get(plug)
        if state is None:
            raise RuntimeError(f"Missing aggregate state for active event {event.id}")

        state.count -= 1
        state.total_load -= value
        affected.add(plug)

        if state.count < 0:
            raise RuntimeError(f"Negative support count for {plug}")
        if state.count == 0:
            del self.aggregates[plug]

    def _add(self, event: StreamEvent, affected: set[URIRef]) -> None:
        if event.property != 1:
            return
        if event.id in self.active_events:
            return

        plug = plug_uri(event)
        state = self.aggregates.setdefault(plug, MutablePlugState())
        state.count += 1
        state.total_load += event.value
        self.active_events[event.id] = (plug, event.value)
        affected.add(plug)

    def _refresh_fact(self, plug: URIRef) -> tuple[bool, bool]:
        fact = (plug, EX.hasState, EX.HighRecentConsumption)
        state = self.aggregates.get(plug)
        qualifies = (
            state is not None
            and state.count > 0
            and state.average_load >= self.threshold_watts
        )
        already_materialised = fact in self.facts

        added = qualifies and not already_materialised
        retracted = already_materialised and not qualifies

        if added:
            self.facts.add(fact)
        elif retracted:
            self.facts.remove(fact)

        return added, retracted

    def apply(self, window: WindowTransition) -> IncrementalWindowResult:
        start = time.perf_counter()
        affected: set[URIRef] = set()

        for event in window.expired:
            self._expire(event, affected)
        for event in window.added:
            self._add(event, affected)

        triples_added = 0
        triples_retracted = 0
        for plug in affected:
            added, retracted = self._refresh_fact(plug)
            triples_added += int(added)
            triples_retracted += int(retracted)

        update_ms = (time.perf_counter() - start) * 1000

        return IncrementalWindowResult(
            window_index=window.index,
            start_timestamp=window.start_timestamp,
            end_timestamp=window.end_timestamp,
            incremental_update_ms=update_ms,
            events_added=sum(1 for event in window.added if event.property == 1),
            events_expired=sum(1 for event in window.expired if event.property == 1),
            affected_entities=len(affected),
            triples_added=triples_added,
            triples_retracted=triples_retracted,
            materialised_fact_count=len(self.facts),
            facts=frozenset(self.facts),
        )
