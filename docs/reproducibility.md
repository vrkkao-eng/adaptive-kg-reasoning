# Reproducing benchmark evidence

The v0.2.4 runner preserves the v0.2.3 comparison and CSV metrics. New runs go
to unique `results/runs/<uuid>/` directories; committed historical results are
never overwritten by this runner. `--output-dir` must name a new directory.

```sh
python -m pip install -r requirements.txt
python -m pytest -q
python experiments/run_v0_2_benchmark.py --regenerate --events 5000 --seed 42 --output-dir results/runs/example
```

Each bundle contains `input.csv`, `detail.csv`, `summary.csv`, and `manifest.json`.
The manifest records configuration, actual load-event count, UTC timestamps,
Python/platform/dependency versions, Git revision and dirty status when available,
source-file hashes and their aggregate fingerprint, and hashes of every artifact.
Seed/event arguments describe generation only when `generated=true`. With
`--input`, the copied CSV and its hash identify the actual input. Failures retain
a `status=failed` manifest and never publish a success result for empty input.

Replay the exact input into a new directory using the recorded threshold,
scenarios and flush setting:

```sh
python experiments/run_v0_2_benchmark.py --input results/runs/example/input.csv --scenarios 3600:60,3600:300,3600:900 --threshold 450 --output-dir results/runs/replay
```

Use the recorded commit plus any source changes identified by the fingerprint,
and install the dependency versions in the manifest when comparing environments.
The bundle fingerprints source; it does not archive a dirty working tree or
guarantee bit-for-bit timing reproduction. Requirements specify supported ranges,
not a lockfile. Hashes detect accidental drift, not malicious tampering.

CI uploads fresh v0.2.4 bundles and JUnit results for 30 days, including on failure.
Older experiment scripts still use their documented legacy output paths and are
retained as historical smoke tests. A version in the changelog denotes a code
milestone; publication as a Git tag/GitHub Release is a separate operation.
