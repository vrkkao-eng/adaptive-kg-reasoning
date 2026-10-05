import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def invoke(*args):
    return subprocess.run([sys.executable, str(ROOT / "experiments/run_v0_3_policy.py"),
                           *map(str, args)], capture_output=True, text=True)


def test_policy_cli_bundle_is_replayable(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    options = ("--scenarios", "30:10,30:30", "--repetitions", 2, "--flush")
    result = invoke("--events", 80, *options, "--output-dir", first)
    assert result.returncode == 0, result.stderr
    result = invoke("--input", first / "input.csv", *options, "--output-dir", second)
    assert result.returncode == 0, result.stderr
    def measures(directory):
        with (directory / "summary.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 64  # 2 overlaps * 4 workloads * 2 repeats * 4 strategies
        return [(r["scenario"], r["workload"], r["strategy"], r["queries"],
                 r["realized_model_units"], r["checked_states"], r["symmetric_difference_count"])
                for r in rows]
    assert measures(first) == measures(second)
    for directory in (first, second):
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest["status"] == "passed"
        assert "workloads.json" in manifest["artifacts_sha256"]
        assert len(manifest["execution_order"]) == 16


def test_invalid_workload_retains_failed_manifest(tmp_path):
    output = tmp_path / "failed"
    result = invoke("--events", 10, "--workloads", "unknown", "--output-dir", output)
    assert result.returncode != 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert "Unknown workload" in manifest["error"]
