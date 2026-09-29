from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.namespaces import EX
from adaptive_kg_reasoning.recompute import (
    aggregate_window_loads,
    plug_uri,
    recompute_high_recent_consumption,
    recompute_window,
)
from adaptive_kg_reasoning.windows import StreamEvent, WindowTransition


def event(
    event_id: int,
    timestamp: int,
    value: float,
    *,
    plug_id: int = 1,
    property_value: int = 1,
) -> StreamEvent:
    return StreamEvent(
        id=event_id,
        timestamp=timestamp,
        value=value,
        property=property_value,
        plug_id=plug_id,
        household_id=1,
        house_id=1,
    )


def test_full_window_aggregate_is_recomputed_from_current_events():
    events = (
        event(1, 0, 400.0),
        event(2, 1, 600.0),
        event(3, 2, 100.0, plug_id=2),
    )

    aggregates = aggregate_window_loads(events)
    plug_1 = plug_uri(events[0])
    plug_2 = plug_uri(events[2])

    assert aggregates[plug_1].count == 2
    assert aggregates[plug_1].total_load == 1000.0
    assert aggregates[plug_1].average_load == 500.0
    assert aggregates[plug_2].average_load == 100.0


def test_threshold_derivation_uses_full_window_average():
    events = (
        event(1, 0, 400.0),
        event(2, 1, 600.0),
        event(3, 2, 100.0, plug_id=2),
    )

    facts = recompute_high_recent_consumption(events, threshold_watts=450.0)

    assert (plug_uri(events[0]), EX.hasState, EX.HighRecentConsumption) in facts
    assert (plug_uri(events[2]), EX.hasState, EX.HighRecentConsumption) not in facts
    assert len(facts) == 1


def test_non_load_events_do_not_affect_oracle():
    load = event(1, 0, 500.0)
    accumulated_work = event(2, 1, 10_000.0, property_value=0)

    aggregates = aggregate_window_loads((load, accumulated_work))
    facts = recompute_high_recent_consumption((load, accumulated_work))

    plug = plug_uri(load)
    assert aggregates[plug].count == 1
    assert aggregates[plug].average_load == 500.0
    assert (plug, EX.hasState, EX.HighRecentConsumption) in facts


def test_empty_window_has_no_derived_facts():
    assert aggregate_window_loads(()) == {}
    assert recompute_high_recent_consumption(()) == set()


def test_recompute_window_reports_reference_metrics():
    events = (event(1, 0, 500.0), event(2, 1, 600.0))
    window = WindowTransition(
        index=3,
        start_timestamp=0,
        end_timestamp=10,
        events=events,
        added=events,
        expired=(),
    )

    result = recompute_window(window)

    assert result.window_index == 3
    assert result.active_load_events == 2
    assert result.distinct_plugs == 1
    assert result.high_recent_count == 1
    assert result.recomputation_ms >= 0.0
    assert len(result.facts) == 1
