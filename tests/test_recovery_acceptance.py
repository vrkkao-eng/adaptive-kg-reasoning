from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.recovery import FaultSpec, POLICIES, benchmark_recovery
from adaptive_kg_reasoning.recovery_acceptance import _expected_protocol, expected_outcome, validate_recovery_matrix
from adaptive_kg_reasoning.resources import MemoryModel, NodeProfile
from adaptive_kg_reasoning.windows import StreamEvent, iter_sliding_windows


def matrix(tmp_path, retries=2, cadence=2, fault_window=2, persistent_point="after_update"):
    events = [StreamEvent(index, index * 5, 800., 1, 1, 1, 1) for index in range(4)]
    windows = list(iter_sliding_windows(events, width_seconds=10, slide_seconds=5, flush=True))
    queries = [0, 1, 3, 0, 2]
    assert len(windows) == len(queries)
    specs = {"none": FaultSpec(), **{point: FaultSpec(fault_window, point)
             for point in ("before_update", "after_update", "after_checkpoint")},
             "persistent_crash": FaultSpec(fault_window, persistent_point, retries + 1)}
    summaries, details, audits = [], [], []
    for name, spec in specs.items():
        for policy in POLICIES:
            rows, summary, audit = benchmark_recovery(windows, queries, policy=policy, fault=spec,
                checkpoint_path=tmp_path / f"{name}-{policy}.json",
                node=NodeProfile("fog", 100000, 0, 0, 0, 0), memory=MemoryModel(), max_retries=retries,
                checkpoint_every=cadence)
            context = {"workload": "fixture", "fault": name, "policy": policy}
            summaries.append({**context, **summary})
            details.extend({**context, **row} for row in rows)
            audits.extend({**context, **row} for row in audit)
    return summaries, details, audits, {"schedules": [{"workload": "fixture", "queries": queries}],
                                       "specs": specs, "max_retries": retries, "checkpoint_every": cadence}


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_expected_matrix_and_zero_retry_contract(tmp_path, retries):
    summaries, details, audits, config = matrix(tmp_path, retries)
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "passed", [(case["fault"], case["policy"], case["mismatches"])
                                          for case in report["cases"] if case["status"] == "failed"]
    assert len(report["cases"]) == 15
    # Explicit stop-prefix expectations, independently of benchmark counters.
    expected = expected_outcome([0, 1, 3, 0, 2], "stop_on_failure", FaultSpec(2, "after_update"), retries)
    assert expected["status"] == "stopped_on_failure"
    assert expected["served_windows"] == 2 and expected["served_queries"] == 1
    assert expected["unserved_queries"] == 5 and expected["recovery_attempts"] == 0


@pytest.mark.parametrize("mutation", ["swapped_status", "missing", "duplicate", "unexpected",
                                      "served_queries", "faults", "retries", "total", "terminal_total",
                                      "detail", "detail_counter", "unchecked_states", "audit_terminal",
                                      "audit_duplicate", "audit_sequence"])
def test_gate_rejects_per_case_and_accounting_regressions(tmp_path, mutation):
    summaries, details, audits, config = matrix(tmp_path)
    if mutation == "swapped_status":
        # Preserve aggregate outcome counts: a global histogram would miss this.
        summaries[0]["status"], summaries[3]["status"] = summaries[3]["status"], summaries[0]["status"]
    elif mutation == "missing":
        summaries.pop()
    elif mutation == "duplicate":
        summaries.append(deepcopy(summaries[0]))
    elif mutation == "unexpected":
        summaries.append({**summaries[0], "workload": "unrequested"})
    elif mutation == "served_queries":
        summaries[0]["served_queries"] += 1
    elif mutation == "faults":
        summaries[0]["faults_injected"] = 1
    elif mutation == "retries":
        summaries[4]["recovery_attempts"] = 2
    elif mutation == "total":
        summaries[0]["measured_total_ms"] = float("nan")
    elif mutation == "terminal_total":
        summaries[3]["measured_total_ms"] = 0.
    elif mutation == "detail":
        details.pop(0)
    elif mutation == "detail_counter":
        details[0]["checked_states"] += 1
    elif mutation == "unchecked_states":
        summaries[0]["checked_states"] = 0
        for row in details[:5]:
            row["checked_states"] = 0
    elif mutation == "audit_terminal":
        next(row for row in audits if row["event"] == "terminal")["reason"] = "checkpoint_rejected"
    elif mutation == "audit_duplicate":
        audits.insert(0, deepcopy(audits[0]))
    else:
        audits[0]["sequence"] += 1
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "failed"
    assert report["unexpected_cases"] or any(case["mismatches"] for case in report["cases"])


def test_empty_matrix_is_not_accepted():
    assert validate_recovery_matrix([], [], [], schedules=[], specs={}, max_retries=2)["status"] == "failed"


@pytest.mark.parametrize("queries,policy,fault,retries", [([], "cold_rebuild", FaultSpec(), 2),
    ([1], "unknown", FaultSpec(), 2), ([True], "cold_rebuild", FaultSpec(), 2),
    ([1], "cold_rebuild", FaultSpec(1, "before_update"), 2), ([1], "cold_rebuild", FaultSpec(), -1)])
def test_invalid_acceptance_configuration(queries, policy, fault, retries):
    with pytest.raises(ValueError):
        expected_outcome(queries, policy, fault, retries)


@pytest.mark.parametrize("mutation", ["fault_window", "fault_point", "retry_window", "retry_number",
    "restore_cursor", "replay_target", "checkpoint_window", "checkpoint_bytes", "audit_order",
    "window_state_checks", "window_query_checks", "bool_index", "float_counter", "missing_counter",
    "negative_counter", "negative_time", "nan_time", "wrong_total", "nan_work"])
def test_strict_acceptance_regressions(tmp_path, mutation):
    summaries, details, audits, config = matrix(tmp_path)
    def event(kind):
        return next(row for row in audits if row["event"] == kind)
    if mutation == "fault_window":
        event("injected_crash")["window_index"] = 999
    elif mutation == "fault_point":
        event("injected_crash")["point"] = "wrong_boundary"
    elif mutation == "retry_window":
        event("recovery_started")["window_index"] = 999
    elif mutation == "retry_number":
        event("recovery_started")["retry"] = 999
    elif mutation == "restore_cursor":
        event("checkpoint_restored")["cursor"] += 1
    elif mutation == "replay_target":
        event("window_replayed")["recovery_target"] = 999
    elif mutation == "checkpoint_window":
        event("checkpoint_published")["window_index"] += 1
    elif mutation == "checkpoint_bytes":
        event("checkpoint_published")["bytes"] = -1
    elif mutation == "audit_order":
        left = audits.index(event("recovery_started"))
        audits[left - 1], audits[left] = audits[left], audits[left - 1]
        audits[left - 1]["sequence"], audits[left]["sequence"] = (audits[left]["sequence"],
                                                               audits[left - 1]["sequence"])
    elif mutation in ("window_state_checks", "window_query_checks"):
        field = "checked_states" if mutation == "window_state_checks" else "checked_queries"
        source = 0 if mutation == "window_state_checks" else 1
        details[source + 1][field] += details[source][field]
        details[source][field] = 0
    elif mutation == "bool_index":
        details[0]["window_index"] = False
    elif mutation == "float_counter":
        summaries[0]["checkpoint_loads"] = 0.0
        details[0]["checkpoint_loads"] = 0.0
    elif mutation == "missing_counter":
        del details[0]["checkpoint_loads"]
    elif mutation == "negative_counter":
        summaries[0]["checkpoint_bytes_written"] = -1
        details[0]["checkpoint_bytes_written"] = -1
    elif mutation == "negative_time":
        details[0]["measured_maintenance_ms"] = -1.
    elif mutation == "nan_time":
        details[0]["measured_query_ms"] = float("nan")
    elif mutation == "wrong_total":
        summaries[0]["measured_total_ms"] += 1000.
    else:
        summaries[0]["measured_work_ms"] = float("nan")
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "failed"
    assert any(case["mismatches"] for case in report["cases"])


@pytest.mark.parametrize("cadence,fault_window,point", [(1, 0, "before_update"), (1, 4, "after_update"),
    (2, 0, "after_checkpoint"), (2, 4, "after_checkpoint"), (3, 3, "before_update"),
    (3, 3, "after_update"), (3, 3, "after_checkpoint"), (10, 0, "before_update")])
def test_protocol_accepts_custom_cadence_first_last_and_persistent_faults(tmp_path, cadence, fault_window, point):
    summaries, details, audits, config = matrix(tmp_path, cadence=cadence, fault_window=fault_window,
                                               persistent_point=point)
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "passed", [case["mismatches"] for case in report["cases"] if case["mismatches"]]
    assert report["contract"] == "feasible-bounded-worker-recovery-v2"
    assert report["checkpoint_every"] == cadence


def test_after_publication_protocol_has_hand_checked_order():
    trace, counts = _expected_protocol([0, 2, 1], "checkpoint_replay", FaultSpec(2, "after_checkpoint"), 1, 2)
    assert trace == [
        {"event": "checkpoint_published", "window_index": 0},
        {"event": "window_served", "window_index": 0, "queries": 0},
        {"event": "window_served", "window_index": 1, "queries": 2},
        {"event": "checkpoint_published", "window_index": 2},
        {"event": "injected_crash", "window_index": 2, "point": "after_checkpoint"},
        {"event": "recovery_started", "window_index": 2, "policy": "checkpoint_replay", "retry": 1},
        {"event": "checkpoint_restored", "window_index": 2, "cursor": 2},
        {"event": "checkpoint_published", "window_index": 2},
        {"event": "window_served", "window_index": 2, "queries": 1},
    ]
    assert counts[2]["update_calls"] == counts[2]["checkpoint_loads"] == 1
    assert counts[2]["checked_states"] == counts[2]["checkpoint_writes"] == 2
    assert counts[2]["replayed_windows"] == 0


@pytest.mark.parametrize("field", ["faults_injected", "recovery_attempts", "update_calls", "cold_bootstraps",
                                   "replayed_windows", "checkpoint_writes", "checkpoint_loads", "checkpoint_bytes_written"])
def test_moving_work_between_windows_cannot_preserve_acceptance(tmp_path, field):
    summaries, details, audits, config = matrix(tmp_path)
    source = next(row for row in details if row[field] > 0)
    target = next(row for row in details if all(row[key] == source[key] for key in ("workload", "fault", "policy"))
                  and row["window_index"] != source["window_index"])
    target[field] += source[field]
    source[field] = 0
    assert validate_recovery_matrix(summaries, details, audits, **config)["status"] == "failed"


@pytest.mark.parametrize("table", ["summary", "detail"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "0", None])
def test_time_fields_require_finite_nonnegative_numbers(tmp_path, table, value):
    summaries, details, audits, config = matrix(tmp_path)
    (summaries if table == "summary" else details)[0]["measured_query_ms"] = value
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "failed"
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("value", [-1, True, 0.0, "0", None])
def test_counter_fields_require_exact_nonnegative_integers(tmp_path, value):
    summaries, details, audits, config = matrix(tmp_path)
    summaries[0]["checkpoint_loads"] = details[0]["checkpoint_loads"] = value
    assert validate_recovery_matrix(summaries, details, audits, **config)["status"] == "failed"


@pytest.mark.parametrize("mutation", ["missing_total", "wrong_work", "wrong_recovery", "subset_overrun",
                                      "detail_time_sum", "cursor_type", "audit_bool", "missing_audit_field"])
def test_additional_accounting_and_audit_schema_checks(tmp_path, mutation):
    summaries, details, audits, config = matrix(tmp_path)
    if mutation == "missing_total":
        del summaries[3]["measured_total_ms"]  # A terminal null is required, not an absent field.
    elif mutation == "wrong_work":
        summaries[0]["measured_work_ms"] += 1.
    elif mutation == "wrong_recovery":
        summaries[4]["measured_recovery_ms"] += 1.
    elif mutation == "subset_overrun":
        details[0]["recovery_apply_ms"] = details[0]["measured_maintenance_ms"] + 1.
    elif mutation == "detail_time_sum":
        details[0]["measured_query_ms"] += 1.
    elif mutation == "cursor_type":
        next(row for row in details if row["checkpoint_cursor"] == 0)["checkpoint_cursor"] = False
    elif mutation == "audit_bool":
        audits[0]["sequence"] = False
    else:
        del next(row for row in audits if row["event"] == "injected_crash")["point"]
    assert validate_recovery_matrix(summaries, details, audits, **config)["status"] == "failed"


def test_small_timing_roundoff_is_accepted_without_repair(tmp_path):
    summaries, details, audits, config = matrix(tmp_path)
    summaries[0]["measured_total_ms"] += 1e-10
    value = summaries[0]["measured_total_ms"]
    assert validate_recovery_matrix(summaries, details, audits, **config)["status"] == "passed"
    assert summaries[0]["measured_total_ms"] == value


@pytest.mark.parametrize("table", ["summary", "detail", "audit"])
def test_missing_case_keys_retain_schema_diagnostics(tmp_path, table):
    summaries, details, audits, config = matrix(tmp_path)
    target = {"summary": summaries, "detail": details, "audit": audits}[table]
    del target[0]["policy"]
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "failed" and report["schema_errors"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("cadence", [0, -1, True, 2.0])
def test_invalid_acceptance_cadence_rejected(cadence):
    with pytest.raises(ValueError):
        validate_recovery_matrix([], [], [], schedules=[], specs={}, max_retries=2, checkpoint_every=cadence)
