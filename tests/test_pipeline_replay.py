import asyncio
import json
import os

import pytest
from jevloan.audit.log import AuditLog
from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.data.schema import write_book
from jevloan.cli import main
from jevloan.pipeline.replay import replay
from jevloan.pipeline.runner import build_pipeline
from jevloan.pipeline.trail import trail_complete
from jevloan.policy.outcomes import Outcome


def test_fifty_file_sim_replay_writes_eval_rows_and_complete_trails(tmp_path):
    book_path = tmp_path / "book.jsonl"
    write_book(generate_book(50, seed=91), book_path)
    runtime = RuntimeConfig(
        backend="sim",
        db_path=str(tmp_path / "audit.sqlite3"),
        book_path=str(book_path),
        max_concurrency=8,
    )

    summary = asyncio.run(
        replay(
            book_path,
            runtime,
            concurrency=8,
            run_id="integration-50",
            runs_dir=tmp_path / "runs",
        )
    )

    assert summary.files == 50
    assert summary.decisions == 150
    rows = [json.loads(line) for line in (tmp_path / "runs/integration-50/decisions.jsonl").read_text().splitlines()]
    assert len(rows) == 150
    assert {(row["file_id"], row["stage"]) for row in rows} == {
        (file.file_id, stage) for file in generate_book(50, seed=91) for stage in ("appraisal", "sanction_docs", "monitoring")
    }
    for row in rows:
        assert row["run_id"] == "integration-50"
        assert row["decision"]["file_id"] == row["file_id"]
        assert row["decision"]["stage"] == row["stage"]
        assert row["decision"]["modules"]
        assert isinstance(row["answers"], dict)
        for answer in row["answers"].values():
            assert answer["type"] in {"noul", "score", "choice"}
            if answer["type"] == "noul":
                assert "noul" in answer and "derived_confidence" in answer
            else:
                assert "confidence" in answer
            if answer["type"] in {"score", "choice"}:
                assert isinstance(answer["probabilities"], dict)
        assert "latency_ms" in row and "input_tokens" in row and "failure_kind" in row

    metadata = json.loads((tmp_path / "runs/integration-50/metadata.json").read_text())
    assert metadata["backend"] == "sim"
    assert metadata["model"] == runtime.model
    assert metadata["policy_version"]
    assert metadata["pricing_version"]
    assert metadata["input_token_price_usd_per_million"] == runtime.usd_per_million_input_tokens
    assert metadata["book_path"] == str(book_path)
    assert metadata["book_n"] == 50
    assert metadata["processed_n"] == 50
    assert metadata["stages"] == ["appraisal", "sanction_docs", "monitoring"]
    assert metadata["input_tokens_total"] == sum(row["input_tokens"] or 0 for row in rows)
    assert metadata["audit_db_path"] == runtime.db_path

    audit = AuditLog(runtime.db_path)
    assert audit.verify().ok
    for file in generate_book(50, seed=91):
        assert trail_complete(audit, file.file_id, ("appraisal", "sanction_docs", "monitoring")) == (True, [])


def test_online_sanction_docs_require_explicit_audited_human_authorization(tmp_path):
    file = generate_book(1, seed=17)[0]
    runtime = RuntimeConfig(backend="sim", db_path=str(tmp_path / "audit.sqlite3"))
    pipeline = build_pipeline(runtime)

    async def attempt():
        try:
            await pipeline.run_stage(file, "sanction_docs")
        except PermissionError:
            return False
        return True

    assert asyncio.run(attempt()) is False
    assert pipeline.audit.trail(file.file_id) == []

    pipeline.audit.append(
        event_type="HUMAN_DECISION",
        file_id=file.file_id,
        segment=file.segment,
        stage="appraisal",
        policy_version=pipeline.policy_version,
        policy_outcome=Outcome.PROCEED_TO_SANCTIONING_AUTHORITY.value,
        human_decision="accept",
        reviewer_id="reviewer-1",
        detail_json={"sanction_authorized": True},
    )
    assert pipeline.has_sanction_authorization(file.file_id)

    async def run_authorized():
        try:
            result = await pipeline.run_stage(file, "sanction_docs")
            return result
        finally:
            await pipeline.gateway.aclose()

    decision = asyncio.run(run_authorized())
    assert decision.stage == "sanction_docs"


def test_check_trails_cli_counts_repeated_stage_attempts(tmp_path, capsys):
    file = generate_book(1, seed=24)[0]
    runtime = RuntimeConfig(backend="sim", db_path=str(tmp_path / "audit.sqlite3"))
    pipeline = build_pipeline(runtime)

    async def run_twice():
        try:
            await pipeline.run_stage(file, "appraisal", offline=True)
            await pipeline.run_stage(file, "appraisal", offline=True)
        finally:
            await pipeline.gateway.aclose()

    asyncio.run(run_twice())
    assert main([
        "pipeline", "check-trails", "--file-id", file.file_id, "--stages", "appraisal,appraisal",
        "--db", runtime.db_path,
    ]) == 0
    assert "trails complete: 1/1" in capsys.readouterr().out


@pytest.mark.real_jev
def test_twenty_file_real_jev_smoke_replay(tmp_path):
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        pytest.skip("TYPESAFE_API_KEY is not set")
    book_path = tmp_path / "book.jsonl"
    write_book(generate_book(20, seed=7), book_path)
    runtime = RuntimeConfig(
        backend="real",
        db_path=str(tmp_path / "real-audit.sqlite3"),
        book_path=str(book_path),
        max_concurrency=2,
    )

    summary = asyncio.run(
        replay(
            book_path,
            runtime,
            concurrency=2,
            run_id="real-smoke-20",
            runs_dir=tmp_path / "runs",
        )
    )

    assert summary.files == 20
    assert summary.decisions == 60
    assert len((tmp_path / "runs/real-smoke-20/decisions.jsonl").read_text().splitlines()) == 60
    rows = [json.loads(line) for line in (tmp_path / "runs/real-smoke-20/decisions.jsonl").read_text().splitlines()]
    assert all(row["answers"] and row["failure_kind"] is None for row in rows), "real smoke requires actual model answers"
    assert all(row["model_version"] and not row["model_version"].startswith("sim-") for row in rows)
    assert AuditLog(runtime.db_path).verify().ok
