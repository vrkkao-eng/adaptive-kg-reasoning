# Architecture — v0.1 through v0.5

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

## v0.4 placement path

```text
declared profiles + workload estimates -> fixed-site decision + planning.csv
                                                       |
evaluation stream -> shared incremental/oracle trace    |
                             |                         |
                   edge / fog / cloud cost models      |
                             |                         |
                  fixed-site outcomes <--- selected baseline reference
                             |
                feasibility + partial/complete costs + evidence
```

Planning precedes loading evaluation data. `resources.py` validates profiles and
logical-memory estimates; `network_cost.py` accounts for direct-link service
demand; `placement.py` checks a shared reference trace and evaluates fixed sites.
No node executes remotely. Host measurements and modeled milliseconds remain
separate. Budget violations stop modeled service without migration or recovery.
See the [v0.4 accounting contract](v0.4-design.md).

## v0.5 recovery path

```text
shared input/windows/queries + fixed node + declared fault scenario
                              |
          stop / cold rebuild / checkpoint replay
                              |
       state update -> local snapshot -> checked query readout
              |             |
       injected crash -> bounded restore/replay or terminal outcome
                              |
             oracle checks + component costs + audit bundle
```

`checkpoint.py` publishes a complete local state snapshot and validates its
integrity/support. `recovery.py` discards worker state on injected faults,
restores an applied-window cursor, replays deltas without re-serving past
queries, and bounds retries. Resource admission failures remain terminal.
The coordinator survives fault injection; external acknowledgments are outside
the checkpoint transaction. [v0.5 design](v0.5-design.md) defines the contract.

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
