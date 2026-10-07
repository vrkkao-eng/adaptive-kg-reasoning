import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.evidence import sha256


def invoke(*args):
    return subprocess.run([sys.executable, str(ROOT / "experiments/run_v0_6_resume.py"), *map(str, args)],
                          capture_output=True, text=True, timeout=240)


def rows(path):
    with path.open(encoding="utf-8") as source:
        return [{key: value for key, value in row.items() if key != "process_wall_ms"}
                for row in csv.DictReader(source)]


def assert_hashes(output):
    manifest = json.loads((output / "manifest.json").read_text())
    for name, checksum in manifest["artifacts_sha256"].items():
        assert sha256(output / name) == checksum
    return manifest


def test_full_matrix_replay_and_nested_evidence(tmp_path):
    first, replay = tmp_path / "first", tmp_path / "replay"
    result = invoke("--events", 80, "--width", 30, "--slide", 10, "--flush", "--output-dir", first)
    assert result.returncode == 0, result.stderr
    result = invoke("--input", first / "input.csv", "--profiles", first / "profiles.json",
                    "--width", 30, "--slide", 10, "--flush", "--output-dir", replay)
    assert result.returncode == 0, result.stderr
    assert rows(first / "summary.csv") == rows(replay / "summary.csv")
    manifest = assert_hashes(first)
    assert manifest["status"] == manifest["acceptance_status"] == "passed"
    assert manifest["benchmark"] == "v0.6.1" and manifest["package_version"] == "0.7.0"
    assert manifest["completed_cases"] == 40 and manifest["environment"]["sqlite"]
    assert manifest["generated"] and manifest["generator_seed"] == 42
    assert len(rows(first / "summary.csv")) == 40
    acceptance = json.loads((first / "acceptance.json").read_text())
    assert acceptance == json.loads((replay / "acceptance.json").read_text())
    assert all(row["acceptance"] == "passed" for row in acceptance["cases"])
    for job in first.iterdir():
        if job.is_dir():
            for name in ("receipts.json", "audit.json"):
                assert (job / name).read_bytes() == (replay / job.name / name).read_bytes()
            transcript = json.loads((job / "supervisor.json").read_text())
            assert transcript and all(row["returncode"] != 0 for row in transcript if row["termination"] == "parent_kill")
    assert_hashes(replay)
    assert not json.loads((replay / "manifest.json").read_text())["generated"]
    before = sha256(first / "manifest.json")
    assert invoke("--output-dir", first).returncode != 0
    assert sha256(first / "manifest.json") == before


@pytest.mark.parametrize("scenario", ["empty", "outside", "capacity", "unknown_workload"])
def test_invalid_or_infeasible_matrix_retains_failed_manifest(tmp_path, scenario):
    output = tmp_path / "run"
    arguments = ["--events", 40, "--width", 30, "--slide", 10, "--flush", "--output-dir", output]
    if scenario == "empty":
        source = tmp_path / "input.csv"
        source.write_text("id,timestamp,value,property,plug_id,household_id,house_id\n")
        arguments += ["--input", source]
    elif scenario == "outside":
        arguments += ["--fault-window", 100000]
    elif scenario == "unknown_workload":
        arguments += ["--workloads", "../escape"]
    else:
        data = json.loads((ROOT / "configs/placement_profiles.json").read_text())
        for node in data["nodes"]:
            node["memory_budget_bytes"] = 0
        profile = tmp_path / "profiles.json"
        profile.write_text(json.dumps(data))
        arguments += ["--profiles", profile]
    result = invoke(*arguments)
    assert result.returncode != 0
    manifest = assert_hashes(output)
    assert manifest["status"] == "failed" and manifest["error"]
    assert manifest["acceptance_status"] == "not_evaluated"
    acceptance = json.loads((output / "acceptance.json").read_text())
    assert acceptance["status"] == "not_evaluated" and acceptance["error"]
    assert acceptance["completed_cases"] == manifest["completed_cases"]
    if scenario == "capacity":
        transcript = json.loads((output / "none-none/supervisor.json").read_text())
        assert len(transcript) == 1
        launch = transcript[0]
        assert launch["failure_category"] == "child_exit" and launch["returncode"] != 0
        assert "memory_budget_exceeded" in launch["stderr"]
        assert launch["cursor"] == -1 and launch["launch_number"] == 1
        assert acceptance["aborted_case"] == {"workload": "none", "scenario": "none"}
    assert not (tmp_path / "escape").exists()


def test_gate_failure_retains_results_and_reproducible_manifest(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("resume_runner", ROOT / "experiments/run_v0_6_resume.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = runner.execute_case
    def mutate(*args, **kwargs):
        row = original(*args, **kwargs)
        row.update(acceptance="failed", mismatches=["injected regression"])
        return row
    monkeypatch.setattr(runner, "execute_case", mutate)
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["run_v0_6_resume.py", "--events", "40", "--width", "30",
        "--slide", "10", "--flush", "--workloads", "dense", "--output-dir", str(output)])
    with pytest.raises(AssertionError, match="acceptance failed"):
        runner.main()
    manifest = assert_hashes(output)
    assert manifest["status"] == manifest["acceptance_status"] == "failed"
    assert (output / "summary.csv").exists()
    assert json.loads((output / "acceptance.json").read_text())["status"] == "failed"
    assert any(name.endswith("progress.sqlite") for name in manifest["artifacts_sha256"])
