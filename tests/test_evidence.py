import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.evidence import sha256


def invoke(*args):
    return subprocess.run([sys.executable, str(ROOT / "experiments/run_v0_2_benchmark.py"),
                           *map(str, args)], capture_output=True, text=True)


def test_bundle_replay_and_historical_results_are_preserved(tmp_path):
    reference = ROOT / "results/benchmark_v0_2_3_summary.csv"
    before = sha256(reference)
    first, second = tmp_path / "first", tmp_path / "second"
    result = invoke("--regenerate", "--events", 80, "--scenarios", "30:10", "--output-dir", first)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["status"] == "passed"
    assert manifest["loaded_load_events"] > 0
    assert manifest["source"]["source_sha256"]
    for name, digest in manifest["artifacts_sha256"].items():
        assert sha256(first / name) == digest
    result = invoke("--input", first / "input.csv", "--scenarios", "30:10", "--output-dir", second)
    assert result.returncode == 0, result.stderr
    def correctness(path):
        with path.open() as fh:
            return [(r["window_index"], r["result_equivalent"], r["materialised_fact_count"])
                    for r in csv.DictReader(fh)]
    assert correctness(first / "detail.csv") == correctness(second / "detail.csv")
    replay = json.loads((second / "manifest.json").read_text())
    assert replay["config"]["generator_seed"] is None
    assert replay["config"]["generated"] is False
    assert sha256(reference) == before
    result = invoke("--output-dir", first)
    assert result.returncode != 0
    assert json.loads((first / "manifest.json").read_text()) == manifest


def test_empty_input_leaves_failed_manifest(tmp_path):
    source = tmp_path / "empty.csv"
    source.write_text("id,timestamp,value,property,plug_id,household_id,house_id\n")
    output = tmp_path / "failed"
    result = invoke("--input", source, "--output-dir", output)
    assert result.returncode != 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert "load event" in manifest["error"]
    assert not (output / "summary.csv").exists()
