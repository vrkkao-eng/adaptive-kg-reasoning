"""HTTP contract smoke test against an already running loopback demonstration."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def request(base, method, path, body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.load(response)


def smoke(base, key="container-smoke"):
    assert request(base, "GET", "/readyz")[0] == 200
    status, created = request(base, "POST", "/jobs", {}, key)
    assert status in (200, 201)
    job_id = created["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, current = request(base, "GET", "/jobs/" + job_id)
        if current["state"] in ("completed", "failed", "interrupted"):
            break
        time.sleep(0.05)
    assert current["state"] == "completed", current
    assert current["progress"]["committed_queries"] == 56
    assert current["progress"]["unserved_queries"] == 0
    _, repeated = request(base, "POST", "/jobs", {}, key)
    assert repeated["id"] == job_id and repeated["launches"] == 1
    _, resumed = request(base, "POST", "/jobs/" + job_id + "/resume")
    assert resumed["launches"] == 1
    _, receipts = request(base, "GET", "/jobs/" + job_id + "/receipts")
    assert [r["window_index"] for r in receipts["items"]] == list(range(7))
    return {"status": "passed", "job_id": job_id, "committed_queries": 56, "launches": 1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = smoke(args.url)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
