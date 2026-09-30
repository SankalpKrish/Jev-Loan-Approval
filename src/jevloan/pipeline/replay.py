"""Full-book offline replay and evaluator artifacts."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jevloan.audit.log import JSON_COLUMNS
from jevloan.canonical import sha256_hex
from jevloan.config import RuntimeConfig, resolve_path
from jevloan.data.schema import LoanFile, load_book
from jevloan.pipeline.runner import Pipeline, build_pipeline, decision_dict
from jevloan.state.base import STAGES


@dataclass(frozen=True)
class ReplaySummary:
    run_id: str
    files: int
    decisions: int
    stages: tuple[str, ...]
    decisions_path: str
    metadata_path: str
    audit_db_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _entry_json(entry: dict) -> dict:
    decoded = dict(entry)
    for key in JSON_COLUMNS:
        if decoded.get(key) is not None and isinstance(decoded[key], str):
            decoded[key] = json.loads(decoded[key])
    return decoded


def _replay_row(run_id: str, file: LoanFile, stage: str, decision: Any, trail: list[dict]) -> dict:
    events = [_entry_json(row) for row in trail if row.get("stage") == stage]
    calls = [row for row in events if row.get("event_type") in ("MODEL_CALL", "MODEL_FAILURE", "PII_BLOCK")]
    if not calls:
        raise RuntimeError(f"audit trail has no gateway result for {file.file_id} {stage}")
    call = calls[-1]
    detail = call.get("detail_json") if isinstance(call.get("detail_json"), dict) else {}
    failure = detail.get("failure")
    if call.get("event_type") == "PII_BLOCK":
        failure = "pii_blocked"
    answers = call.get("answers_json") if isinstance(call.get("answers_json"), dict) else {}
    return {
        "run_id": run_id,
        "file_id": file.file_id,
        "segment": file.segment,
        "stage": stage,
        "outcome": str(decision.outcome),
        "decision": decision_dict(decision),
        "answers": answers,
        "model_version": call.get("model_version"),
        "latency_ms": call.get("latency_ms") or 0.0,
        "input_tokens": call.get("input_tokens"),
        "failure_kind": failure,
        "gateway_audit_seq": call["seq"],
        "state_hash": call.get("state_hash"),
        "request_ts": call.get("request_ts"),
        "response_ts": call.get("response_ts"),
    }


async def replay(
    book_path: str | Path,
    runtime: RuntimeConfig,
    limit: int | None = None,
    concurrency: int | None = None,
    *,
    run_id: str | None = None,
    runs_dir: str | Path = "data/runs",
    pipeline: Pipeline | None = None,
) -> ReplaySummary:
    """Replay every selected file through all stages and write decisions plus reproducibility metadata."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    workers = concurrency if concurrency is not None else runtime.max_concurrency
    if workers < 1:
        raise ValueError("concurrency must be at least 1")
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in run_id):
        raise ValueError("run_id may contain only letters, numbers, '-' and '_'")

    source = resolve_path(book_path)
    book_files = list(load_book(source))
    files = book_files
    if limit is not None:
        files = files[:limit]
    run_dir = resolve_path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    active = pipeline or build_pipeline(runtime)
    decisions_path = run_dir / "decisions.jsonl"
    metadata_path = run_dir / "metadata.json"
    audit_path = resolve_path(runtime.db_path)
    metadata = {
        "run_id": run_id,
        "backend": runtime.backend,
        "model": runtime.model,
        "policy_version": active.policy_version,
        "pricing_version": active.pricing.version,
        "input_token_price_usd_per_million": runtime.usd_per_million_input_tokens,
        "policy_path": "config/policy/policy_v1.yaml",
        "book_path": str(source),
        "book_n": len(book_files),
        "processed_n": len(files),
        "stages": list(STAGES),
        "audit_db_path": str(audit_path),
        "runtime": runtime.model_dump(mode="json"),
        "book_sha256": sha256_hex(source.read_bytes()),
        "started_ts": datetime.now(UTC).isoformat(),
        "processed_file_ids": [file.file_id for file in files],
    }
    for name, config_path in (("policy", "config/policy/policy_v1.yaml"), ("pricing", "config/pricing/pricing_v1.yaml")):
        contents = resolve_path(config_path).read_bytes()
        frozen = run_dir / f"{name}.yaml"
        frozen.write_bytes(contents)
        metadata[f"{name}_path"] = str(frozen)
        metadata[f"{name}_sha256"] = sha256_hex(contents)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    semaphore = asyncio.Semaphore(workers)

    async def process(file: LoanFile) -> list[dict]:
        async with semaphore:
            before = len(active.audit.trail(file.file_id))
            decisions = await active.process_file(file, tuple(STAGES), offline=True)
            trail = active.audit.trail(file.file_id)[before:]
            return [_replay_row(run_id, file, stage, decisions[stage], trail) for stage in STAGES]

    try:
        file_rows = await asyncio.gather(*(process(file) for file in files))
        with decisions_path.open("w", encoding="utf-8") as out:
            for rows in file_rows:
                for row in rows:
                    out.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        metadata["input_tokens_total"] = sum(
            row["input_tokens"] or 0 for rows in file_rows for row in rows
        )
        metadata["finished_ts"] = datetime.now(UTC).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        if pipeline is None:
            await active.gateway.aclose()
    return ReplaySummary(
        run_id=run_id,
        files=len(files),
        decisions=len(files) * len(STAGES),
        stages=tuple(STAGES),
        decisions_path=str(decisions_path),
        metadata_path=str(metadata_path),
        audit_db_path=str(audit_path),
    )
