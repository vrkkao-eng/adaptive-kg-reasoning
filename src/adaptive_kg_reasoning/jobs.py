"""Single-host job control with durable intent and kernel-owned execution slots."""
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from .checkpoint import canonical_bytes
from .job_registry import Registry, ServiceError, STATES
from .job_status import progress
from .process_resume import inspect_job, prepare_job, ResumeError
from .resume_experiment import _OutputCapture
from .worker_ownership import FileLock, OwnershipBusy, kill_owned_process

MAX_JOBS = 64
MAX_LAUNCHES = 3
DEFAULT_SPEC = {"fixture": "demo", "width": 30, "slide": 10, "threshold": 450.0,
                "flush": True, "workload": "dense", "node": "fog"}


def spec_request(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SPEC):
        raise ServiceError("invalid_request", 422)
    spec = {**DEFAULT_SPEC, **value}
    if (spec["fixture"] != "demo" or spec["workload"] not in ("none", "sparse", "dense", "bursty")
            or spec["node"] not in ("edge", "fog", "cloud") or type(spec["flush"]) is not bool
            or type(spec["width"]) is not int or not 5 <= spec["width"] <= 120
            or type(spec["slide"]) is not int or not 1 <= spec["slide"] <= min(60, spec["width"])
            or type(spec["threshold"]) not in (int, float) or not math.isfinite(spec["threshold"])
            or abs(spec["threshold"]) > 100000):
        raise ServiceError("invalid_request", 422)
    spec["threshold"] = float(spec["threshold"])
    return spec


class JobService:
    def __init__(self, root, *, fixtures=None, worker_pause=None, boundary=None, timeout=60):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.service_lock = FileLock(self.root / "service.lock").acquire()
        self.mutex = threading.RLock()
        self.children = {}
        self.stop = threading.Event()
        self.timeout = timeout
        self.worker_pause = worker_pause  # Trusted test harness only; never request fields.
        self.boundary = boundary or (lambda phase, job_id: None)
        assets = Path(__file__).parent / "fixtures"
        self.fixtures = fixtures or {"demo": (assets / "demo.csv", assets / "profiles.json")}
        try:
            if sqlite_version_old():
                raise ServiceError("unsupported_runtime", 503)
            self.registry = Registry(self.root, initialize=True)
            (self.root / "jobs").mkdir(exist_ok=True)
            self.reconcile()
            self.monitor = threading.Thread(target=self._monitor, daemon=True)
            self.monitor.start()
        except BaseException:
            self.service_lock.close()
            raise

    def directory(self, job_id):
        self.registry.get(job_id)  # Validate UUID and membership before constructing paths.
        return self.root / "jobs" / job_id

    def _locks(self, directory=None):
        stack = ExitStack()
        try:
            stack.enter_context(FileLock(self.root / "execution.lock"))
            if directory is not None:
                stack.enter_context(FileLock(directory / "worker.lock"))
            return stack
        except BaseException:
            stack.close()
            raise

    def submit(self, request, key, request_id):
        spec = spec_request(request)
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) is None:
            raise ServiceError("invalid_idempotency_key", 422)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        encoded = canonical_bytes(spec).decode()
        with self.mutex:
            with self.registry.database() as db:
                existing = db.execute("SELECT id,request_json FROM jobs WHERE key_sha256=?", (key_hash,)).fetchone()
                if existing:
                    if existing[1] != encoded:
                        raise ServiceError("idempotency_conflict")
                    return self.status(existing[0]), False
                if db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                    raise ServiceError("job_limit")
                if db.execute("SELECT 1 FROM jobs WHERE state IN ('starting','running')").fetchone():
                    raise ServiceError("execution_busy")
            try:
                with self._locks():
                    job_id = str(uuid.uuid4())
                    with self.registry.database(write=True) as db:
                        db.execute("INSERT INTO jobs VALUES (?,?,?,'preparing',NULL,0,NULL,NULL,?)",
                                   (job_id, key_hash, encoded, time.time()))
                        Registry.event(db, job_id, "submission_reserved", request_id=request_id)
                    self.boundary("after_reservation", job_id)
                    directory = self.root / "jobs" / job_id
                    source, profiles = self.fixtures[spec["fixture"]]
                    try:
                        job = prepare_job(directory, input_path=source, profiles_path=profiles,
                                          **{k: v for k, v in spec.items() if k != "fixture"})
                        metadata = {"identity": job.identity, "windows": len(job.windows),
                                    "queries": job.queries, "offsets": job.input_offsets}
                        with self.registry.database(write=True) as db:
                            db.execute("UPDATE jobs SET state='created',metadata_json=? WHERE id=?",
                                       (canonical_bytes(metadata).decode(), job_id))
                            Registry.event(db, job_id, "job_prepared", request_id=request_id)
                    except Exception:
                        with self.registry.database(write=True) as db:
                            db.execute("UPDATE jobs SET state='failed',failure='preparation_failed' WHERE id=?", (job_id,))
                            Registry.event(db, job_id, "preparation_failed", request_id=request_id)
                        raise ServiceError("preparation_failed", 503)
                self.boundary("after_preparation", job_id)
                self._start(job_id, request_id)
                return self.status(job_id), True
            except OwnershipBusy as exc:
                raise ServiceError("execution_busy") from exc

    def _start(self, job_id, request_id):
        row = self.registry.get(job_id)
        if row["state"] in ("starting", "running", "completed"):
            return
        if row["state"] not in ("created", "interrupted"):
            raise ServiceError("terminal_failure")
        directory = self.directory(job_id)
        try:
            with self._locks(directory):
                if any(r["state"] in ("starting", "running") for r in self.registry.list()):
                    raise ServiceError("execution_busy")
                try:
                    checked = inspect_job(directory)
                except Exception as exc:
                    with self.registry.database(write=True) as db:
                        db.execute("UPDATE jobs SET state='failed',failure='integrity_failure' WHERE id=?", (job_id,))
                        Registry.event(db, job_id, "validation_failed", request_id=request_id, failure="integrity_failure")
                    raise ServiceError("integrity_failure", 409) from exc
                if checked["served_windows"] == checked["windows"]:
                    with self.registry.database(write=True) as db:
                        db.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_id,))
                    return
                if row["launches"] >= MAX_LAUNCHES:
                    raise ServiceError("restart_exhausted")
                token = str(uuid.uuid4())
                with self.registry.database(write=True) as db:
                    db.execute("UPDATE jobs SET state='starting',token=?,launches=launches+1,failure=NULL WHERE id=?", (token, job_id))
                    Registry.event(db, job_id, "start_requested", token=token, request_id=request_id, cursor=checked["cursor"])
                self.boundary("after_start_intent", job_id)
            # A delayed child must acquire the execution lock AND match this token.
            # Restart reconciliation can revoke a not-yet-owned token safely.
            command = [sys.executable, "-m", "adaptive_kg_reasoning.service_worker", "--root", str(self.root),
                       "--job-id", job_id, "--token", token, "--timeout", str(self.timeout)]
            if self.worker_pause:
                command += ["--test-pause-point", self.worker_pause[0], "--test-pause-window", str(self.worker_pause[1])]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).parent.parent)
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, env=env,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self.registry.finish(job_id, token, "interrupted", "spawn_error")
                raise ServiceError("spawn_error", 503) from exc
            captures = [_OutputCapture(), _OutputCapture()]
            readers = []
            for capture, stream in zip(captures, (process.stdout, process.stderr)):
                reader = threading.Thread(target=capture.read, args=(stream,), daemon=True)
                reader.start()
                readers.append(reader)
            self.children[job_id] = (process, token, captures, readers, time.monotonic())
            self.boundary("after_spawn", job_id)
        except OwnershipBusy as exc:
            raise ServiceError("execution_busy") from exc

    def resume(self, job_id, request_id):
        with self.mutex:
            self.reconcile()
            self._start(job_id, request_id)
            return self.status(job_id)

    def status(self, job_id):
        row = self.registry.get(job_id)
        current = progress(self.directory(job_id), row)
        current.pop("receipts")
        return {"id": job_id, "state": row["state"], "launches": row["launches"],
                "launch_limit": MAX_LAUNCHES, "failure_category": row["failure"], "progress": current}

    def receipts(self, job_id, after=-1, limit=100):
        if type(after) is not int or after < -1 or type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError("invalid_pagination", 422)
        row = self.registry.get(job_id)
        result = progress(self.directory(job_id), row, after=after, limit=limit)
        return {"items": result["receipts"], "snapshot_cursor": result["cursor"],
                "next_after": result["receipts"][-1]["window_index"] if result["receipts"] else after}

    def _snapshot(self, row, state, failure):
        if not row["token"]:
            return False
        destination = self.directory(row["id"]) / "evidence" / row["token"]
        if destination.exists():
            return (destination / "manifest.json").exists()
        try:
            from .service_worker import publish_snapshot
            publish_snapshot(self.registry, row["id"], row["token"], state, failure)
            return True
        except Exception:
            return False  # Preserve incomplete evidence; never overwrite it.

    def reconcile(self):
        with self.mutex:
            # Do not briefly take the slot while our new child is about to
            # acquire it: its non-blocking lock would otherwise fail spuriously.
            if any(child[0].poll() is None for child in self.children.values()):
                return
            try:
                with self._locks():
                    for row in self.registry.list():
                        if row["state"] not in ("preparing", "created", "starting", "running"):
                            continue
                        child = self.children.get(row["id"])
                        if child and child[0].poll() is None:
                            continue  # A locally known child may not yet own its lock.
                        if row["state"] == "created":
                            continue  # Explicit resume starts prepared-but-unlaunched jobs.
                        directory = self.root / "jobs" / row["id"]
                        state, failure = "failed", "preparation_interrupted"
                        if row["metadata_json"] is not None:
                            try:
                                with FileLock(directory / "worker.lock"):
                                    checked = inspect_job(directory)
                                state = "completed" if checked["served_windows"] == checked["windows"] else "interrupted"
                                failure = None if state == "completed" else "child_exit"
                            except OwnershipBusy:
                                continue
                            except Exception:
                                failure = "integrity_failure"
                        exported = self._snapshot(row, state, failure) if row["metadata_json"] is not None else False
                        with self.registry.database(write=True) as db:
                            db.execute("UPDATE jobs SET state=?,failure=?,token=NULL WHERE id=?", (state, failure, row["id"]))
                            Registry.event(db, row["id"], "reconciled", token=row["token"], outcome=state,
                                           failure=failure, evidence_complete=exported)
            except OwnershipBusy:
                pass  # A live orphan remains the sole execution-slot owner.

    def _monitor(self):
        while not self.stop.wait(0.1):
            try:
                self.reap()
            except Exception:
                # Ready/status expose storage errors; a monitor never repairs drift.
                self.stop.set()

    def reap(self):
        with self.mutex:
            for job_id, (process, token, captures, readers, started) in list(self.children.items()):
                if process.poll() is None and time.monotonic() - started > self.timeout:
                    kill_owned_process(process)
                    # Reconcile a killed transaction before publishing timeout.
                    # A read-only status connection cannot recover a hot journal.
                    with self._locks():
                        try:
                            with FileLock(self.directory(job_id) / "worker.lock"):
                                inspect_job(self.directory(job_id))
                            state, failure = "interrupted", "timeout"
                        except Exception:
                            state, failure = "failed", "integrity_failure"
                        exported = self._snapshot(self.registry.get(job_id), state, failure)
                        self.registry.finish(job_id, token, state, failure, evidence_complete=exported)
                if process.poll() is None:
                    continue
                for reader in readers:
                    reader.join(timeout=1)
                record = {"job_id": job_id, "attempt_id": token, "returncode": process.returncode,
                          **captures[0].fields("stdout"), **captures[1].fields("stderr")}
                output = self.directory(job_id) / "launches"
                output.mkdir(exist_ok=True)
                with (output / (token + ".json")).open("xb") as stream:
                    stream.write(canonical_bytes(record) + b"\n")
                process.stdout.close()
                process.stderr.close()
                del self.children[job_id]
            self.reconcile()

    def ready(self):
        if self.stop.is_set():
            raise ServiceError("monitor_unavailable", 503)
        with self.registry.database() as db:
            db.execute("SELECT count(*) FROM jobs").fetchone()
        return {"status": "ready", "single_host": True, "execution_slots": 1}

    def metrics(self):
        rows = self.registry.list()
        lines = ["# TYPE kg_jobs gauge"]
        for state in STATES:
            lines.append(f'kg_jobs{{state="{state}"}} {sum(r["state"] == state for r in rows)}')
        lines += ["# TYPE kg_launch_intents_total counter", f'kg_launch_intents_total {sum(r["launches"] for r in rows)}']
        views = [progress(self.directory(r["id"]), r) for r in rows]
        for field in ("committed_windows", "committed_queries"):
            lines += [f"# TYPE kg_{field} gauge", f"kg_{field} {sum(v.get(field, 0) for v in views)}"]
        return "\n".join(lines) + "\n"

    def close(self):
        self.stop.set()
        self.monitor.join(timeout=2)
        with self.mutex:
            for process, *_ in self.children.values():
                if process.poll() is None:
                    kill_owned_process(process)
            try:
                self.reap()
            finally:
                self.service_lock.close()


def sqlite_version_old():
    import sqlite3
    return sqlite3.sqlite_version_info < (3, 37, 0)
