# Technical reviewer evidence map

This page maps likely review questions to the implementation and benchmark evidence in the repository. It is not a production-readiness claim.

| Reviewer question | Short answer | Evidence to inspect |
| --- | --- | --- |
| **Why is incremental maintenance faster here?** | Adjacent windows overlap. The incremental path updates support state only for entering/expiring events and affected plugs, while the oracle recomputes the complete current window. As overlap falls, the measured advantage also falls. | [v0.2 design](v0.2-design.md), [benchmark summary](../results/benchmark_v0_2_3_summary.md) |
| **How do you know the optimization is correct?** | Performance is secondary to a strict invariant: every incremental window result must equal the independent full-recomputation result. Any non-zero symmetric difference is a correctness failure. | [incremental tests](../tests/test_incremental.py), [metric tests](../tests/test_metrics.py) |
| **Is 17.6× a general performance claim?** | No. It is a deterministic GitHub Actions reference result for one synthetic workload/configuration. Runtime, stream shape, entity count and overlap affect the result. | README benchmark caveat and committed result artifacts |
| **Is this a new RDF Stream Processing engine?** | No. The repository deliberately isolates one transparent window-dependent materialised state. It does not implement arbitrary continuous SPARQL, late-event semantics or distributed execution. | README “What this project does not claim”; [architecture](architecture.md) |
| **Why use Python rather than Kafka/Flink/Spark?** | v0.2 is an experiment about maintenance cost and correctness. Keeping window transitions and support state explicit makes the comparison auditable before introducing infrastructure whose own costs would confound the baseline. | [architecture](architecture.md), [v0.2 design](v0.2-design.md) |
| **What is semantic reasoning vs procedural aggregation here?** | OWL-RL provides a small entailment baseline for stable class semantics; dynamic averages/window state are maintained procedurally. The project does not mislabel SHACL or aggregation as logical inference. | README v0.1.1 section and architecture docs |
| **What would invalidate the current conclusion?** | Different overlap, churn, entity counts, threshold-crossing frequency, late/out-of-order events or a different runtime may erase the measured benefit. Those are experimental factors, not hidden assumptions. | [v0.2 design](v0.2-design.md) |
| **What is the next research/engineering step?** | Use the measured maintenance-cost term as one input to an adaptive materialisation policy; do not assume incremental maintenance is always preferable. | v0.2 design “Link to later versions” |

## Core invariant

```text
incremental_facts(window_t)
==
full_recomputation_facts(window_t)
```

Optimization is accepted only after that invariant holds. This keeps the repository useful as an engineering portfolio artifact: the benchmark demonstrates not merely that one path runs faster, but that the faster path is checked against a deliberately simpler oracle.

## Suggested verification path

1. read the v0.2 correctness criterion and scope boundaries;
2. inspect the equivalence/retraction/idempotence tests;
3. inspect `.github/workflows/tests.yml`;
4. reproduce the committed benchmark;
5. vary window overlap and observe whether the speedup trend changes as expected.
