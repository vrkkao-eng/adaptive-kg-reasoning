# Changelog

## v0.7.1 — CI runtime maintenance

- Update all three CI jobs to Node.js 24-based `actions/checkout@v7`, `actions/setup-python@v7` and `actions/upload-artifact@v6`.
- Retain Python 3.12, read-only workflow permissions, existing benchmark/service/Windows verification gates and evidence uploads.
- Align package/OpenAPI/evidence version assertions and current container examples with v0.7.1; preserve historical benchmark labels and v0.7.0 release history.
- No KG algorithm, recovery policy, API behavior or Python dependency migration is introduced. The Starlette/httpx TestClient warning remains deferred.
- Existing service roots remain bound to their original runtime/source identity; use the original environment or fresh reproduction rather than bypassing upgrade validation.

## v0.7.0 — bounded local job service

- Package an optional FastAPI/Uvicorn service with approved fixtures, strict bounded requests, durable idempotency, submit/status/resume and paginated receipts/events.
- Add exact-schema/runtime-bound registry, durable preparation/start intent, token fencing, kernel lifetime locks and one execution slot. Preserve live orphan ownership across controller death; require explicit bounded resume.
- Hold core per-job ownership before validation through execution. Lock code becomes an engine fingerprint input; old jobs need their original engine or fresh reproduction, not silent migration.
- Separate lightweight progress reads from offline oracle checks; add request/job/attempt correlation, fixed errors, accounting metrics and immutable snapshots, including reconciled stopped prefixes.
- Retain bounded diagnostics and handle owned Windows Python redirector descendants. Never reclaim unrelated/orphan processes by PID.
- Add non-root digest-pinned container, optional dependency constraints, real HTTP/volume-restart/oracle checker and separate Linux service/container and Windows ownership CI jobs.
- Initialize each service CI job's evidence parent directory before pytest creates its nested temporary directory, including on a clean checkout.
- Bound status polling retries for the documented `503 progress_unavailable` response in service tests and the smoke client; continue failing on other errors, permanent unavailability and incorrect final accounting.
- Preserve v0.5/v0.6 comparative contracts. No online stream, external exactly-once delivery, authentication, dashboard, Jev/agent or separate MSAI application.

## v0.6.1 — process-resume integrity and failure evidence

- Validate the complete generated SQLite DDL, including primary-key, NOT NULL and CHECK constraints; independently reject invalid progress singleton values before new commits.
- Version the job identity as `fixed-trace-process-resume-v2` with `utf8-crlf-to-lf-v1` engine fingerprints. Normalize only CRLF to LF for engine identity; preserve raw source/input/artifact hashes for provenance and enforce LF checkout for Python files.
- Reject old/unknown identity contracts without rewriting jobs. v0.6.0 jobs require the original engine/environment or a fresh reproduction; SQLite user version 1 and v0.5 checkpoint payloads remain unchanged.
- Retain bounded stdout/stderr, exit codes, failure categories, launch context and validated/null frontiers for unexpected child exits, protocol errors, timeouts and process creation failures. Drain both pipes to avoid diagnostic-output deadlocks.
- Reject malformed rendezvous markers, duplicate keys, bool/float window indices and non-finite JSON. Continue killing/reaping unexpected or stuck children without treating them as accepted fault scenarios.
- Export `acceptance.json` with `not_evaluated` and the completed prefix/aborted case when the full matrix cannot run. Keep gate mismatches `failed`, not `not_evaluated`; no terminal manifest retains `pending`.
- Add schema, portability, diagnostic-output, timeout, protocol and partial-matrix regressions; update the existing CI matrix label/path and English documentation without new dependencies or jobs.
- No API, worker ownership service, live status endpoint, dashboard, agent/provider or external-delivery guarantee is added.

## v0.6.0 — fixed-trace process resume

- Add real parent-controlled process termination and fresh-process continuation at before-update, after-update, before-commit and after-commit boundaries.
- Couple validated KG state, filtered input frontier, applied/service cursors, unique local window receipts and incremental audit in SQLite transactions.
- Bind jobs to exact input/profile/config/trace/request/engine identities; reject mismatched schemas, corrupt prefixes and stale writers without repair.
- Compare uninterrupted, stop and resume execution, including bounded persistent restart exhaustion, first/last windows and idempotent completed continuation.
- Retain recursive hashed evidence, launch/exit transcripts, committed receipts, incremental audit exports and failed manifests.
- Reuse the schema-1 snapshot payload codec without changing v0.5 checkpoint bytes or recovery policies. Keep the v0.5 benchmark label separate from the current package version.
- Add a bounded v0.6 process matrix to the existing CI job and English delivery/measurement documentation. Use standard-library SQLite; no new package dependency.
- Local receipt accounting is not external exactly-once delivery, a live/deployed API, a power-loss test, agent/provider integration or the separate MSAI application.

## v0.5.2 — strict recovery evidence acceptance

- Derive expected audit order and per-window work counters from declared fault boundaries, retry budgets, queries and checkpoint cadence, not observed outcome totals.
- Check fault locations, retry numbering, cold-start reasons, latest restore cursors, replay targets and checkpoint publications with exact event field types.
- Require all counter fields as non-negative integers; reject booleans, floats and missing zero fields. Reconcile per-window state/query checks, work counters and published bytes.
- Require finite non-negative component times, detail/summary reconciliation, disjoint work/completed totals and correct recovery subsets with explicit accounting-roundoff tolerance.
- Identify acceptance contract v2 while preserving schema-1 checkpoints; retain strict-JSON diagnostics, failed manifests and evidence on gate failure.
- Add negative mutations, hand-checked protocol expectations, custom cadence, first/last-window faults, persistent faults and failure-artifact regression tests.
- Update English documentation, package version and the existing CI benchmark label/path. No new dependency, job, algorithm, recovery policy, agent or deployment feature.

## v0.5.1 — retained-state validation and recovery acceptance

- Close a recovery-check blind spot: validate aggregate entity keys, exact integer counts and finite totals against independent full-window support, even when cached facts still match.
- Reuse documented checkpoint roundoff tolerances without repairing totals; require exact cached and support-derived fact agreement.
- Precompute aggregate references outside component timing, including checkpoint restore validation.
- Add opt-in per-case acceptance for feasible recovery fixtures; check outcomes, service prefixes, retries, query accounting and audit evidence rather than aggregate outcome counts alone.
- Preserve `acceptance.json`, observed results and failed manifests on gate failure; require the gate in CI and keep exploratory capacity-failure runs valid when it is disabled.
- Add corruption, tolerance/threshold, acceptance/accounting and deterministic gated-replay regressions.
- Update English reviewer documentation to distinguish KG engineering evidence from agent evaluation, durable observability, security and deployed-service claims; state Python 3.12 as the tested runtime.
- Correct the stale package version from `0.1.0` to `0.5.1` and align the current recovery manifest milestone with it; historical benchmark labels remain unchanged.
- Keep checkpoint schema 1, recovery policies, v0.4 behavior and historical benchmark artifacts unchanged. No agent/provider, OS-process resume or deployment is introduced.

## v0.5.0 — bounded checkpoint recovery

- Compare stop-on-failure, current-window cold rebuild and checkpoint replay on the same fixed-site workloads and injected crashes.
- Publish local snapshots with file flush/fsync and atomic replacement; validate schema, checksum, trace identity and retained support state on restore.
- Preserve applied-window cursor and floating-point aggregates; replay state without re-serving historical queries.
- Bound per-window retries and expose persistent failure, invalid checkpoints, no-checkpoint cold starts and unrecoverable resource exhaustion.
- Record component timings, failed-attempt/replay work, checkpoint I/O, served/unserved queries and ordered audit events in hashed bundles.
- Add snapshot integrity, publication failure, fault boundary, oracle, fresh-process and replay regression coverage plus CI recovery evidence.
- Keep v0.4 fixed placement baselines intact and document English recovery semantics and limitations.

## v0.4.0 — resource-aware fixed placement simulation

- Compare fixed edge, fog and cloud placement with a budget-aware, estimate-only selector.
- Hold incremental reasoning semantics constant to isolate placement effects.
- Model node compute, logical retained-memory budgets, direct-link RTT and byte transfer costs with explicit units.
- Persist planning decisions before reading the evaluation stream; reject invalid profiles and record no-feasible-candidate outcomes.
- Stop simulated service at the first memory-budget violation, expose unserved queries and keep partial costs separate from completed totals.
- Verify a shared reference trace against full recomputation; keep measured host timings separate from simulated service demand.
- Preserve profiles, planning, actual payload counts, workload schedules and hashed evidence for deterministic replay.
- Add hand-calculated accounting, underestimation, failure, replay and CI smoke tests; document the simulator's limits in English.
- No distributed deployment, runtime migration, agent/LLM integration or thesis application is introduced.

## v0.3.0 — cost-aware materialisation experiment

- Align recomputation, query-time, incremental and adaptive strategies on identical window/query workloads.
- Add a prior-demand policy with explicit event, result-read, retention and switching cost coefficients.
- Charge full bootstrap after query-time mode; include measured selection and release costs.
- Keep synthetic model units separate from measured milliseconds and the retained-state memory proxy.
- Check every answer and maintained state against full recomputation, including expiration and threshold boundaries.
- Add idle/sparse/dense/bursty workloads, repeated runs with seeded strategy order, decision traces and CI smoke coverage.
- Preserve v0.2 benchmark history and the original v0.4 placement direction; no agent/LLM or thesis application scope.

## v0.2.4 — reproducible benchmark evidence

- Preserve the existing oracle and incremental comparison; write fresh run bundles instead of overwriting v0.2.3 references.
- Record input/artifact/source hashes, actual event count, configuration, environment and available Git identity.
- Retain failed-run manifests and reject empty input/output-directory reuse.
- Upload CI benchmark bundles and JUnit results, including on failure.
- Correct evidence links and the implemented module map; document replay and version/publication boundaries.

## v0.2.3 — comparative recomputation vs incremental benchmark

- Added a per-window comparison record joining v0.2.1 recomputation and v0.2.2 incremental maintenance.
- Added aggregate median, p95, mean, and total timing for both strategies.
- Added per-window and total speedup ratios.
- Added correctness metrics including symmetric difference, false additions, and missed facts.
- Added materialised fact addition/retraction totals, affected-entity counts, and a documented Python state-size proxy.
- Added a minimal fact-read timing proxy; it is explicitly not presented as SPARQL/RSP query latency.
- Added default overlap scenarios `3600/60`, `3600/300`, and `3600/900`.
- Added detailed and summary CSV outputs for reproducible reference runs.
- CI reference: 26 tests passed; all benchmark windows were equivalent to full recomputation.
- CI total speedup was 17.613× (3600/60), 5.448× (3600/300), and 2.176× (3600/900).

### Scope boundary

v0.2.3 compares maintenance strategies for one transparent aggregate-derived state. Timing and object-size numbers are environment-specific reference measurements and are not general claims about RDF Stream Processing systems.

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
