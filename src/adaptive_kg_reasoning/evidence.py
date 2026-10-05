"""Versioned, self-contained benchmark evidence bundles."""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_run_dir(root: Path, output_dir: Path | None = None) -> Path:
    path = output_dir or root / "results" / "runs" / str(uuid.uuid4())
    # Refuse reuse, including failed runs, rather than silently overwrite evidence.
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Cannot publish an empty benchmark table")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def source_identity(root: Path) -> dict:
    files = []
    for name in ("src", "experiments", "tests", "docs", ".github", "queries", "data/ontology", "configs"):
        files.extend(p for p in (root / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    files.extend(root / name for name in ("README.md", "CHANGELOG.md", "requirements.txt"))
    hashes = {p.relative_to(root).as_posix(): sha256(p)
              for p in sorted(files) if p.exists()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()

    def git(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
                text=True, timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"),
            "dirty": None if status is None else bool(status),
            "source_sha256": digest, "files_sha256": hashes}


def new_manifest(root: Path, run_dir: Path, *, benchmark: str, config: dict) -> dict:
    return {
        "schema_version": 1, "run_id": run_dir.name, "benchmark": benchmark,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "config": config,
        "command": [Path(sys.argv[0]).name, *sys.argv[1:]],
        "source": source_identity(root),
        "environment": {"python": platform.python_version(),
                        "platform": platform.platform(),
                        "dependencies": {name: version(name) for name in ("rdflib", "owlrl", "psutil", "pytest")}},
    }


def save_manifest(run_dir: Path, manifest: dict) -> None:
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["artifacts_sha256"] = {
        p.name: sha256(p) for p in sorted(run_dir.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
