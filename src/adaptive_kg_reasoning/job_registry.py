"""Durable local control intent, deliberately separate from KG progress SQLite."""
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import time
import uuid

from .checkpoint import canonical_bytes
from .process_resume import engine_hashes, schema_signature

SERVICE_CONTRACT = "fixed-trace-job-service-v1"
STATES = ("preparing", "created", "starting", "running", "interrupted", "completed", "failed")
DDL = {
    "meta": "CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), contract TEXT NOT NULL, runtime TEXT NOT NULL) STRICT",
    "jobs": """CREATE TABLE jobs (
        id TEXT PRIMARY KEY, key_sha256 TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('preparing','created','starting','running','interrupted','completed','failed')),
        token TEXT, launches INTEGER NOT NULL CHECK(launches>=0),
        metadata_json TEXT, failure TEXT, created_at REAL NOT NULL
    ) STRICT""",
    "events": """CREATE TABLE events (
        sequence INTEGER PRIMARY KEY, job_id TEXT NOT NULL, token TEXT,
        payload TEXT NOT NULL
    ) STRICT""",
}


class ServiceError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def valid_id(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def runtime_identity():
    root = Path(__file__).parent
    sources = {name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
               for name in ("__init__.py", "job_registry.py", "job_status.py", "jobs.py", "service_worker.py", "api.py")}
    dependencies = {}
    for name in ("rdflib", "owlrl", "psutil", "fastapi", "uvicorn", "pydantic", "starlette"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = None
    return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "engine": engine_hashes(), "service": sources, "dependencies": dependencies}


class Registry:
    def __init__(self, root, *, initialize=False):
        self.root = Path(root).resolve()
        self.path = self.root / "registry.sqlite"
        if initialize:
            existed = self.path.exists()
            with self.database(create=True, transaction=False) as db:
                if not existed:
                    db.execute("PRAGMA journal_mode=DELETE")
                    db.execute("BEGIN IMMEDIATE")
                    for sql in DDL.values():
                        db.execute(sql)
                    db.execute("PRAGMA user_version=1")
                    db.execute("INSERT INTO meta VALUES (1,?,?)", (SERVICE_CONTRACT, canonical_bytes(runtime_identity()).decode()))
                    db.commit()
        with self.database() as db:
            definitions = dict(db.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))
            if (db.execute("PRAGMA user_version").fetchone()[0] != 1 or
                    set(definitions) != set(DDL) or any(schema_signature(definitions[n]) != schema_signature(sql) for n, sql in DDL.items())):
                raise ServiceError("registry_invalid", 503)
            meta = [tuple(r) for r in db.execute("SELECT * FROM meta")]
            if meta != [(1, SERVICE_CONTRACT, canonical_bytes(runtime_identity()).decode())]:
                raise ServiceError("runtime_mismatch", 503)

    @contextmanager
    def database(self, *, write=False, create=False, transaction=True):
        uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
        db = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            if transaction:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if transaction:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, job_id):
        if not valid_id(job_id):
            raise ServiceError("job_not_found", 404)
        with self.database() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ServiceError("job_not_found", 404)
            return dict(row)

    def list(self):
        with self.database() as db:
            return [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at,id")]

    @staticmethod
    def event(db, job_id, event, *, token=None, **fields):
        # Fields originate from validated/server-generated IDs and fixed codes.
        payload = {"event": event, "job_id": job_id, "attempt_id": token,
                   "time_utc_unix": time.time(), **fields}
        db.execute("INSERT INTO events (job_id,token,payload) VALUES (?,?,?)",
                   (job_id, token, canonical_bytes(payload).decode()))

    def finish(self, job_id, token, state, failure=None, **fields):
        with self.database(write=True) as db:
            if db.execute("UPDATE jobs SET state=?,failure=? WHERE id=? AND token=?",
                          (state, failure, job_id, token)).rowcount != 1:
                raise ServiceError("stale_attempt")
            self.event(db, job_id, "worker_finished", token=token, outcome=state, failure=failure, **fields)

    def emit(self, job_id, token, event, **fields):
        with self.database(write=True) as db:
            row = db.execute("SELECT token FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row[0] != token:
                raise ServiceError("stale_attempt")
            self.event(db, job_id, event, token=token, **fields)

    def events(self, job_id, after=0, limit=100):
        with self.database() as db:
            rows = db.execute("SELECT sequence,payload FROM events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                              (job_id, after, limit)).fetchall()
            return [{"sequence": r[0], **json.loads(r[1])} for r in rows]
