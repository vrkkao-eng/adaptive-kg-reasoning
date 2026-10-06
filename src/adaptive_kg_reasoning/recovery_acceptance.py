"""Independent, per-case acceptance contract for feasible recovery fixtures."""
from __future__ import annotations

from collections import defaultdict
import math

from .recovery import FaultSpec, POLICIES
from .resources import nonnegative_int


def expected_outcome(queries: list[int], policy: str, fault: FaultSpec, max_retries: int) -> dict:
    """Derive service/fault expectations from the scenario, not observed outcomes."""
    nonnegative_int("max_retries", max_retries)
    if policy not in POLICIES or not queries:
        raise ValueError("Expected a known policy and non-empty query schedule")
    for count in queries:
        nonnegative_int("queries", count)
    has_fault = fault.point != "none"
    if has_fault and fault.window_index >= len(queries):
        raise ValueError("Fault window is outside the acceptance schedule")
    stopped = has_fault and policy == "stop_on_failure"
    exhausted = has_fault and not stopped and fault.failures > max_retries
    terminal = stopped or exhausted
    prefix = fault.window_index if terminal else len(queries)
    served = sum(queries[:prefix])
    return {
        "status": ("stopped_on_failure" if stopped else "retry_exhausted" if exhausted
                   else "recovered" if has_fault else "completed"),
        "failed_at_window": fault.window_index if terminal else None,
        "windows": len(queries), "served_windows": prefix,
        "requested_queries": sum(queries), "served_queries": served,
        "unserved_queries": sum(queries) - served, "checked_queries": served,
        "faults_injected": (1 if stopped else min(fault.failures, max_retries + 1)) if has_fault else 0,
        "recovery_attempts": min(fault.failures, max_retries) if has_fault and not stopped else 0,
        "completed_total": not terminal,
    }


def validate_recovery_matrix(summaries: list[dict], details: list[dict], audits: list[dict], *,
                             schedules: list[dict], specs: dict[str, FaultSpec], max_retries: int) -> dict:
    """Reject missing/duplicate cases and unexpected service or accounting outcomes.

    This opt-in contract assumes every window fits the declared resource budget.
    Exploratory infeasibility runs must leave the acceptance gate disabled.
    """
    def key(row):
        return row["workload"], row["fault"], row["policy"]

    groups = []
    for table in (summaries, details, audits):
        grouped = defaultdict(list)
        for row in table:
            grouped[key(row)].append(row)
        groups.append(grouped)
    summary_groups, detail_groups, audit_groups = groups
    cases, expected_keys = [], set()
    for schedule in schedules:
        queries = schedule["queries"]
        for name, spec in specs.items():
            for policy in POLICIES:
                case_key = schedule["workload"], name, policy
                if case_key in expected_keys:
                    raise ValueError("Duplicate acceptance scenario")
                expected_keys.add(case_key)
                expected = expected_outcome(queries, policy, spec, max_retries)
                rows = summary_groups[case_key]
                mismatches = []
                observed = {}
                if len(rows) != 1:
                    mismatches.append(f"Expected one summary, found {len(rows)}")
                else:
                    summary = rows[0]
                    observed = {field: summary.get(field) for field in expected if field != "completed_total"}
                    total = summary.get("measured_total_ms")
                    observed["completed_total"] = (isinstance(total, (int, float))
                                                    and not isinstance(total, bool)
                                                    and math.isfinite(total) and total >= 0)
                    for field, value in expected.items():
                        if observed[field] != value or (field != "status" and type(observed[field]) is not type(value)):
                            mismatches.append(f"{field}: expected {value!r}, observed {observed[field]!r}")
                    if not expected["completed_total"] and total is not None:
                        mismatches.append("Terminal measured_total_ms must be null")
                    checked_states = summary.get("checked_states")
                    if type(checked_states) is not int or checked_states < expected["served_windows"]:
                        mismatches.append("Every served window requires a checked state")
                detail = detail_groups[case_key]
                prefix = expected["served_windows"]
                expected_detail = [(index, "served" if index < prefix else expected["status"],
                                    count, count if index < prefix else 0)
                                   for index, count in enumerate(queries)]
                actual_detail = [(row.get("window_index"), row.get("status"),
                                  row.get("requested_queries"), row.get("served_queries")) for row in detail]
                if actual_detail != expected_detail:
                    mismatches.append("Detail windows/service do not match the expected prefix")
                if len(rows) == 1:
                    for field in ("checked_states", "checked_queries", "faults_injected", "recovery_attempts",
                                  "update_calls", "cold_bootstraps", "replayed_windows", "checkpoint_writes",
                                  "checkpoint_loads", "checkpoint_bytes_written"):
                        if sum(row.get(field, 0) for row in detail) != rows[0].get(field):
                            mismatches.append(f"{field}: detail/summary accounting mismatch")
                audit = audit_groups[case_key]
                served_audit = [(row.get("window_index"), row.get("queries"))
                                for row in audit if row.get("event") == "window_served"]
                if served_audit != list(enumerate(queries[:prefix])):
                    mismatches.append("Audit must serve each expected window exactly once")
                terminals = [(row.get("window_index"), row.get("reason"))
                             for row in audit if row.get("event") == "terminal"]
                expected_terminals = ([] if expected["completed_total"] else
                                      [(expected["failed_at_window"], expected["status"])])
                if terminals != expected_terminals:
                    mismatches.append("Audit terminal outcome disagrees with scenario")
                for event, field in (("injected_crash", "faults_injected"), ("recovery_started", "recovery_attempts")):
                    if sum(row.get("event") == event for row in audit) != expected[field]:
                        mismatches.append(f"Audit {event} count disagrees with scenario")
                if [row.get("sequence") for row in audit] != list(range(len(audit))):
                    mismatches.append("Audit sequence is not consecutive")
                cases.append({"workload": case_key[0], "fault": name, "policy": policy,
                              "expected": expected, "observed": observed, "mismatches": mismatches,
                              "status": "failed" if mismatches else "passed"})
    extra = sorted(set().union(*(set(group) for group in groups)) - expected_keys)
    passed = bool(cases) and not extra and all(case["status"] == "passed" for case in cases)
    return {"schema_version": 1, "contract": "feasible-bounded-worker-recovery-v1",
            "status": "passed" if passed else "failed", "unexpected_cases": extra,
            "cases": cases}
