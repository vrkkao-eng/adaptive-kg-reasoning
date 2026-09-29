# Changelog

## v0.1.1 — semantic entailment baseline

- Promoted `owlrl` from a planned optional dependency to an executable baseline.
- Extended the class hierarchy to `SmartPlug ⊑ EnergyDevice ⊑ Device`.
- Added OWL-RL deductive closure as a separate reasoning path from Python window derivations.
- Added tests for transitive class membership and SOSA sensor entailment.
- Added a reproducible entailment experiment reporting closure size, inferred triples, and closure time.
- Kept aggregation/window rules outside OWL-RL to avoid conflating temporal aggregation with ontology entailment.

### Scope boundary

v0.1.1 does not yet implement incremental entailment, truth maintenance, formal RDF Stream Processing window semantics, or distributed reasoning.

# Changelog

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
- No complete OWL-RL, rule-engine, or OBDA baseline yet.
