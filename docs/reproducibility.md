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

CI uploads fresh v0.2.4 through v0.6.0 bundles and JUnit results for 30 days, including on failure.
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

## v0.5 recovery replay

```sh
python experiments/run_v0_5_recovery.py --events 1000 --seed 42 --flush --require-expected-outcomes --output-dir results/runs/recovery-example
python experiments/run_v0_5_recovery.py --input results/runs/recovery-example/input.csv --profiles results/runs/recovery-example/profiles.json --node fog --width 120 --slide 30 --threshold 450 --workloads none,sparse,dense,bursty --faults none,before_update,after_update,after_checkpoint,persistent_crash --fault-window 2 --checkpoint-every 2 --max-retries 2 --flush --require-expected-outcomes --output-dir results/runs/recovery-replay
```

Preserve every recorded semantic, resource, request, fault, cadence and retry
setting. Deterministic summary fields, audit events, schedules and checkpoint
contents should match; measured fields ending in `_ms`, environment, run IDs
and timestamps need not. Per-run summary timing includes checkpoint I/O and
failed/replayed work; terminal outcomes have no completed total.

The runner always starts a new experiment and refuses existing output/checkpoint
paths. `load_checkpoint` is the separately tested state-restoration API; replaying
the entire matrix is distinct from resuming a production service. The checkpoint
identity is derived from the exact transitions and threshold in the current
experiment. A changed trace cannot reuse the snapshot. See the
[v0.5 contract](v0.5-design.md) before interpreting recovery or delivery claims.

The v0.5.1 gate writes `acceptance.json` with deterministic per-case expectations,
observations and mismatches. Replaying the same feasible fixture should reproduce
this report as well as non-timing summaries, audit events and checkpoint bytes.
An acceptance failure keeps those files and a failed manifest, then returns a
non-zero exit code. Omit `--require-expected-outcomes` when intentionally studying
capacity failures; then `acceptance_status=not_requested`. A passed exploratory
manifest does not imply the declared recovery outcomes met the CI contract.
See [validation and acceptance details](v0.5.1-validation.md).

Current v0.5.2 runs identify acceptance contract v2, record the checkpoint cadence
and accounting tolerances, and additionally enforce typed per-window work,
exact audit protocol and finite/reconciled times. Preserve `--checkpoint-every`
when replaying, including non-default values. Successful reports exclude host
timings and should match exactly. Historical schema-1 checkpoints are unchanged;
older acceptance reports keep their original contract identifier. See
[strict acceptance details](v0.5.2-acceptance.md).

## v0.6 process-resume replay

```sh
python experiments/run_v0_6_resume.py --events 300 --width 120 --slide 30 --flush --output-dir results/runs/process-example
python experiments/run_v0_6_resume.py --input results/runs/process-example/input.csv --profiles results/runs/process-example/profiles.json --width 120 --slide 30 --flush --output-dir results/runs/process-replay
```

Preserve threshold, node, workload list, dimensions, flush, kill window and restart
budget as well as exact engine source/dependency versions. Compare acceptance,
non-timing summaries and each job's receipts/audit. Raw SQLite bytes and process
exit-code conventions are not portable deterministic outputs. Recursive hashes
identify the retained nested evidence; SQLite version is recorded separately.

`resume_worker.py --job-dir <existing-job>` continues the authoritative progress
DB in a fresh process. It differs from replaying the matrix into a new directory.
After manual continuation, earlier exported receipts/audit and artifact hashes
describe the earlier bundle only. Never mutate a published bundle and claim its
original manifest proves the new state. Retain rollback journals with their DB
and allow SQLite to recover them. See [the process-resume contract](v0.6-design.md).
