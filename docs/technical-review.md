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
| **How is adaptive selection evaluated?** | Four strategies share input, windows and request schedules. Selection sees only prior demand; bootstrap, release and decision costs are included. Model units and measured time remain separate. | [v0.3 design](v0.3-design.md), [policy tests](../tests/test_adaptive.py) |
| **How can I trace a result to its execution?** | Every new benchmark run records configuration, source/input/artifact hashes and environment. CI retains bundles, including on failure. | [reproducibility](reproducibility.md) |
| **Does placement use future evaluation data?** | No. A fixed decision is persisted from declared estimates before loading the evaluation stream. Underestimation may make the chosen node infeasible; there is no hindsight fallback. | [v0.4 design](v0.4-design.md), [placement tests](../tests/test_placement.py) |
| **Are edge/cloud latency and memory physically measured?** | No. Node and direct-link profiles model service demand and logical retained state. Measured reference timings are labeled separately; no distributed deployment or hardware calibration is claimed. | [placement accounting](v0.4-design.md), [profile](../configs/placement_profiles.json) |
| **Can failure appear as a cheap successful execution?** | Completed-prefix cost is separate from total cost. Incomplete placements have a null total, explicit failure window and unserved queries. | [placement tests](../tests/test_placement.py), [CLI/replay tests](../tests/test_placement_cli.py) |
| **How is worker crash recovery checked?** | Stop, cold rebuild and checkpoint replay share workloads/fault points; event registry, aggregate entities/counts/totals and exact facts are independently checked. Retry exhaustion and resource admission remain explicit terminal outcomes. | [v0.5.1 validation](v0.5.1-validation.md), [recovery tests](../tests/test_recovery.py) |
| **Can a changed recovery outcome silently pass CI?** | CI requires a per-case feasible-fixture contract, not just a successful experiment or matching global histogram. v0.5.2 checks typed per-window work, exact fault/restore protocol and component-time reconciliation; failures retain diagnostic acceptance reports. | [strict acceptance contract](v0.5.2-acceptance.md), [regressions](../tests/test_recovery_acceptance.py) |
| **Does this complete an enterprise agent portfolio?** | No. v0.7 adds bounded local HTTP/container operation and metrics/events. Agent/provider evaluation, observability backends, authentication and public production deployment remain absent. | [Service boundaries](v0.7-design.md) |
| **Can controller restart duplicate a worker?** | Lifetime locks and durable attempt tokens preserve live orphan ownership and revoke no-owner intents. Actual controller/worker deaths and explicit resume are tested. | [Restart tests](../tests/test_job_restart.py) |
| **Does HTTP success prove service correctness?** | No. Real HTTP, receipt accounting, independent offline oracle, non-root container and persistent-volume restart supplement original acceptance matrices. | [Operations](operations.md), [container checker](../experiments/container_service_check.py) |
| **Can the whole worker process resume?** | v0.6 kills real child processes and resumes from transactionally committed state/input/service frontiers in fresh processes; before/after-commit prefixes and restart limits are checked. | [process-resume design](v0.6-design.md), [termination/corruption tests](../tests/test_process_resume.py) |
| **Does resume mean exactly-once delivery?** | No. Unique local receipts prevent duplicate committed accounting, but uncommitted reads can repeat and external client acknowledgments/side effects are outside the transaction. | [delivery boundary](v0.6-design.md), [process experiment](../src/adaptive_kg_reasoning/resume_experiment.py) |
| **Can unsupported schemas or checkout newlines weaken recovery evidence?** | v0.6.1 checks the complete generated DDL and singleton, and versions CRLF-to-LF engine fingerprints separately from raw provenance hashes. Old jobs are not silently migrated. | [hardening contract](v0.6.1-hardening.md), [negative regressions](../tests/test_resume_hardening.py) |
| **What survives an unexpected startup failure or timeout?** | A failed launch retains bounded output, exit/category/context and a validated or unknown frontier. Aborted matrices export `not_evaluated` with their evaluated prefix rather than claiming acceptance. | [diagnostic contract](v0.6.1-hardening.md), [CLI tests](../tests/test_resume_cli.py) |
| **What does checkpoint durability prove?** | File flush/fsync and atomic replacement preserve a valid prior file on replacement failure. Checksums, trace identity and support validation reject drift/corruption. Coordinator failure and external exactly-once delivery are outside this experiment. | [checkpoint implementation](../src/adaptive_kg_reasoning/checkpoint.py), [fresh-process and replay tests](../tests/test_recovery_cli.py) |

## Core invariant

```text
incremental_facts(window_t)
==
full_recomputation_facts(window_t)
```

Optimization is accepted only after that invariant holds. This keeps the repository useful as an engineering portfolio artifact: the benchmark demonstrates not merely that one path runs faster, but that the faster path is checked against a deliberately simpler oracle.

The recovery harness now additionally checks retained event membership, aggregate
key sets, exact counts and finite totals within documented floating tolerances.
A matching current fact set alone is insufficient to prove future recovery state
is sound. These support checks apply to v0.5.1 recovery evaluation; historical
v0.2 result files are unchanged.

## Suggested verification path

1. read the v0.2 correctness criterion and scope boundaries;
2. inspect the equivalence/retraction/idempotence tests;
3. inspect `.github/workflows/tests.yml`;
4. reproduce the committed benchmark;
5. vary window overlap and observe whether the speedup trend changes as expected.
