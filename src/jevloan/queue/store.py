"""The human queue (PLAN section 3.10): where every file the system will not act on by itself waits for a person.

Queue items live in the same SQLite file as the audit log, in their own mutable table (the audit log stays the
only append-only store). Enqueueing writes a QUEUED audit entry; deciding writes a HUMAN_DECISION entry with the
reviewer, the decision and the reason code, and those codes are the calibration data the reviewers produce.

Thread-safe: every call uses its own short connection, and the claim on an item (open -> decided) is one atomic
statement, so of two reviewers deciding the same item at once exactly one wins and the other gets
`ItemAlreadyDecided`.
"""

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jevloan.audit.log import AuditLog, utc_now_iso
from jevloan.policy.config import Policy, load_policy
from jevloan.policy.outcomes import CREDIT_REVIEW, QUEUES

if TYPE_CHECKING:
    from jevloan.policy.engine import StageDecision

DECISIONS = ("accept", "modify", "reject")
STATUSES = ("open", "decided")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS queue_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id TEXT NOT NULL,
        segment TEXT,
        stage TEXT NOT NULL,
        queue TEXT NOT NULL,
        outcome TEXT NOT NULL,
        closeness REAL,
        top_reason_json TEXT,
        reasons_json TEXT NOT NULL,
        reason_codes_json TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'decided')),
        created_ts TEXT NOT NULL,
        decided_ts TEXT,
        decision TEXT,
        reason_code TEXT,
        reviewer_id TEXT,
        notes TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_queue_items_queue_status ON queue_items(queue, status)",
    "CREATE INDEX IF NOT EXISTS idx_queue_items_file_id ON queue_items(file_id)",
)


class QueueError(Exception):
    """Base class for queue failures."""


class InvalidDecision(QueueError, ValueError):
    """The decision, reason code or reviewer is not acceptable."""


class ItemNotFound(QueueError, LookupError):
    """No queue item with that id."""


class ItemAlreadyDecided(QueueError):
    """The item has already been decided; a decision is final."""


@dataclass(frozen=True)
class QueueItem:
    id: int
    file_id: str
    segment: str | None
    stage: str
    queue: str
    outcome: str
    closeness: float | None
    top_reason: dict | None  # {qid, p, confidence, text}
    reasons: list[dict]
    reason_codes: list[str]
    policy_version: str
    status: str
    created_ts: str
    decided_ts: str | None
    decision: str | None
    reason_code: str | None
    reviewer_id: str | None
    notes: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=row["id"], file_id=row["file_id"], segment=row["segment"], stage=row["stage"], queue=row["queue"],
        outcome=row["outcome"], closeness=row["closeness"],
        top_reason=json.loads(row["top_reason_json"]) if row["top_reason_json"] else None,
        reasons=json.loads(row["reasons_json"]), reason_codes=json.loads(row["reason_codes_json"]),
        policy_version=row["policy_version"], status=row["status"], created_ts=row["created_ts"],
        decided_ts=row["decided_ts"], decision=row["decision"], reason_code=row["reason_code"],
        reviewer_id=row["reviewer_id"], notes=row["notes"],
    )


def _order(item: QueueItem) -> tuple:
    """credit_review: nearest to passing first (closeness descending, none last), then oldest. Others: oldest first."""
    if item.queue == CREDIT_REVIEW:
        return (item.queue, item.closeness is None, -(item.closeness or 0.0), item.created_ts, item.id)
    return (item.queue, False, 0.0, item.created_ts, item.id)


class HumanQueue:
    def __init__(self, db_path: str | Path, audit: AuditLog, policy: Policy | None = None) -> None:
        """`policy` supplies the reason codes a reviewer may record (default: the shipped policy)."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit = audit
        self._reason_codes = (policy or load_policy()).human_reason_codes()
        self._lock = threading.Lock()
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)  # we issue BEGIN ourselves
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ writing

    def enqueue(self, decision: "StageDecision") -> int:
        """Put a routed decision in its queue and write the QUEUED audit entry. Returns the queue item id."""
        if decision.queue not in QUEUES:
            raise ValueError(f"decision for {decision.file_id} {decision.stage} has no valid queue ({decision.queue!r}); nothing to enqueue")
        top = None if decision.top_reason is None else json.dumps(asdict(decision.top_reason), sort_keys=True)
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO queue_items (file_id, segment, stage, queue, outcome, closeness, top_reason_json, reasons_json,"
                " reason_codes_json, policy_version, status, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    decision.file_id, decision.segment, decision.stage, decision.queue, str(decision.outcome), decision.closeness,
                    top, json.dumps([asdict(r) for r in decision.reasons], sort_keys=True),
                    json.dumps(list(decision.reason_codes)), decision.policy_version, utc_now_iso(),
                ),
            )
            item_id = cursor.lastrowid
        try:
            self._audit.append(
                event_type="QUEUED", file_id=decision.file_id, segment=decision.segment, stage=decision.stage,
                policy_version=decision.policy_version, policy_outcome=str(decision.outcome),
                detail_json={
                    "queue_item_id": item_id, "queue": decision.queue, "reason_codes": list(decision.reason_codes),
                    "closeness": decision.closeness,
                },
            )
        except BaseException:  # never leave a queue item the audit log does not know about
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            raise
        return item_id

    def decide(self, item_id: int, *, decision: str, reason_code: str, reviewer_id: str, notes: str | None = None) -> dict:
        """Record a human decision (accept, modify or reject) on an open item. Returns the HUMAN_DECISION audit entry.

        Raises InvalidDecision for a bad decision, an unknown reason code or an empty reviewer id; ItemNotFound for an
        unknown id; ItemAlreadyDecided if the item was decided already (including a moment ago by someone else)."""
        if decision not in DECISIONS:
            raise InvalidDecision(f"decision must be one of {DECISIONS}, got {decision!r}")
        if reason_code not in self._reason_codes:
            raise InvalidDecision(f"unknown reason code {reason_code!r}; the policy defines {sorted(self._reason_codes)}")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise InvalidDecision("reviewer_id is required: every decision has a named human")
        reviewer_id = reviewer_id.strip()
        decided_ts = utc_now_iso()

        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
                if row is None:
                    raise ItemNotFound(f"no queue item {item_id}")
                if row["status"] != "open":
                    raise ItemAlreadyDecided(f"queue item {item_id} was already decided (by {row['reviewer_id']} at {row['decided_ts']})")
                conn.execute(
                    "UPDATE queue_items SET status = 'decided', decided_ts = ?, decision = ?, reason_code = ?, reviewer_id = ?,"
                    " notes = ? WHERE id = ?",
                    (decided_ts, decision, reason_code, reviewer_id, notes, item_id),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        try:
            return self._audit.append(
                event_type="HUMAN_DECISION", file_id=row["file_id"], segment=row["segment"], stage=row["stage"],
                policy_version=row["policy_version"], policy_outcome=row["outcome"], human_decision=decision,
                human_reason_code=reason_code, reviewer_id=reviewer_id, decision_ts=decided_ts,
                detail_json={"queue_item_id": item_id, "queue": row["queue"], "notes": notes},
            )
        except BaseException:  # no audit entry, no decision: put the item back so it is not silently lost
            with closing(self._connect()) as conn:
                conn.execute(
                    "UPDATE queue_items SET status = 'open', decided_ts = NULL, decision = NULL, reason_code = NULL,"
                    " reviewer_id = NULL, notes = NULL WHERE id = ?",
                    (item_id,),
                )
            raise

    # ------------------------------------------------------------------ reading

    def get(self, item_id: int) -> QueueItem:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ItemNotFound(f"no queue item {item_id}")
        return _to_item(row)

    def list(self, queue: str | None = None, status: str | None = "open") -> "list[QueueItem]":
        """Items in review order. credit_review lists the file nearest to passing first (then oldest); every other
        queue is oldest first. `queue=None` lists all queues (grouped by queue); `status=None` includes decided items."""
        if queue is not None and queue not in QUEUES:
            raise ValueError(f"unknown queue {queue!r}; expected one of {QUEUES}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        clauses, params = [], []
        if queue is not None:
            clauses.append("queue = ?")
            params.append(queue)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT * FROM queue_items{where}", params).fetchall()
        return sorted((_to_item(r) for r in rows), key=_order)

    def stats(self) -> dict[str, Any]:
        """Counts for the health page and the calibration loop: by queue and status, decisions, and reason codes."""
        with closing(self._connect()) as conn:
            by_queue = {q: {"open": 0, "decided": 0} for q in QUEUES}
            for row in conn.execute("SELECT queue, status, COUNT(*) AS n FROM queue_items GROUP BY queue, status"):
                by_queue[row["queue"]][row["status"]] = row["n"]
            decisions = {d: 0 for d in DECISIONS}
            for row in conn.execute("SELECT decision, COUNT(*) AS n FROM queue_items WHERE status = 'decided' GROUP BY decision"):
                decisions[row["decision"]] = row["n"]
            reason_codes = {
                row["reason_code"]: row["n"]
                for row in conn.execute(
                    "SELECT reason_code, COUNT(*) AS n FROM queue_items WHERE status = 'decided' GROUP BY reason_code ORDER BY reason_code"
                )
            }
            oldest = conn.execute("SELECT MIN(created_ts) FROM queue_items WHERE status = 'open'").fetchone()[0]
        open_total = sum(q["open"] for q in by_queue.values())
        decided_total = sum(q["decided"] for q in by_queue.values())
        return {
            "total": open_total + decided_total, "open": open_total, "decided": decided_total, "by_queue": by_queue,
            "decisions": decisions, "reason_codes": reason_codes, "oldest_open_ts": oldest,
        }
