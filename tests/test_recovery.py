import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning import checkpoint, recovery
from adaptive_kg_reasoning.checkpoint import CheckpointError, canonical_bytes, load_checkpoint, save_checkpoint
from adaptive_kg_reasoning.incremental import IncrementalHighRecentState
from adaptive_kg_reasoning.recompute import recompute_window
from adaptive_kg_reasoning.recovery import FaultSpec, POLICIES, benchmark_recovery, trace_identity
from adaptive_kg_reasoning.resources import MemoryModel, NodeProfile
from adaptive_kg_reasoning.windows import StreamEvent, iter_sliding_windows


def fixture_windows():
    events = [StreamEvent(1, 0, 800., 1, 1, 1, 1), StreamEvent(2, 5, 100., 1, 1, 1, 1),
              StreamEvent(3, 10, 900., 1, 2, 1, 1), StreamEvent(4, 15, 0., 1, 2, 1, 1)]
    return list(iter_sliding_windows(events, width_seconds=10, slide_seconds=5, flush=True))


def run(tmp_path, policy="checkpoint_replay", fault=FaultSpec(), **kwargs):
    windows = fixture_windows()
    return benchmark_recovery(windows, [2] * len(windows), policy=policy, fault=fault,
        checkpoint_path=tmp_path / "checkpoint.json", node=NodeProfile("fog", 100000, 0, 0, 0, 0),
        memory=MemoryModel(), **kwargs)


def snapshot(tmp_path):
    windows = fixture_windows()
    state = IncrementalHighRecentState()
    state.apply(windows[0])
    identity = trace_identity(windows, threshold=450.)
    path = tmp_path / "snapshot.json"
    save_checkpoint(path, state, cursor=0, identity=identity)
    return path, state, identity


def test_snapshot_preserves_support_and_continuation(tmp_path):
    path, state, identity = snapshot(tmp_path)
    restored, cursor = load_checkpoint(path, expected_identity=identity)
    assert cursor == 0 and restored.active_events == state.active_events
    assert restored.aggregates == state.aggregates and restored.facts == state.facts
    for window in fixture_windows()[1:]:
        assert restored.apply(window).facts == state.apply(window).facts
    assert not restored.active_events and not restored.facts


@pytest.mark.parametrize("point", ["before_update", "after_update", "after_checkpoint"])
@pytest.mark.parametrize("policy", POLICIES)
def test_fault_boundaries_and_no_duplicate_served_queries(tmp_path, point, policy):
    rows, summary, audit = run(tmp_path, policy, FaultSpec(2, point))
    assert summary["faults_injected"] == 1
    assert summary["checked_queries"] == summary["served_queries"]
    if policy == "stop_on_failure":
        assert summary["status"] == "stopped_on_failure"
        assert summary["served_queries"] == 4 and summary["unserved_queries"] == 6
        assert summary["measured_total_ms"] is None
    else:
        assert summary["status"] == "recovered"
        assert summary["served_queries"] == 10 and summary["unserved_queries"] == 0
        assert summary["recovery_attempts"] == 1
        assert [r["window_index"] for r in audit if r["event"] == "window_served"] == list(range(5))
    if policy == "checkpoint_replay":
        assert summary["checkpoint_loads"] == 1
        assert summary["cold_bootstraps"] == 0
        assert summary["replayed_windows"] == (0 if point == "after_checkpoint" else 2)
    if policy == "cold_rebuild":
        assert summary["cold_bootstraps"] == 1 and summary["checkpoint_writes"] == 0


@pytest.mark.parametrize("policy", POLICIES)
def test_no_fault_baselines_are_equivalent(tmp_path, policy):
    _, summary, _ = run(tmp_path, policy)
    assert summary["status"] == "completed"
    assert summary["served_queries"] == summary["checked_queries"] == 10
    assert summary["recovery_attempts"] == 0


def test_no_checkpoint_cold_start_is_explicit(tmp_path):
    _, summary, audit = run(tmp_path, fault=FaultSpec(0, "after_update"))
    assert summary["status"] == "recovered" and summary["cold_bootstraps"] == 1
    assert any(row.get("reason") == "no_checkpoint" for row in audit)


@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_persistent_crash_stops_at_retry_budget(tmp_path, max_retries):
    _, summary, _ = run(tmp_path, fault=FaultSpec(2, "after_update", 5), max_retries=max_retries)
    assert summary["status"] == "retry_exhausted"
    assert summary["recovery_attempts"] == max_retries
    assert summary["faults_injected"] == max_retries + 1
    assert summary["served_queries"] == 4 and summary["unserved_queries"] == 6
    assert summary["measured_total_ms"] is None


def test_corrupt_checkpoint_has_no_implicit_fallback(tmp_path, monkeypatch):
    original = recovery.load_checkpoint
    def reject(*args, **kwargs):
        path = args[0]
        raw = json.loads(path.read_text())
        raw["payload"]["cursor"] += 1
        path.write_text(json.dumps(raw))
        return original(*args, **kwargs)
    monkeypatch.setattr(recovery, "load_checkpoint", reject)
    _, summary, audit = run(tmp_path, fault=FaultSpec(2, "after_update"))
    assert summary["status"] == "checkpoint_rejected"
    assert summary["served_queries"] == 4 and summary["unserved_queries"] == 6
    assert summary["cold_bootstraps"] == 0
    assert audit[-1]["error"] == "Checkpoint checksum mismatch"


def test_original_floating_point_total_survives_snapshot(tmp_path):
    windows = fixture_windows()
    state = IncrementalHighRecentState()
    state.apply(windows[0])
    aggregate = next(iter(state.aggregates.values()))
    aggregate.total_load += 1e-10  # Representative incremental roundoff.
    path = tmp_path / "roundoff.json"
    save_checkpoint(path, state, cursor=0, identity="fixture")
    restored, _ = load_checkpoint(path, expected_identity="fixture")
    assert next(iter(restored.aggregates.values())).total_load == aggregate.total_load


def test_recovery_rejects_unexpected_cursor(tmp_path, monkeypatch):
    original = recovery.load_checkpoint
    def moved(*args, **kwargs):
        state, cursor = original(*args, **kwargs)
        return state, cursor + 1
    monkeypatch.setattr(recovery, "load_checkpoint", moved)
    _, summary, audit = run(tmp_path, fault=FaultSpec(2, "after_update"))
    assert summary["status"] == "checkpoint_rejected"
    assert summary["served_queries"] == 4
    assert "cursor" in audit[-1]["error"]


def test_timing_components_are_not_double_counted(tmp_path):
    rows, summary, _ = run(tmp_path, fault=FaultSpec(2, "after_update"))
    components = ("measured_maintenance_ms", "measured_query_ms", "measured_checkpoint_write_ms",
                  "measured_checkpoint_restore_ms")
    assert summary["measured_total_ms"] == pytest.approx(sum(summary[name] for name in components))
    assert summary["measured_work_ms"] == pytest.approx(sum(sum(row[name] for name in components) for row in rows))
    assert summary["recovery_apply_ms"] <= summary["measured_maintenance_ms"]


def test_budget_violation_is_not_repaired_by_restart(tmp_path):
    windows = fixture_windows()
    _, summary, audit = benchmark_recovery(windows, [1] * len(windows), policy="checkpoint_replay",
        fault=FaultSpec(2, "after_update"), checkpoint_path=tmp_path / "c.json",
        node=NodeProfile("edge", 0, 0, 0, 0, 0), memory=MemoryModel())
    assert summary["status"] == "memory_budget_exceeded"
    assert summary["recovery_attempts"] == 0 and summary["faults_injected"] == 0
    assert summary["served_queries"] == 0


def test_atomic_replace_failure_preserves_previous_checkpoint(tmp_path, monkeypatch):
    path, state, identity = snapshot(tmp_path)
    original = path.read_bytes()
    def fail(*args):
        raise OSError("injected write failure")
    monkeypatch.setattr(checkpoint.os, "replace", fail)
    with pytest.raises(OSError, match="write failure"):
        save_checkpoint(path, state, cursor=1, identity=identity)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []
    assert load_checkpoint(path, expected_identity=identity)[1] == 0


def test_checksum_and_identity_rejection(tmp_path):
    path, _, identity = snapshot(tmp_path)
    with pytest.raises(CheckpointError, match="identity mismatch"):
        load_checkpoint(path, expected_identity="another-run")
    raw = json.loads(path.read_text())
    raw["payload"]["cursor"] = 100
    path.write_text(json.dumps(raw))
    with pytest.raises(CheckpointError, match="checksum mismatch"):
        load_checkpoint(path, expected_identity=identity)


@pytest.mark.parametrize("mutation", ["count", "total", "fact", "duplicate", "schema", "cursor"])
def test_semantic_validation_even_with_recomputed_checksum(tmp_path, mutation):
    path, _, identity = snapshot(tmp_path)
    raw = json.loads(path.read_text())
    if mutation == "count":
        raw["payload"]["aggregates"][0]["count"] += 1
    elif mutation == "total":
        raw["payload"]["aggregates"][0]["total_load"] += 100
    elif mutation == "fact":
        raw["payload"]["facts"] = []
    elif mutation == "duplicate":
        raw["payload"]["active_events"].append(raw["payload"]["active_events"][0])
    elif mutation == "schema":
        raw["schema_version"] = 2
    else:
        raw["payload"]["cursor"] = True
    raw["sha256"] = hashlib.sha256(canonical_bytes(raw["payload"])).hexdigest()
    path.write_text(json.dumps(raw))
    with pytest.raises(CheckpointError):
        load_checkpoint(path, expected_identity=identity)


@pytest.mark.parametrize("contents", ['{"schema_version":1,', '{"a":1,"a":2}', '[]'])
def test_truncated_and_malformed_checkpoint_rejected(tmp_path, contents):
    path = tmp_path / "c.json"
    path.write_text(contents)
    with pytest.raises(CheckpointError):
        load_checkpoint(path, expected_identity="run")


def test_oracle_divergence_fails_experiment(tmp_path, monkeypatch):
    original = IncrementalHighRecentState.apply
    def corrupt(self, window):
        result = original(self, window)
        self.facts.clear()
        return result
    monkeypatch.setattr(IncrementalHighRecentState, "apply", corrupt)
    with pytest.raises(AssertionError, match="diverges"):
        run(tmp_path)


def test_invalid_cadence_and_fault_window_rejected(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        run(tmp_path, checkpoint_every=0)
    with pytest.raises(ValueError, match="outside"):
        run(tmp_path, fault=FaultSpec(100, "before_update"))
    with pytest.raises(ValueError, match="boundary"):
        run(tmp_path, fault=FaultSpec(1, "after_checkpoint"))
    with pytest.raises(ValueError):
        FaultSpec(1, "unknown")


@pytest.mark.parametrize("mutation", ["count", "bool_count", "float_count", "total", "nan", "infinity",
                                      "bool_total", "missing", "extra"])
def test_live_state_support_corruption_is_rejected(mutation):
    window = fixture_windows()[0]
    state = IncrementalHighRecentState()
    state.apply(window)
    plug, aggregate = next(iter(state.aggregates.items()))
    if mutation == "count":
        aggregate.count += 1
    elif mutation == "bool_count":
        aggregate.count = True
    elif mutation == "float_count":
        aggregate.count = float(aggregate.count)
    elif mutation == "total":
        aggregate.total_load = 0.
    elif mutation in ("nan", "infinity"):
        aggregate.total_load = float("nan" if mutation == "nan" else "inf")
    elif mutation == "bool_total":
        aggregate.total_load = True
    elif mutation == "missing":
        del state.aggregates[plug]
    else:
        state.aggregates[plug + "-extra"] = aggregate
    # The event registry and cached answer are unchanged: fact-only checks miss this.
    with pytest.raises(AssertionError, match="diverges"):
        recovery.check_state(state, window, recompute_window(window))


@pytest.mark.parametrize("policy", ["stop_on_failure", "cold_rebuild"])
def test_non_checkpoint_paths_reject_corrupt_support(tmp_path, monkeypatch, policy):
    original = IncrementalHighRecentState.apply
    def corrupt(self, window):
        result = original(self, window)
        next(iter(self.aggregates.values())).total_load = 0.
        return result
    monkeypatch.setattr(IncrementalHighRecentState, "apply", corrupt)
    with pytest.raises(AssertionError, match="diverges"):
        run(tmp_path, policy)


def test_live_state_roundoff_and_empty_expiration():
    state = IncrementalHighRecentState()
    for window in fixture_windows():
        state.apply(window)
        if state.aggregates:
            next(iter(state.aggregates.values())).total_load += 1e-10
        recovery.check_state(state, window, recompute_window(window))
    assert not state.aggregates


def test_restore_checks_support_against_reference_even_after_decoding(tmp_path, monkeypatch):
    original = recovery.load_checkpoint
    def corrupt(*args, **kwargs):
        state, cursor = original(*args, **kwargs)
        next(iter(state.aggregates.values())).total_load = 0.
        return state, cursor
    monkeypatch.setattr(recovery, "load_checkpoint", corrupt)
    _, summary, audit = run(tmp_path, fault=FaultSpec(2, "after_update"))
    assert summary["status"] == "checkpoint_rejected"
    assert summary["served_queries"] == 4
    assert "does not match cursor" in audit[-1]["error"]


def test_tolerated_total_drift_cannot_hide_a_threshold_crossing():
    window = fixture_windows()[0]
    state = IncrementalHighRecentState(threshold_watts=450.)
    state.apply(window)
    next(iter(state.aggregates.values())).total_load -= 1e-10
    with pytest.raises(AssertionError, match="facts"):
        recovery.check_state(state, window, recompute_window(window, threshold_watts=450.))


def test_precomputed_support_oracle_is_used_during_restore(tmp_path, monkeypatch):
    original = recovery.aggregate_window_loads
    calls = 0
    def counted(events):
        nonlocal calls
        calls += 1
        return original(events)
    monkeypatch.setattr(recovery, "aggregate_window_loads", counted)
    _, summary, _ = run(tmp_path, fault=FaultSpec(2, "after_update"))
    assert summary["status"] == "recovered"
    assert calls == len(fixture_windows())  # No oracle recomputation in restore timing.
