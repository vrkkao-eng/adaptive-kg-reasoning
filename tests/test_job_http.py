"""Real loopback HTTP operation, distinct from in-process API contract tests."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error

import pytest
pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")

from test_job_service import ROOT, kill_owned_process
sys.path.insert(0, str(ROOT / "experiments"))
from service_smoke import request, smoke
import service_smoke as smoke_module


def http_error(code=503, category="progress_unavailable"):
    import io
    import json
    return urllib.error.HTTPError("http://127.0.0.1/jobs/job", code, "Unavailable", None,
                                  io.BytesIO(json.dumps({"error": category}).encode()))


def test_smoke_retries_only_unavailable_status_and_preserves_accounting(monkeypatch):
    reads = 0
    def fake(base, method, path, body=None, key=None):
        nonlocal reads
        if path == "/readyz":
            return 200, {"status": "ready"}
        if method == "GET" and path == "/jobs/job":
            reads += 1
            if reads <= 2:
                raise http_error()
            return 200, {"state": "completed", "progress": {"committed_queries": 56, "unserved_queries": 0}}
        if path.endswith("/receipts"):
            return 200, {"items": [{"window_index": i} for i in range(7)]}
        return 200, {"id": "job", "launches": 1}
    monkeypatch.setattr(smoke_module, "request", fake)
    assert smoke("http://127.0.0.1", poll_interval=.001)["committed_queries"] == 56
    assert reads == 3


@pytest.mark.parametrize("code,category", [(503, "progress_invalid"), (503, "storage_unavailable"),
                                          (503, "monitor_unavailable"), (500, "progress_unavailable")])
def test_smoke_poll_does_not_retry_other_failures(monkeypatch, code, category):
    def fail(*args, **kwargs):
        raise http_error(code, category)
    monkeypatch.setattr(smoke_module, "request", fail)
    with pytest.raises(urllib.error.HTTPError):
        smoke_module.poll_status("http://127.0.0.1", "job")


def test_smoke_persistent_busy_read_never_accepts_creation_as_completion(monkeypatch):
    def fake(base, method, path, body=None, key=None):
        if path == "/readyz":
            return 200, {"status": "ready"}
        if method == "POST":
            return 200, {"id": "job", "state": "completed"}
        raise http_error()
    monkeypatch.setattr(smoke_module, "request", fake)
    with pytest.raises(AssertionError):
        smoke("http://127.0.0.1", timeout=.05, poll_interval=.001)


def test_loopback_server_and_real_http_smoke(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    log = tmp_path / "server.log"
    with log.open("wb") as stream:
        process = subprocess.Popen([sys.executable, "-m", "adaptive_kg_reasoning.api",
                                    "--root", str(tmp_path / "jobs"), "--port", str(port)],
                                   env=env, stdout=stream, stderr=stream,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, log.read_text()
                try:
                    if request(base, "GET", "/readyz")[0] == 200:
                        break
                except (OSError, urllib.error.URLError):
                    assert time.monotonic() < deadline, log.read_text()
                    time.sleep(.1)
            assert smoke(base)["committed_queries"] == 56
        finally:
            kill_owned_process(process)
