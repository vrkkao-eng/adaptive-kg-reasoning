"""Independent, per-case acceptance contract for feasible recovery fixtures."""
from __future__ import annotations

from collections import defaultdict
import math

from .recovery import FaultSpec, POLICIES
from .resources import nonnegative_int

COUNTERS = ("checked_states", "checked_queries", "faults_injected", "recovery_attempts",
            "update_calls", "cold_bootstraps", "replayed_windows", "checkpoint_writes",
            "checkpoint_loads", "checkpoint_bytes_written")
COMPONENT_TIMES = ("measured_maintenance_ms", "measured_query_ms",
                   "measured_checkpoint_write_ms", "measured_checkpoint_restore_ms")
DETAIL_TIMES = (*COMPONENT_TIMES, "recovery_apply_ms")
TIME_REL_TOL = 1e-9
TIME_ABS_TOL_MS = 1e-7
CONTEXT = ("workload", "fault", "policy")


def _count(value):
    return type(value) is int and value >= 0


def _time(value):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def _close(actual, expected):
    return (_time(actual) and _time(expected)
            and math.isclose(actual, expected, rel_tol=TIME_REL_TOL, abs_tol=TIME_ABS_TOL_MS))


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


def _expected_protocol(queries, policy, fault, max_retries, checkpoint_every):
    """Model protocol events/work counts, never worker state, sizes or observations."""
    events, cursor, remaining = [], None, fault.failures
    counts = [{field: 0 for field in COUNTERS if field != "checkpoint_bytes_written"} for _ in queries]

    def emit(event, index, **fields):
        events.append({"event": event, "window_index": index, **fields})

    for index, count in enumerate(queries):
        retry = 0
        bootstrap, recovering, restored_current = False, False, False
        work = counts[index]
        while True:
            crashing = fault.window_index == index and remaining > 0
            if not (crashing and fault.point == "before_update") and not restored_current:
                work["update_calls"] += 1
                work["checked_states"] += 1
                work["cold_bootstraps"] += int(bootstrap)
                work["replayed_windows"] += int(recovering and not bootstrap)
            # Only an after-publication fault permits publishing on the failed attempt.
            if (policy == "checkpoint_replay" and index % checkpoint_every == 0
                    and (not crashing or fault.point == "after_checkpoint")):
                emit("checkpoint_published", index)
                cursor = index
                work["checkpoint_writes"] += 1
            if not crashing:
                emit("window_served", index, queries=count)
                work["checked_queries"] = count
                break
            remaining -= 1
            work["faults_injected"] += 1
            emit("injected_crash", index, point=fault.point)
            if policy == "stop_on_failure" or retry == max_retries:
                emit("terminal", index, reason="stopped_on_failure" if policy == "stop_on_failure"
                     else "retry_exhausted")
                return events, counts
            retry += 1
            work["recovery_attempts"] += 1
            recovering, restored_current = True, False
            emit("recovery_started", index, policy=policy, retry=retry)
            if policy == "cold_rebuild" or cursor is None:
                bootstrap = True
                emit("cold_restart", index, reason="policy" if policy == "cold_rebuild" else "no_checkpoint")
            else:
                bootstrap = False
                emit("checkpoint_restored", index, cursor=cursor)
                work["checkpoint_loads"] += 1
                work["checked_states"] += 1
                for replay_index in range(cursor + 1, index):
                    emit("window_replayed", replay_index, recovery_target=index)
                    for field in ("update_calls", "checked_states", "replayed_windows"):
                        work[field] += 1
                restored_current = cursor == index
    return events, counts


def _validate_case(summary, detail, audit, queries, expected, trace, expected_counts):
    mismatches = []

    def integer_fields(row, fields, label):
        for field in fields:
            if field not in row or not _count(row[field]):
                mismatches.append(f"{label}.{field}: required non-negative integer")

    def timing_fields(row, fields, label):
        for field in fields:
            if field not in row or not _time(row[field]):
                mismatches.append(f"{label}.{field}: required finite non-negative time")

    observed = {}
    if summary is not None:
        integer_fields(summary, (*COUNTERS, "windows", "served_windows", "requested_queries",
                                 "served_queries", "unserved_queries"), "summary")
        timing_fields(summary, (*DETAIL_TIMES, "measured_work_ms", "measured_recovery_ms"), "summary")
        observed = {field: summary.get(field) for field in expected if field != "completed_total"}
        observed["completed_total"] = _time(summary.get("measured_total_ms"))
        for field, value in expected.items():
            if observed[field] != value or type(observed[field]) is not type(value):
                mismatches.append(f"{field}: expected {value!r}, observed {observed[field]!r}")
        if "measured_total_ms" not in summary:
            mismatches.append("summary.measured_total_ms is required, including terminal nulls")
        elif not expected["completed_total"] and summary["measured_total_ms"] is not None:
            mismatches.append("Terminal measured_total_ms must be null")
        if all(_time(summary.get(field)) for field in COMPONENT_TIMES):
            work = sum(summary[field] for field in COMPONENT_TIMES)
            if not _close(summary.get("measured_work_ms"), work):
                mismatches.append("measured_work_ms disagrees with disjoint component sum")
            if expected["completed_total"] and not _close(summary.get("measured_total_ms"), work):
                mismatches.append("measured_total_ms disagrees with completed component sum")
        restore, replay = summary.get("measured_checkpoint_restore_ms"), summary.get("recovery_apply_ms")
        if _time(restore) and _time(replay) and not _close(summary.get("measured_recovery_ms"), restore + replay):
            mismatches.append("measured_recovery_ms disagrees with restore plus recovery apply")

    if len(detail) != len(queries):
        mismatches.append(f"Expected {len(queries)} detail windows, found {len(detail)}")
    publications = [event["window_index"] for event in trace if event["event"] == "checkpoint_published"]
    for position, row in enumerate(detail):
        label = f"detail[{position}]"
        integer_fields(row, (*COUNTERS, "window_index", "requested_queries", "served_queries",
                             "required_memory_bytes", "memory_budget_bytes"), label)
        timing_fields(row, DETAIL_TIMES, label)
        if position < len(queries):
            served = position < expected["served_windows"]
            fields = {"window_index": position, "status": "served" if served else expected["status"],
                      "requested_queries": queries[position], "served_queries": queries[position] if served else 0,
                      "checked_queries": queries[position] if served else 0,
                      "checkpoint_cursor": max((index for index in publications if index <= position), default=None),
                      **expected_counts[position]}
            for field, value in fields.items():
                if field not in row or row[field] != value or type(row[field]) is not type(value):
                    mismatches.append(f"{label}.{field}: disagrees with expected window")
            if served and (not _count(row.get("checked_states")) or row["checked_states"] < 1):
                mismatches.append(f"{label}: served window must have a checked state")
        if all(_count(row.get(field)) for field in ("checked_states", "update_calls", "checkpoint_loads")):
            if row["checked_states"] != row["update_calls"] + row["checkpoint_loads"]:
                mismatches.append(f"{label}: checked states disagree with applied/restored states")
    for label, row in [("summary", summary), *[(f"detail[{i}]", row) for i, row in enumerate(detail)]]:
        if row is not None and _time(row.get("recovery_apply_ms")) and _time(row.get("measured_maintenance_ms")):
            if row["recovery_apply_ms"] > row["measured_maintenance_ms"] and not _close(
                    row["recovery_apply_ms"], row["measured_maintenance_ms"]):
                mismatches.append(f"{label}: recovery apply exceeds maintenance subset")
    if summary is not None:
        for field in expected_counts[0]:
            if summary.get(field) != sum(row[field] for row in expected_counts):
                mismatches.append(f"{field}: summary disagrees with declared work")
        for field in (*COUNTERS, *DETAIL_TIMES):
            valid = _count if field in COUNTERS else _time
            if valid(summary.get(field)) and all(valid(row.get(field)) for row in detail):
                total = sum(row[field] for row in detail)
                equal = summary[field] == total if field in COUNTERS else _close(summary[field], total)
                if not equal:
                    mismatches.append(f"{field}: detail/summary accounting mismatch")

    if len(audit) != len(trace):
        mismatches.append(f"Expected {len(trace)} audit events, found {len(audit)}")
    for position, (row, reference) in enumerate(zip(audit, trace)):
        fields = {"sequence": position, **reference}
        required = set(fields) | set(CONTEXT)
        if reference["event"] == "checkpoint_published":
            required.add("bytes")
            if not _count(row.get("bytes")) or row["bytes"] == 0:
                mismatches.append(f"audit[{position}].bytes: required positive integer")
        if set(row) != required:
            mismatches.append(f"audit[{position}]: unexpected or missing event fields")
        for field, value in fields.items():
            if field not in row or row[field] != value or type(row[field]) is not type(value):
                mismatches.append(f"audit[{position}].{field}: disagrees with declared protocol")
    if summary is not None:
        # replayed_windows also includes the recovering current transition, which
        # is not a window_replayed audit event; do not equate those two counts.
        for event, field in (("checkpoint_published", "checkpoint_writes"),
                             ("checkpoint_restored", "checkpoint_loads")):
            if summary.get(field) != sum(row.get("event") == event for row in audit):
                mismatches.append(f"{field}: audit/summary accounting mismatch")
        byte_counts = [row.get("bytes") for row in audit if row.get("event") == "checkpoint_published"]
        if all(_count(value) and value > 0 for value in byte_counts):
            if summary.get("checkpoint_bytes_written") != sum(byte_counts):
                mismatches.append("checkpoint_bytes_written: audit/summary accounting mismatch")
    for position, row in enumerate(detail):
        published = [event.get("bytes") for event in audit
                     if event.get("event") == "checkpoint_published" and event.get("window_index") == position]
        if all(_count(value) and value > 0 for value in published):
            if row.get("checkpoint_bytes_written") != sum(published):
                mismatches.append(f"detail[{position}]: published byte accounting mismatch")
    # Diagnostic reports remain valid JSON even when an observed scalar is malformed.
    observed = {field: value if value is None or type(value) in (str, int, bool)
                or (type(value) is float and math.isfinite(value)) else repr(value)
                for field, value in observed.items()}
    return mismatches, observed


def validate_recovery_matrix(summaries: list[dict], details: list[dict], audits: list[dict], *,
                             schedules: list[dict], specs: dict[str, FaultSpec], max_retries: int,
                             checkpoint_every: int = 2) -> dict:
    """Check typed evidence, per-window accounting, timing and the declared audit protocol.

    This opt-in contract assumes every window fits the declared resource budget.
    Exploratory infeasibility runs must leave the acceptance gate disabled.
    """
    nonnegative_int("checkpoint_every", checkpoint_every)
    if checkpoint_every == 0:
        raise ValueError("checkpoint_every must be positive")
    groups, schema_errors = [], []
    for name, table in (("summary", summaries), ("detail", details), ("audit", audits)):
        grouped = defaultdict(list)
        for index, row in enumerate(table):
            if not isinstance(row, dict) or any(type(row.get(field)) is not str or not row[field] for field in CONTEXT):
                schema_errors.append(f"{name}[{index}]: expected object with string case keys")
                continue
            grouped[tuple(row[field] for field in CONTEXT)].append(row)
        groups.append(grouped)
    summary_groups, detail_groups, audit_groups = groups
    cases, expected_keys = [], set()
    for schedule in schedules:
        queries = schedule["queries"]
        if type(schedule["workload"]) is not str or not schedule["workload"]:
            raise ValueError("Acceptance workload must be a non-empty string")
        for name, spec in specs.items():
            if type(name) is not str or not name:
                raise ValueError("Acceptance fault name must be a non-empty string")
            if spec.point == "after_checkpoint" and spec.window_index % checkpoint_every != 0:
                raise ValueError("after_checkpoint fault must target a checkpoint boundary")
            for policy in POLICIES:
                case_key = schedule["workload"], name, policy
                if case_key in expected_keys:
                    raise ValueError("Duplicate acceptance scenario")
                expected_keys.add(case_key)
                expected = expected_outcome(queries, policy, spec, max_retries)
                trace, expected_counts = _expected_protocol(queries, policy, spec, max_retries, checkpoint_every)
                rows = summary_groups[case_key]
                mismatches, observed = _validate_case(rows[0] if len(rows) == 1 else None,
                    detail_groups[case_key], audit_groups[case_key], queries, expected, trace, expected_counts)
                if len(rows) != 1:
                    mismatches.insert(0, f"Expected one summary, found {len(rows)}")
                cases.append({"workload": case_key[0], "fault": name, "policy": policy,
                              "expected": expected, "observed": observed, "mismatches": mismatches,
                              "status": "failed" if mismatches else "passed"})
    extra = sorted(set().union(*(set(group) for group in groups)) - expected_keys)
    passed = bool(cases) and not extra and not schema_errors and all(case["status"] == "passed" for case in cases)
    return {"schema_version": 1, "contract": "feasible-bounded-worker-recovery-v2",
            "checkpoint_every": checkpoint_every, "timing_tolerance": {"rel_tol": TIME_REL_TOL,
            "abs_tol_ms": TIME_ABS_TOL_MS}, "status": "passed" if passed else "failed",
            "schema_errors": schema_errors, "unexpected_cases": extra, "cases": cases}
