from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.recovery import FaultSpec, POLICIES, benchmark_recovery
from adaptive_kg_reasoning.recovery_acceptance import expected_outcome, validate_recovery_matrix
from adaptive_kg_reasoning.resources import MemoryModel, NodeProfile
from adaptive_kg_reasoning.windows import StreamEvent, iter_sliding_windows


def matrix(tmp_path, retries=2):
    events = [StreamEvent(index, index * 5, 800., 1, 1, 1, 1) for index in range(4)]
    windows = list(iter_sliding_windows(events, width_seconds=10, slide_seconds=5, flush=True))
    queries = [0, 1, 3, 0, 2]
    assert len(windows) == len(queries)
    specs = {"none": FaultSpec(), **{point: FaultSpec(2, point)
             for point in ("before_update", "after_update", "after_checkpoint")},
             "persistent_crash": FaultSpec(2, "after_update", retries + 1)}
    summaries, details, audits = [], [], []
    for name, spec in specs.items():
        for policy in POLICIES:
            rows, summary, audit = benchmark_recovery(windows, queries, policy=policy, fault=spec,
                checkpoint_path=tmp_path / f"{name}-{policy}.json",
                node=NodeProfile("fog", 100000, 0, 0, 0, 0), memory=MemoryModel(), max_retries=retries)
            context = {"workload": "fixture", "fault": name, "policy": policy}
            summaries.append({**context, **summary})
            details.extend({**context, **row} for row in rows)
            audits.extend({**context, **row} for row in audit)
    return summaries, details, audits, {"schedules": [{"workload": "fixture", "queries": queries}],
                                       "specs": specs, "max_retries": retries}


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_expected_matrix_and_zero_retry_contract(tmp_path, retries):
    summaries, details, audits, config = matrix(tmp_path, retries)
    report = validate_recovery_matrix(summaries, details, audits, **config)
    assert report["status"] == "passed"
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
