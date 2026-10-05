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

CI uploads fresh v0.2.4, v0.3 and v0.4 bundles and JUnit results for 30 days, including on failure.
Older experiment scripts still use their documented legacy output paths and are
retained as historical smoke tests. A version in the changelog denotes a code
milestone; publication as a Git tag/GitHub Release is a separate operation.

## v0.4 placement replay

```sh
python experiments/run_v0_4_placement.py --events 5000 --seed 42 --flush --output-dir results/runs/placement-example
python experiments/run_v0_4_placement.py --input results/runs/placement-example/input.csv --profiles results/runs/placement-example/profiles.json --scenarios 3600:60,3600:300,3600:900 --workloads none,sparse,dense,bursty --threshold 450 --flush --output-dir results/runs/placement-replay
```

Preserve the recorded profiles, threshold, window scenarios, workloads and flush
setting. Plans depend only on profile estimates, not evaluation input or the
generator seed. `planning.csv` and modeled `summary.csv` should match exactly
under the same source/dependency environment. Run IDs, timestamps, input-mode
metadata and host timings in `reference.csv` need not match.

The manifest includes configuration files in its source fingerprint; the copied
`profiles.json` has its own artifact hash, including when supplied from outside
the repository. All actual payload sizes and query schedules are retained.
Inspect placement outcomes as well as manifest status: a completed experiment
may correctly report an infeasible site. Never compare its partial cost to a
completed execution's total. The [v0.4 design](v0.4-design.md) defines the cost,
memory and failure semantics in detail.
