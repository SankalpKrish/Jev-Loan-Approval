from __future__ import annotations

from fastapi.testclient import TestClient

from jevloan.api.app import create_app
from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.data.schema import write_book
from jevloan.policy.config import load_policy


def _app(tmp_path, *, force_human=True, seed=29):
    book_path = tmp_path / "book.jsonl"
    write_book(generate_book(2, seed), book_path)
    runtime = RuntimeConfig(
        backend="sim",
        db_path=str(tmp_path / "api.db"),
        book_path=str(book_path),
        force_human_review=force_human,
        timeout_budget_s=0.2,
    )
    return create_app(runtime), runtime, book_path


def test_review_probabilities_are_bound_to_each_queue_attempt(tmp_path):
    app, _, _ = _app(tmp_path, force_human=False, seed=7)
    file_id = generate_book(2, 7)[0].file_id
    with TestClient(app) as client:
        first_score = client.post(f"/api/v1/files/{file_id}/score").json()
        first_items = client.get("/api/v1/queue").json()["items"]
        first_item = next(item for item in first_items if item["file_id"] == file_id)
        first_answer_ids = set(first_score["answers"])
        assert first_answer_ids

        # A later attempt fails before getting model answers, and creates a second review item.
        app.state.runtime.force_human_review = True
        later_score = client.post(f"/api/v1/files/{file_id}/score").json()
        assert later_score["outcome"] == "HUMAN_REVIEW"
        all_items = client.get("/api/v1/queue").json()["items"]
        later_item = max((item for item in all_items if item["file_id"] == file_id), key=lambda item: item["id"])
        assert later_item["id"] != first_item["id"]

        later_page = client.get(f"/review/{later_item['id']}").text
        assert "No successful model answer is recorded for this stage." in later_page
        assert not any(qid in later_page for qid in first_answer_ids)

        first_page = client.get(f"/review/{first_item['id']}").text
        assert all(qid in first_page for qid in first_answer_ids)


def test_score_failure_is_advisory_and_queued_for_human(tmp_path):
    app, runtime, book_path = _app(tmp_path)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{file_id}/score", params={"stage": "appraisal"})
        assert response.status_code == 200
        body = response.json()
        assert body["advisory_only"] is True
        assert "human reviewer" in body["disclaimer"].lower()
        assert body["outcome"] == "HUMAN_REVIEW"
        assert "approved" not in response.text.lower()
        listed = client.get("/api/v1/queue", params={"status": "open"}).json()["items"]
        assert len(listed) == 1
        trail = client.get(f"/api/v1/files/{file_id}/trail").json()
        assert [row["event_type"] for row in trail["entries"]].count("MODEL_FAILURE") == 1
        assert client.get("/api/v1/health").json()["human_only"] is True


def test_decisions_require_named_reviewer_and_duplicate_is_conflict(tmp_path):
    app, _, _ = _app(tmp_path)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        client.post(f"/api/v1/files/{file_id}/score")
        item = client.get("/api/v1/queue").json()["items"][0]
        reason_code = next(iter(load_policy().reason_codes_for(item["queue"])))
        response = client.post(f"/api/v1/queue/{item['id']}/decision", json={
            "decision": "accept", "reason_code": reason_code, "reviewer_id": "Reviewer K",
        })
        assert response.status_code == 200
        duplicate = client.post(f"/api/v1/queue/{item['id']}/decision", json={
            "decision": "reject", "reason_code": reason_code, "reviewer_id": "Reviewer M",
        })
        assert duplicate.status_code == 409
        assert client.get(f"/api/v1/queue/{item['id']}").json()["reviewer_id"] == "Reviewer K"


def test_sanction_docs_cannot_run_from_model_proceed_or_generic_accept(tmp_path):
    app, _, _ = _app(tmp_path)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{file_id}/score", params={"stage": "sanction_docs"})
        assert response.status_code == 409
        # An accept on an ordinary review item is not the explicit sanction authority marker.
        client.post(f"/api/v1/files/{file_id}/score", params={"stage": "appraisal"})
        items = client.get("/api/v1/queue").json()["items"]
        item = next(row for row in items if row["file_id"] == file_id)
        reason_code = next(iter(load_policy().reason_codes_for(item["queue"])))
        client.post(f"/api/v1/queue/{item['id']}/decision", json={
            "decision": "accept", "reason_code": reason_code, "reviewer_id": "Reviewer K",
        })
        assert client.post(f"/api/v1/files/{file_id}/score", params={"stage": "sanction_docs"}).status_code == 409


def test_review_pages_render_reason_confidence_and_named_decision_form(tmp_path):
    app, _, _ = _app(tmp_path)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        client.post(f"/api/v1/files/{file_id}/score")
        item = client.get("/api/v1/queue").json()["items"][0]
        page = client.get(f"/review/{item['id']}")
        assert page.status_code == 200
        assert "Top doubt factor" in page.text
        assert "confidence" in page.text.lower()
        assert "reviewer_id" in page.text
        code = next(iter(load_policy().reason_codes_for(item["queue"])))
        result = client.post(f"/review/{item['id']}/decision", data={
            "decision": "modify", "reason_code": code, "reviewer_id": "Named reviewer",
        }, follow_redirects=False)
        assert result.status_code == 303
        assert client.get(f"/review/{item['id']}").status_code == 200


def test_invalid_and_unknown_queue_inputs_are_safe(tmp_path):
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/queue", params={"queue": "not-a-queue"}).status_code == 422
        assert client.get("/api/v1/queue/9999").status_code == 404
        invalid = client.post("/api/v1/queue/9999/decision", json={
            "decision": "accept", "reason_code": "bad", "reviewer_id": "",
        })
        assert invalid.status_code == 422
        assert "Traceback" not in invalid.text


def test_successful_simulated_score_exposes_probabilities_as_advice(tmp_path):
    app, _, _ = _app(tmp_path, force_human=False)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()
        assert health["audit_ok"] is True
        assert health["backend"] == "sim"
        assert health["human_only"] is False
        response = client.post(f"/api/v1/files/{file_id}/score")
        assert response.status_code == 200
        payload = response.json()
        assert payload["advisory_only"] is True
        assert payload["answers"]
        assert all("confidence" in answer for answer in payload["answers"].values())
        page_item = client.get("/api/v1/queue").json()["items"]
        if page_item:
            page = client.get(f"/review/{page_item[0]['id']}")
            assert page.status_code == 200
            assert "Model probabilities and confidence" in page.text


def test_personal_identifiers_in_reviewer_notes_never_enter_audit(tmp_path):
    app, _, _ = _app(tmp_path)
    file_id = generate_book(2, 29)[0].file_id
    with TestClient(app) as client:
        client.post(f"/api/v1/files/{file_id}/score")
        item = client.get("/api/v1/queue").json()["items"][0]
        code = next(iter(load_policy().reason_codes_for(item["queue"])))
        response = client.post(f"/api/v1/queue/{item['id']}/decision", json={
            "decision": "modify", "reason_code": code, "reviewer_id": "Reviewer K",
            "notes": "PAN ABCDE1234F was attached",
        })
        assert response.status_code == 422
        entry = client.get(f"/api/v1/files/{file_id}/trail").text
        assert "ABCDE1234F" not in entry
