"""Advisory HTTP API and human review pages (PLAN section 3.13)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jevloan.config import RuntimeConfig, resolve_path
from jevloan.data.schema import LoanFile, load_book
from jevloan.pipeline.runner import build_pipeline
from jevloan.policy.config import load_policy
from jevloan.pii.gate import PIIGate, PIIBlocked
from jevloan.queue.store import ItemAlreadyDecided, ItemNotFound, InvalidDecision

DISCLAIMER = "Advisory only. A named human reviewer makes every decision; model output never approves or sanctions a loan."
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accept", "modify", "reject"]
    reason_code: str = Field(min_length=1, max_length=100)
    reviewer_id: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


def _safe_item(item) -> dict:
    return item.to_dict()


def _check_review_notes(notes: str | None, gate: PIIGate) -> None:
    """Reviewer notes are free text that will enter the audit log, so reject PII before storing them."""
    if not notes:
        return
    try:
        gate.check({"reviewer_notes": notes})
    except PIIBlocked as exc:
        raise HTTPException(status_code=422, detail="review notes cannot contain personal or account identifiers") from exc


def _decision_json(decision) -> dict:
    """A deliberately narrow, JSON-safe advisory representation."""
    modules = {}
    for name, result in decision.modules.items():
        modules[name] = {
            "outcome": result.outcome,
            "score": result.score,
            "band": result.band,
            "low_confidence": result.low_confidence,
            "reasons": [r.__dict__ for r in result.reasons],
        }
    return {
        "file_id": decision.file_id,
        "segment": decision.segment,
        "stage": decision.stage,
        "outcome": str(decision.outcome),
        "queue": decision.queue,
        "closeness": decision.closeness,
        "top_reason": None if decision.top_reason is None else decision.top_reason.__dict__,
        "modules": modules,
        "reason_codes": list(decision.reason_codes),
        "reasons": [r.__dict__ for r in decision.reasons],
        "deficiency_items": list(decision.deficiency_items),
        "composite": decision.composite,
        "band": decision.band,
        "answers": decision.derived,
        "advisory_only": True,
        "disclaimer": DISCLAIMER,
    }


def _probabilities(audit, file_id: str, stage: str, queue_item_id: int | None = None) -> list[dict]:
    """Read the model result that produced this queue item, including an empty failed result."""
    trail = audit.trail(file_id)
    cutoff = float("inf")
    if queue_item_id is not None:
        cutoff = None
        for row in trail:
            if row.get("event_type") != "QUEUED":
                continue
            detail = json.loads(row.get("detail_json") or "{}")
            if detail.get("queue_item_id") == queue_item_id:
                cutoff = row["seq"]
                break
        if cutoff is None:
            return []
    for row in reversed(trail):
        if row["seq"] >= cutoff or row.get("stage") != stage:
            continue
        if row.get("event_type") in {"MODEL_FAILURE", "PII_BLOCK"}:
            return []
        if row.get("event_type") != "MODEL_CALL":
            continue
        try:
            answers = json.loads(row.get("answers_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return []
        rows = []
        for qid, answer in answers.items():
            if not isinstance(answer, dict):
                continue
            kind = answer.get("type")
            if kind == "noul":
                probability = answer.get("noul")
                confidence = answer.get("derived_confidence")
            elif kind == "choice":
                probability = answer.get("probabilities")
                confidence = answer.get("confidence")
            elif kind == "score":
                probability = answer.get("probabilities")
                confidence = answer.get("confidence")
            else:
                continue
            rows.append({"qid": qid, "type": kind, "probability": probability, "confidence": confidence})
        return rows
    return []


def create_app(runtime: RuntimeConfig, *, transport=None) -> FastAPI:
    """Create the API around the normal guarded pipeline. The gateway closes with the app lifespan."""
    pipeline = build_pipeline(runtime, transport=transport)
    policy = pipeline.policy or load_policy()
    human_note_gate = PIIGate()
    files: dict[str, LoanFile] | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal files
        try:
            files = {item.file_id: item for item in load_book(runtime.book_path)}
        except FileNotFoundError:
            # Health and queue inspection remain useful before the book is provisioned.
            files = {}
        try:
            yield
        finally:
            await pipeline.gateway.aclose()

    app = FastAPI(title="Jev Loan Advisory API", version="1", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.pipeline = pipeline

    def book_file(file_id: str) -> LoanFile:
        nonlocal files
        if files is None:
            try:
                files = {item.file_id: item for item in load_book(runtime.book_path)}
            except FileNotFoundError:
                files = {}
        try:
            return files[file_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="file not found") from exc

    @app.get("/api/v1/health")
    async def health():
        try:
            audit = pipeline.audit.verify()
            audit_ok = audit.ok
        except Exception:
            audit_ok = False
        return {
            "backend": runtime.backend,
            "model": runtime.model,
            "breaker_state": pipeline.gateway.breaker.state,
            "human_only": pipeline.gateway.human_only,
            "policy_version": pipeline.policy_engine.policy_version,
            "audit_ok": audit_ok,
        }

    @app.post("/api/v1/files/{file_id}/score")
    async def score(file_id: str, stage: Literal["appraisal", "sanction_docs", "monitoring"] = Query("appraisal")):
        file = book_file(file_id)
        if stage == "sanction_docs" and not pipeline.has_sanction_authorization(file_id):
            raise HTTPException(status_code=409, detail="named human sanction authorization is required before sanction document review")
        try:
            result = await pipeline.run_stage(file, stage)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="named human sanction authorization is required before sanction document review") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="file could not be processed") from exc
        except Exception as exc:
            # Internal errors are logged by the underlying layers; no exception details escape the API.
            raise HTTPException(status_code=503, detail="advisory processing is temporarily unavailable") from exc
        return _decision_json(result)

    @app.get("/api/v1/queue")
    async def queue_list(queue: str | None = None, status: Literal["open", "decided"] | None = "open"):
        try:
            items = pipeline.queue.list(queue=queue, status=status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid queue filter") from exc
        return {"items": [_safe_item(item) for item in items]}

    @app.get("/api/v1/queue/{item_id}")
    async def queue_item(item_id: int):
        try:
            return _safe_item(pipeline.queue.get(item_id))
        except ItemNotFound as exc:
            raise HTTPException(status_code=404, detail="queue item not found") from exc

    @app.post("/api/v1/queue/{item_id}/decision")
    async def queue_decision(item_id: int, body: DecisionBody):
        _check_review_notes(body.notes, human_note_gate)
        try:
            entry = pipeline.queue.decide(
                item_id,
                decision=body.decision,
                reason_code=body.reason_code,
                reviewer_id=body.reviewer_id,
                notes=body.notes,
            )
        except ItemNotFound as exc:
            raise HTTPException(status_code=404, detail="queue item not found") from exc
        except ItemAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail="queue item already has a human decision") from exc
        except InvalidDecision as exc:
            raise HTTPException(status_code=422, detail="decision, reason code, and named reviewer are invalid") from exc
        return {"decision_recorded": True, "audit_seq": entry["seq"], "advisory_only": True, "disclaimer": DISCLAIMER}

    @app.get("/api/v1/files/{file_id}/trail")
    async def trail(file_id: str):
        rows = pipeline.audit.trail(file_id)
        if not rows:
            # Distinguish an unknown synthetic file from a known file with no calls yet.
            book_file(file_id)
        return {"file_id": file_id, "entries": rows}

    @app.get("/review", response_class=HTMLResponse)
    async def review_list(request: Request):
        items = pipeline.queue.list(status="open")
        return TEMPLATES.TemplateResponse(request, "review_list.html", {"items": items, "disclaimer": DISCLAIMER})

    @app.get("/review/{item_id}", response_class=HTMLResponse)
    async def review_detail(request: Request, item_id: int):
        try:
            item = pipeline.queue.get(item_id)
        except ItemNotFound as exc:
            raise HTTPException(status_code=404, detail="queue item not found") from exc
        probability_rows = _probabilities(pipeline.audit, item.file_id, item.stage, item.id)
        return TEMPLATES.TemplateResponse(request, "review_detail.html", {
            "item": item,
            "probabilities": probability_rows,
            "disclaimer": DISCLAIMER,
            "reason_codes": policy.reason_codes_for(item.queue),
            "top_doubt": item.top_reason,
            "reasons": item.reasons,
            "score_qids": {row["qid"] for row in probability_rows if row["type"] == "score"},
        })

    @app.post("/review/{item_id}/decision")
    async def review_post(item_id: int, request: Request):
        # Parse URL-encoded forms with the standard library; python-multipart is intentionally unnecessary.
        raw = (await request.body()).decode("utf-8", errors="replace")
        form = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
        try:
            body = DecisionBody.model_validate(form)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="decision form is incomplete or invalid") from exc
        _check_review_notes(body.notes, human_note_gate)
        try:
            pipeline.queue.decide(
                item_id, decision=body.decision, reason_code=body.reason_code,
                reviewer_id=body.reviewer_id, notes=body.notes,
            )
        except ItemNotFound as exc:
            raise HTTPException(status_code=404, detail="queue item not found") from exc
        except ItemAlreadyDecided as exc:
            raise HTTPException(status_code=409, detail="queue item already has a human decision") from exc
        except InvalidDecision as exc:
            raise HTTPException(status_code=422, detail="decision, reason code, and named reviewer are invalid") from exc
        return RedirectResponse("/review", status_code=303)

    return app
