# adaptive-kg-reasoning

**v0.2.0 — window mechanics for a research-oriented prototype on materialisation decisions in dynamic Knowledge Graph workloads.**

The core question is:

> **When is an inferred fact worth materialising?**

This repository is a technical bridge from ontology / Knowledge Graph engineering toward stream reasoning, query processing, and efficient semantic systems. It deliberately starts with a small reproducible experiment rather than claiming to implement a production RDF Stream Processing engine.

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
pytest -q
```

Outputs are written to:

```text
results/benchmark_v0_1.csv
results/benchmark_v0_1.md
results/entailment_v0_1_1.csv
results/window_trace_v0_2_0.csv
results/recompute_v0_2_1.csv
```

To test scaling:

```bash
python experiments/run_v0_1.py --regenerate --events 100000 --repetitions 20
```

## Repository structure

```text
adaptive-kg-reasoning/
├── data/
│   ├── raw/          # synthetic DEBS-shaped base stream
│   ├── ontology/     # small application ontology
│   ├── static/       # reserved for exported static KG snapshots
│   └── streams/      # reserved for later windowed stream fixtures
├── docs/
│   ├── architecture.md
│   ├── literature-notes.md
│   ├── research-question.md
│   └── v0.2-design.md
├── experiments/
│   ├── run_v0_1.py
│   ├── run_v0_1_1_entailment.py
│   └── run_v0_2_windows.py
├── queries/
├── results/
├── src/adaptive_kg_reasoning/
│   └── windows.py      # deterministic sliding-window transitions
└── tests/
    └── test_windows.py
```

## What this project does **not** claim

- It is **not** a production RDF Stream Processing engine.
- It is **not** a complete OBDA or SPARQL query-rewriting system.
- It does **not** propose a novel reasoning algorithm in v0.1.
- Its query-time strategy is a transparent targeted derivation baseline, not a full virtual Knowledge Graph implementation.
- It does **not** yet model real edge hardware or distributed execution.
- It does **not** treat SHACL validation as logical inference.
- OWL-RL in v0.1.1 is a small entailment baseline, not a complete logic/stream-reasoning architecture.

These boundaries are deliberate: the repository is intended to make the transition from semantic modelling to reasoning-systems research measurable and auditable.

## Roadmap

### v0.2 — incremental/window maintenance
- **v0.2.0 complete:** deterministic sliding windows with explicit additions/expirations and boundary tests;
- **v0.2.1 complete:** full-window recomputation oracle with per-window timing and facts;
- v0.2.2: incremental support-state maintenance and retractions;
- v0.2.3: comparative benchmark and aggregate metrics;
- design specification: [`docs/v0.2-design.md`](docs/v0.2-design.md).

### v0.3 — cost-aware selective materialisation
Introduce an explicit utility function using factors such as reuse frequency, update frequency, result size, and memory cost.

### v0.4 — edge/fog/cloud placement simulation
Add resource budgets and network RTT/transfer costs, then ask both **what** to materialise and **where** to place it.

### Later validation
- run an established RSP workload (e.g. CityBench);
- use WatDiv for static SPARQL/query-planning comparisons;
- compare simplified components with relevant Stream Intelligence Lab systems where technically appropriate.

## Research positioning

The intended progression is:

**ontology modelling → executable semantic constraints → queryability → inference → materialisation cost → dynamic knowledge → adaptive edge/cloud reasoning**

The repository should therefore be read as evidence of a learning trajectory and reproducible experimental practice, not as evidence that the author already has production-level distributed-systems expertise.
