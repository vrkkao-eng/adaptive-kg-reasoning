"""Atomic local checkpoints with integrity and retained-state validation."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from rdflib import URIRef

from .incremental import IncrementalHighRecentState, MutablePlugState
from .namespaces import EX


class CheckpointError(ValueError):
    """A checkpoint cannot safely resume this experiment."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _integer(value, name: str, minimum=0):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise CheckpointError(f"Invalid {name}")
    return value


def _finite(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise CheckpointError(f"Invalid {name}")
    return value


def _uri(value):
    if not isinstance(value, str) or not value.startswith(str(EX) + "house-"):
        raise CheckpointError("Invalid plug URI")
    return URIRef(value)


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CheckpointError("Unexpected checkpoint fields")


def decode_payload(payload: dict, *, expected_identity: str) -> tuple[IncrementalHighRecentState, int]:
    _fields(payload, ("cursor", "identity", "threshold_watts", "active_events", "aggregates", "facts"))
    if payload["identity"] != expected_identity:
        raise CheckpointError("Checkpoint identity mismatch")
    cursor = _integer(payload["cursor"], "cursor")
    state = IncrementalHighRecentState(threshold_watts=_finite(payload["threshold_watts"], "threshold"))
    for name in ("active_events", "aggregates", "facts"):
        if not isinstance(payload[name], list):
            raise CheckpointError(f"Invalid {name} list")
    counts, totals = defaultdict(int), defaultdict(float)
    for row in payload["active_events"]:
        _fields(row, ("id", "plug", "value"))
        event_id = _integer(row["id"], "event id", None)
        plug, value = _uri(row["plug"]), _finite(row["value"], "event value")
        if event_id in state.active_events:
            raise CheckpointError("Duplicate active event")
        state.active_events[event_id] = (plug, value)
        counts[plug] += 1
        totals[plug] += value
    for row in payload["aggregates"]:
        _fields(row, ("plug", "count", "total_load"))
        plug = _uri(row["plug"])
        count = _integer(row["count"], "support count", 1)
        total = _finite(row["total_load"], "support total")
        if plug in state.aggregates or count != counts[plug]:
            raise CheckpointError("Inconsistent support counts")
        # Keep the original floating-point total, including incremental roundoff.
        if not math.isclose(total, totals[plug], rel_tol=1e-12, abs_tol=1e-7):
            raise CheckpointError("Inconsistent support total")
        state.aggregates[plug] = MutablePlugState(count, total)
    if set(state.aggregates) != set(counts):
        raise CheckpointError("Missing aggregate support")
    for row in payload["facts"]:
        if not isinstance(row, list) or len(row) != 3:
            raise CheckpointError("Invalid fact")
        plug = _uri(row[0])
        fact = (plug, EX.hasState, EX.HighRecentConsumption)
        if row != list(map(str, fact)) or fact in state.facts:
            raise CheckpointError("Invalid or duplicate fact")
        state.facts.add(fact)
    expected_facts = {(plug, EX.hasState, EX.HighRecentConsumption)
                      for plug, aggregate in state.aggregates.items()
                      if aggregate.average_load >= state.threshold_watts}
    if state.facts != expected_facts:
        raise CheckpointError("Facts disagree with aggregate support")
    return state, cursor


def save_checkpoint(path: Path, state: IncrementalHighRecentState, *, cursor: int, identity: str) -> int:
    """Publish a complete snapshot with atomic replacement; preserve the old file on failure."""
    payload = {
        "cursor": cursor, "identity": identity, "threshold_watts": state.threshold_watts,
        "active_events": [{"id": event_id, "plug": str(plug), "value": value}
                          for event_id, (plug, value) in sorted(state.active_events.items())],
        "aggregates": [{"plug": str(plug), "count": aggregate.count, "total_load": aggregate.total_load}
                       for plug, aggregate in sorted(state.aggregates.items())],
        "facts": sorted([list(map(str, fact)) for fact in state.facts]),
    }
    decode_payload(payload, expected_identity=identity)
    data = canonical_bytes({"schema_version": 1, "payload": payload,
                            "sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest()}) + b"\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as fh:
            temporary = Path(fh.name)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(data)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointError("Duplicate JSON key")
        result[key] = value
    return result


def load_checkpoint(path: Path, *, expected_identity: str) -> tuple[IncrementalHighRecentState, int]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
        _fields(raw, ("schema_version", "payload", "sha256"))
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise CheckpointError("Unsupported checkpoint schema")
        digest = hashlib.sha256(canonical_bytes(raw["payload"])).hexdigest()
        if not isinstance(raw["sha256"], str) or digest != raw["sha256"]:
            raise CheckpointError("Checkpoint checksum mismatch")
        return decode_payload(raw["payload"], expected_identity=expected_identity)
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
        if isinstance(exc, CheckpointError):
            raise
        raise CheckpointError(f"Unreadable checkpoint: {type(exc).__name__}") from exc
