"""Append-only, hash-chained audit log (PLAN section 3.2).

Every entry commits to the previous one: entry_hash = sha256(prev_hash + canonical_json(all columns
except seq and entry_hash)). SQLite triggers refuse UPDATE and DELETE, and `verify()` detects edits,
deleted rows and a truncated tail even if someone drops the triggers.

Each call opens its own short-lived connection, so an AuditLog needs no close() and can be shared
across threads, asyncio code (a synchronous append is a fast local write) and several instances or
processes pointing at the same file (BEGIN IMMEDIATE serialises writers). The path must be a real
file, not ":memory:".
"""

import json
import math
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jevloan.canonical import canonical_json, sha256_hex

EVENT_TYPES = (
    "MODEL_CALL",
    "MODEL_FAILURE",
    "PII_BLOCK",
    "POLICY_OUTCOME",
    "PRICING",
    "QUEUED",
    "HUMAN_DECISION",
    "MONITOR_ALERT",
    "SYSTEM",
)
GENESIS_HASH = "0" * 64
JSON_COLUMNS = ("state_json", "questions_json", "answers_json", "detail_json")

# Columns after `seq`, in table order, with their SQL types. The column names are a contract.
_COLUMNS = (
    ("event_type", "TEXT NOT NULL"),
    ("file_id", "TEXT"),
    ("segment", "TEXT"),
    ("stage", "TEXT"),
    ("policy_version", "TEXT"),
    ("model_version", "TEXT"),
    ("state_hash", "TEXT"),
    ("state_json", "TEXT"),
    ("questions_json", "TEXT"),
    ("answers_json", "TEXT"),
    ("policy_outcome", "TEXT"),
    ("human_decision", "TEXT"),
    ("human_reason_code", "TEXT"),
    ("reviewer_id", "TEXT"),
    ("request_ts", "TEXT"),
    ("response_ts", "TEXT"),
    ("decision_ts", "TEXT"),
    ("latency_ms", "REAL"),
    ("input_tokens", "INTEGER"),
    ("detail_json", "TEXT"),
    ("created_ts", "TEXT NOT NULL"),
    ("prev_hash", "TEXT NOT NULL"),
    ("entry_hash", "TEXT NOT NULL"),
)
COLUMNS = tuple(name for name, _ in _COLUMNS)
_CALLER_COLUMNS = frozenset(COLUMNS) - {"prev_hash", "entry_hash"}  # event_type is a keyword-only argument

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    + ", ".join(f"{name} {sql_type}" for name, sql_type in _COLUMNS)
    + ")",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_file_id ON audit_log(file_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type)",
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
)


def utc_now_iso() -> str:
    """Now in UTC, ISO-8601 with millisecond precision, e.g. 2026-09-29T10:15:00.123Z."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int  # entries in the log
    first_bad_seq: int | None = None
    reason: str | None = None


def _coerce(name: str, value: object) -> object:
    """Normalise a value to exactly what SQLite will hand back, so the hash still matches on re-read."""
    if value is None:
        return None
    if name in JSON_COLUMNS:
        if isinstance(value, str):
            json.loads(value)  # ValueError if the caller passed text that is not JSON
            return value
        return canonical_json(value)
    if name == "latency_ms":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"latency_ms must be a finite number, got {value!r}")
        return float(value)
    if name == "input_tokens":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"input_tokens must be an int, got {value!r}")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a str, got {type(value).__name__}")
    return value


def _entry_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k not in ("seq", "entry_hash")}
    return sha256_hex(record["prev_hash"] + canonical_json(body))


class AuditLog:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)  # we issue BEGIN ourselves
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, *, event_type: str, **fields: object) -> dict:
        """Add one entry and return the stored row (including `seq`). Unknown columns raise ValueError."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        unknown = set(fields) - _CALLER_COLUMNS
        if unknown:
            raise ValueError(f"unknown or non-writable audit column(s): {sorted(unknown)}")
        given = {"event_type": event_type, **fields}
        record = {name: _coerce(name, given.get(name)) for name in COLUMNS}

        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                last = conn.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
                record["prev_hash"] = last["entry_hash"] if last else GENESIS_HASH
                if record["created_ts"] is None:
                    record["created_ts"] = utc_now_iso()
                record["entry_hash"] = _entry_hash(record)
                cursor = conn.execute(
                    f"INSERT INTO audit_log ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                    tuple(record.values()),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return {"seq": cursor.lastrowid, **record}

    def verify(self) -> VerifyResult:
        """Walk the chain in seq order, checking contiguity, linkage and every hash."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")  # one snapshot, so concurrent appends can't skew the walk
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            high = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'audit_log'").fetchone()
            expected_seq, prev_hash = 1, GENESIS_HASH

            def bad(reason: str) -> VerifyResult:
                return VerifyResult(False, total, expected_seq, reason)

            for row in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
                record = dict(row)
                if record["seq"] != expected_seq:
                    return bad(f"entry {expected_seq} is missing (found {record['seq']} next)")
                if record["prev_hash"] != prev_hash:
                    return bad("prev_hash does not match the previous entry")
                if _entry_hash(record) != record["entry_hash"]:
                    return bad("entry_hash mismatch: the entry was modified")
                prev_hash = record["entry_hash"]
                expected_seq += 1
            # AUTOINCREMENT remembers the highest seq ever issued, which exposes deleted trailing entries.
            if expected_seq - 1 != (high["seq"] if high else 0):
                return bad(f"log truncated: entries after seq {expected_seq - 1} are missing")
        return VerifyResult(True, total)

    def trail(self, file_id: str) -> list[dict]:
        """Every entry for one file, in seq order."""
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM audit_log WHERE file_id = ? ORDER BY seq", (file_id,))]

    def iter_all(self) -> Iterator[dict]:
        """Every entry in seq order."""
        with closing(self._connect()) as conn:
            for row in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
                yield dict(row)
