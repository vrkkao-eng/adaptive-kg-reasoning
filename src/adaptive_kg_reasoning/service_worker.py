"""One trusted fixed-trace service attempt, independent of the HTTP process."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

from .checkpoint import canonical_bytes
from .evidence import sha256
from .job_registry import Registry, ServiceError, valid_id
from .job_status import progress
from .process_resume import inspect_job, run_worker, ResumeError, POINTS
from .worker_ownership import FileLock, OwnershipBusy


def publish_snapshot(registry, job_id, token, outcome, failure):
    directory = registry.root / "jobs" / job_id
    with FileLock(directory / "worker.lock"):
        checked = inspect_job(directory)
        row = registry.get(job_id)
        readout = progress(directory, row)
        db = sqlite3.connect((directory / "progress.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            state = db.execute("SELECT state_json FROM progress").fetchone()[0]
        finally:
            db.close()
        destination = directory / "evidence" / token
        destination.mkdir(parents=True, exist_ok=False)
        for name in ("input.csv", "profiles.json", "job.json"):
            shutil.copyfile(directory / name, destination / name)
        for name, value in (("receipts.json", checked["receipts"]), ("audit.json", checked["audit"]),
                            ("state.json", json.loads(state) if state else None),
                            ("summary.json", {"worker_outcome": outcome, "failure_category": failure, **readout})):
            (destination / name).write_bytes(canonical_bytes(value) + b"\n")
        manifest = {"contract": "fixed-trace-service-evidence-v1", "job_id": job_id, "attempt_id": token,
                    "identity": json.loads(row["metadata_json"])["identity"], "validated_cursor": checked["cursor"],
                    "artifacts_sha256": {p.name: sha256(p) for p in sorted(destination.iterdir()) if p.is_file()}}
        with registry.database() as db:
            manifest["runtime"] = json.loads(db.execute("SELECT runtime FROM meta").fetchone()[0])
        (destination / "manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
        return checked["cursor"]


def classify(exc):
    if isinstance(exc, OwnershipBusy):
        return "interrupted", "ownership_busy"
    if isinstance(exc, TimeoutError):
        return "interrupted", "timeout"
    if isinstance(exc, ResumeError) and "memory_budget_exceeded" in str(exc):
        return "failed", "capacity_admission"
    if isinstance(exc, (ResumeError, ValueError, AssertionError)):
        return "failed", "integrity_failure"
    if isinstance(exc, (sqlite3.DatabaseError, OSError)):
        return "failed", "storage_failure"
    return "failed", "worker_error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--test-pause-point", choices=POINTS)
    parser.add_argument("--test-pause-window", type=int, default=2)
    args = parser.parse_args()
    if not valid_id(args.job_id) or not valid_id(args.token) or not 0 < args.timeout <= 300:
        parser.error("Invalid trusted worker arguments")
    registry = Registry(args.root)
    directory = registry.root / "jobs" / args.job_id
    with FileLock(registry.root / "execution.lock"):
        with registry.database(write=True) as db:
            if db.execute("UPDATE jobs SET state='running' WHERE id=? AND token=? AND state='starting'",
                          (args.job_id, args.token)).rowcount != 1:
                raise ServiceError("stale_attempt")
            Registry.event(db, args.job_id, "worker_started", token=args.token)
        started = time.monotonic()
        window_started = started

        def hook(point, index):
            nonlocal window_started
            if time.monotonic() - started >= args.timeout:
                raise TimeoutError("Bounded job attempt expired")
            if point == "before_update":
                window_started = time.monotonic()
            if point == "after_commit":
                current = progress(directory, registry.get(args.job_id))
                registry.emit(args.job_id, args.token, "window_committed", window_index=index,
                              cursor=current["cursor"], committed_queries=current["committed_queries"],
                              checked_window_wall_ms=(time.monotonic() - window_started) * 1000)
            if point == args.test_pause_point and index == args.test_pause_window:
                control = directory / "testing"
                control.mkdir(exist_ok=True)
                (control / (args.token + ".ready")).write_bytes(canonical_bytes({"point": point, "window": index, "pid": os.getpid()}))
                # Test-only rendezvous survives controller death. No client can
                # specify this boundary, path or worker executable through API.
                while not (control / (args.token + ".continue")).exists():
                    if time.monotonic() - started >= args.timeout:
                        raise TimeoutError("Bounded test rendezvous expired")
                    time.sleep(0.02)

        outcome, failure = "completed", None
        try:
            run_worker(directory, hook=hook)
        except Exception as exc:
            outcome, failure = classify(exc)
        cursor = None
        try:
            cursor = publish_snapshot(registry, args.job_id, args.token, outcome, failure)
        except Exception as exc:
            if failure is None:
                outcome, failure = classify(exc)
        registry.finish(args.job_id, args.token, outcome, failure, cursor=cursor)
        result = {"event": "worker_result", "job_id": args.job_id, "attempt_id": args.token,
                  "outcome": outcome, "failure_category": failure, "cursor": cursor}
        try:
            print(json.dumps(result), flush=True)
        except BrokenPipeError:
            pass  # The durable result does not depend on a surviving HTTP parent.
        return 0 if outcome == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
