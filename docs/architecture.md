# Architecture — v0.1

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
