"""Validated resource profiles for simulation, not physical resource enforcement."""
from __future__ import annotations

import math
from dataclasses import dataclass


def finite_nonnegative(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def nonnegative_int(name: str, value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class NodeProfile:
    name: str
    memory_budget_bytes: int
    update_ms_per_event: float
    refresh_ms_per_entity: float
    read_ms_per_fact: float
    query_base_ms: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Node name must be non-empty")
        nonnegative_int("memory_budget_bytes", self.memory_budget_bytes)
        for name in ("update_ms_per_event", "refresh_ms_per_entity", "read_ms_per_fact", "query_base_ms"):
            finite_nonnegative(name, getattr(self, name))

    def compute_ms(self, *, event_work: int, affected_entities: int,
                   queries: float, facts: int) -> float:
        return (event_work * self.update_ms_per_event
                + affected_entities * self.refresh_ms_per_entity
                + queries * (self.query_base_ms + facts * self.read_ms_per_fact))


@dataclass(frozen=True)
class MemoryModel:
    """Declared logical bytes for retained state; excludes shared inputs/transients."""
    base_bytes: int = 4096
    bytes_per_event: int = 128
    bytes_per_entity: int = 128
    bytes_per_fact: int = 256

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            nonnegative_int(name, getattr(self, name))

    def retained_bytes(self, events: int, entities: int, facts: int) -> int:
        for name, value in (("events", events), ("entities", entities), ("facts", facts)):
            nonnegative_int(name, value)
        return (self.base_bytes + events * self.bytes_per_event
                + entities * self.bytes_per_entity + facts * self.bytes_per_fact)


@dataclass(frozen=True)
class WorkloadEstimate:
    """User-declared planning inputs, independent of evaluation windows and answers."""
    windows: int
    active_events: int
    distinct_entities: int
    added_events: int
    expired_events: int
    affected_entities: int
    queries_per_window: float
    result_facts: int
    event_payload_bytes: int
    fact_payload_bytes: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if name != "queries_per_window":
                nonnegative_int(name, getattr(self, name))
        if self.windows == 0:
            raise ValueError("Planning horizon must contain at least one window")
        finite_nonnegative("queries_per_window", self.queries_per_window)
        if self.result_facts > self.distinct_entities or self.distinct_entities > self.active_events:
            raise ValueError("Expected facts <= entities <= active events")
        if self.added_events > self.active_events or self.expired_events > self.active_events:
            raise ValueError("Expected additions/expirations cannot exceed expected active events")
        if self.affected_entities > 2 * self.distinct_entities:
            raise ValueError("Affected entities exceed previous/current entity union bound")
        if not self.event_payload_bytes or not self.fact_payload_bytes:
            raise ValueError("Payload byte estimates must be positive")
