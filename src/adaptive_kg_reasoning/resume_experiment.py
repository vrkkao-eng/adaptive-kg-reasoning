"""Supervise real process termination and independently check durable prefixes."""
from __future__ import annotations

import json
import math
from pathlib import Path
from queue import Queue, Empty
import subprocess
import sys
import threading
import time

from .checkpoint import canonical_bytes
from .process_resume import inspect_job, load_job, read_json, POINTS
from .resources import nonnegative_int


OUTPUT_LIMIT = 16384


class WorkerLaunchError(RuntimeError):
    """Unexpected launch outcome with bounded, exportable diagnostic evidence."""
    def __init__(self, message, record):
        super().__init__(message)
        self.record = record


class WorkerTimeoutError(WorkerLaunchError, TimeoutError):
    """A worker exceeded a bounded rendezvous or completion wait."""


class _OutputCapture:
    def __init__(self):
        self.data = bytearray()
        self.total = 0
        self.error = None

    def add(self, block):
        self.total += len(block)
        self.data.extend(block[:max(0, OUTPUT_LIMIT - len(self.data))])

    def read(self, stream, lines=None):
        sent = False
        try:
            if lines is not None:
                first = stream.readline(OUTPUT_LIMIT + 1)
                self.add(first)
                lines.put(first)
                sent = True
            for block in iter(lambda: stream.read(4096), b""):
                self.add(block)
        except (OSError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if lines is not None and not sent:
                lines.put(None)

    def fields(self, name):
        return {name: self.data.decode("utf-8", errors="replace"),
                name + "_bytes": self.total, name + "_truncated": self.total > OUTPUT_LIMIT,
                name + "_capture_error": self.error}


def launch_worker(script: Path, directory: Path, *, point=None, window=2, timeout=60):
    nonnegative_int("window", window)
    if point is not None and point not in POINTS:
        raise ValueError("Unknown kill point")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Expected a positive finite worker timeout")
    command = [sys.executable, str(script), "--job-dir", str(directory)]
    if point:
        command += ["--pause-point", point, "--pause-window", str(window)]
    start = time.perf_counter()
    process, readers = None, []
    stdout, stderr = _OutputCapture(), _OutputCapture()
    lines = Queue()
    killed, cleanup_killed, failure = False, False, None
    stage = "spawn"
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        for capture, stream, queue in ((stdout, process.stdout, lines), (stderr, process.stderr, None)):
            reader = threading.Thread(target=capture.read, args=(stream, queue), daemon=True)
            reader.start()
            readers.append(reader)
        stage = "rendezvous"
        first = lines.get(timeout=timeout)
        if first is None:
            raise ValueError("Could not capture the worker rendezvous")
        if point:
            expected = {"event": "kill_ready", "point": point, "window_index": window}
            if len(first) > OUTPUT_LIMIT or canonical_bytes(read_json(first or b"null")) != canonical_bytes(expected):
                raise ValueError("Worker failed before kill boundary: invalid rendezvous marker")
            process.kill()
            killed = True
        stage = "completion"
        process.wait(timeout=timeout)
        if not killed and process.returncode != 0:
            failure = ("child_exit", "Worker failed with a non-zero exit code")
        if killed and process.returncode == 0:
            failure = ("termination_error", "Terminated worker unexpectedly succeeded")
    except (Empty, subprocess.TimeoutExpired):
        failure = ("timeout", f"Worker exceeded its bounded {stage} wait")
    except ValueError as exc:
        failure = ("protocol_error", str(exc))
    except Exception as exc:
        failure = ("spawn_error" if process is None else "process_control_error", f"{type(exc).__name__}: {exc}")
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                    cleanup_killed = True
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    failure = ("cleanup_error", f"Could not reap worker: {exc}")
            for reader in readers:
                reader.join(timeout=1)
            process.stdin.close()
            # The fixed worker has no descendants. If an unexpected descendant
            # retains a pipe, avoid blocking on closing a reader-held stream.
            for index, stream in enumerate((process.stdout, process.stderr)):
                if index >= len(readers) or not readers[index].is_alive():
                    stream.close()
    incomplete = any(reader.is_alive() for reader in readers)
    if not failure and (incomplete or stdout.error or stderr.error):
        failure = ("capture_error", "Worker output capture did not complete")
    record = {"termination": "parent_kill" if killed else ("cleanup_kill" if cleanup_killed else
              ("spawn_error" if process is None else "normal")),
              "returncode": process.returncode if process is not None else None,
              "requested_point": point, "window_index": window if point else None,
              **stdout.fields("stdout"), **stderr.fields("stderr"), "output_incomplete": incomplete,
              "process_wall_ms": (time.perf_counter() - start) * 1000,
              "failure_category": failure[0] if failure else None,
              "error": failure[1] if failure else None}
    if failure:
        error_type = WorkerTimeoutError if failure[0] == "timeout" else WorkerLaunchError
        raise error_type(f"{failure[1]}: {record['stderr']}", record)
    return record


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
        launch_error = None
        try:
            row = launch_worker(script, directory, point=point if killing else None,
                                window=fault_window, timeout=timeout)
        except WorkerLaunchError as exc:
            row, launch_error = exc.record, exc
        row.update(job_id=directory.name, launch_number=len(launches) + 1)
        try:
            row["cursor"] = inspect_job(directory)["cursor"]
            row["frontier_error"] = None
        except Exception as exc:
            row.update(cursor=None, frontier_error=f"{type(exc).__name__}: {exc}")
            if launch_error is None:
                launch_error = exc
        launches.append(row)
        (directory / "supervisor.json").write_text(json.dumps(launches, indent=2, allow_nan=False) + "\n",
                                                   encoding="utf-8")
        if launch_error is not None:
            raise launch_error
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
