from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.generate_data import generate_debs_shaped_csv
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.namespaces import EX
from adaptive_kg_reasoning.recompute import plug_uri, recompute_window
from adaptive_kg_reasoning.windows import (
    StreamEvent,
    WindowTransition,
    iter_sliding_windows,
    load_events,
)


def event(event_id: int, timestamp: int, value: float, *, plug_id: int = 1) -> StreamEvent:
    return StreamEvent(
        id=event_id,
        timestamp=timestamp,
        value=value,
        property=1,
        plug_id=plug_id,
        household_id=1,
        house_id=1,
    )


def window(index, events, added, expired, start=0, end=10):
    return WindowTransition(
        index=index,
        start_timestamp=start,
        end_timestamp=end,
        events=tuple(events),
        added=tuple(added),
        expired=tuple(expired),
    )


def test_incremental_adds_fact_when_threshold_is_crossed_upward():
    first = event(1, 0, 300.0)
    second = event(2, 1, 700.0)
    state = IncrementalHighRecentState(threshold_watts=450.0)

    r0 = state.apply(window(0, [first], [first], []))
    assert r0.triples_added == 0
    assert len(r0.facts) == 0

    r1 = state.apply(window(1, [first, second], [second], [], start=1, end=11))
    fact = (plug_uri(first), EX.hasState, EX.HighRecentConsumption)
    assert fact in r1.facts
    assert r1.triples_added == 1
    assert r1.triples_retracted == 0


def test_incremental_retracts_fact_when_support_expires():
    high = event(1, 0, 800.0)
    low = event(2, 1, 100.0)
    state = IncrementalHighRecentState(threshold_watts=450.0)

    first = state.apply(window(0, [high], [high], []))
    assert first.materialised_fact_count == 1

    second = state.apply(
        window(1, [low], [low], [high], start=1, end=11)
    )
    assert second.materialised_fact_count == 0
    assert second.triples_retracted == 1


def test_replaying_transition_is_idempotent():
    high = event(1, 0, 800.0)
    transition = window(0, [high], [high], [])
    state = IncrementalHighRecentState()

    first = state.apply(transition)
    second = state.apply(transition)

    plug = plug_uri(high)
    assert first.materialised_fact_count == 1
    assert second.materialised_fact_count == 1
    assert state.aggregates[plug].count == 1
    assert state.aggregates[plug].total_load == 800.0
    assert second.triples_added == 0
    assert second.triples_retracted == 0


def test_incremental_matches_full_recomputation_for_every_window(tmp_path):
    csv_path = generate_debs_shaped_csv(
        tmp_path / "sample.csv",
        n_events=800,
        seed=73,
        n_houses=3,
        households_per_house=2,
        plugs_per_household=3,
    )
    events = load_events(csv_path, property_filter=1)
    windows = list(
        iter_sliding_windows(
            events,
            width_seconds=120,
            slide_seconds=30,
            flush=True,
        )
    )

    state = IncrementalHighRecentState(threshold_watts=450.0)
    for current in windows:
        incremental = state.apply(current)
        oracle = recompute_window(current, threshold_watts=450.0)
        assert incremental.facts == oracle.facts, f"window={current.index}"


def test_incremental_active_registry_matches_current_load_window(tmp_path):
    csv_path = generate_debs_shaped_csv(tmp_path / "sample.csv", n_events=300, seed=9)
    events = load_events(csv_path, property_filter=1)
    windows = iter_sliding_windows(events, width_seconds=60, slide_seconds=15, flush=True)
    state = IncrementalHighRecentState()

    for current in windows:
        state.apply(current)
        assert set(state.active_events) == {event.id for event in current.events}
