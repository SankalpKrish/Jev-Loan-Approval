import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from jevloan.audit.log import AuditLog
from jevloan.cli import main


def raw(db: Path):
    """Direct sqlite3 access, as an attacker (or a DBA) would have: autocommit, closed on exit."""
    return closing(sqlite3.connect(db, isolation_level=None))


def populate(db: Path) -> AuditLog:
    log = AuditLog(db)
    log.append(event_type="SYSTEM", detail_json={"msg": "start"})
    log.append(
        event_type="MODEL_CALL",
        file_id="F000123",
        segment="salaried_personal",
        stage="appraisal",
        model_version="sim-jev-0.1",
        answers_json={"A_income_proof_current": {"p": 0.93}},
        latency_ms=12.5,
    )
    log.append(event_type="POLICY_OUTCOME", file_id="F000123", stage="appraisal", policy_outcome="HUMAN_REVIEW")
    log.append(event_type="MODEL_CALL", file_id="F000456", stage="appraisal")
    return log


def test_verify_on_fresh_db(tmp_db: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["audit", "verify", "--db", str(tmp_db)]) == 0
    assert "OK" in capsys.readouterr().out
    assert tmp_db.exists()


def test_verify_on_populated_db(tmp_db: Path, capsys: pytest.CaptureFixture[str]):
    populate(tmp_db)
    assert main(["audit", "verify", "--db", str(tmp_db)]) == 0
    assert "4 entries" in capsys.readouterr().out


def test_verify_fails_with_exit_1_on_tampering(tmp_db: Path, capsys: pytest.CaptureFixture[str]):
    populate(tmp_db)
    with raw(tmp_db) as conn:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("UPDATE audit_log SET policy_outcome = 'PROCEED_TO_SANCTIONING_AUTHORITY' WHERE seq = 3")
    assert main(["audit", "verify", "--db", str(tmp_db)]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "seq 3" in out


def test_verify_default_db_comes_from_runtime_config(tmp_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    populate(tmp_db)
    monkeypatch.setenv("JEVLOAN_DB_PATH", str(tmp_db))
    assert main(["audit", "verify"]) == 0
    assert "4 entries" in capsys.readouterr().out


def test_dump_to_stdout(tmp_db: Path, capsys: pytest.CaptureFixture[str]):
    populate(tmp_db)
    assert main(["audit", "dump", "--file-id", "F000123", "--db", str(tmp_db)]) == 0
    dump = json.loads(capsys.readouterr().out)
    assert dump["file_id"] == "F000123" and dump["chain_verified"] is True
    assert [e["event_type"] for e in dump["entries"]] == ["MODEL_CALL", "POLICY_OUTCOME"]
    call = dump["entries"][0]
    assert call["answers_json"] == {"A_income_proof_current": {"p": 0.93}}  # parsed back into an object
    assert call["state_json"] is None and call["latency_ms"] == 12.5 and call["seq"] == 2


def test_dump_to_file(tmp_db: Path, tmp_path: Path):
    populate(tmp_db)
    out = tmp_path / "out" / "trail.json"
    out.parent.mkdir()
    assert main(["audit", "dump", "--file-id", "F000456", "--db", str(tmp_db), "--out", str(out)]) == 0
    dump = json.loads(out.read_text(encoding="utf-8"))
    assert dump["file_id"] == "F000456" and len(dump["entries"]) == 1 and dump["chain_verified"] is True


def test_dump_reports_a_broken_chain(tmp_db: Path, capsys: pytest.CaptureFixture[str]):
    populate(tmp_db)
    with raw(tmp_db) as conn:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("UPDATE audit_log SET policy_outcome = 'X' WHERE seq = 3")
    main(["audit", "dump", "--file-id", "F000123", "--db", str(tmp_db)])
    assert json.loads(capsys.readouterr().out)["chain_verified"] is False


def test_dump_requires_file_id(tmp_db: Path):
    with pytest.raises(SystemExit) as exc:
        main(["audit", "dump", "--db", str(tmp_db)])
    assert exc.value.code == 2
