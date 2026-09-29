# Changelog

## v0.2.2 — incremental support-state maintenance

- Added per-plug incremental support state using active-event registry, count, and total load.
- Added idempotent transition handling so replayed additions/expirations do not double-count.
- Added fact additions when averages cross the threshold upward.
- Added fact retractions when expirations or updates remove sufficient support.
- Added strict per-window equivalence checks against the v0.2.1 full recomputation oracle.
- Added divergence metrics: symmetric difference, false additions, and missed facts.
- Added a reproducible incremental experiment writing `results/incremental_v0_2_2.csv`.

### Scope boundary

v0.2.2 maintains one transparent aggregate-derived state only. It is not a general truth-maintenance engine, arbitrary continuous SPARQL evaluator, or incremental OWL-RL reasoner.

## v0.2.1 — full-window recomputation oracle

- Added a reference implementation that recomputes per-plug load aggregates from the complete current window.
- Added transparent `count`, `total_load`, and `average_load` aggregates.
- Added `HighRecentConsumption` derivation from the recomputed window only.
- Added per-window timing and result metrics for the oracle path.
- Added tests for threshold behaviour, non-load filtering, empty windows, and reference metrics.
- Added a reproducible recomputation experiment writing `results/recompute_v0_2_1.csv`.

### Scope boundary

v0.2.1 deliberately does not reuse prior-window state or `added` / `expired` deltas. It is the independent correctness oracle for the incremental implementation planned in v0.2.2.

## v0.2.0 — deterministic sliding-window mechanics

- Added an event model for the DEBS-shaped stream.
- Added half-open sliding-window semantics: `[start, end)`.
- Added explicit per-transition `added` and `expired` event sets.
- Added optional flush mode so every active event can be observed expiring.
- Added validation for duplicate IDs, out-of-order event time, invalid widths/slides, and window gaps.
- Added property-filtered CSV loading for later load-only reasoning experiments.
- Added a reproducible window-trace experiment.
- Added tests for exact boundary behaviour, event lifecycle uniqueness, and transition reconstruction.

### Scope boundary

v0.2.0 implements window mechanics only. It does not yet implement full recomputation, incremental aggregates, fact retractions, result-equivalence benchmarking, or formal RDF Stream Processing semantics.

# Changelog

## v0.1.1 — semantic entailment baseline

- Promoted `owlrl` from a planned optional dependency to an executable baseline.
- Extended the class hierarchy to `SmartPlug ⊑ EnergyDevice ⊑ Device`.
- Added OWL-RL deductive closure as a separate reasoning path from Python window derivations.
- Added tests for transitive class membership and SOSA sensor entailment.
- Added a reproducible entailment experiment reporting closure size, inferred triples, and closure time.
- Committed a GitHub Actions reference run: 40,229 explicit triples → 79,808 closure triples, with 39,579 inferred triples.
- CI verification: 6 tests passed on Python 3.12.
- Kept aggregation/window rules outside OWL-RL to avoid conflating temporal aggregation with ontology entailment.

### Scope boundary

v0.1.1 does not yet implement incremental entailment, truth maintenance, formal RDF Stream Processing window semantics, or distributed reasoning.

## v0.1.0 — baseline scaffold

- Added deterministic synthetic stream following the DEBS 2014 base-stream field structure.
- Added SOSA-aligned RDF mapping and a minimal application vocabulary.
- Added stable, semi-dynamic, and volatile inference baselines.
- Added full, query-time, and selective materialisation strategies.
- Added benchmark outputs for preprocessing cost, query latency, graph growth, and Python allocation peak.
- Added automated tests asserting answer consistency across strategies.
- Added static topology and 10-second stream fixtures for inspection.

### Known limitations

- No incremental truth maintenance yet.
- No formal RSP window semantics.
- No cost-based adaptive policy yet.
- No edge/fog/cloud placement model yet.
