"""Bounded crash recovery on one fixed site with independently checked answers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import time

from .checkpoint import (CheckpointError, SUPPORT_ABS_TOL, SUPPORT_REL_TOL,
                         canonical_bytes, load_checkpoint, save_checkpoint)
from .incremental import IncrementalHighRecentState
from .namespaces import EX
from .recompute import aggregate_window_loads, plug_uri, recompute_window
from .resources import MemoryModel, NodeProfile, nonnegative_int
from .windows import WindowTransition

POLICIES = ("stop_on_failure", "cold_rebuild", "checkpoint_replay")
FAULT_POINTS = ("before_update", "after_update", "after_checkpoint")


@dataclass(frozen=True)
class FaultSpec:
    window_index: int | None = None
    point: str = "none"
    failures: int = 1

    def __post_init__(self):
        if self.point == "none":
            if self.window_index is not None:
                raise ValueError("No-fault case must not specify a window")
        else:
            if self.point not in FAULT_POINTS:
                raise ValueError("Unknown fault point")
            nonnegative_int("fault window", self.window_index)
        nonnegative_int("fault failures", self.failures)
        if self.failures == 0:
            raise ValueError("Fault failures must be positive")


class InjectedCrash(RuntimeError):
    pass


def trace_identity(windows: list[WindowTransition], *, threshold: float) -> str:
    """Bind checkpoint state to the exact transition stream and semantic configuration."""
    digest = hashlib.sha256(canonical_bytes({"semantics": "highrecent-incremental-v1", "threshold": threshold}))
    for window in windows:
        digest.update(canonical_bytes(asdict(window)))
        digest.update(b"\n")
    return digest.hexdigest()


def check_state(state, window, oracle, *, expected_aggregates=None):
    """Check retained support as well as answers; never repair the state being checked."""
    def diverges(component):
        raise AssertionError(f"Recovered state diverges at window {window.index}: {component}")

    expected_events = {event.id: (plug_uri(event), event.value) for event in window.events}
    if state.active_events != expected_events:
        diverges("active events")
    expected = (aggregate_window_loads(window.events)
                if expected_aggregates is None else expected_aggregates)
    if set(state.aggregates) != set(expected):
        diverges("aggregate entities")
    for plug, reference in expected.items():
        actual = state.aggregates[plug]
        if type(actual.count) is not int or actual.count != reference.count:
            diverges("support count")
        total = actual.total_load
        if (isinstance(total, bool) or not isinstance(total, (int, float))
                or not math.isfinite(total)
                or not math.isclose(total, reference.total_load,
                                    rel_tol=SUPPORT_REL_TOL, abs_tol=SUPPORT_ABS_TOL)):
            diverges("support total")
    support_facts = {(plug, EX.hasState, EX.HighRecentConsumption)
                     for plug, aggregate in state.aggregates.items()
                     if aggregate.average_load >= state.threshold_watts}
    if frozenset(state.facts) != oracle.facts or state.facts != support_facts:
        diverges("facts")


def benchmark_recovery(windows: list[WindowTransition], queries: list[int], *, policy: str,
                       fault: FaultSpec, checkpoint_path: Path, node: NodeProfile,
                       memory: MemoryModel, threshold: float = 450., checkpoint_every: int = 2,
                       max_retries: int = 2) -> tuple[list[dict], dict, list[dict]]:
    if policy not in POLICIES or not windows or len(windows) != len(queries):
        raise ValueError("Expected a known policy and aligned non-empty windows/queries")
    if not math.isfinite(threshold):
        raise ValueError("Threshold must be finite")
    nonnegative_int("checkpoint_every", checkpoint_every)
    nonnegative_int("max_retries", max_retries)
    if checkpoint_every == 0:
        raise ValueError("checkpoint_every must be positive")
    if checkpoint_path.exists():
        raise ValueError("Refuse to reuse a checkpoint path")
    if fault.window_index is not None and fault.window_index >= len(windows):
        raise ValueError("Fault window is outside the trace")
    if fault.point == "after_checkpoint" and fault.window_index % checkpoint_every != 0:
        raise ValueError("after_checkpoint fault must target a checkpoint boundary")
    for index, (window, count) in enumerate(zip(windows, queries, strict=True)):
        nonnegative_int("queries", count)
        if window.index != index:
            raise ValueError("Recovery requires consecutive zero-based window indices")
        if any(event.property != 1 or not math.isfinite(event.value)
               for event in (*window.events, *window.added, *window.expired)):
            raise ValueError("Recovery requires finite load-only events")
    identity = trace_identity(windows, threshold=threshold)
    # Oracle work is outside all service/recovery measurements.
    oracles = [recompute_window(window, threshold_watts=threshold) for window in windows]
    aggregate_oracles = [aggregate_window_loads(window.events) for window in windows]
    state = IncrementalHighRecentState(threshold_watts=threshold)
    metrics = {name: 0 for name in ("faults_injected", "recovery_attempts", "update_calls",
               "cold_bootstraps", "replayed_windows", "checkpoint_writes", "checkpoint_loads",
               "checkpoint_bytes_written", "checked_states", "checked_queries")}
    metrics.update({name: 0.0 for name in ("measured_maintenance_ms", "measured_query_ms",
                    "measured_checkpoint_write_ms", "measured_checkpoint_restore_ms", "recovery_apply_ms")})
    rows, audit = [], []
    terminal, failed_at = None, None
    remaining = fault.failures
    checkpoint_cursor = None

    def record(kind, index, **fields):
        audit.append({"sequence": len(audit), "event": kind, "window_index": index, **fields})

    def inject(point, index):
        nonlocal remaining
        if fault.point == point and fault.window_index == index and remaining:
            remaining -= 1
            metrics["faults_injected"] += 1
            record("injected_crash", index, point=point)
            raise InjectedCrash(point)

    def apply(window, *, recovering=False, replay=False, bootstrap=False):
        start = time.perf_counter()
        state.apply(window)
        elapsed = (time.perf_counter() - start) * 1000
        metrics["measured_maintenance_ms"] += elapsed
        metrics["recovery_apply_ms"] += elapsed if recovering else 0.0
        metrics["update_calls"] += 1
        metrics["replayed_windows"] += int(replay)
        metrics["cold_bootstraps"] += int(bootstrap)
        check_state(state, windows[window.index], oracles[window.index],
                    expected_aggregates=aggregate_oracles[window.index])
        metrics["checked_states"] += 1

    for index, (window, count) in enumerate(zip(windows, queries, strict=True)):
        before = dict(metrics)
        required = memory.retained_bytes(len(window.events), oracles[index].distinct_plugs,
                                         len(oracles[index].facts))
        served, retries, recovered_current, recovering, bootstrap = 0, 0, False, False, False
        if terminal is None and required > node.memory_budget_bytes:
            terminal, failed_at = "memory_budget_exceeded", index
            record("terminal", index, reason=terminal)
        while terminal is None:
            try:
                inject("before_update", index)
                if not recovered_current:
                    transition = (WindowTransition(index, window.start_timestamp, window.end_timestamp,
                                                   window.events, window.events, ()) if bootstrap else window)
                    apply(transition, recovering=recovering,
                          replay=recovering and not bootstrap, bootstrap=bootstrap)
                inject("after_update", index)
                if policy == "checkpoint_replay" and index % checkpoint_every == 0:
                    start = time.perf_counter()
                    size = save_checkpoint(checkpoint_path, state, cursor=index, identity=identity)
                    metrics["measured_checkpoint_write_ms"] += (time.perf_counter() - start) * 1000
                    metrics["checkpoint_writes"] += 1
                    metrics["checkpoint_bytes_written"] += size
                    checkpoint_cursor = index
                    record("checkpoint_published", index, bytes=size)
                inject("after_checkpoint", index)
                for _ in range(count):
                    start = time.perf_counter()
                    answer = tuple(state.facts)
                    metrics["measured_query_ms"] += (time.perf_counter() - start) * 1000
                    if frozenset(answer) != oracles[index].facts:
                        raise AssertionError(f"Query divergence at window {index}")
                    metrics["checked_queries"] += 1
                served = count
                record("window_served", index, queries=count)
                break
            except InjectedCrash:
                if policy == "stop_on_failure" or retries >= max_retries:
                    terminal = "stopped_on_failure" if policy == "stop_on_failure" else "retry_exhausted"
                    failed_at = index
                    record("terminal", index, reason=terminal)
                    break
                retries += 1
                metrics["recovery_attempts"] += 1
                recovering, recovered_current = True, False
                record("recovery_started", index, policy=policy, retry=retries)
                if policy == "cold_rebuild" or checkpoint_cursor is None:
                    state = IncrementalHighRecentState(threshold_watts=threshold)
                    bootstrap = True
                    record("cold_restart", index, reason="policy" if policy == "cold_rebuild" else "no_checkpoint")
                else:
                    start = time.perf_counter()
                    try:
                        state, cursor = load_checkpoint(checkpoint_path, expected_identity=identity)
                        if cursor != checkpoint_cursor or cursor > index or state.threshold_watts != threshold:
                            raise CheckpointError("Checkpoint cursor or threshold mismatch")
                        try:
                            check_state(state, windows[cursor], oracles[cursor],
                                        expected_aggregates=aggregate_oracles[cursor])
                        except AssertionError as exc:
                            raise CheckpointError("Checkpoint state does not match cursor") from exc
                    except CheckpointError as exc:
                        terminal, failed_at = "checkpoint_rejected", index
                        record("terminal", index, reason=terminal, error=str(exc))
                        break
                    finally:
                        metrics["measured_checkpoint_restore_ms"] += (time.perf_counter() - start) * 1000
                    metrics["checkpoint_loads"] += 1
                    metrics["checked_states"] += 1
                    bootstrap = False
                    record("checkpoint_restored", index, cursor=cursor)
                    for replay_index in range(cursor + 1, index):
                        apply(windows[replay_index], recovering=True, replay=True)
                        record("window_replayed", replay_index, recovery_target=index)
                    recovered_current = cursor == index
        complete_window = terminal is None
        rows.append({"window_index": index, "node": node.name,
                     "status": "served" if complete_window else terminal,
                     "required_memory_bytes": required, "memory_budget_bytes": node.memory_budget_bytes,
                     "requested_queries": count, "served_queries": served,
                     "checkpoint_cursor": checkpoint_cursor,
                     **{name: metrics[name] - before[name] for name in metrics}})
    service_ms = sum(metrics[name] for name in ("measured_maintenance_ms", "measured_query_ms",
                    "measured_checkpoint_write_ms", "measured_checkpoint_restore_ms"))
    served_queries = sum(row["served_queries"] for row in rows)
    summary = {"policy": policy, "node": node.name,
               "status": terminal or ("recovered" if metrics["faults_injected"] else "completed"),
               "failed_at_window": failed_at, "requested_queries": sum(queries),
               "served_queries": served_queries, "unserved_queries": sum(queries) - served_queries,
               "served_windows": sum(row["status"] == "served" for row in rows),
               "windows": len(windows), "measured_work_ms": service_ms,
               "measured_total_ms": service_ms if terminal is None else None,
               "measured_recovery_ms": metrics["measured_checkpoint_restore_ms"] + metrics["recovery_apply_ms"],
               **metrics}
    return rows, summary, audit
