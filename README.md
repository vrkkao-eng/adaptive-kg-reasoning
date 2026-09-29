# adaptive-kg-reasoning

**v0.1.1 — a research-oriented learning prototype for materialisation decisions and a small OWL-RL entailment baseline in dynamic Knowledge Graph workloads.**

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
pytest -q
```

Outputs are written to:

```text
results/benchmark_v0_1.csv
results/benchmark_v0_1.md
results/entailment_v0_1_1.csv
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
│   └── research-question.md
├── experiments/
│   ├── run_v0_1.py
│   └── run_v0_1_1_entailment.py
├── queries/
├── results/
├── src/adaptive_kg_reasoning/
└── tests/
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
- advance the stream through repeated windows;
- distinguish additions and expirations;
- compare recomputation vs incremental maintenance;
- measure maintenance cost and stale-result risk;
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
