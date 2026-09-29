from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True, slots=True)
class StreamEvent:
    id: int
    timestamp: int
    value: float
    property: int
    plug_id: int
    household_id: int
    house_id: int


@dataclass(frozen=True, slots=True)
class WindowTransition:
    index: int
    start_timestamp: int
    end_timestamp: int
    events: tuple[StreamEvent, ...]
    added: tuple[StreamEvent, ...]
    expired: tuple[StreamEvent, ...]

    @property
    def active_count(self) -> int:
        return len(self.events)

    @property
    def added_ids(self) -> tuple[int, ...]:
        return tuple(event.id for event in self.added)

    @property
    def expired_ids(self) -> tuple[int, ...]:
        return tuple(event.id for event in self.expired)


def load_events(
    csv_path: str | Path,
    *,
    property_filter: int | None = None,
) -> tuple[StreamEvent, ...]:
    """Load a deterministic, event-time ordered stream from CSV.

    v0.2.0 deliberately rejects out-of-order timestamps and duplicate event IDs.
    Late-event handling is outside this milestone.
    """
    events: list[StreamEvent] = []
    seen_ids: set[int] = set()
    previous_timestamp: int | None = None

    with Path(csv_path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            event = StreamEvent(
                id=int(row["id"]),
                timestamp=int(row["timestamp"]),
                value=float(row["value"]),
                property=int(row["property"]),
                plug_id=int(row["plug_id"]),
                household_id=int(row["household_id"]),
                house_id=int(row["house_id"]),
            )

            if event.id in seen_ids:
                raise ValueError(f"Duplicate event id: {event.id}")
            seen_ids.add(event.id)

            if previous_timestamp is not None and event.timestamp < previous_timestamp:
                raise ValueError(
                    "Events must be ordered by non-decreasing event timestamp in v0.2.0"
                )
            previous_timestamp = event.timestamp

            if property_filter is None or event.property == property_filter:
                events.append(event)

    return tuple(events)


def _validate_events(events: tuple[StreamEvent, ...]) -> None:
    seen_ids: set[int] = set()
    previous_timestamp: int | None = None

    for event in events:
        if event.id in seen_ids:
            raise ValueError(f"Duplicate event id: {event.id}")
        seen_ids.add(event.id)

        if previous_timestamp is not None and event.timestamp < previous_timestamp:
            raise ValueError(
                "Events must be ordered by non-decreasing event timestamp in v0.2.0"
            )
        previous_timestamp = event.timestamp


def iter_sliding_windows(
    events: Iterable[StreamEvent],
    *,
    width_seconds: int = 3_600,
    slide_seconds: int = 60,
    flush: bool = False,
) -> Iterator[WindowTransition]:
    """Yield deterministic half-open sliding windows [start, end).

    Events at start belong to the current window. Events at end do not;
    they first become eligible in a later window. added and expired capture
    the delta from the preceding window.

    v0.2.0 requires slide_seconds <= width_seconds so the sequence has no
    uncovered gaps. Late or out-of-order event handling is not implemented.
    """
    if width_seconds <= 0:
        raise ValueError("width_seconds must be positive")
    if slide_seconds <= 0:
        raise ValueError("slide_seconds must be positive")
    if slide_seconds > width_seconds:
        raise ValueError("v0.2.0 requires slide_seconds <= width_seconds")

    ordered = tuple(events)
    if not ordered:
        return

    _validate_events(ordered)

    start = ordered[0].timestamp
    end = start + width_seconds
    last_timestamp = ordered[-1].timestamp

    active: deque[StreamEvent] = deque()
    cursor = 0
    window_index = 0

    while start <= last_timestamp or (flush and active):
        expired: list[StreamEvent] = []
        while active and active[0].timestamp < start:
            expired.append(active.popleft())

        added: list[StreamEvent] = []
        while cursor < len(ordered) and ordered[cursor].timestamp < end:
            event = ordered[cursor]
            if event.timestamp < start:
                raise RuntimeError(
                    "Window cursor skipped an event; this violates v0.2.0 overlap assumptions"
                )
            active.append(event)
            added.append(event)
            cursor += 1

        yield WindowTransition(
            index=window_index,
            start_timestamp=start,
            end_timestamp=end,
            events=tuple(active),
            added=tuple(added),
            expired=tuple(expired),
        )

        window_index += 1
        start += slide_seconds
        end += slide_seconds
