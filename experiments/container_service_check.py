"""Build-independent container smoke + persistent-volume restart evidence.

Uses task-specific container/volume names and removes only resources it creates.
An image must already have been built; no remote publication is performed.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import uuid

from service_smoke import request, smoke


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=60).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="adaptive-kg-service:v071")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    suffix = uuid.uuid4().hex[:12]
    name, volume = "kg-check-" + suffix, "kg-data-" + suffix
    manifest = {"status": "running", "image": args.image, "container": name, "volume": volume}
    created_volume, created_container = False, False
    try:
        docker("volume", "create", volume)
        created_volume = True
        docker("run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-v", volume + ":/jobs", args.image)
        created_container = True
        manifest["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", args.image)
        manifest["environment"] = json.loads(docker("exec", name, "python", "-c",
            "import json,platform,sqlite3; from adaptive_kg_reasoning.job_registry import runtime_identity; print(json.dumps({'platform':platform.platform(),'runtime':runtime_identity()}))"))
        port = docker("port", name, "8000/tcp").split(":")[-1]
        base = "http://127.0.0.1:" + port
        def ready():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if request(base, "GET", "/readyz")[0] == 200:
                        return
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            raise TimeoutError("Container did not become ready")
        ready()
        first = smoke(base)
        manifest["first"] = first
        assert docker("exec", name, "id", "-u") == "10001"
        docker("restart", name)
        # Docker may allocate a different ephemeral published port on restart.
        port = docker("port", name, "8000/tcp").split(":")[-1]
        base = "http://127.0.0.1:" + port
        ready()
        replay = smoke(base)
        assert replay["job_id"] == first["job_id"] and replay["launches"] == first["launches"]
        # Use the installed independent offline oracle on stopped/completed state,
        # not only HTTP success codes or the demo's known receipt count.
        script = "from pathlib import Path; from adaptive_kg_reasoning.process_resume import inspect_job; x=inspect_job(Path('/jobs/jobs')/'" + first["job_id"] + "'); assert x['served_queries']==x['requested_queries']==56; print('oracle validated')"
        assert docker("exec", name, "python", "-c", script) == "oracle validated"
        manifest.update(status="passed", first=first, after_restart=replay, non_root_uid=10001, oracle="validated")
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        cleanup_errors = []
        if created_container:
            try:
                (args.output_dir / "container.log").write_text(docker("logs", name))
            except Exception as exc:
                cleanup_errors.append(str(exc))
            try:
                docker("rm", "-f", name)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if created_volume:
            try:
                docker("volume", "rm", volume)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        manifest["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            manifest["status"] = "failed"
        (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if cleanup_errors:
            raise RuntimeError("Test resource cleanup failed; inspect manifest")


if __name__ == "__main__":
    main()
