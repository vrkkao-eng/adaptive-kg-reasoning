"""v0.6.1 fail-closed schema, identity and failed-launch evidence regressions."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from test_process_resume import fixture_job, ROOT, WORKER
from adaptive_kg_reasoning.evidence import sha256
from adaptive_kg_reasoning.process_resume import (
    CONTRACT, ENGINE_FILES, ENGINE_FINGERPRINT, SCHEMA_SQL, ResumeError,
    engine_hashes, load_job, run_worker,
)
from adaptive_kg_reasoning.resume_experiment import (
    OUTPUT_LIMIT, WorkerLaunchError, WorkerTimeoutError, execute_case, launch_worker,
)


@pytest.mark.parametrize("table,old,new", [
    ("progress", "INTEGER PRIMARY KEY", "INTEGER"),
    ("receipts", "INTEGER PRIMARY KEY", "INTEGER"),
    ("audit", "INTEGER PRIMARY KEY", "INTEGER"),
    ("progress", "identity TEXT NOT NULL", "identity TEXT"),
    ("progress", "applied_cursor INTEGER NOT NULL", "applied_cursor INTEGER"),
    ("progress", "served_cursor INTEGER NOT NULL", "served_cursor INTEGER"),
    ("progress", "input_cursor INTEGER NOT NULL", "input_cursor INTEGER"),
    ("receipts", "input_cursor INTEGER NOT NULL", "input_cursor INTEGER"),
    ("receipts", "queries INTEGER NOT NULL", "queries INTEGER"),
    ("receipts", "answer_sha256 TEXT NOT NULL", "answer_sha256 TEXT"),
    ("audit", "payload TEXT NOT NULL", "payload TEXT"),
    ("progress", "CHECK(singleton=1)", ""),
    ("progress", "CHECK(singleton=1)", "CHECK(singleton>=1)"),
    ("progress", "CHECK(input_cursor>=0)", "CHECK(input_cursor>=-1)"),
    ("receipts", "CHECK(queries>=0)", ""),
    ("receipts", "CHECK(queries>=0)", "CHECK(queries>=-1)"),
    ("receipts", "queries INTEGER NOT NULL", "queries INTEGER NOT NULL DEFAULT 0"),
])
def test_altered_constraints_reject_resume_without_writes(tmp_path, table, old, new):
    job = fixture_job(tmp_path)
    database = job.directory / "progress.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(f"ALTER TABLE {table} RENAME TO previous")
        db.execute(SCHEMA_SQL[table].replace(old, new))
        db.execute(f"INSERT INTO {table} SELECT * FROM previous")
        db.execute("DROP TABLE previous")
    before = sha256(database)
    with pytest.raises(ResumeError, match="schema"):
        run_worker(job.directory)
    assert sha256(database) == before


def test_invalid_singleton_is_rejected_even_with_supported_ddl(tmp_path):
    job = fixture_job(tmp_path)
    database = job.directory / "progress.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute("UPDATE progress SET singleton=2")
    before = sha256(database)
    with pytest.raises(ResumeError, match="Progress identity"):
        run_worker(job.directory)
    assert sha256(database) == before


def test_schema_allows_only_case_and_whitespace_variations(tmp_path):
    job = fixture_job(tmp_path)
    with sqlite3.connect(job.directory / "progress.sqlite") as db:
        db.execute("ALTER TABLE receipts RENAME TO previous")
        db.execute(SCHEMA_SQL["receipts"].lower().replace(" ", "\n"))
        db.execute("DROP TABLE previous")
    assert run_worker(job.directory)["status"] == "completed"


def test_crlf_lf_engine_identity_equivalence_and_real_drift(tmp_path, monkeypatch):
    import adaptive_kg_reasoning.process_resume as module
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    lf.mkdir(); crlf.mkdir()
    for name in ENGINE_FILES:
        content = (ROOT / "src/adaptive_kg_reasoning" / name).read_bytes().replace(b"\r\n", b"\n")
        (lf / name).write_bytes(content)
        (crlf / name).write_bytes(content.replace(b"\n", b"\r\n"))
    hashes = engine_hashes
    assert hashes(lf) == hashes(crlf)
    assert sha256(lf / "process_resume.py") != sha256(crlf / "process_resume.py")
    monkeypatch.setattr(module, "engine_hashes", lambda: hashes(lf))
    job = fixture_job(tmp_path)
    assert job.config["engine_fingerprint"] == ENGINE_FINGERPRINT
    monkeypatch.setattr(module, "engine_hashes", lambda: hashes(crlf))
    assert load_job(job.directory).identity == job.identity
    # This real child uses the current checkout rather than the mocked root.
    assert launch_worker(WORKER, job.directory)["returncode"] == 0
    before = sha256(job.directory / "progress.sqlite")
    with (crlf / "incremental.py").open("ab") as stream:
        stream.write(b"\r\n# source drift\r\n")
    with pytest.raises(ResumeError, match="engine source mismatch"):
        run_worker(job.directory)
    assert sha256(job.directory / "progress.sqlite") == before


@pytest.mark.parametrize("mutation", ["old_contract", "unknown_fingerprint", "missing_fingerprint"])
def test_unknown_identity_contract_is_not_migrated(tmp_path, mutation):
    job = fixture_job(tmp_path)
    path = job.directory / "job.json"
    config = json.loads(path.read_text())
    assert config["contract"] == CONTRACT
    if mutation == "old_contract":
        config["contract"] = "fixed-trace-process-resume-v1"
        del config["engine_fingerprint"]
    elif mutation == "missing_fingerprint":
        del config["engine_fingerprint"]
    else:
        config["engine_fingerprint"] = "unknown"
    path.write_text(json.dumps(config))
    before_config, before_db = sha256(path), sha256(job.directory / "progress.sqlite")
    with pytest.raises(ResumeError, match="contract|fingerprint"):
        run_worker(job.directory)
    assert sha256(path) == before_config and sha256(job.directory / "progress.sqlite") == before_db


@pytest.mark.parametrize("marker", [
    '{"event":"kill_ready","point":"before_commit","window_index":false}',
    '{"event":"kill_ready","point":"before_commit","window_index":0.0}',
    '{"event":"kill_ready","point":"before_commit","window_index":0,"window_index":0}',
    '{"event":"kill_ready","point":"before_commit","window_index":0,"extra":0}',
    '{"event":"kill_ready","point":"before_commit","window_index":NaN}',
    'not JSON',
    'x' * (OUTPUT_LIMIT + 1),
])
def test_invalid_marker_is_retained_and_child_reaped(tmp_path, marker):
    script = tmp_path / "marker.py"
    script.write_text(f"import time\nprint({marker!r}, flush=True)\ntime.sleep(60)\n")
    with pytest.raises(WorkerLaunchError) as caught:
        launch_worker(script, tmp_path, point="before_commit", window=0, timeout=2)
    record = caught.value.record
    assert record["failure_category"] == "protocol_error"
    assert record["termination"] == "cleanup_kill" and record["returncode"] != 0
    assert record["stdout"] and record["process_wall_ms"] >= 0
    assert not record["output_incomplete"]
    json.dumps(record, allow_nan=False)


def test_large_stdout_stderr_are_drained_but_retained_prefixes_are_bounded(tmp_path):
    script = tmp_path / "verbose.py"
    script.write_text("import sys\nsys.stderr.write('e' * 131072)\nsys.stderr.flush()\n"
                      "sys.stdout.write('o' * 131072 + '\\n')\nsys.stdout.flush()\n")
    record = launch_worker(script, tmp_path, timeout=5)
    assert record["returncode"] == 0 and record["failure_category"] is None
    for name in ("stdout", "stderr"):
        assert len(record[name]) == OUTPUT_LIMIT
        assert record[name + "_truncated"] and record[name + "_bytes"] >= 131072
    assert not record["output_incomplete"]


@pytest.mark.parametrize("stage", ["rendezvous", "completion"])
def test_timeout_retains_output_and_reaps_child(tmp_path, stage):
    script = tmp_path / "timeout.py"
    script.write_text("import sys,time\nprint('timeout diagnostic', file=sys.stderr, flush=True)\n" +
                      ("print('started', flush=True)\n" if stage == "completion" else "") + "time.sleep(60)\n")
    with pytest.raises(WorkerTimeoutError, match=stage) as caught:
        launch_worker(script, tmp_path, timeout=0.5)
    assert isinstance(caught.value, TimeoutError)
    record = caught.value.record
    assert record["failure_category"] == "timeout" and record["returncode"] != 0
    assert record["termination"] == "cleanup_kill"
    assert "timeout diagnostic" in record["stderr"]
    assert not record["output_incomplete"]


def test_spawn_failure_has_structured_record(tmp_path, monkeypatch):
    import adaptive_kg_reasoning.resume_experiment as module
    def fail(*args, **kwargs):
        raise OSError("injected process creation failure")
    monkeypatch.setattr(module.subprocess, "Popen", fail)
    with pytest.raises(WorkerLaunchError) as caught:
        launch_worker(WORKER, tmp_path)
    record = caught.value.record
    assert record["failure_category"] == record["termination"] == "spawn_error"
    assert record["returncode"] is None and not record["stdout"]


@pytest.mark.parametrize("failure", ["child_exit", "timeout", "protocol_error", "spawn_error"])
def test_failed_launch_exports_context_and_truthful_prefix(tmp_path, monkeypatch, failure):
    import adaptive_kg_reasoning.resume_experiment as module
    job = fixture_job(tmp_path)
    script = tmp_path / "failed.py"
    if failure == "child_exit":
        script.write_text("import sys\nprint('failure detail', file=sys.stderr)\nsys.exit(7)\n")
    elif failure == "timeout":
        script.write_text("import time\ntime.sleep(60)\n")
    elif failure == "protocol_error":
        script.write_text("import time\nprint('{}',flush=True)\ntime.sleep(60)\n")
    else:
        def fail(*args, **kwargs):
            raise OSError("injected spawn failure")
        monkeypatch.setattr(module.subprocess, "Popen", fail)
    with pytest.raises(WorkerLaunchError):
        execute_case(script, job.directory, point="before_commit" if failure == "protocol_error" else None,
                     timeout=0.5)
    transcript = json.loads((job.directory / "supervisor.json").read_text())
    assert len(transcript) == 1
    row = transcript[0]
    assert row["failure_category"] == failure
    assert row["job_id"] == "job" and row["launch_number"] == 1
    assert row["cursor"] == -1 and row["frontier_error"] is None
    assert not (job.directory / "receipts.json").exists()


def test_failed_frontier_validation_does_not_invent_a_cursor(tmp_path):
    job = fixture_job(tmp_path)
    with sqlite3.connect(job.directory / "progress.sqlite") as db:
        db.execute("UPDATE progress SET identity='invalid'")
    before = sha256(job.directory / "progress.sqlite")
    with pytest.raises(WorkerLaunchError):
        execute_case(WORKER, job.directory)
    row = json.loads((job.directory / "supervisor.json").read_text())[0]
    assert row["failure_category"] == "child_exit"
    assert row["cursor"] is None and row["frontier_error"]
    assert sha256(job.directory / "progress.sqlite") == before


def test_matrix_abort_retains_passed_prefix_and_failed_launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("patch_runner", ROOT / "experiments/run_v0_6_resume.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = runner.execute_case
    calls = 0
    script = tmp_path / "failed.py"
    script.write_text("import sys\nprint('second case failed',file=sys.stderr)\nsys.exit(9)\n")
    def fail_second(worker, directory, **kwargs):
        nonlocal calls
        calls += 1
        return original(worker if calls == 1 else script, directory, **kwargs)
    monkeypatch.setattr(runner, "execute_case", fail_second)
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["runner", "--events", "40", "--width", "30", "--slide", "10",
                                    "--flush", "--workloads", "dense", "--output-dir", str(output)])
    with pytest.raises(WorkerLaunchError):
        runner.main()
    manifest = json.loads((output / "manifest.json").read_text())
    acceptance = json.loads((output / "acceptance.json").read_text())
    assert manifest["status"] == "failed" and manifest["acceptance_status"] == "not_evaluated"
    assert acceptance["expected_cases"] == 10 and acceptance["completed_cases"] == 1
    assert acceptance["cases"][0]["acceptance"] == "passed"
    assert acceptance["aborted_case"] == {"workload": "dense", "scenario": "before_update-stop"}
    row = json.loads((output / "dense-before_update-stop/supervisor.json").read_text())[0]
    assert row["failure_category"] == "protocol_error" and row["returncode"] != 0
    for name, checksum in manifest["artifacts_sha256"].items():
        assert sha256(output / name) == checksum
