import csv
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.evidence import sha256, source_identity


def invoke(*args):
    return subprocess.run([sys.executable, str(ROOT / "experiments/run_v0_4_placement.py"),
                           *map(str, args)], capture_output=True, text=True)


def table(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_placement_bundle_replay_and_selection_independence(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    flags = ("--events", 80, "--scenarios", "30:10,30:30", "--flush")
    result = invoke(*flags, "--output-dir", first)
    assert result.returncode == 0, result.stderr
    result = invoke("--input", first / "input.csv", "--profiles", first / "profiles.json",
                    "--scenarios", "30:10,30:30", "--flush", "--output-dir", second)
    assert result.returncode == 0, result.stderr
    assert table(first / "summary.csv") == table(second / "summary.csv")
    assert len(table(first / "summary.csv")) == 96  # 2 overlaps * 4 workloads * 3 profiles * 4 alternatives
    assert table(first / "planning.csv") == table(second / "planning.csv")
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["status"] == "passed"
    assert manifest["reference_checked_queries"] > 0
    for name, digest in manifest["artifacts_sha256"].items():
        assert sha256(first / name) == digest
    assert "configs/placement_profiles.json" in source_identity(ROOT)["files_sha256"]
    third = tmp_path / "different-demand"
    result = invoke("--events", 10, "--workloads", "none", "--output-dir", third)
    assert result.returncode == 0, result.stderr
    assert table(first / "planning.csv") == table(third / "planning.csv")
    # A run cannot overwrite earlier evidence, and legacy reference artifacts are not outputs.
    before = sha256(first / "manifest.json")
    assert invoke("--output-dir", first).returncode != 0
    assert sha256(first / "manifest.json") == before


def test_all_infeasible_is_a_valid_simulation_outcome_not_claimed_service(tmp_path):
    raw = json.loads((ROOT / "configs/placement_profiles.json").read_text())
    for node in raw["nodes"]:
        node["memory_budget_bytes"] = 0
    profile = tmp_path / "limited.json"
    profile.write_text(json.dumps(raw))
    output = tmp_path / "limited-run"
    result = invoke("--profiles", profile, "--events", 10, "--scenarios", "30:10",
                    "--workloads", "dense", "--output-dir", output)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["placement_outcomes"] == {"feasible": 0, "infeasible": 9, "no_feasible_estimate": 3}
    assert all(r["served_queries"] == "0" and r["simulated_total_ms"] == "" for r in table(output / "summary.csv"))


def test_invalid_profile_leaves_failed_manifest(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": 999}')
    output = tmp_path / "failure"
    result = invoke("--profiles", path, "--output-dir", output)
    assert result.returncode != 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert "schema_version" in manifest["error"]
    assert not (output / "summary.csv").exists()


def test_empty_input_does_not_publish_success(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("id,timestamp,value,property,plug_id,household_id,house_id\n")
    output = tmp_path / "failure"
    result = invoke("--input", source, "--output-dir", output)
    assert result.returncode != 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed" and "load event" in manifest["error"]
