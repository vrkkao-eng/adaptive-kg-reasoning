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
