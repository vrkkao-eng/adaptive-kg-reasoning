# adaptive-kg-reasoning

**Incremental reasoning over dynamic Knowledge Graph windows, with reproducible correctness and cost benchmarks.**

[![tests](https://github.com/vrkkao-eng/adaptive-kg-reasoning/actions/workflows/tests.yml/badge.svg)](https://github.com/vrkkao-eng/adaptive-kg-reasoning/actions/workflows/tests.yml)
![Python tested](https://img.shields.io/badge/Python-3.12%20tested-blue)
![License MIT](https://img.shields.io/badge/License-MIT-green)
![Status Research Prototype](https://img.shields.io/badge/Status-Research%20Prototype-orange)

**Current milestone: v0.6.1.** The project evaluates materialisation, placement and recovery for dynamic KG state. A fixed-trace experiment kills real worker processes and resumes in fresh processes using transactional local KG state, input/service cursors and incremental audit records. This patch closes schema-validation gaps, versions line-ending-independent engine identity and preserves bounded failed-launch diagnostics. v0.5.2's exception-recovery policies and strict evidence gate remain separate, reproducible baselines. Hashed evidence, oracle checks and CI expose both completed service and retained terminal prefixes. Local receipts are not external exactly-once delivery. This is a code milestone; tags and published releases are tracked separately.

| Recruiter / reviewer signal | Current evidence |
| --- | --- |
| **Correctness** | Historical fact equivalence plus recovery support-state invariants, corruption regressions and per-case CI acceptance |
| **Measured performance** | Historical v0.2.3 reference: 2.176×–17.613× across three overlap scenarios; not a v0.5 recovery speedup |
| **Semantic reasoning** | OWL-RL entailment baseline separated from procedural window aggregation |
| **Engineering practice** | Deterministic fixtures, pytest regression tests, CI benchmarks, per-run manifests and downloadable artifacts |
| **Adaptive decisions** | Prior-demand policy, explicit bootstrap/switch costs, dense/sparse/bursty/idle comparisons; model units separate from timings |
| **Resource-aware placement** | Three fixed baselines, estimate-only selection, explicit budget failures and byte/RTT accounting; simulated milliseconds separate from host measurements |
| **Failure recovery** | v0.5 bounded exception recovery plus v0.6 real process kills, new-process resume, transactional local receipts/cursors and explicit expected-prefix gates |
| **Research discipline** | Explicit limitations; environment-specific timings are not presented as general performance claims |

The core question is:

> **When is an inferred fact worth materialising?**

The operational problem is a changing knowledge graph serving repeated requests:
maintaining derived facts costs updates and memory; deriving them on demand costs
query work. Success means preserving exact results while exposing that trade-off,
including cases where adaptive selection loses. Start with the
[v0.3 experiment and cost-accounting contract](docs/v0.3-design.md).

This repository is a technical bridge from ontology / Knowledge Graph engineering toward stream reasoning, query processing, and efficient semantic systems. It deliberately starts with a small reproducible experiment rather than claiming to implement a production RDF Stream Processing engine.

> **Technical reviewers:** see [`docs/technical-review.md`](docs/technical-review.md) for a concise map from common architecture, correctness, evaluation and production-readiness questions to repository evidence.

## Quick verification

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python experiments/run_v0_2_benchmark.py --regenerate --events 5000 --scenarios 3600:60,3600:300,3600:900
python experiments/run_v0_3_policy.py --events 5000 --repetitions 3
python experiments/run_v0_4_placement.py --events 1000 --scenarios 120:30,120:120 --flush
python experiments/run_v0_5_recovery.py --events 1000 --width 120 --slide 30 --flush --require-expected-outcomes
python experiments/run_v0_6_resume.py --events 300 --width 120 --slide 30 --flush
```

The comparative benchmark writes `input.csv`, `detail.csv`, `summary.csv` and `manifest.json` under a new `results/runs/<run_id>/` directory. See [reproduction and replay](docs/reproducibility.md). Committed v0.2.3 timings below remain historical GitHub Actions results, not new v0.2.4 measurements or hardware-independent claims.

## What v0.1 tests

Three kinds of inferred facts are compared:

| Inference class | Example | Expected stability |
|---|---|---|
| Stable | sensor → plug → household → house ⇒ sensor deployed at house | high |
| Semi-dynamic | one-hour average load ⇒ `HighRecentConsumption` | medium |
| Highly volatile | ten-second load window ⇒ `CurrentOverload` | low |

Three strategies are benchmarked:

1. **Full materialisation** — precompute and store all three classes.
2. **Query-time derivation** — store none and derive the requested class when queried.
3. **Selective materialisation** — store stable + semi-dynamic facts; derive volatile state on demand.

v0.1.1 also adds a deliberately small **semantic entailment baseline** using OWL-RL. The class hierarchy

```text
SmartPlug ⊑ EnergyDevice ⊑ Device
LoadSensor ⊑ sosa:Sensor
```

is expanded by an OWL-RL reasoner rather than by the hand-written Python derivation functions. This keeps two concerns explicit: ontology entailment for stable semantic facts, and procedural/window aggregation for dynamic stream state.

Measured outputs include:

- materialisation time;
- median query latency;
- number of materialised derived triples;
- explicit graph size after materialisation;
- peak Python allocation during the materialisation step (a proxy, not total process memory).

## v0.1.1 OWL-RL baseline result

The committed v0.1.1 reference run uses the deterministic 5,000-event synthetic DEBS-shaped stream on GitHub Actions (Ubuntu 24.04, Python 3.12.14, RDFLib 7.6.0, owlrl 7.6.2).

| Metric | Result |
|---|---:|
| Explicit triples | 40,229 |
| Closure triples | 79,808 |
| Inferred triples added | 39,579 |
| OWL-RL closure time | 22,866.366 ms |
| SmartPlugs entailed as `EnergyDevice` | 32 |
| SmartPlugs entailed as `Device` | 32 |
| LoadSensors entailed as `sosa:Sensor` | 32 |

The timing is **environment-specific** and should be treated as a reproducible reference run, not a performance claim. The important v0.1.1 correctness signal is that the OWL-RL closure derives the expected class memberships without relying on the hand-written Python derivation path.

## v0.2.0 sliding-window mechanics

v0.2.0 adds deterministic event-time sliding windows before any incremental reasoning is introduced. Window membership uses the half-open interval `[start, end)`: an event at `start` is included, while an event at `end` is deferred to a later window.

Each transition exposes:

- the active events in the current window;
- events newly added since the preceding window;
- events expired since the preceding window;
- deterministic window start/end timestamps.

The first implementation deliberately requires ordered event time and `slide <= width`; it does not yet handle late or out-of-order events. With `--flush`, the trace continues until every previously active event has expired, which makes event lifecycle tests auditable.
## v0.2.1 full-window recomputation oracle

v0.2.1 adds a deliberately simple reference path that recomputes `HighRecentConsumption` from the complete contents of each window. It does not reuse prior aggregates or window deltas.

For every window:

```text
current window events
→ group load observations by plug
→ recompute count / sum / average from scratch
→ derive HighRecentConsumption
```

This path is intended to act as the correctness oracle for v0.2.2 incremental maintenance.
## v0.2.2 incremental maintenance

v0.2.2 maintains per-plug support state across window transitions instead of recomputing every aggregate from scratch. The maintained state consists of active load-event IDs plus per-plug `count` and `total_load`.

For each transition:

```text
expire old load events
+ add new load events
→ update only affected plugs
→ recompute affected averages
→ add or retract HighRecentConsumption
```

Every incremental result is checked against the independent v0.2.1 full-window oracle. Any non-zero symmetric difference is treated as a correctness failure.
## v0.2.3 comparative benchmark

v0.2.3 runs the v0.2.1 full recomputation oracle and the v0.2.2 incremental maintainer on the same window sequence, then records both correctness and maintenance cost.

Headline metrics include:

- recomputation median / p95 / mean / total time;
- incremental-update median / p95 / mean / total time;
- per-window and total speedup ratios;
- events added / expired and affected entities;
- materialised fact additions / retractions;
- a documented Python object-size proxy for incremental state;
- a minimal materialised-fact readout proxy;
- strict result equivalence and symmetric-difference counts.

The default overlap scenarios are `3600/60`, `3600/300`, and `3600/900` (window width / slide, in seconds). Timing results are runtime-specific reference measurements, not general performance claims.
## v0.2.3 CI reference result

The v0.2.3 reference run used the deterministic 5,000-event synthetic stream on GitHub Actions. All scenarios preserved exact equivalence between incremental maintenance and full recomputation.

| Scenario (width/slide) | Windows | Equivalent | Recompute total (ms) | Incremental total (ms) | Total speedup | Recompute median (ms) | Incremental median (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 3600/60 | 84 | 84/84 | 367.007 | 20.838 | 17.613× | 4.821 | 0.122 |
| 3600/300 | 17 | 17/17 | 76.301 | 14.006 | 5.448× | 4.896 | 0.293 |
| 3600/900 | 6 | 6/6 | 28.092 | 12.909 | 2.176× | 5.288 | 1.064 |

The reference run also recorded materialised fact churn and a Python object-size proxy. These timings are environment-specific and should not be interpreted as general RDF Stream Processing performance claims. The useful signal for the next version is that the relative benefit of incremental maintenance decreases as the slide grows and overlap falls.
## v0.4.0 resource-aware placement

The operational question is now: **where should the same maintained state run,
given memory limits and the cost of moving events and query results?**

The experiment compares `fixed_edge`, `fixed_fog`, `fixed_cloud` and
`budget_aware`. It keeps incremental maintenance fixed so placement effects are
not confused with changes in reasoning strategy. Three declared planning
scenarios select different sites; the same evaluation stream is then used to
test those decisions, including forecast errors.

```bash
python experiments/run_v0_4_placement.py --events 5000 --flush --output-dir results/runs/placement-example
```

Inspect `planning.csv` for the decision and rejected candidates, `summary.csv`
for feasibility and completed/partial modeled costs, and `reference.csv` for
oracle checks and measured host timings. `budget_aware` references its chosen
fixed baseline; it is not another distributed execution. A budget violation
stops simulated service with explicit unserved queries, not a hidden fallback.

Profiles are illustrative, not hardware calibrations. Memory is a declared
logical retained-state model, not process RSS. Simulated total milliseconds
represent additive service demand, not measured end-to-end latency. See the
[v0.4 accounting contract and limitations](docs/v0.4-design.md) and
[replay instructions](docs/reproducibility.md).

## v0.5.0 bounded failure recovery

The reliability question is: **can maintained KG state recover after a worker
crash, and how much work does recovery add?** Three policies share the same fixed
node, windows, requests and fault scenario: `stop_on_failure`, `cold_rebuild` and
`checkpoint_replay`. The recovery coordinator discards worker state and restores
an atomically published local checkpoint or explicitly rebuilds when none exists.

```bash
python experiments/run_v0_5_recovery.py --events 1000 --flush --output-dir results/runs/recovery-example
```

The default matrix covers no fault, crashes before/after update and after
checkpoint publication, and persistent crashes that exhaust bounded retries.
Inspect `summary.csv` for service outcomes and recovery work, `audit.json` for
the fault/restore/replay sequence, and checkpoint files for retained support.
A present invalid checkpoint stops recovery; an insufficient memory budget
remains terminal. Every recovered state and served answer is checked against
full recomputation. Normal-path checkpoint I/O and failed/replayed work are
included in measured component costs.

This is a local worker-recovery experiment. The coordinator survives fault
injection, and external query acknowledgments are not persisted. See the
[v0.5 recovery contract](docs/v0.5-design.md) for checkpoint integrity, retry,
timing and delivery boundaries, and [replay instructions](docs/reproducibility.md).

### v0.5.1 verification patch

Aggregate key sets and integer support counts must match full-window recomputation.
Totals must be finite and agree within `rel_tol=1e-12`, `abs_tol=1e-7`; both cached
and support-derived facts must agree exactly. Tolerance does not excuse a changed
classification or silently repair state. Reference grouping remains outside
measured component timings.

`--require-expected-outcomes` adds a feasible-fixture acceptance gate. It checks
each requested workload/fault/policy case, service prefix, fault/retry counts,
query accounting, completed/null totals and ordered audit evidence. It writes
`acceptance.json`; failed acceptance returns a non-zero exit code while preserving
results and a failed manifest. Leave the gate disabled when deliberately exploring
insufficient resource budgets. See [the validation contract](docs/v0.5.1-validation.md).

### v0.5.2 acceptance hardening

The gate no longer accepts a matching global count when work belongs to the wrong
window. It derives the expected event order and integer work counts from declared
queries, faults, retries and checkpoint cadence. Fault boundaries, retry numbers,
restore cursors, replay targets and publications must agree with that protocol.
Required counters reject missing fields, booleans, fractional values and negatives.
Each served window requires a checked state and exactly its served-query checks.

All component times must be finite and non-negative; detail totals reconcile with
summaries, completed totals with disjoint components, and recovery costs with their
documented subsets. Timing comparisons permit only floating-point accounting
roundoff (`rel_tol=1e-9`, `abs_tol_ms=1e-7`), not a speed or latency target.
`acceptance.json` identifies contract `feasible-bounded-worker-recovery-v2` and
retains strict-JSON diagnostics on failure. Checkpoint schema and worker semantics
are unchanged. See [the v0.5.2 acceptance contract](docs/v0.5.2-acceptance.md).

### v0.6.0 process-kill and resume

The new experiment compares uninterrupted execution, stop and bounded resume at
before-update, after-update, before-commit and after-commit boundaries. The parent
kills a real worker; a fresh process revalidates the persisted job and continues
only uncommitted windows. SQLite transactions couple retained KG state, filtered
input frontier, local service receipts and incremental commit audit records.

Four workloads and ten scenarios form a 40-case acceptance matrix, including
persistent process failure. A killed uncommitted window may re-execute, but each
window has one local receipt. No client acknowledgment or external tool/API side
effect is included. The standard-library SQLite backend requires SQLite 3.37+.
See [the process-resume contract](docs/v0.6-design.md) for frontiers, failure
boundaries, manual continuation and artifact-hash caveats.

### v0.6.1 integrity and diagnostics patch

Progress stores must match the complete generated SQLite schema, not only column
names/types. Engine identity uses versioned CRLF-to-LF normalization while run
manifests retain raw-byte hashes. Old v0.6.0 jobs are not automatically migrated:
use their original engine/environment or reproduce into a fresh job.

Failed launches retain bounded output, exit/failure context and a validated
frontier or explicit unknown value. An aborted matrix exports `not_evaluated`
acceptance with its completed prefix; actual gate mismatches remain `failed`.
No unexpected exit or timeout becomes an accepted process-kill scenario.
See [the hardening and compatibility contract](docs/v0.6.1-hardening.md).

## Applied AI / FDE evidence boundaries

The original P0–P4 agent roadmap and the KG systems milestones are not equivalent.
This repository currently demonstrates the engineering practices below, not a
completed enterprise agent application:

| Priority | Evidence already present | Not implemented here |
| --- | --- | --- |
| P0 Evaluation | Deterministic KG oracle, aligned baselines, state/answer checks, recovery acceptance | Agent answer quality, groundedness, LLM/provider comparisons |
| P1 Observability | Run manifests, component metrics, v0.6 incremental transactional audit | Live traces, metrics backend, dashboard, supervisor-crash export recovery |
| P2 Recovery | Worker exceptions plus real process termination/resume with durable local frontiers | External delivery guarantees, tool/API timeout/backoff, escalation, deployed service |
| P3 Security/auditability | Input/source/artifact hashes, checkpoint integrity and identity | Authentication, authorization, secret controls, tamper-evident audit |
| P4 Advanced agents | No agent feature is claimed | Jev integration, ReAct/planning, agent tools and governance |

The evidence chain is **operational problem → explicit contract → aligned
baselines → oracle/regression checks → CI → reproducible local evidence**.
Real deployment/API operation is still missing; the edge/fog/cloud experiment is
a simulation. The MSAI thesis demonstration remains a separate application
direction. This patch introduces no new repo, provider or research algorithm.

## Data

The raw sample uses the DEBS 2014 Grand Challenge base-stream field structure:

```text
id,timestamp,value,property,plug_id,household_id,house_id
```

The included stream is **synthetic and deterministic**. It is not a redistributed copy of the official DEBS dataset. This keeps the repository small and reproducible while preserving the shape needed for the first experiment.

See: https://debs.org/grand-challenges/2014/

Observations reuse SOSA vocabulary terms where practical: https://www.w3.org/TR/vocab-ssn-2023/

## Quick start

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
python experiments/run_v0_1.py --regenerate --events 5000 --repetitions 10
python experiments/run_v0_1_1_entailment.py --regenerate --events 5000
python experiments/run_v0_2_windows.py --regenerate --events 5000 --width 3600 --slide 60 --flush
python experiments/run_v0_2_recompute.py --regenerate --events 5000 --width 3600 --slide 60 --flush
python experiments/run_v0_2_incremental.py --regenerate --events 5000 --width 3600 --slide 60 --flush
python experiments/run_v0_2_benchmark.py --regenerate --events 5000 --scenarios 3600:60,3600:300,3600:900
pytest -q
```

Outputs are written to:

```text
results/benchmark_v0_1.csv
results/benchmark_v0_1.md
results/entailment_v0_1_1.csv
results/window_trace_v0_2_0.csv
results/recompute_v0_2_1.csv
results/incremental_v0_2_2.csv
results/runs/<run_id>/detail.csv
results/runs/<run_id>/summary.csv
results/runs/<run_id>/manifest.json
results/runs/<run_id>/input.csv
```

To test scaling:

```bash
python experiments/run_v0_1.py --regenerate --events 100000 --repetitions 20
```

## Repository structure

```text
adaptive-kg-reasoning/
├── configs/
│   └── placement_profiles.json
├── data/
│   ├── raw/          # synthetic DEBS-shaped base stream
│   ├── ontology/     # small application ontology
│   ├── static/       # exported static topology fixture
│   └── streams/      # exported stream fixture
├── docs/
│   ├── architecture.md
│   ├── literature-notes.md
│   ├── research-question.md
│   ├── v0.2-design.md
│   ├── v0.3-design.md
│   ├── v0.4-design.md
│   ├── v0.5-design.md
│   ├── v0.5.1-validation.md
│   ├── v0.5.2-acceptance.md
│   ├── v0.6-design.md
│   ├── v0.6.1-hardening.md
│   ├── reproducibility.md
│   └── technical-review.md
├── experiments/
│   ├── run_v0_1.py
│   ├── run_v0_1_1_entailment.py
│   ├── run_v0_2_windows.py
│   ├── run_v0_2_recompute.py
│   ├── run_v0_2_incremental.py
│   ├── run_v0_2_benchmark.py
│   ├── run_v0_3_policy.py
│   ├── run_v0_4_placement.py
│   ├── run_v0_5_recovery.py
│   ├── run_v0_6_resume.py
│   └── resume_worker.py
├── queries/
├── results/
├── src/adaptive_kg_reasoning/
│   ├── windows.py      # deterministic sliding-window transitions
│   ├── recompute.py    # independent correctness oracle
│   ├── incremental.py  # support-state maintenance
│   ├── metrics.py      # comparative measurement
│   ├── evidence.py     # run manifests and fingerprints
│   ├── adaptive.py     # cost-aware policy and aligned strategy benchmark
│   ├── resources.py    # validated resource and workload estimates
│   ├── network_cost.py # direct-link RTT and payload accounting
│   ├── placement.py    # fixed-site planning and modeled evaluation
│   ├── checkpoint.py   # atomic local snapshot publication and validation
│   ├── recovery.py     # bounded fault/recovery comparison and audit
│   ├── recovery_acceptance.py # independent per-case acceptance contract
│   ├── process_resume.py # transactional local progress and new-process worker
│   ├── resume_experiment.py # real process supervision and prefix acceptance
│   └── ...             # RDF mapping, entailment, v0.1 strategies
└── tests/
    ├── test_windows.py
    ├── test_recompute.py
    ├── test_incremental.py
    ├── test_metrics.py
    └── ...
```

## What this project does **not** claim

- It is **not** a production RDF Stream Processing engine.
- It is **not** a complete OBDA or SPARQL query-rewriting system.
- It does **not** propose a novel reasoning algorithm in v0.1.
- Its query-time strategy is a transparent targeted derivation baseline, not a full virtual Knowledge Graph implementation.
- Its v0.4 profiles do **not** represent calibrated edge hardware or real distributed execution.
- It does **not** treat SHACL validation as logical inference.
- OWL-RL in v0.1.1 is a small entailment baseline, not a complete logic/stream-reasoning architecture.

These boundaries are deliberate: the repository is intended to make the transition from semantic modelling to reasoning-systems research measurable and auditable.

## Roadmap

### v0.2 — incremental/window maintenance
- **v0.2.0 complete:** deterministic sliding windows with explicit additions/expirations and boundary tests;
- **v0.2.1 complete:** full-window recomputation oracle with per-window timing and facts;
- **v0.2.2 complete:** incremental support-state maintenance, additions/retractions, idempotence, and strict oracle equivalence;
- **v0.2.3 complete:** comparative benchmark with aggregate timing, correctness, overlap scenarios, and state/readout proxies;
- **v0.2.4:** isolated run evidence, replay instructions, CI artifacts and corrected reviewer navigation;
- design specification: [`docs/v0.2-design.md`](docs/v0.2-design.md).

### v0.3 — cost-aware selective materialisation
Implemented for one derived state: an explicit synthetic utility model using prior
query demand, event updates, last observed result size and retention cost. Compare
four strategies on identical windows and request schedules; count bootstrap,
release and policy overhead separately. Adaptive performance is measured, not
assumed. See [design, limitations and replay](docs/v0.3-design.md).

### v0.4 — edge/fog/cloud placement simulation
Implemented: resource budgets, network RTT/transfer accounting, fixed-site
baselines and estimate-only selection. v0.3 asks **what** to materialise; v0.4
isolates **where** by holding the incremental strategy constant. Joint strategy
and placement optimization remains future work. See [v0.4 design](docs/v0.4-design.md).

### v0.5 — bounded recovery evidence

Implemented: a fixed-site recovery contract, local checkpoint integrity and
atomic replacement, three recovery policies, bounded retry exhaustion and
fault-injection evidence. Recovery preserves oracle equivalence and exposes
unserved requests. The v0.4 placement experiment retains its original stop
semantics. See [v0.5 design](docs/v0.5-design.md).

**v0.5.1:** full recovery support-state checks and explicit CI acceptance, without
changing checkpoint schema or earlier placement/strategy contracts. See
[validation and claim boundaries](docs/v0.5.1-validation.md).

**v0.5.2:** typed per-window counters, scenario-derived audit protocol and reconciled
timing evidence; no new reasoning/recovery policy, dependency or CI job. See
[acceptance hardening](docs/v0.5.2-acceptance.md).

### v0.6.0 — process-resume validation

Implemented: real parent-terminated worker processes, fresh-process continuation,
transactional KG state/input/service frontiers, local receipts, incremental audit,
bounded restart exhaustion and a 40-case prefix acceptance matrix. Existing v0.5
file checkpoint schema/policies remain unchanged. See [the contract](docs/v0.6-design.md).

**v0.6.1:** complete generated-schema checks, versioned engine fingerprints and
bounded failed-launch/aborted-matrix evidence; no new recovery policy or service.
See [hardening](docs/v0.6.1-hardening.md).

### Next boundary — live operation and external delivery (not implemented)

Define external request identity, delivery/acknowledgment semantics, API operation
and operational observability separately. Current immutable whole-trace identity
does not support an append-only online stream. No agent/provider or thesis-app
scope is implied by process resume.

### Later validation
- run an established RSP workload (e.g. CityBench);
- use WatDiv for static SPARQL/query-planning comparisons;
- compare simplified components with relevant Stream Intelligence Lab systems where technically appropriate.

## Research positioning

The intended progression is:

**ontology modelling → executable semantic constraints → queryability → inference → materialisation cost → dynamic knowledge → adaptive edge/cloud reasoning**

The repository should therefore be read as evidence of a learning trajectory and reproducible experimental practice, not as evidence that the author already has production-level distributed-systems expertise.
