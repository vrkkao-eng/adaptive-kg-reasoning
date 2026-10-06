import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.checkpoint import canonical_bytes
from adaptive_kg_reasoning.evidence import sha256
from adaptive_kg_reasoning.process_resume import (ResumeError, ResumeStore, digest, inspect_job,
                                                 load_job, prepare_job, run_worker)
from adaptive_kg_reasoning.resume_experiment import execute_case, launch_worker

WORKER = ROOT / "experiments/resume_worker.py"


def fixture_job(tmp_path, workload="dense", budget=None, flush=True):
    source = tmp_path / "source.csv"
    source.write_text("id,timestamp,value,property,plug_id,household_id,house_id\n"
                      "0,0,449,1,1,1,1\n1,5,451,1,1,1,1\n2,10,800,1,1,1,1\n"
                      "3,15,100,1,1,1,1\n4,20,900,1,2,1,1\n", encoding="utf-8")
    profiles = ROOT / "configs/placement_profiles.json"
    if budget is not None:
        data = json.loads(profiles.read_text())
        for node in data["nodes"]:
            node["memory_budget_bytes"] = budget
        profiles = tmp_path / "profiles.json"
        profiles.write_text(json.dumps(data))
    return prepare_job(tmp_path / "job", input_path=source, profiles_path=profiles,
                       width=10, slide=5, workload=workload, flush=flush)


@pytest.mark.parametrize("point", ["before_update", "after_update", "before_commit", "after_commit"])
@pytest.mark.parametrize("fault_window", [0, 2, 5])
def test_real_process_kill_and_new_process_resume(tmp_path, point, fault_window):
    job = fixture_job(tmp_path)
    assert len(job.windows) == 6
    result = execute_case(WORKER, job.directory, point=point, fault_window=fault_window)
    assert result["status"] == "resumed" and result["acceptance"] == "passed"
    assert result["served_windows"] == 6 and result["served_queries"] == 48
    assert result["input_cursor"] == 5
    assert result["process_launches"] == 2 and result["process_kills"] == 1
    transcript = json.loads((job.directory / "supervisor.json").read_text())
    assert transcript[0]["termination"] == "parent_kill" and transcript[0]["returncode"] != 0
    assert transcript[0]["cursor"] == fault_window - int(point != "after_commit")
    assert transcript[1]["returncode"] == 0
    receipts = inspect_job(job.directory)["receipts"]
    assert [row["window_index"] for row in receipts] == list(range(6))


@pytest.mark.parametrize("point", ["before_update", "after_update", "before_commit", "after_commit"])
def test_stopped_process_exposes_only_its_committed_prefix(tmp_path, point):
    job = fixture_job(tmp_path)
    row = execute_case(WORKER, job.directory, point=point, policy="stop", fault_window=2)
    prefix = 3 if point == "after_commit" else 2
    assert row["status"] == "interrupted" and row["acceptance"] == "passed"
    assert row["served_windows"] == prefix and row["served_queries"] == 8 * prefix
    assert row["unserved_queries"] == 48 - 8 * prefix
    # Manual fresh-process continuation works even when the previous supervisor stopped.
    assert launch_worker(WORKER, job.directory)["returncode"] == 0
    assert inspect_job(job.directory)["served_windows"] == 6


@pytest.mark.parametrize("restarts", [0, 1, 2])
def test_persistent_kills_are_bounded_and_preserve_a_prefix(tmp_path, restarts):
    job = fixture_job(tmp_path)
    result = execute_case(WORKER, job.directory, point="before_commit", persistent=True,
                          max_restarts=restarts)
    assert result["status"] == "restart_exhausted" and result["acceptance"] == "passed"
    assert result["process_launches"] == result["process_kills"] == restarts + 1
    assert result["served_windows"] == 2
    assert inspect_job(job.directory)["attempts"] == restarts + 1


@pytest.mark.parametrize("workload", ["none", "sparse", "dense", "bursty"])
def test_committed_completion_is_an_idempotent_no_op(tmp_path, workload):
    job = fixture_job(tmp_path, workload=workload)
    assert run_worker(job.directory)["status"] == "completed"
    before = sha256(job.directory / "progress.sqlite")
    assert launch_worker(WORKER, job.directory)["returncode"] == 0
    assert sha256(job.directory / "progress.sqlite") == before
    result = inspect_job(job.directory)
    assert result["served_queries"] == sum(job.queries)
    assert result["attempts"] == 1


def test_exception_before_commit_rolls_back_state_receipt_and_audit(tmp_path):
    job = fixture_job(tmp_path)
    def fail(point, index):
        if point == "before_commit" and index == 2:
            raise OSError("injected transaction failure")
    with pytest.raises(OSError, match="transaction failure"):
        run_worker(job.directory, hook=fail)
    result = inspect_job(job.directory)
    assert result["cursor"] == 1 and len(result["receipts"]) == 2
    assert [row["window_index"] for row in result["audit"] if row["event"] == "window_committed"] == [0, 1]
    assert run_worker(job.directory)["status"] == "completed"


def test_resource_failure_is_not_repaired_by_resume(tmp_path):
    job = fixture_job(tmp_path, budget=0)
    for _ in range(2):
        with pytest.raises(ResumeError, match="memory_budget_exceeded"):
            run_worker(job.directory)
    result = inspect_job(job.directory)
    assert result["cursor"] == -1 and not result["receipts"]


@pytest.mark.parametrize("mutation", ["identity", "input_cursor", "served_cursor", "applied_cursor",
    "state_checksum", "state_cursor", "state_total", "state_fact", "state_threshold", "receipt_queries",
    "receipt_digest", "receipt_delete", "audit_delete", "audit_order", "audit_bool", "audit_extra", "schema"])
def test_corrupt_or_inconsistent_progress_is_rejected_without_new_commits(tmp_path, mutation):
    job = fixture_job(tmp_path)
    def stop(point, index):
        if point == "after_commit" and index == 2:
            raise RuntimeError("stop fixture")
    with pytest.raises(RuntimeError):
        run_worker(job.directory, hook=stop)
    database = job.directory / "progress.sqlite"
    with sqlite3.connect(database) as db:
        if mutation == "identity":
            db.execute("UPDATE progress SET identity='wrong'")
        elif mutation in ("input_cursor", "served_cursor", "applied_cursor"):
            db.execute(f"UPDATE progress SET {mutation}={mutation}+1")
        elif mutation.startswith("state_"):
            payload = json.loads(db.execute("SELECT state_json FROM progress").fetchone()[0])
            if mutation == "state_checksum":
                db.execute("UPDATE progress SET state_sha256='wrong'")
            else:
                if mutation == "state_cursor":
                    payload["cursor"] = True
                elif mutation == "state_total":
                    payload["aggregates"][0]["total_load"] += 10
                elif mutation == "state_fact":
                    payload["facts"] = []
                else:
                    payload["threshold_watts"] += 1
                db.execute("UPDATE progress SET state_json=?, state_sha256=?", (canonical_bytes(payload).decode(), digest(payload)))
        elif mutation == "receipt_queries":
            db.execute("UPDATE receipts SET queries=queries+1 WHERE window_index=0")
        elif mutation == "receipt_digest":
            db.execute("UPDATE receipts SET answer_sha256='wrong' WHERE window_index=0")
        elif mutation == "receipt_delete":
            db.execute("DELETE FROM receipts WHERE window_index=0")
        elif mutation == "audit_delete":
            db.execute("DELETE FROM audit WHERE sequence=1")
        elif mutation == "schema":
            db.execute("PRAGMA user_version=99")
        else:
            event = json.loads(db.execute("SELECT payload FROM audit WHERE sequence=1").fetchone()[0])
            if mutation == "audit_order":
                event["window_index"] = 1
            elif mutation == "audit_bool":
                event["window_index"] = False
            else:
                event["unknown"] = 0
            db.execute("UPDATE audit SET payload=? WHERE sequence=1", (canonical_bytes(event).decode(),))
    before = sha256(database)
    with pytest.raises((ValueError, AssertionError, sqlite3.DatabaseError)):
        run_worker(job.directory)
    assert sha256(database) == before


@pytest.mark.parametrize("name", ["input.csv", "profiles.json", "job.json"])
def test_changed_input_or_configuration_rejects_resume(tmp_path, name):
    job = fixture_job(tmp_path)
    run_worker(job.directory)
    path = job.directory / name
    if name == "job.json":
        data = json.loads(path.read_text())
        data["threshold"] += 1
        path.write_text(json.dumps(data))
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ResumeError, match="identity"):
        run_worker(job.directory)


def test_engine_drift_and_duplicate_config_keys_are_rejected(tmp_path, monkeypatch):
    job = fixture_job(tmp_path)
    import adaptive_kg_reasoning.process_resume as module
    monkeypatch.setattr(module, "engine_hashes", lambda: {})
    with pytest.raises(ResumeError, match="engine source mismatch"):
        load_job(job.directory)
    path = job.directory / "job.json"
    path.write_text('{"width":10,"width":10}')
    with pytest.raises(ResumeError, match="Duplicate"):
        load_job(job.directory)


def test_missing_database_is_not_silently_created(tmp_path):
    job = fixture_job(tmp_path)
    (job.directory / "progress.sqlite").rename(job.directory / "retained.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        run_worker(job.directory)
    assert not (job.directory / "progress.sqlite").exists()


def test_stale_writer_is_rejected(tmp_path):
    job = fixture_job(tmp_path)
    stale = ResumeStore(job)
    try:
        state, _, _, _, _ = stale.load()
        run_worker(job.directory)
        state.apply(job.windows[0])
        with pytest.raises(ResumeError, match="Stale"):
            stale.commit_window(state, 0, lambda point, index: None, checked_queries=job.queries[0])
    finally:
        stale.close()
    assert inspect_job(job.directory)["served_windows"] == len(job.windows)


def test_prepare_refuses_existing_job_directory(tmp_path):
    job = fixture_job(tmp_path)
    before = sha256(job.directory / "progress.sqlite")
    with pytest.raises(FileExistsError):
        prepare_job(job.directory, input_path=job.directory / "input.csv",
                    profiles_path=job.directory / "profiles.json")
    assert sha256(job.directory / "progress.sqlite") == before


@pytest.mark.parametrize("checked", [0, 7, True, 8.0, None])
def test_receipt_requires_actual_complete_typed_query_checks(tmp_path, checked):
    job = fixture_job(tmp_path)
    store = ResumeStore(job)
    try:
        state, _, _, _, _ = store.load()
        store.start(-1, 1)
        state.apply(job.windows[0])
        with pytest.raises(ResumeError, match="checked-query"):
            store.commit_window(state, 0, lambda point, index: None, checked_queries=checked)
    finally:
        store.close()
    assert inspect_job(job.directory)["cursor"] == -1


@pytest.mark.parametrize("mutation", ["extra_table", "extra_column", "trigger", "non_strict"])
def test_unexpected_sqlite_schema_is_rejected(tmp_path, mutation):
    job = fixture_job(tmp_path)
    with sqlite3.connect(job.directory / "progress.sqlite") as db:
        if mutation == "extra_table":
            db.execute("CREATE TABLE unexpected (value TEXT)")
        elif mutation == "extra_column":
            db.execute("ALTER TABLE receipts ADD COLUMN extra TEXT")
        elif mutation == "trigger":
            db.execute("CREATE TRIGGER unexpected AFTER INSERT ON audit BEGIN SELECT 1; END")
        else:
            db.execute("ALTER TABLE receipts RENAME TO previous")
            db.execute("CREATE TABLE receipts (window_index INTEGER PRIMARY KEY, input_cursor INTEGER NOT NULL, queries INTEGER NOT NULL, answer_sha256 TEXT NOT NULL)")
            db.execute("DROP TABLE previous")
    with pytest.raises(ResumeError, match="schema"):
        run_worker(job.directory)


def test_supervisor_timeout_kills_and_reaps_a_stuck_child(tmp_path, monkeypatch):
    import adaptive_kg_reasoning.resume_experiment as supervisor
    script = tmp_path / "stuck.py"
    script.write_text("import time\ntime.sleep(60)\n")
    original = supervisor.subprocess.Popen
    children = []
    def capture(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(supervisor.subprocess, "Popen", capture)
    with pytest.raises(TimeoutError, match="rendezvous"):
        launch_worker(script, tmp_path, timeout=0.2)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].returncode != 0


def test_after_commit_restart_has_hand_checked_audit_prefix(tmp_path):
    job = fixture_job(tmp_path)
    execute_case(WORKER, job.directory, point="after_commit", fault_window=0)
    audit = inspect_job(job.directory)["audit"]
    assert audit[0] == {"sequence": 0, "event": "process_started", "attempt": 1, "cursor": -1}
    assert audit[1]["event"] == "window_committed" and audit[1]["window_index"] == 0
    assert audit[2] == {"sequence": 2, "event": "process_started", "attempt": 2, "cursor": 0}
    assert [event["window_index"] for event in audit if event["event"] == "window_committed"] == list(range(6))


@pytest.mark.parametrize("config", [{"max_restarts": -1}, {"max_restarts": True},
    {"fault_window": True}, {"fault_window": 100}, {"point": "unknown"}, {"policy": "unknown"},
    {"point": "after_commit", "persistent": True}])
def test_invalid_supervisor_configuration_fails_before_start(tmp_path, config):
    job = fixture_job(tmp_path)
    with pytest.raises(ValueError):
        execute_case(WORKER, job.directory, point=config.get("point", "before_commit"),
                     **{key: value for key, value in config.items() if key != "point"})
    assert inspect_job(job.directory)["attempts"] == 0


def test_old_sqlite_fails_before_creating_a_job(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))
    with pytest.raises(ResumeError, match="SQLite 3.37"):
        fixture_job(tmp_path)
    assert not (tmp_path / "job").exists()
