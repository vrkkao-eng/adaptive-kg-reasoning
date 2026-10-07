"""Real worker lifecycle, ownership, progress and immutable evidence tests."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.checkpoint import canonical_bytes
from adaptive_kg_reasoning.evidence import sha256
from adaptive_kg_reasoning.job_registry import Registry, ServiceError
from adaptive_kg_reasoning.jobs import JobService, spec_request, MAX_LAUNCHES
from adaptive_kg_reasoning.process_resume import inspect_job, run_worker, load_job
from adaptive_kg_reasoning.worker_ownership import FileLock, OwnershipBusy, kill_owned_process


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Bounded test condition did not occur")


def terminal(service, job_id):
    def result():
        status = service.status(job_id)
        return status if status["state"] in ("completed", "interrupted", "failed") else None
    return wait_for(result)


def paused(service, job_id):
    token = service.registry.get(job_id)["token"]
    wait_for(lambda: (service.directory(job_id) / "testing" / (token + ".ready")).exists())
    return token


def kill(service, job_id):
    child = service.children[job_id][0]
    kill_owned_process(child)
    wait_for(lambda: service.status(job_id)["state"] == "interrupted")


def assert_snapshot(path):
    manifest = json.loads((path / "manifest.json").read_text())
    for name, checksum in manifest["artifacts_sha256"].items():
        assert sha256(path / name) == checksum
    return manifest


def test_durable_idempotency_completion_and_snapshot(tmp_path):
    with_service = JobService(tmp_path)
    try:
        row, new = with_service.submit({}, "same-key", "request-1")
        assert new
        final = terminal(with_service, row["id"])
        assert final["state"] == "completed" and final["progress"]["committed_complete"]
        duplicate, new = with_service.submit({"threshold": 450}, "same-key", "request-2")
        assert not new and duplicate["id"] == row["id"] and duplicate["launches"] == 1
        with pytest.raises(ServiceError, match="idempotency_conflict"):
            with_service.submit({"threshold": 451}, "same-key", "request-3")
        db = with_service.directory(row["id"]) / "progress.sqlite"
        before = sha256(db)
        assert with_service.resume(row["id"], "request-4")["launches"] == 1
        assert sha256(db) == before
        snapshots = list((db.parent / "evidence").iterdir())
        assert len(snapshots) == 1
        assert_snapshot(snapshots[0])
    finally:
        with_service.close()
    restarted = JobService(tmp_path)
    try:
        replay, new = restarted.submit({}, "same-key", "request-5")
        assert not new and replay["id"] == row["id"] and replay["state"] == "completed"
    finally:
        restarted.close()


@pytest.mark.parametrize("point", ["before_update", "after_update", "before_commit", "after_commit"])
def test_real_kill_resume_preserves_exact_prefix(tmp_path, point):
    service = JobService(tmp_path, worker_pause=(point, 2))
    try:
        row, _ = service.submit({}, "kill", "request")
        paused(service, row["id"])
        # The worker itself, not merely the HTTP controller, holds job ownership.
        before = sha256(service.directory(row["id"]) / "progress.sqlite")
        with pytest.raises(OwnershipBusy):
            run_worker(service.directory(row["id"]))
        assert sha256(service.directory(row["id"]) / "progress.sqlite") == before
        duplicate = service.resume(row["id"], "request-duplicate")
        assert duplicate["launches"] == 1
        with pytest.raises(ServiceError, match="execution_busy"):
            service.submit({}, "another-key", "request")
        kill(service, row["id"])
        expected = 2 if point == "after_commit" else 1
        assert service.status(row["id"])["progress"]["cursor"] == expected
        prefix = next((service.directory(row["id"]) / "evidence").iterdir())
        prefix_hash = sha256(prefix / "manifest.json")
        assert assert_snapshot(prefix)["validated_cursor"] == expected
        service.worker_pause = None
        service.resume(row["id"], "resume-request")
        final = terminal(service, row["id"])
        assert final["state"] == "completed" and final["launches"] == 2
        checked = inspect_job(service.directory(row["id"]))
        assert [r["window_index"] for r in checked["receipts"]] == list(range(checked["windows"]))
        assert checked["attempts"] == 2 and checked["served_queries"] == checked["requested_queries"]
        snapshots = list((service.directory(row["id"]) / "evidence").iterdir())
        assert len(snapshots) == 2
        assert max(assert_snapshot(p)["validated_cursor"] for p in snapshots) == checked["cursor"]
        assert sha256(prefix / "manifest.json") == prefix_hash
    finally:
        service.close()


def test_duplicate_requests_are_serialized_without_duplicate_launches(tmp_path):
    service = JobService(tmp_path, worker_pause=("before_update", 0))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: service.submit({}, "one-key", f"request-{n}"), range(8)))
        ids = {r[0]["id"] for r in results}
        assert len(ids) == 1 and sum(r[1] for r in results) == 1
        job_id = ids.pop()
        paused(service, job_id)
        assert service.registry.get(job_id)["launches"] == 1
    finally:
        service.close()


def test_receipt_status_reads_do_not_rebuild_oracle(tmp_path, monkeypatch):
    service = JobService(tmp_path, worker_pause=("after_commit", 2))
    try:
        row, _ = service.submit({}, "reads", "request")
        paused(service, row["id"])
        import adaptive_kg_reasoning.process_resume as module
        def forbidden(*args, **kwargs):
            raise AssertionError("Operational reads must not load the whole trace")
        monkeypatch.setattr(module, "load_job", forbidden)
        assert service.status(row["id"])["progress"]["cursor"] == 2
        page = service.receipts(row["id"], limit=2)
        assert [r["window_index"] for r in page["items"]] == [0, 1]
        assert service.receipts(row["id"], after=page["next_after"], limit=2)["items"][0]["window_index"] == 2
        assert "kg_committed_queries 24" in service.metrics()
    finally:
        service.close()


def test_bounded_attempts_and_terminal_corruption(tmp_path):
    service = JobService(tmp_path, worker_pause=("before_commit", 2))
    try:
        row, _ = service.submit({}, "bounded", "request")
        for attempt in range(MAX_LAUNCHES):
            paused(service, row["id"])
            kill(service, row["id"])
            if attempt < MAX_LAUNCHES - 1:
                service.resume(row["id"], "resume")
        with pytest.raises(ServiceError, match="restart_exhausted"):
            service.resume(row["id"], "resume")
        db_path = service.directory(row["id"]) / "progress.sqlite"
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE progress SET identity='changed'")
        before = sha256(db_path)
        with pytest.raises(ServiceError, match="integrity_failure"):
            service.resume(row["id"], "resume")
        assert service.registry.get(row["id"])["state"] == "failed"
        assert sha256(db_path) == before
    finally:
        service.close()


def test_capacity_failure_is_terminal_and_snapshot_prefix_valid(tmp_path):
    source = ROOT / "src/adaptive_kg_reasoning/fixtures/demo.csv"
    profile = json.loads((ROOT / "src/adaptive_kg_reasoning/fixtures/profiles.json").read_text())
    for node in profile["nodes"]:
        node["memory_budget_bytes"] = 0
    config = tmp_path / "capacity.json"
    config.write_bytes(canonical_bytes(profile))
    service = JobService(tmp_path / "root", fixtures={"demo": (source, config)})
    try:
        row, _ = service.submit({}, "capacity", "request")
        final = terminal(service, row["id"])
        assert final["state"] == "failed" and final["failure_category"] == "capacity_admission"
        assert final["progress"]["committed_queries"] == 0
        with pytest.raises(ServiceError, match="terminal_failure"):
            service.resume(row["id"], "request")
        snapshot = next((service.directory(row["id"]) / "evidence").iterdir())
        assert assert_snapshot(snapshot)["validated_cursor"] == -1
    finally:
        service.close()


def test_second_service_and_runtime_drift_fail_closed(tmp_path):
    service = JobService(tmp_path)
    with pytest.raises(OwnershipBusy):
        JobService(tmp_path)
    service.close()
    with sqlite3.connect(tmp_path / "registry.sqlite") as db:
        db.execute("UPDATE meta SET runtime='{}'")
    with pytest.raises(ServiceError, match="runtime_mismatch"):
        JobService(tmp_path)
    # Failed initialization released the service lifetime lock.
    with FileLock(tmp_path / "service.lock"):
        pass


def test_timeout_prefix_is_immutable_across_explicit_resume(tmp_path):
    service = JobService(tmp_path, worker_pause=("before_commit", 2))
    try:
        row, _ = service.submit({}, "timeout", "request")
        paused(service, row["id"])
        service.timeout = .1  # Expire an owned child at a known transaction boundary.
        final = terminal(service, row["id"])
        assert final["state"] == "interrupted" and final["failure_category"] == "timeout"
        snapshot = next((service.directory(row["id"]) / "evidence").iterdir())
        assert assert_snapshot(snapshot)["validated_cursor"] == 1
        checksum = sha256(snapshot / "manifest.json")
        service.worker_pause = None
        service.timeout = 60
        service.resume(row["id"], "resume")
        assert terminal(service, row["id"])["state"] == "completed"
        assert sha256(snapshot / "manifest.json") == checksum
    finally:
        service.close()


def test_reconciliation_does_not_compete_with_known_live_child(tmp_path, monkeypatch):
    service = JobService(tmp_path, worker_pause=("before_commit", 2))
    try:
        row, _ = service.submit({}, "reconcile", "request")
        paused(service, row["id"])
        def forbidden(*args, **kwargs):
            raise AssertionError("Controller must not claim a live child's slot")
        with monkeypatch.context() as patch:
            patch.setattr(service, "_locks", forbidden)
            service.reconcile()
            assert service.ready()["status"] == "ready"
        kill(service, row["id"])
    finally:
        service.close()


def test_schema_drift_and_key_limits(tmp_path, monkeypatch):
    import adaptive_kg_reasoning.jobs as module
    service = JobService(tmp_path)
    try:
        for key in ("", "space key", "x" * 65, "../path"):
            with pytest.raises(ServiceError, match="invalid_idempotency_key"):
                service.submit({}, key, "request")
        monkeypatch.setattr(module, "MAX_JOBS", 0)
        with pytest.raises(ServiceError, match="job_limit"):
            service.submit({}, "valid", "request")
    finally:
        service.close()
    with sqlite3.connect(tmp_path / "registry.sqlite") as db:
        db.execute("CREATE TABLE unexpected (value TEXT)")
    with pytest.raises(ServiceError, match="registry_invalid"):
        JobService(tmp_path)


def test_failed_spawn_consumes_intent_and_stale_token_cannot_write(tmp_path, monkeypatch):
    import os
    import adaptive_kg_reasoning.jobs as module
    service = JobService(tmp_path)
    real_popen = module.subprocess.Popen
    def failed_spawn(*args, **kwargs):
        raise OSError("Operator-only error must not become an API payload")
    try:
        monkeypatch.setattr(module.subprocess, "Popen", failed_spawn)
        with pytest.raises(ServiceError, match="spawn_error"):
            service.submit({}, "spawn", "request")
        row = service.registry.list()[0]
        assert row["state"] == "interrupted" and row["launches"] == 1
        old_token = row["token"]
        with service.registry.database(write=True) as db:
            db.execute("UPDATE jobs SET token=NULL WHERE id=?", (row["id"],))
        directory = service.directory(row["id"])
        checksum = sha256(directory / "progress.sqlite")
        monkeypatch.setattr(module.subprocess, "Popen", real_popen)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        stale = subprocess.run([sys.executable, "-m", "adaptive_kg_reasoning.service_worker",
                                "--root", str(tmp_path), "--job-id", row["id"], "--token", old_token],
                               env=env, capture_output=True, timeout=10)
        assert stale.returncode != 0 and b"stale_attempt" in stale.stderr
        assert sha256(directory / "progress.sqlite") == checksum
        service.resume(row["id"], "resume")
        assert terminal(service, row["id"])["launches"] == 2
    finally:
        service.close()


@pytest.mark.parametrize("spec", [
    {"width": True}, {"slide": 1.5}, {"width": 4}, {"slide": 121}, {"width": 5, "slide": 6},
    {"threshold": float("nan")}, {"threshold": True}, {"flush": 1}, {"fixture": "../demo"},
    {"node": "shell"}, {"script": "evil.py"}, {"workload": "unknown"},
])
def test_invalid_spec(spec):
    with pytest.raises(ServiceError):
        spec_request(spec)
