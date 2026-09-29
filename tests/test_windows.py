from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_kg_reasoning.windows import (
    StreamEvent,
    iter_sliding_windows,
    load_events,
)


def event(event_id: int, timestamp: int) -> StreamEvent:
    return StreamEvent(
        id=event_id,
        timestamp=timestamp,
        value=float(event_id),
        property=1,
        plug_id=1,
        household_id=1,
        house_id=1,
    )


def test_half_open_window_boundaries():
    events = (event(1, 0), event(2, 5), event(3, 10), event(4, 15))
    windows = list(
        iter_sliding_windows(events, width_seconds=10, slide_seconds=5, flush=True)
    )

    assert [e.id for e in windows[0].events] == [1, 2]
    assert windows[0].added_ids == (1, 2)
    assert windows[0].expired_ids == ()

    # timestamp 10 is excluded from [0, 10) and enters [5, 15)
    assert [e.id for e in windows[1].events] == [2, 3]
    assert windows[1].added_ids == (3,)
    assert windows[1].expired_ids == (1,)

    assert [e.id for e in windows[2].events] == [3, 4]
    assert windows[2].added_ids == (4,)
    assert windows[2].expired_ids == (2,)

    assert [e.id for e in windows[-1].events] == []
    assert windows[-1].expired_ids == (4,)


def test_every_event_enters_and_expires_once_when_flushed():
    events = tuple(event(i + 1, i * 3) for i in range(20))
    windows = list(
        iter_sliding_windows(events, width_seconds=12, slide_seconds=3, flush=True)
    )

    added = [event_id for window in windows for event_id in window.added_ids]
    expired = [event_id for window in windows for event_id in window.expired_ids]

    assert sorted(added) == list(range(1, 21))
    assert sorted(expired) == list(range(1, 21))
    assert len(added) == len(set(added))
    assert len(expired) == len(set(expired))


def test_transition_delta_reconstructs_each_window():
    events = tuple(event(i + 1, i * 2) for i in range(12))
    windows = list(
        iter_sliding_windows(events, width_seconds=8, slide_seconds=2, flush=True)
    )

    previous: set[int] = set()
    for window in windows:
        reconstructed = previous.difference(window.expired_ids).union(window.added_ids)
        current = {item.id for item in window.events}
        assert reconstructed == current
        previous = current


def test_rejects_out_of_order_events():
    events = (event(1, 10), event(2, 9))
    with pytest.raises(ValueError, match="ordered"):
        list(iter_sliding_windows(events, width_seconds=10, slide_seconds=5))


def test_rejects_duplicate_event_ids():
    events = (event(1, 10), event(1, 11))
    with pytest.raises(ValueError, match="Duplicate"):
        list(iter_sliding_windows(events, width_seconds=10, slide_seconds=5))


def test_rejects_window_gaps():
    events = (event(1, 0), event(2, 20))
    with pytest.raises(ValueError, match="slide_seconds <= width_seconds"):
        list(iter_sliding_windows(events, width_seconds=5, slide_seconds=10))


def test_load_events_property_filter(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text(
        "id,timestamp,value,property,plug_id,household_id,house_id\n"
        "1,100,10.0,1,1,1,1\n"
        "2,101,0.5,0,1,1,1\n"
        "3,102,11.0,1,1,1,1\n",
        encoding="utf-8",
    )

    all_events = load_events(path)
    load_only = load_events(path, property_filter=1)

    assert [item.id for item in all_events] == [1, 2, 3]
    assert [item.id for item in load_only] == [1, 3]
