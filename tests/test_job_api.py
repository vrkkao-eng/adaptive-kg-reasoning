"""Optional API contract tests; core benchmark installation stays independent."""
import json
from pathlib import Path
import sys
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaptive_kg_reasoning.api import create_app
from test_job_service import terminal, paused


def test_api_end_to_end_contract(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/healthz").json()["status"] == "alive"
        assert client.get("/readyz").status_code == 200
        assert client.get("/openapi.json").json()["info"]["version"] == "0.7.0"
        response = client.post("/jobs", json={}, headers={"Idempotency-Key": "demo"})
        assert response.status_code == 201 and response.headers["x-request-id"]
        job_id = response.json()["id"]
        terminal(client.app.state.service, job_id)
        assert client.post("/jobs", json={}, headers={"Idempotency-Key": "demo"}).status_code == 200
        assert client.post("/jobs", json={"threshold": 451}, headers={"Idempotency-Key": "demo"}).status_code == 409
        assert client.post(f"/jobs/{job_id}/resume").json()["launches"] == 1
        page = client.get(f"/jobs/{job_id}/receipts?limit=2").json()
        assert len(page["items"]) == 2 and page["next_after"] == 1
        events = client.get(f"/jobs/{job_id}/events").json()["items"]
        assert any(e["event"] == "worker_finished" for e in events)
        assert "kg_committed_queries 56" in client.get("/metrics").text


@pytest.mark.parametrize("body", [
    {"fixture": "../../secret"}, {"width": True}, {"flush": "true"}, {"width": 5, "slide": 6},
    {"threshold": "NaN"}, {"command": "shell"}, {"input_path": "C:/secret"}, {"max_retries": 999},
])
def test_api_rejects_unsafe_and_untyped_body_without_echo(tmp_path, body):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/jobs", json=body, headers={"Idempotency-Key": "safe"})
        assert response.status_code == 422
        assert set(response.json()) == {"error", "request_id"}
        assert client.app.state.service.registry.list() == []


@pytest.mark.parametrize("body,status", [
    ('{"width":30,"width":30}', 422), ('{"threshold":NaN}', 422), ('x' * 2049, 413),
])
def test_body_bound_and_strict_json(tmp_path, body, status):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/jobs", content=body, headers={"content-type": "application/json", "Idempotency-Key": "safe"})
        assert response.status_code == status
        assert client.app.state.service.registry.list() == []


def test_unknown_ids_pagination_and_missing_key(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/jobs", json={}).status_code == 422
        for job_id in ("not-a-job", str(uuid.uuid4())):
            assert client.get(f"/jobs/{job_id}").status_code == 404
            assert client.post(f"/jobs/{job_id}/resume").status_code == 404
        row = client.post("/jobs", json={}, headers={"Idempotency-Key": "demo"}).json()
        assert client.get(f"/jobs/{row['id']}/receipts?limit=101").status_code == 422
        assert client.get(f"/jobs/{row['id']}/events?after=-1").status_code == 422


def test_busy_and_repeated_resume_no_new_worker(tmp_path):
    with TestClient(create_app(tmp_path, worker_pause=("before_update", 0))) as client:
        row = client.post("/jobs", json={}, headers={"Idempotency-Key": "demo"}).json()
        paused(client.app.state.service, row["id"])
        assert client.post("/jobs", json={}, headers={"Idempotency-Key": "other"}).status_code == 409
        assert client.post(f"/jobs/{row['id']}/resume").json()["launches"] == 1
        assert len(client.app.state.service.children) == 1
