import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning import __version__
from adaptive_kg_reasoning.evidence import sha256


def invoke(*args):
    return subprocess.run([sys.executable, str(ROOT / "experiments/run_v0_5_recovery.py"),
                           *map(str, args)], capture_output=True, text=True)


def deterministic_summary(path):
    with path.open(encoding="utf-8") as fh:
        return [{key: value for key, value in row.items() if not key.endswith("_ms")}
                for row in csv.DictReader(fh)]


def test_recovery_bundle_replay_and_hashes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    result = invoke("--events", 120, "--width", 30, "--slide", 10, "--flush",
                    "--require-expected-outcomes", "--output-dir", first)
    assert result.returncode == 0, result.stderr
    result = invoke("--input", first / "input.csv", "--profiles", first / "profiles.json",
                    "--width", 30, "--slide", 10, "--flush", "--require-expected-outcomes", "--output-dir", second)
    assert result.returncode == 0, result.stderr
    rows = deterministic_summary(first / "summary.csv")
    assert len(rows) == 60
    assert rows == deterministic_summary(second / "summary.csv")
    assert json.loads((first / "audit.json").read_text()) == json.loads((second / "audit.json").read_text())
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["status"] == "passed"
    assert __version__ == "0.5.2"
    assert manifest["benchmark"] == f"v{__version__}" and manifest["acceptance_status"] == "passed"
    acceptance = json.loads((first / "acceptance.json").read_text())
    assert acceptance["status"] == "passed" and len(acceptance["cases"]) == 60
    assert acceptance["contract"] == "feasible-bounded-worker-recovery-v2"
    assert acceptance == json.loads((second / "acceptance.json").read_text())
    assert manifest["recovery_outcomes"] == {"completed": 12, "recovered": 24,
        "retry_exhausted": 8, "stopped_on_failure": 16}
    assert manifest["checked_queries"] > 0
    for name, digest in manifest["artifacts_sha256"].items():
        assert sha256(first / name) == digest
    assert any(name.startswith("checkpoint-") for name in manifest["artifacts_sha256"])
    for path in first.glob("checkpoint-*.json"):
        assert path.read_bytes() == (second / path.name).read_bytes()
    digest = sha256(first / "manifest.json")
    assert invoke("--output-dir", first).returncode != 0
    assert sha256(first / "manifest.json") == digest


def test_checkpoint_load_in_fresh_process(tmp_path):
    output = tmp_path / "run"
    result = invoke("--events", 40, "--width", 30, "--slide", 10, "--flush",
                    "--faults", "none", "--workloads", "dense", "--output-dir", output)
    assert result.returncode == 0, result.stderr
    checkpoint = output / "checkpoint-0-0-checkpoint_replay.json"
    raw = json.loads(checkpoint.read_text())
    code = ("import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
            "from adaptive_kg_reasoning.checkpoint import load_checkpoint; "
            "state, cursor = load_checkpoint(Path(sys.argv[1]), expected_identity=sys.argv[2]); "
            "print(cursor, len(state.active_events), len(state.facts))")
    loaded = subprocess.run([sys.executable, "-c", code, str(checkpoint), raw["payload"]["identity"]],
                            capture_output=True, text=True)
    assert loaded.returncode == 0, loaded.stderr
    assert int(loaded.stdout.split()[0]) == raw["payload"]["cursor"]


def test_resource_exhaustion_has_no_recovery_success(tmp_path):
    raw = json.loads((ROOT / "configs/placement_profiles.json").read_text())
    for node in raw["nodes"]:
        node["memory_budget_bytes"] = 0
    profile = tmp_path / "profiles.json"
    profile.write_text(json.dumps(raw))
    output = tmp_path / "run"
    result = invoke("--profiles", profile, "--events", 40, "--width", 30, "--slide", 10,
                    "--workloads", "dense", "--output-dir", output)
    assert result.returncode == 0, result.stderr
    rows = deterministic_summary(output / "summary.csv")
    assert all(row["status"] == "memory_budget_exceeded" and row["served_queries"] == "0" for row in rows)
    assert all(row["recovery_attempts"] == "0" for row in rows)
    assert json.loads((output / "manifest.json").read_text())["acceptance_status"] == "not_requested"
    assert not (output / "acceptance.json").exists()
    gated = tmp_path / "gated"
    result = invoke("--profiles", profile, "--events", 40, "--width", 30, "--slide", 10,
                    "--workloads", "dense", "--require-expected-outcomes", "--output-dir", gated)
    assert result.returncode != 0 and "acceptance failed" in result.stderr
    manifest = json.loads((gated / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["acceptance_status"] == "failed"
    assert manifest["recovery_outcomes"] == {"memory_budget_exceeded": 15}
    assert json.loads((gated / "acceptance.json").read_text())["status"] == "failed"
    assert (gated / "summary.csv").exists() and (gated / "audit.json").exists()
    for name, digest in manifest["artifacts_sha256"].items():
        assert sha256(gated / name) == digest


def test_failed_configuration_retains_manifest(tmp_path):
    output = tmp_path / "invalid"
    result = invoke("--events", 40, "--fault-window", 100000, "--output-dir", output)
    assert result.returncode != 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed" and "outside" in manifest["error"]
    assert not (output / "summary.csv").exists()


def test_empty_input_and_nonfinite_load_fail(tmp_path):
    for name, body in (("empty", ""), ("nan", "1,0,nan,1,1,1,1\n")):
        source = tmp_path / f"{name}.csv"
        source.write_text("id,timestamp,value,property,plug_id,household_id,house_id\n" + body)
        output = tmp_path / name
        result = invoke("--input", source, "--faults", "none", "--output-dir", output)
        assert result.returncode != 0
        assert json.loads((output / "manifest.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("cadence,fault_window", [(1, 0), (3, 3)])
def test_cli_passes_declared_cadence_to_acceptance(tmp_path, cadence, fault_window):
    output = tmp_path / "run"
    result = invoke("--events", 80, "--width", 30, "--slide", 10, "--flush", "--workloads", "dense",
                    "--checkpoint-every", cadence, "--fault-window", fault_window,
                    "--require-expected-outcomes", "--output-dir", output)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "acceptance.json").read_text())
    assert report["status"] == "passed" and report["checkpoint_every"] == cadence


@pytest.mark.parametrize("mutation", ["nan_time", "missing_counter", "audit_bool"])
def test_strict_gate_failure_preserves_json_and_evidence(tmp_path, monkeypatch, mutation):
    spec = importlib.util.spec_from_file_location("recovery_cli", ROOT / "experiments/run_v0_5_recovery.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = runner.benchmark_recovery
    def corrupt(*args, **kwargs):
        detail, summary, audit = original(*args, **kwargs)
        if mutation == "nan_time":
            detail[0]["measured_maintenance_ms"] = float("nan")
        elif mutation == "missing_counter":
            for row in detail:
                del row["checkpoint_loads"]
        else:
            audit[0]["sequence"] = False
        return detail, summary, audit
    monkeypatch.setattr(runner, "benchmark_recovery", corrupt)
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["run_v0_5_recovery.py", "--events", "40", "--width", "30",
        "--slide", "10", "--flush", "--workloads", "dense", "--faults", "none",
        "--require-expected-outcomes", "--output-dir", str(output)])
    with pytest.raises(AssertionError, match="acceptance failed"):
        runner.main()
    manifest = json.loads((output / "manifest.json").read_text())
    report = json.loads((output / "acceptance.json").read_text())
    assert manifest["status"] == manifest["acceptance_status"] == report["status"] == "failed"
    json.dumps(report, allow_nan=False)
    assert manifest["recovery_outcomes"] == {"completed": 3}
    assert all((output / name).exists() for name in ("summary.csv", "detail.csv", "audit.json", "workloads.json"))
    for name, digest in manifest["artifacts_sha256"].items():
        assert sha256(output / name) == digest
