# Local job-service operations

This unauthenticated demonstration is single-host and loopback-only. Do not expose
it publicly, through a reverse proxy or to untrusted networks. Request/job bounds
are not complete denial-of-service controls. Local filesystem operators are
trusted; symlink attacks and multi-tenant workloads are out of scope.

## Installed package

Python 3.12 and SQLite 3.37+ are required. Core benchmarks need no HTTP extras.
In a virtual environment, from this repo:

```bash
pip install -c constraints-service.txt ".[service,test]"
kg-service --root results/runs/service-demo --port 8000
```

The entry point binds `127.0.0.1`, one worker. From another terminal:

```bash
python experiments/service_smoke.py --url http://127.0.0.1:8000 --output results/runs/http-smoke.json
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -H "Idempotency-Key: my-demo" -d '{}'
```

Use `curl.exe` on PowerShell if `curl` is an alias. The smoke key is
`container-smoke`; repeat runs intentionally return the same job. Defaults produce
seven windows / 56 checked local requests. There is no upload/arbitrary-path API.

| Endpoint | Purpose |
| --- | --- |
| `POST /jobs` | Required `Idempotency-Key`; new 201 or replay 200 |
| `GET /jobs/{id}` | Lifecycle, launch budget, committed/unserved progress |
| `POST /jobs/{id}/resume` | Explicit continuation; active/completed requests do not relaunch |
| `GET /jobs/{id}/receipts?after=-1&limit=100` | Local receipts, snapshot cursor and next page |
| `GET /jobs/{id}/events?after=0&limit=100` | Use last sequence for next page |
| `/healthz`, `/readyz` | Alive / controller-monitor and registry readiness |
| `/metrics`, `/docs`, `/openapi.json` | Metrics text, interactive and machine-readable schemas |

Errors return fixed categories and server request IDs without echoing input,
paths or exception details. 409 means busy/conflict/terminal/exhausted; 422 rejects
input; 413 rejects bodies above 2,048 bytes, including chunked input. 503 means
operational data/storage/monitor/internal failure, not accepted work. Readiness
does not perform a full per-job oracle scan or guarantee spare capacity.

For status polling only, `503 progress_unavailable` can be transient during a
SQLite commit or owned recovery. The smoke client retries that specific code
within a 30-second deadline; persistent unavailability fails verification.
Other errors, including invalid progress and storage failures, are not hidden.

## Non-root container and persistent state

```bash
docker build -t adaptive-kg-service:v071 .
docker volume create kg-demo-data
docker run --name kg-demo -p 127.0.0.1:8000:8000 -v kg-demo-data:/jobs adaptive-kg-service:v071
```

UID 10001 runs the installed package. New named volumes inherit `/jobs` ownership;
existing bind mounts need suitable permissions. Internal container networking is
all interfaces, but the host mapping is loopback only; do not use `-p 8000:8000`.
Stop/start/restart with the same compatible image and volume to preserve keys and
progress. Runtime/source changes cannot use an existing root without migration.
The base image is digest-pinned, dependencies constrained to tested versions.
No image publication, vulnerability scan or cloud deployment is implied.

```bash
python experiments/container_service_check.py --output-dir results/runs/container-demo
```

Use a fresh output directory. This creates uniquely named temporary test resources,
checks real HTTP, UID, duplicate/resume behavior, volume restart and independent
offline oracle, and removes only its own container/volume. Manifest and container
log survive cleanup, including cleanup failures. User demo volumes are untouched.

## Failure response

1. Save request/job/attempt IDs, status and events. Do not use a new key to bypass
   failures or create competing work.
2. Resume an `interrupted` job explicitly after its owner exits, within three
   launch intents. Busy means wait, not delete locks.
3. Capacity/integrity failures are terminal. Inspect operator diagnostics and
   reproduce with an approved configuration/new root; never rewrite identities.
4. Restart against the same compatible root. Live orphans continue owning their
   slot; prepared jobs need resume; interrupted preparation fails closed.
5. Restore the original runtime on mismatch or reproduce fresh. Stop controller
   and workers before a filesystem backup. A live SQLite copy without journals
   is not a validated backup. No job-deletion API is provided.

`jobs/<id>/launches/<attempt>.json` contains bounded owned-child diagnostics.
`evidence/<attempt>/manifest.json` marks complete immutable export; absence means
incomplete evidence. `progress.sqlite` is the mutable recovery source;
`registry.sqlite` stores lifecycle/events separately. Receipts are not external
acknowledgments. Use `inspect_job` for a stopped, owned job's full schema/support/
oracle validation, not live polling. Orphans lack hard out-of-boundary timeout
supervision; see the [design limitations](v0.7-design.md).

## Verification

```bash
pytest -q
pytest -q tests/test_job_service.py tests/test_job_api.py tests/test_job_restart.py tests/test_job_http.py
python experiments/run_v0_6_resume.py --events 300 --width 120 --slide 30 --flush --output-dir results/runs/resume-demo
python experiments/run_v0_5_recovery.py --events 1000 --width 120 --slide 30 --flush --require-expected-outcomes --output-dir results/runs/recovery-demo
```

API/HTTP tests skip without optional dependencies. Linux CI explicitly installs
extras and verifies the container; Windows CI checks actual ownership/death.
Original benchmarks and 60-case exception / 40-case process matrices remain.
Configured CI is not a successful remote run until pushed checks pass.
