from __future__ import annotations

from fastapi.testclient import TestClient

from jevloan.api.app import create_app
from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.data.schema import write_book
from jevloan.policy.outcomes import Outcome


def _authorized_app(tmp_path):
    file = generate_book(1, 73)[0]
    book = tmp_path / "book.jsonl"
    write_book([file], book)
    runtime = RuntimeConfig(
        backend="sim", db_path=str(tmp_path / "auth.db"), book_path=str(book),
        force_human_review=True,
    )
    return create_app(runtime), file.file_id


def _authorization_event(pipeline, file_id: str, *, version: str | None = None, decision="accept"):
    return pipeline.audit.append(
        event_type="HUMAN_DECISION",
        file_id=file_id,
        stage="appraisal",
        policy_version=version or pipeline.policy_version,
        policy_outcome=Outcome.PROCEED_TO_SANCTIONING_AUTHORITY.value,
        human_decision=decision,
        human_reason_code="AUTHORITY_REVIEWED",
        reviewer_id="Named credit authority",
        detail_json={"sanction_authorized": True},
    )


def test_newer_human_rejection_revokes_earlier_sanction_authorization(tmp_path):
    app, file_id = _authorized_app(tmp_path)
    pipeline = app.state.pipeline
    with TestClient(app) as client:
        _authorization_event(pipeline, file_id)
        assert pipeline.has_sanction_authorization(file_id) is True
        # The authorized stage is reachable, though the model is currently configured human-only.
        assert client.post(f"/api/v1/files/{file_id}/score", params={"stage": "sanction_docs"}).status_code == 200

        _authorization_event(pipeline, file_id, decision="reject")
        assert pipeline.has_sanction_authorization(file_id) is False
        blocked = client.post(f"/api/v1/files/{file_id}/score", params={"stage": "sanction_docs"})
        assert blocked.status_code == 409


def test_sanction_authorization_from_an_old_policy_version_is_rejected(tmp_path):
    app, file_id = _authorized_app(tmp_path)
    pipeline = app.state.pipeline
    with TestClient(app) as client:
        _authorization_event(pipeline, file_id, version="policy-v0-obsolete")
        assert pipeline.has_sanction_authorization(file_id) is False
        response = client.post(f"/api/v1/files/{file_id}/score", params={"stage": "sanction_docs"})
        assert response.status_code == 409
