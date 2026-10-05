# Architecture — v0.1 through v0.3

## Current execution path

```text
deterministic load events -> shared sliding windows + request schedule
                                      |
           recompute / query-time / incremental / adaptive
                                      |
                    full-result queries + state checks
                                      |
       independent oracle equivalence + measured component costs
                                      |
               CSV trace/summary + hashed run manifest
```

The adaptive policy predicts demand from the previous window only and can release
or rebuild incremental state. The harness keeps oracle work outside strategy
timings. [v0.3 design](v0.3-design.md) specifies cost accounting and scope;
[v0.2 design](v0.2-design.md) specifies window and maintenance semantics.

## Original v0.1 baseline

```text
synthetic DEBS-shaped CSV
          |
          v
RDF mapping (SOSA-aligned observations + small application vocabulary)
          |
          +--------------------------+
          |                          |
          v                          v
static topology rules        window aggregation rules
          |                          |
          +------------+-------------+
                       v
          reasoning strategy layer
        /          |             \
     full      query-time      selective
        \          |             /
         +---------+------------+
                   v
       benchmark / reproducible results
```

## Deliberate simplifications

- No Kafka, MQTT, Flink, Spark, or production stream broker.
- No claim of complete RDF Stream Processing semantics.
- Query-time mode uses transparent targeted derivation, not a complete OBDA/query-rewriting system.
- Edge/fog/cloud constraints are not modelled in v0.1.
- Window rules are implemented in Python to keep their cost visible and auditable.

The purpose of v0.1 is to establish a measurable baseline before introducing incremental maintenance and execution placement.
