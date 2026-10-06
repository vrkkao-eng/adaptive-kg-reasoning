"""Fixed-trace, single-writer process resume with an atomic local receipt ledger."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3

from .adaptive import query_schedule
from .checkpoint import canonical_bytes, decode_payload, encode_payload
from .evidence import sha256
from .incremental import IncrementalHighRecentState
from .placement import load_config
from .recompute import RecomputeWindowResult, recompute_window
from .recovery import check_state, trace_identity
from .resources import MemoryModel, NodeProfile, nonnegative_int
from .windows import WindowTransition, iter_sliding_windows, load_events

CONTRACT = "fixed-trace-process-resume-v1"
POINTS = ("before_update", "after_update", "before_commit", "after_commit")
ENGINE_FILES = ("adaptive.py", "checkpoint.py", "incremental.py", "placement.py",
                "process_resume.py", "recompute.py", "recovery.py", "resources.py",
                "windows.py", "namespaces.py", "network_cost.py", "metrics.py")


class ResumeError(ValueError):
    """Persisted progress cannot safely resume the declared job."""


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def facts_digest(facts):
    return digest(sorted(list(map(str, fact)) for fact in facts))


def read_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ResumeError("Duplicate JSON key")
            result[key] = item
        return result
    def reject_constant(token):
        raise ResumeError("Non-finite JSON")
    return json.loads(value, object_pairs_hook=unique, parse_constant=reject_constant)


def engine_hashes():
    root = Path(__file__).parent
    return {name: sha256(root / name) for name in ENGINE_FILES}


@dataclass
class ResumeJob:
    directory: Path
    config: dict
    identity: str
    windows: list[WindowTransition]
    queries: list[int]
    input_offsets: list[int]
    oracles: list[RecomputeWindowResult]
    node: NodeProfile
    memory: MemoryModel


def load_job(directory: Path) -> ResumeJob:
    """Re-derive the entire fixed trace independently of the persisted state."""
    config = read_json((directory / "job.json").read_text(encoding="utf-8"))
    fields = {"contract", "width", "slide", "flush", "threshold", "workload", "node",
              "input_sha256", "profiles_sha256", "engine_sha256"}
    if not isinstance(config, dict) or set(config) != fields or config["contract"] != CONTRACT:
        raise ResumeError("Unknown job contract or fields")
    for name in ("width", "slide"):
        nonnegative_int(name, config[name])
        if not config[name]:
            raise ResumeError("Window dimensions must be positive")
    if type(config["flush"]) is not bool:
        raise ResumeError("flush must be boolean")
    if (type(config["threshold"]) not in (int, float) or not math.isfinite(config["threshold"])
            or type(config["workload"]) is not str or type(config["node"]) is not str):
        raise ResumeError("Invalid semantic configuration")
    if config["engine_sha256"] != engine_hashes():
        raise ResumeError("Resume engine source mismatch")
    for name in ("input", "profiles"):
        path = directory / ("input.csv" if name == "input" else "profiles.json")
        if sha256(path) != config[name + "_sha256"]:
            raise ResumeError(f"{name} identity mismatch")
    events = load_events(directory / "input.csv", property_filter=1)
    if not events or any(not math.isfinite(event.value) for event in events):
        raise ResumeError("Expected non-empty finite load events")
    windows = list(iter_sliding_windows(events, width_seconds=config["width"],
                  slide_seconds=config["slide"], flush=config["flush"]))
    queries = query_schedule(config["workload"], len(windows))
    profiles = load_config(directory / "profiles.json")
    node = next((node for node in profiles.nodes if node.name == config["node"]), None)
    if node is None:
        raise ResumeError("Unknown fixed node")
    offsets, consumed = [], 0
    for window in windows:
        consumed += len(window.added)
        offsets.append(consumed)
    identity = digest({"config": config, "trace": trace_identity(windows, threshold=config["threshold"]),
                       "queries": queries})
    return ResumeJob(directory, config, identity, windows, queries, offsets,
                     [recompute_window(window, threshold_watts=config["threshold"]) for window in windows],
                     node, profiles.memory)


def prepare_job(directory: Path, *, input_path: Path, profiles_path: Path, width=120,
                slide=30, flush=True, threshold=450., workload="dense", node="fog") -> ResumeJob:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise ResumeError("Process resume requires SQLite 3.37 or newer")
    directory.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(input_path, directory / "input.csv")
    shutil.copyfile(profiles_path, directory / "profiles.json")
    config = {"contract": CONTRACT, "width": width, "slide": slide, "flush": flush,
              "threshold": threshold, "workload": workload, "node": node,
              "input_sha256": sha256(directory / "input.csv"),
              "profiles_sha256": sha256(directory / "profiles.json"), "engine_sha256": engine_hashes()}
    (directory / "job.json").write_bytes(canonical_bytes(config) + b"\n")
    job = load_job(directory)
    connection = sqlite3.connect(directory / "progress.sqlite", isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            BEGIN IMMEDIATE;
            PRAGMA user_version=1;
            CREATE TABLE progress (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity TEXT NOT NULL,
                applied_cursor INTEGER NOT NULL, served_cursor INTEGER NOT NULL,
                input_cursor INTEGER NOT NULL CHECK(input_cursor>=0),
                state_json TEXT, state_sha256 TEXT
            ) STRICT;
            CREATE TABLE receipts (
                window_index INTEGER PRIMARY KEY, input_cursor INTEGER NOT NULL,
                queries INTEGER NOT NULL CHECK(queries>=0), answer_sha256 TEXT NOT NULL
            ) STRICT;
            CREATE TABLE audit (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL) STRICT;
        """)
        connection.execute("INSERT INTO progress VALUES (1, ?, -1, -1, 0, NULL, NULL)", (job.identity,))
        connection.commit()
    finally:
        connection.close()
    return job


class ResumeStore:
    """One SQLite transaction couples state, input frontier, receipt and audit."""
    def __init__(self, job: ResumeJob):
        self.job = job
        uri = (job.directory / "progress.sqlite").resolve().as_uri() + "?mode=rw"
        self.connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        try:
            if self.connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ResumeError("Unsupported progress schema")
            if self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ResumeError("Expected rollback-journal progress store")
            objects = set(self.connection.execute("SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"))
            if objects != {("table", name) for name in ("progress", "receipts", "audit")}:
                raise ResumeError("Unexpected progress schema objects")
            strict_tables = {row[1] for row in self.connection.execute("PRAGMA table_list")
                             if row[0] == "main" and row[5] == 1}
            if strict_tables != {"progress", "receipts", "audit"}:
                raise ResumeError("Expected STRICT progress schema tables")
            columns = {
                "progress": (("singleton", "INTEGER"), ("identity", "TEXT"), ("applied_cursor", "INTEGER"),
                             ("served_cursor", "INTEGER"), ("input_cursor", "INTEGER"),
                             ("state_json", "TEXT"), ("state_sha256", "TEXT")),
                "receipts": (("window_index", "INTEGER"), ("input_cursor", "INTEGER"),
                             ("queries", "INTEGER"), ("answer_sha256", "TEXT")),
                "audit": (("sequence", "INTEGER"), ("payload", "TEXT")),
            }
            for table, expected in columns.items():
                observed = tuple((row[1], row[2]) for row in self.connection.execute(f"PRAGMA table_info({table})"))
                if observed != expected:
                    raise ResumeError("Unexpected progress schema columns")
            self.connection.execute("PRAGMA synchronous=FULL")
        except Exception:
            self.close()
            raise

    def close(self):
        self.connection.close()

    def _audit(self, event):
        sequence = self.connection.execute("SELECT count(*) FROM audit").fetchone()[0]
        self.connection.execute("INSERT INTO audit VALUES (?, ?)",
                                (sequence, canonical_bytes(event).decode()))

    def load(self):
        """Validate a consistent committed prefix, not just a cached answer."""
        db, job = self.connection, self.job
        db.execute("BEGIN")
        try:
            rows = db.execute("SELECT identity, applied_cursor, served_cursor, input_cursor, state_json, state_sha256 FROM progress").fetchall()
            if len(rows) != 1:
                raise ResumeError("Expected one progress record")
            identity, cursor, served, offset, payload, checksum = rows[0]
            if (identity != job.identity or type(cursor) is not int or not -1 <= cursor < len(job.windows)
                    or type(served) is not int or served != cursor or type(offset) is not int
                    or offset != (job.input_offsets[cursor] if cursor >= 0 else 0)):
                raise ResumeError("Progress identity or input/applied/served cursor mismatch")
            receipts = [dict(zip(("window_index", "input_cursor", "queries", "answer_sha256"), row))
                        for row in db.execute("SELECT * FROM receipts ORDER BY window_index")]
            expected = [{"window_index": index, "input_cursor": job.input_offsets[index],
                         "queries": job.queries[index], "answer_sha256": facts_digest(job.oracles[index].facts)}
                        for index in range(cursor + 1)]
            if canonical_bytes(receipts) != canonical_bytes(expected):
                raise ResumeError("Receipt prefix, query count or answer mismatch")
            audit, frontier, attempts = [], -1, 0
            for sequence, text in db.execute("SELECT * FROM audit ORDER BY sequence"):
                event = read_json(text)
                if sequence != len(audit):
                    raise ResumeError("Non-consecutive audit sequence")
                if isinstance(event, dict) and event.get("event") == "process_started":
                    attempts += 1
                    reference = {"event": "process_started", "attempt": attempts, "cursor": frontier}
                elif isinstance(event, dict) and event.get("event") == "window_committed" and frontier < cursor:
                    frontier += 1
                    reference = {"event": "window_committed", **expected[frontier]}
                    if not attempts:
                        raise ResumeError("Commit precedes process start")
                else:
                    raise ResumeError("Unknown or excess audit event")
                if canonical_bytes(event) != canonical_bytes(reference):
                    raise ResumeError("Audit fields disagree with committed prefix")
                audit.append({"sequence": sequence, **event})
            if frontier != cursor:
                raise ResumeError("Missing committed audit event")
            if cursor == -1:
                if payload is not None or checksum is not None:
                    raise ResumeError("Unexpected initial snapshot")
                state = IncrementalHighRecentState(threshold_watts=job.config["threshold"])
            else:
                raw = read_json(payload)
                if digest(raw) != checksum:
                    raise ResumeError("State checksum mismatch")
                state, decoded_cursor = decode_payload(raw, expected_identity=job.identity)
                if decoded_cursor != cursor or state.threshold_watts != job.config["threshold"]:
                    raise ResumeError("State cursor or threshold mismatch")
                check_state(state, job.windows[cursor], job.oracles[cursor])
            return state, cursor, receipts, audit, attempts
        finally:
            db.rollback()

    def start(self, cursor, attempt):
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT applied_cursor FROM progress").fetchone()[0] != cursor:
                raise ResumeError("Stale process frontier")
            self._audit({"event": "process_started", "attempt": attempt, "cursor": cursor})
            db.commit()
        except Exception:
            db.rollback()
            raise

    def commit_window(self, state, index, hook, *, checked_queries):
        job, db = self.job, self.connection
        nonnegative_int("window index", index)
        if index >= len(job.windows):
            raise ResumeError("Commit window is outside the trace")
        if type(checked_queries) is not int or checked_queries != job.queries[index]:
            raise ResumeError("Incomplete checked-query batch")
        check_state(state, job.windows[index], job.oracles[index])
        payload = encode_payload(state, cursor=index, identity=job.identity)
        receipt = {"window_index": index, "input_cursor": job.input_offsets[index],
                   "queries": checked_queries, "answer_sha256": facts_digest(state.facts)}
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT applied_cursor FROM progress").fetchone()[0] != index - 1:
                raise ResumeError("Stale process frontier")
            db.execute("UPDATE progress SET applied_cursor=?, served_cursor=?, input_cursor=?, state_json=?, state_sha256=?",
                       (index, index, receipt["input_cursor"], canonical_bytes(payload).decode(), digest(payload)))
            db.execute("INSERT INTO receipts VALUES (?, ?, ?, ?)", tuple(receipt.values()))
            self._audit({"event": "window_committed", **receipt})
            hook("before_commit", index)
            db.commit()
        except Exception:
            db.rollback()
            raise
        hook("after_commit", index)


def run_worker(directory: Path, *, hook=lambda point, index: None):
    """Resume only uncommitted windows; successful no-op completion writes nothing."""
    job = load_job(directory)
    store = ResumeStore(job)
    try:
        state, cursor, _, _, attempts = store.load()
        if cursor == len(job.windows) - 1:
            return {"status": "already_completed", "cursor": cursor}
        store.start(cursor, attempts + 1)
        for index in range(cursor + 1, len(job.windows)):
            window, oracle = job.windows[index], job.oracles[index]
            required = job.memory.retained_bytes(len(window.events), oracle.distinct_plugs, len(oracle.facts))
            if required > job.node.memory_budget_bytes:
                raise ResumeError(f"memory_budget_exceeded at window {index}")
            hook("before_update", index)
            state.apply(window)
            check_state(state, window, oracle)
            hook("after_update", index)
            checked_queries = 0
            for _ in range(job.queries[index]):
                if frozenset(tuple(state.facts)) != oracle.facts:
                    raise ResumeError(f"Query divergence at window {index}")
                checked_queries += 1
            store.commit_window(state, index, hook, checked_queries=checked_queries)
        return {"status": "completed", "cursor": len(job.windows) - 1}
    finally:
        store.close()


def inspect_job(directory: Path):
    job = load_job(directory)
    store = ResumeStore(job)
    try:
        _, cursor, receipts, audit, attempts = store.load()
        return {"cursor": cursor, "input_cursor": job.input_offsets[cursor] if cursor >= 0 else 0,
                "windows": len(job.windows), "served_windows": len(receipts),
                "requested_queries": sum(job.queries), "served_queries": sum(row["queries"] for row in receipts),
                "attempts": attempts, "receipts": receipts, "audit": audit}
    finally:
        store.close()
