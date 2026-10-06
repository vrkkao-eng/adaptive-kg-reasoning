"""Supervise real process termination and independently check durable prefixes."""
from __future__ import annotations

import json
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import threading
import time

from .checkpoint import canonical_bytes
from .process_resume import inspect_job, load_job, POINTS
from .resources import nonnegative_int


def launch_worker(script: Path, directory: Path, *, point=None, window=2, timeout=60):
    command = [sys.executable, str(script), "--job-dir", str(directory)]
    if point:
        command += ["--pause-point", point, "--pause-window", str(window)]
    start = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    lines = Queue()
    reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
    reader.start()
    killed, first = False, ""
    try:
        try:
            first = lines.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError("Worker did not reach its bounded rendezvous") from exc
        if point:
            expected = {"event": "kill_ready", "point": point, "window_index": window}
            if json.loads(first or "null") != expected:
                _, error = process.communicate(timeout=timeout)
                raise RuntimeError(f"Worker failed before kill boundary: {error}")
            process.kill()
            killed = True
        output, error = process.communicate(timeout=timeout)
        if not killed and process.returncode != 0:
            raise RuntimeError(f"Worker failed: {error}")
        if killed and process.returncode == 0:
            raise AssertionError("Terminated worker unexpectedly succeeded")
        return {"termination": "parent_kill" if killed else "normal", "returncode": process.returncode,
                "stdout": first + output, "stderr": error,
                "process_wall_ms": (time.perf_counter() - start) * 1000}
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def expected_case(job, *, point, policy, fault_window, max_restarts, persistent):
    """Project committed prefixes and audit order from the declared kill scenario."""
    frontier, events, attempts, kills, launches = -1, [], 0, 0, []
    while True:
        if frontier < len(job.windows) - 1:
            attempts += 1
            events.append({"event": "process_started", "attempt": attempts, "cursor": frontier})
        killing = point is not None and (persistent or not kills)
        target = (fault_window if point == "after_commit" else fault_window - 1) if killing else len(job.windows) - 1
        for index in range(frontier + 1, target + 1):
            # The independently checked receipt contains the oracle digest; protocol
            # projection checks exact sequence and declared request/input counts.
            events.append({"event": "window_committed", "window_index": index,
                           "input_cursor": job.input_offsets[index], "queries": job.queries[index]})
        frontier = target
        launches.append({"termination": "parent_kill" if killing else "normal", "cursor": frontier})
        if not killing:
            status = "completed" if point is None else "resumed"
            break
        kills += 1
        if policy == "stop":
            status = "interrupted"
            break
        if kills > max_restarts:
            status = "restart_exhausted"
            break
    return {"status": status, "cursor": frontier, "served_windows": frontier + 1,
            "served_queries": sum(job.queries[:frontier + 1]), "attempts": attempts,
            "kills": kills, "launches": launches, "audit": events}


def execute_case(script: Path, directory: Path, *, point=None, policy="resume", fault_window=2,
                 max_restarts=2, persistent=False, timeout=60):
    job = load_job(directory)
    nonnegative_int("max_restarts", max_restarts)
    nonnegative_int("fault_window", fault_window)
    if policy not in ("stop", "resume") or (point is not None and point not in POINTS):
        raise ValueError("Unknown process policy or kill point")
    if point and fault_window >= len(job.windows):
        raise ValueError("Kill window is outside the trace")
    if persistent and point != "before_commit":
        raise ValueError("Persistent process faults target before_commit")
    expected = expected_case(job, point=point, policy=policy, fault_window=fault_window,
                             max_restarts=max_restarts, persistent=persistent)
    launches, kills = [], 0
    while True:
        killing = point is not None and (persistent or not kills)
        row = launch_worker(script, directory, point=point if killing else None,
                            window=fault_window, timeout=timeout)
        row["cursor"] = inspect_job(directory)["cursor"]
        launches.append(row)
        (directory / "supervisor.json").write_text(json.dumps(launches, indent=2, allow_nan=False) + "\n",
                                                   encoding="utf-8")
        if not killing:
            status = "completed" if point is None else "resumed"
            break
        kills += 1
        if policy == "stop":
            status = "interrupted"
            break
        if kills > max_restarts:
            status = "restart_exhausted"
            break
    observed = inspect_job(directory)
    observed.update(status=status, kills=kills)
    audit = [{key: value for key, value in event.items() if key not in ("sequence", "answer_sha256")}
             for event in observed["audit"]]
    protocol = [{"termination": row["termination"], "cursor": row["cursor"]} for row in launches]
    mismatches = [field for field in ("status", "cursor", "served_windows", "served_queries", "attempts", "kills")
                  if type(observed[field]) is not type(expected[field]) or observed[field] != expected[field]]
    if canonical_bytes(audit) != canonical_bytes(expected["audit"]):
        mismatches.append("audit_protocol")
    if canonical_bytes(protocol) != canonical_bytes(expected["launches"]):
        mismatches.append("launch_frontiers")
    (directory / "receipts.json").write_bytes(canonical_bytes(observed["receipts"]) + b"\n")
    (directory / "audit.json").write_bytes(canonical_bytes(observed["audit"]) + b"\n")
    return {"status": status, "served_windows": observed["served_windows"], "windows": observed["windows"],
            "served_queries": observed["served_queries"], "requested_queries": observed["requested_queries"],
            "unserved_queries": observed["requested_queries"] - observed["served_queries"],
            "input_cursor": observed["input_cursor"], "applied_cursor": observed["cursor"],
            "served_cursor": observed["cursor"], "process_launches": len(launches), "process_kills": kills,
            "process_wall_ms": sum(row["process_wall_ms"] for row in launches),
            "acceptance": "failed" if mismatches else "passed", "mismatches": mismatches}
