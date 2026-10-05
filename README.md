# adaptive-kg-reasoning

**Incremental reasoning over dynamic Knowledge Graph windows, with reproducible correctness and cost benchmarks.**

[![tests](https://github.com/vrkkao-eng/adaptive-kg-reasoning/actions/workflows/tests.yml/badge.svg)](https://github.com/vrkkao-eng/adaptive-kg-reasoning/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License MIT](https://img.shields.io/badge/License-MIT-green)
![Status Research Prototype](https://img.shields.io/badge/Status-Research%20Prototype-orange)

**Current milestone: v0.4.0.** The project compares materialisation strategies on identical dynamic-KG workloads and now simulates fixed edge/fog/cloud placement under declared resource and network constraints. A budget-aware selector uses workload estimates before reading the evaluation stream. Reference answers and states are checked against full recomputation; hashed evidence bundles preserve the experiment. This is a code milestone, not a claim of a published release or distributed deployment.

| Recruiter / reviewer signal | Current evidence |
| --- | --- |
| **Correctness** | Exact equivalence in all committed v0.2.3 benchmark windows |
| **Measured performance** | 2.176×–17.613× total reference speedup across three overlap scenarios |
| **Semantic reasoning** | OWL-RL entailment baseline separated from procedural window aggregation |
| **Engineering practice** | Deterministic fixtures, pytest regression tests, CI benchmarks, per-run manifests and downloadable artifacts |
| **Adaptive decisions** | Prior-demand policy, explicit bootstrap/switch costs, dense/sparse/bursty/idle comparisons; model units separate from timings |
| **Resource-aware placement** | Three fixed baselines, estimate-only selection, explicit budget failures and byte/RTT accounting; simulated milliseconds separate from host measurements |
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
│   └── run_v0_4_placement.py
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

### v0.5 — proposed reliability evidence

Build on the explicit v0.4 failure states with a separately specified recovery
contract, checkpoint/replay tests and fault-injection evidence. Scope and
acceptance criteria must be agreed before implementation; these features are
not present in v0.4. No separate thesis application or agent stack is required
for this repository's current engineering path.

### Later validation
- run an established RSP workload (e.g. CityBench);
- use WatDiv for static SPARQL/query-planning comparisons;
- compare simplified components with relevant Stream Intelligence Lab systems where technically appropriate.

## Research positioning

The intended progression is:

**ontology modelling → executable semantic constraints → queryability → inference → materialisation cost → dynamic knowledge → adaptive edge/cloud reasoning**

The repository should therefore be read as evidence of a learning trajectory and reproducible experimental practice, not as evidence that the author already has production-level distributed-systems expertise.
