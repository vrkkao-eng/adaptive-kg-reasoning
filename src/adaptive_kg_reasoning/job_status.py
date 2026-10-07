"""Consistent bounded operational reads, not the offline full-oracle validator."""
import json
import sqlite3

from .job_registry import ServiceError


def progress(directory, row, *, after=-1, limit=0):
    if row["metadata_json"] is None:
        return {"available": False, "cursor": None, "receipts": []}
    meta = json.loads(row["metadata_json"])
    db = sqlite3.connect((directory / "progress.sqlite").resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=0.1, isolation_level=None)
    try:
        db.execute("BEGIN")
        rows = db.execute("SELECT singleton,identity,applied_cursor,served_cursor,input_cursor FROM progress").fetchall()
        if len(rows) != 1:
            raise ServiceError("progress_invalid", 503)
        singleton, identity, cursor, served, offset = rows[0]
        count, queries, minimum, maximum = db.execute(
            "SELECT count(*),coalesce(sum(queries),0),min(window_index),max(window_index) FROM receipts").fetchone()
        expected_queries = sum(meta["queries"][:cursor + 1]) if type(cursor) is int else -1
        if (singleton != 1 or identity != meta["identity"] or type(cursor) is not int
                or not -1 <= cursor < meta["windows"] or served != cursor
                or offset != (meta["offsets"][cursor] if cursor >= 0 else 0)
                or count != cursor + 1 or queries != expected_queries
                or (count and (minimum != 0 or maximum != cursor))
                or (row["state"] == "completed" and count != meta["windows"])):
            raise ServiceError("progress_invalid", 503)
        receipts = [dict(zip(("window_index", "input_cursor", "queries", "answer_sha256"), r)) for r in db.execute(
            "SELECT * FROM receipts WHERE window_index>? ORDER BY window_index LIMIT ?", (after, limit))]
        for receipt in receipts:
            index = receipt["window_index"]
            if receipt["queries"] != meta["queries"][index] or receipt["input_cursor"] != meta["offsets"][index]:
                raise ServiceError("progress_invalid", 503)
        return {"available": True, "cursor": cursor, "input_cursor": offset, "served_cursor": served,
                "windows": meta["windows"], "committed_windows": count, "committed_queries": queries,
                "requested_queries": sum(meta["queries"]), "unserved_queries": sum(meta["queries"]) - queries,
                "committed_complete": count == meta["windows"], "receipts": receipts}
    except sqlite3.DatabaseError as exc:
        # Includes a hot journal that a read-only connection cannot recover.
        raise ServiceError("progress_unavailable", 503) from exc
    finally:
        db.rollback()
        db.close()
