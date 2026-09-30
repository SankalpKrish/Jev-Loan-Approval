import asyncio
import json
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from jevloan.audit.log import COLUMNS, EVENT_TYPES, GENESIS_HASH, AuditLog, utc_now_iso
from jevloan.canonical import canonical_json, sha256_hex

ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def raw(db: Path):
    """Direct sqlite3 access, as an attacker (or a DBA) would have: autocommit, closed on exit."""
    return closing(sqlite3.connect(db, isolation_level=None))


def add(log: AuditLog, n: int, file_id: str = "F000001") -> list[dict]:
    return [log.append(event_type="POLICY_OUTCOME", file_id=file_id, stage="appraisal", policy_outcome=f"O{i}") for i in range(n)]


def tamper(db: Path, sql: str, *params: object) -> None:
    """What an attacker with file access would do: drop the append-only triggers, then edit."""
    with raw(db) as conn:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute(sql, params)


def test_schema_and_setup(tmp_db: Path):
    AuditLog(tmp_db)  # creates parent dirs
    with raw(tmp_db) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)")]
        assert columns == ["seq", *COLUMNS]
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        objects = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_audit_log_file_id", "idx_audit_log_event_type"} <= objects
    assert sum(1 for name in objects if name.startswith("audit_log_no_")) == 2
    assert set(EVENT_TYPES) == {
        "MODEL_CALL", "MODEL_FAILURE", "PII_BLOCK", "POLICY_OUTCOME", "PRICING", "QUEUED", "HUMAN_DECISION", "MONITOR_ALERT", "SYSTEM",
    }


def test_empty_log_verifies(tmp_db: Path):
    assert AuditLog(tmp_db).verify().ok
    assert AuditLog(tmp_db).verify().entries == 0


def test_append_returns_full_row_and_chains(tmp_db: Path):
    log = AuditLog(tmp_db)
    first = log.append(
        event_type="MODEL_CALL",
        file_id="F000001",
        segment="salaried_personal",
        stage="appraisal",
        policy_version="policy-v1",
        model_version="sim-jev-0.1",
        state_hash="ab" * 32,
        state_json={"stage": "appraisal", "bureau": {"score_band": "700-749"}},
        questions_json={"A_income_proof_current": {"type": "noul"}},
        answers_json={"A_income_proof_current": {"p": 0.9, "derived_confidence": 0.8}},
        request_ts="2026-09-29T10:00:00.000Z",
        latency_ms=41,
        input_tokens=1800,
        detail_json=["x"],
    )
    second = log.append(event_type="SYSTEM", detail_json={"msg": "hello"})

    assert first["seq"] == 1 and second["seq"] == 2
    assert set(first) == {"seq", *COLUMNS}
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["entry_hash"]
    assert first["state_json"] == canonical_json({"stage": "appraisal", "bureau": {"score_band": "700-749"}})
    assert first["latency_ms"] == 41.0 and isinstance(first["latency_ms"], float)
    assert first["human_decision"] is None and second["file_id"] is None
    assert ISO_MS.match(first["created_ts"])
    assert list(log.iter_all()) == [first, second]  # stored exactly as returned
    assert log.verify().ok and log.verify().entries == 2


def test_entry_hash_formula_commits_to_every_column_including_nulls(tmp_db: Path):
    log = AuditLog(tmp_db)
    row = log.append(event_type="SYSTEM", detail_json={"a": 1}, created_ts="2026-01-01T00:00:00.000Z")
    body = {k: v for k, v in row.items() if k not in ("seq", "entry_hash")}
    assert len(body) == len(COLUMNS) - 1 and body["file_id"] is None
    assert row["entry_hash"] == sha256_hex(GENESIS_HASH + canonical_json(body))
    assert row["created_ts"] == "2026-01-01T00:00:00.000Z"


def test_utc_now_iso_format():
    assert ISO_MS.match(utc_now_iso())


def test_unknown_column_rejected_and_nothing_written(tmp_db: Path):
    log = AuditLog(tmp_db)
    with pytest.raises(ValueError, match="bogus"):
        log.append(event_type="SYSTEM", bogus="x")
    for forbidden in ("seq", "prev_hash", "entry_hash"):
        with pytest.raises(ValueError):
            log.append(event_type="SYSTEM", **{forbidden: "x"})
    assert list(log.iter_all()) == []


def test_unknown_event_type_rejected(tmp_db: Path):
    with pytest.raises(ValueError, match="event_type"):
        AuditLog(tmp_db).append(event_type="NOT_A_THING")


@pytest.mark.parametrize(
    "fields",
    [{"state_json": "{not json"}, {"latency_ms": float("nan")}, {"input_tokens": 1.5}, {"file_id": 123}, {"human_decision": ["accept"]}],
)
def test_values_that_would_not_survive_a_round_trip_are_rejected(tmp_db: Path, fields: dict):
    with pytest.raises(ValueError):
        AuditLog(tmp_db).append(event_type="SYSTEM", **fields)


def test_json_text_passes_through(tmp_db: Path):
    row = AuditLog(tmp_db).append(event_type="SYSTEM", detail_json='{"a":1}')
    assert json.loads(row["detail_json"]) == {"a": 1}


def test_update_refused(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 1)
    with raw(tmp_db) as conn, pytest.raises(sqlite3.DatabaseError, match="audit_log is append-only"):
        conn.execute("UPDATE audit_log SET policy_outcome = 'X' WHERE seq = 1")
    assert log.verify().ok


def test_delete_refused(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 1)
    with raw(tmp_db) as conn, pytest.raises(sqlite3.DatabaseError, match="audit_log is append-only"):
        conn.execute("DELETE FROM audit_log")
    assert log.verify().entries == 1


def test_tampered_field_detected_at_that_seq(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 6)
    tamper(tmp_db, "UPDATE audit_log SET policy_outcome = 'PROCEED_TO_SANCTIONING_AUTHORITY' WHERE seq = 3")
    result = log.verify()
    assert not result.ok and result.first_bad_seq == 3 and result.entries == 6
    assert "modified" in result.reason


def test_tamper_with_recomputed_hash_breaks_the_next_link(tmp_db: Path):
    log = AuditLog(tmp_db)
    rows = add(log, 5)
    forged = {k: v for k, v in rows[2].items() if k not in ("seq", "entry_hash")} | {"policy_outcome": "FORGED"}
    forged_hash = sha256_hex(forged["prev_hash"] + canonical_json(forged))
    tamper(tmp_db, "UPDATE audit_log SET policy_outcome = 'FORGED', entry_hash = ? WHERE seq = 3", forged_hash)
    result = log.verify()
    assert not result.ok and result.first_bad_seq == 4 and "prev_hash" in result.reason


def test_deleted_middle_row_detected(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 6)
    tamper(tmp_db, "DELETE FROM audit_log WHERE seq = 4")
    result = log.verify()
    assert not result.ok and result.first_bad_seq == 4 and "missing" in result.reason


def test_deleted_first_row_detected(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 3)
    tamper(tmp_db, "DELETE FROM audit_log WHERE seq = 1")
    assert log.verify().first_bad_seq == 1


def test_deleted_last_row_detected(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 4)
    tamper(tmp_db, "DELETE FROM audit_log WHERE seq = 4")
    result = log.verify()
    assert not result.ok and result.first_bad_seq == 4 and "truncated" in result.reason


def test_added_column_is_detected(tmp_db: Path):
    log = AuditLog(tmp_db)
    add(log, 3)
    with raw(tmp_db) as conn:
        conn.execute("ALTER TABLE audit_log ADD COLUMN sneaky TEXT")
    assert not log.verify().ok


def test_chain_continues_across_instances(tmp_db: Path):
    first = add(AuditLog(tmp_db), 2)
    second = add(AuditLog(tmp_db), 1)[0]
    assert second["seq"] == 3 and second["prev_hash"] == first[1]["entry_hash"]
    assert AuditLog(tmp_db).verify().ok


def test_concurrent_appends_from_threads(tmp_db: Path):
    log = AuditLog(tmp_db)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                log.append(event_type="POLICY_OUTCOME", file_id=f"F{n:06d}", stage="appraisal", policy_outcome=str(i))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    result = log.verify()
    assert result.ok and result.entries == 400
    assert [r["seq"] for r in log.iter_all()] == list(range(1, 401))
    for n in range(8):  # each thread's own entries stay in order
        assert [r["policy_outcome"] for r in log.trail(f"F{n:06d}")] == [str(i) for i in range(50)]


def test_two_instances_appending_concurrently(tmp_db: Path):
    logs = [AuditLog(tmp_db), AuditLog(tmp_db)]  # separate locks: BEGIN IMMEDIATE must serialise them
    threads = [threading.Thread(target=add, args=(logs[i % 2], 25, f"F{i}")) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert logs[0].verify().ok and logs[0].verify().entries == 100


async def test_append_from_asyncio(tmp_db: Path):
    log = AuditLog(tmp_db)
    log.append(event_type="SYSTEM")  # direct synchronous call inside a coroutine
    await asyncio.gather(*(asyncio.to_thread(log.append, event_type="SYSTEM", detail_json={"i": i}) for i in range(20)))
    assert log.verify().ok and log.verify().entries == 21


def test_trail_filters_by_file_in_seq_order(tmp_db: Path):
    log = AuditLog(tmp_db)
    a1 = log.append(event_type="MODEL_CALL", file_id="F000001", stage="appraisal")
    log.append(event_type="MODEL_CALL", file_id="F000002", stage="appraisal")
    a2 = log.append(event_type="POLICY_OUTCOME", file_id="F000001", stage="appraisal")
    log.append(event_type="SYSTEM")
    assert log.trail("F000001") == [a1, a2]
    assert [r["file_id"] for r in log.trail("F000002")] == ["F000002"]
    assert log.trail("F999999") == []
    assert len(list(log.iter_all())) == 4
