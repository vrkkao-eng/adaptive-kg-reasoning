"""Controller deaths at durable-intent boundaries and live-orphan ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from test_job_service import JobService, ROOT, wait_for, terminal, kill_owned_process


def controller(root, phase):
    source = """
import json,time,sys,os
from pathlib import Path
from adaptive_kg_reasoning.jobs import JobService
root=Path(sys.argv[1]); phase=sys.argv[2]
def boundary(name, job_id):
    if name==phase:
        (root/'controller.ready').write_text(json.dumps({'job_id':job_id,'phase':name,'pid':os.getpid()}))
        while True: time.sleep(.02)
service=JobService(root, boundary=boundary, worker_pause=('before_commit',2))
service.submit({},'durable-key','server-request')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.Popen([sys.executable, "-c", source, str(root), phase], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.mark.parametrize("phase,state", [
    ("after_reservation", "failed"), ("after_preparation", "created"),
    ("after_start_intent", "interrupted"),
])
def test_controller_death_before_launch_preserves_durable_intent(tmp_path, phase, state):
    process = controller(tmp_path, phase)
    try:
        marker = tmp_path / "controller.ready"
        wait_for(marker.exists)
        job_id = json.loads(marker.read_text())["job_id"]
        kill_owned_controller(process, json.loads(marker.read_text())["pid"])
        service = JobService(tmp_path)
        try:
            row, created = service.submit({}, "durable-key", "replayed-request")
            assert not created and row["id"] == job_id and row["state"] == state
            assert not service.children
            if state != "failed":
                service.resume(job_id, "resume-request")
                assert terminal(service, job_id)["state"] == "completed"
        finally:
            service.close()
    finally:
        if process.poll() is None:
            kill_owned_process(process)
        process.stderr.close()


@pytest.mark.parametrize("orphan_action", ["kill", "continue"])
def test_live_orphan_is_not_reclaimed_by_new_controller(tmp_path, orphan_action):
    process = controller(tmp_path, "after_spawn")
    child = None
    service = None
    try:
        marker = tmp_path / "controller.ready"
        wait_for(marker.exists)
        job_id = json.loads(marker.read_text())["job_id"]
        controls = tmp_path / "jobs" / job_id / "testing"
        ready = wait_for(lambda: next(controls.glob("*.ready"), None))
        details = json.loads(ready.read_text())
        child = psutil.Process(details["pid"])
        controller_pid = json.loads(marker.read_text())["pid"]
        assert child.pid in {p.pid for p in psutil.Process(controller_pid).children(recursive=True)}
        birth = child.create_time()
        kill_owned_controller(process, controller_pid)
        service = JobService(tmp_path)
        row, created = service.submit({}, "durable-key", "replayed-request")
        assert not created and row["state"] == "running" and row["launches"] == 1
        assert service.resume(job_id, "duplicate-resume")["launches"] == 1
        assert not service.children
        assert child.create_time() == birth
        if orphan_action == "kill":
            child.kill()
            child.wait(timeout=10)
            wait_for(lambda: service.status(job_id)["state"] == "interrupted")
            service.resume(job_id, "explicit-resume")
        else:
            ready.with_suffix(".continue").write_bytes(b"continue")
        final = terminal(service, job_id)
        assert final["state"] == "completed" and final["progress"]["committed_queries"] == 56
        assert final["launches"] == (2 if orphan_action == "kill" else 1)
    finally:
        if service is not None:
            service.close()
        if process.poll() is None:
            kill_owned_process(process)
        process.stderr.close()
        if child is not None and child.is_running():
            try:
                child.kill(); child.wait(timeout=10)
            except psutil.NoSuchProcess:
                pass


def kill_owned_controller(process, controller_pid):
    """Kill our real controller, deliberately preserving its orphan worker."""
    root = psutil.Process(process.pid)
    assert controller_pid == process.pid or controller_pid in {p.pid for p in root.children(recursive=True)}
    psutil.Process(controller_pid).kill()
    process.wait(timeout=10)
