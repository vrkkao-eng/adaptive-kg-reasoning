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
