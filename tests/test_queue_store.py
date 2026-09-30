"""The human queue: ordering, the decide round trip into the audit chain, and the ways it must refuse."""

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from test_policy_catalog import appraisal_answers, engine, make_call, noul, score

from jevloan.audit.log import AuditLog
from jevloan.policy.config import load_policy
from jevloan.policy.engine import ModuleResult, Reason, StageDecision
from jevloan.policy.outcomes import QUEUES, Outcome
from jevloan.queue.store import (
    DECISIONS,
    HumanQueue,
    InvalidDecision,
    ItemAlreadyDecided,
    ItemNotFound,
    QueueItem,
)

QUEUE_OF = {
    Outcome.HUMAN_REVIEW: "credit_review", Outcome.DECLINE_RECOMMENDED: "decline_confirmation",
    Outcome.DEFICIENCY_NOTICE: "deficiency_ops", Outcome.FRAUD_INVESTIGATION: "fraud_investigation",
    Outcome.DISBURSAL_BLOCKED: "disbursal_correction", Outcome.WATCHLIST_T2: "watchlist_review",
}


def decision(file_id="F000001", outcome=Outcome.HUMAN_REVIEW, queue=None, closeness=None, stage="appraisal", top=None) -> StageDecision:
    return StageDecision(
        file_id=file_id, segment="salaried_personal", stage=stage, outcome=outcome, queue=queue or QUEUE_OF[outcome], closeness=closeness,
        top_reason=top, modules={"C": ModuleResult("C", "borderline", 0.6, "borderline", [], [], False)},
        reason_codes=["COMPOSITE_BORDERLINE"], policy_version="policy-2026.09-v1",
        reasons=[Reason("C_capacity", 2.0, 0.9, "RUBRIC[C_capacity]")],
    )


@pytest.fixture
def audit(tmp_db: Path) -> AuditLog:
    return AuditLog(tmp_db)


@pytest.fixture
def queue(tmp_db: Path, audit: AuditLog) -> HumanQueue:
    return HumanQueue(tmp_db, audit)


def raw_rows(db: Path, sql: str, *params):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]


def force_created(db: Path, item_id: int, ts: str) -> None:
    with closing(sqlite3.connect(db, isolation_level=None)) as conn:
        conn.execute("UPDATE queue_items SET created_ts = ? WHERE id = ?", (ts, item_id))


# ------------------------------------------------------------------------------------------------ enqueue


def test_enqueue_stores_the_item_and_writes_a_queued_audit_entry(queue, audit):
    top = Reason("D_doubt_debt_burden", 0.85, 0.7, "RUBRIC[D_doubt_debt_burden]")
    item_id = queue.enqueue(decision(closeness=0.75, top=top))
    item = queue.get(item_id)
    assert isinstance(item, QueueItem)
    assert (item.file_id, item.segment, item.stage, item.queue, item.outcome) == ("F000001", "salaried_personal", "appraisal", "credit_review", "HUMAN_REVIEW")
    assert (item.status, item.closeness, item.decided_ts, item.decision) == ("open", 0.75, None, None)
    assert item.top_reason == {"qid": "D_doubt_debt_burden", "p": 0.85, "confidence": 0.7, "text": "RUBRIC[D_doubt_debt_burden]"}
    assert item.reasons == [{"qid": "C_capacity", "p": 2.0, "confidence": 0.9, "text": "RUBRIC[C_capacity]"}]
    assert item.reason_codes == ["COMPOSITE_BORDERLINE"] and item.policy_version == "policy-2026.09-v1" and item.created_ts.endswith("Z")

    (entry,) = audit.trail("F000001")
    assert entry["event_type"] == "QUEUED" and (entry["segment"], entry["stage"], entry["policy_outcome"]) == ("salaried_personal", "appraisal", "HUMAN_REVIEW")
    assert entry["policy_version"] == "policy-2026.09-v1"
    assert json.loads(entry["detail_json"]) == {"queue_item_id": item_id, "queue": "credit_review", "reason_codes": ["COMPOSITE_BORDERLINE"], "closeness": 0.75}
    assert audit.verify().ok


def test_a_decision_without_a_queue_is_not_enqueued(queue, audit):
    proceed = StageDecision("F1", "salaried_personal", "appraisal", Outcome.PROCEED_TO_SANCTIONING_AUTHORITY, None, None, None, {}, ["COMPOSITE_PASS"], "v")
    with pytest.raises(ValueError, match="no valid queue"):
        queue.enqueue(proceed)
    with pytest.raises(ValueError, match="no valid queue"):
        queue.enqueue(decision(queue="made_up"))
    assert queue.list(status=None) == [] and audit.verify().entries == 0


def test_a_real_engine_decision_round_trips_through_the_queue(queue):
    answers = appraisal_answers() | {"C_willingness": score(2), "C_capacity": score(2), "D_doubt_debt_burden": noul(0.9)}
    d = engine().decide(file_id="F000077", segment="salaried_personal", stage="appraisal", call=make_call(answers), state={})
    item = queue.get(queue.enqueue(d))
    assert item.queue == "credit_review" and item.closeness == d.closeness == 0.75
    assert item.top_reason["qid"] == "D_doubt_debt_burden" and item.top_reason["text"] == "RUBRIC[D_doubt_debt_burden]"
    assert item.reason_codes == ["COMPOSITE_BORDERLINE"] and [r["qid"] for r in item.reasons][0] in ("C_willingness", "C_capacity")


def test_queue_items_live_in_the_audit_database_file(queue, tmp_db):
    queue.enqueue(decision())
    tables = {r["name"] for r in raw_rows(tmp_db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"audit_log", "queue_items"} <= tables


# ------------------------------------------------------------------------------------------------ ordering


def test_credit_review_lists_the_file_nearest_to_passing_first(queue, tmp_db):
    ids = {name: queue.enqueue(decision(file_id=name, closeness=c)) for name, c in
           [("far", 0.1), ("near", 0.9), ("none", None), ("mid", 0.5), ("far2", 0.1)]}
    assert [i.file_id for i in queue.list("credit_review")] == ["near", "mid", "far", "far2", "none"]  # ties: oldest first; unranked last
    force_created(tmp_db, ids["far2"], "2000-01-01T00:00:00.000Z")  # now far2 is the older of the two ties
    assert [i.file_id for i in queue.list("credit_review")] == ["near", "mid", "far2", "far", "none"]


def test_unranked_credit_review_items_are_oldest_first(queue, tmp_db):
    a, b, c = (queue.enqueue(decision(file_id=f)) for f in ("a", "b", "c"))
    force_created(tmp_db, c, "2000-01-01T00:00:00.000Z")
    assert [i.file_id for i in queue.list("credit_review")] == ["c", "a", "b"]


@pytest.mark.parametrize("outcome", [Outcome.DECLINE_RECOMMENDED, Outcome.DEFICIENCY_NOTICE, Outcome.FRAUD_INVESTIGATION,
                                     Outcome.DISBURSAL_BLOCKED, Outcome.WATCHLIST_T2])
def test_other_queues_are_oldest_first_whatever_the_closeness(queue, tmp_db, outcome):
    ids = [queue.enqueue(decision(file_id=f, outcome=outcome, closeness=c)) for f, c in (("a", 0.1), ("b", 0.9), ("c", None))]
    assert [i.file_id for i in queue.list(QUEUE_OF[outcome])] == ["a", "b", "c"]
    force_created(tmp_db, ids[2], "2000-01-01T00:00:00.000Z")
    assert [i.file_id for i in queue.list(QUEUE_OF[outcome])] == ["c", "a", "b"]


def test_listing_filters_by_queue_and_status(queue):
    review = queue.enqueue(decision(file_id="r"))
    fraud = queue.enqueue(decision(file_id="f", outcome=Outcome.FRAUD_INVESTIGATION))
    queue.decide(review, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="rev1")
    assert [i.file_id for i in queue.list()] == ["f"]  # open items, all queues
    assert [i.file_id for i in queue.list("credit_review")] == []
    assert [i.file_id for i in queue.list("credit_review", status="decided")] == ["r"]
    assert sorted(i.file_id for i in queue.list(status=None)) == ["f", "r"]
    assert [i.queue for i in queue.list(status=None)] == ["credit_review", "fraud_investigation"]  # grouped by queue
    assert queue.list("fraud_investigation")[0].id == fraud
    with pytest.raises(ValueError, match="unknown queue"):
        queue.list("nope")
    with pytest.raises(ValueError, match="unknown status"):
        queue.list(status="closed")


# ------------------------------------------------------------------------------------------------ decide


@pytest.mark.parametrize("verdict", DECISIONS)
def test_decide_round_trip_writes_a_human_decision_and_the_chain_verifies(queue, audit, verdict):
    item_id = queue.enqueue(decision(file_id="F000009", closeness=0.6))
    entry = queue.decide(item_id, decision=verdict, reason_code="MODIFY_TERMS", reviewer_id="  asha.rao  ", notes="shortened tenure")

    assert entry["event_type"] == "HUMAN_DECISION" and entry["seq"] == 2
    assert (entry["human_decision"], entry["human_reason_code"], entry["reviewer_id"]) == (verdict, "MODIFY_TERMS", "asha.rao")
    assert (entry["file_id"], entry["segment"], entry["stage"], entry["policy_outcome"]) == ("F000009", "salaried_personal", "appraisal", "HUMAN_REVIEW")
    assert entry["policy_version"] == "policy-2026.09-v1" and entry["decision_ts"].endswith("Z")
    assert json.loads(entry["detail_json"]) == {"queue_item_id": item_id, "queue": "credit_review", "notes": "shortened tenure"}

    item = queue.get(item_id)
    assert (item.status, item.decision, item.reason_code, item.reviewer_id, item.notes) == ("decided", verdict, "MODIFY_TERMS", "asha.rao", "shortened tenure")
    assert item.decided_ts == entry["decision_ts"]
    assert [e["event_type"] for e in audit.trail("F000009")] == ["QUEUED", "HUMAN_DECISION"]
    assert audit.verify().ok and audit.verify().entries == 2
    assert queue.list("credit_review") == []  # no longer open


def test_notes_are_optional(queue, audit):
    entry = queue.decide(queue.enqueue(decision()), decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    assert json.loads(entry["detail_json"])["notes"] is None


def test_deciding_twice_raises_and_writes_nothing_more(queue, audit):
    item_id = queue.enqueue(decision())
    queue.decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="first")
    with pytest.raises(ItemAlreadyDecided, match="first"):
        queue.decide(item_id, decision="reject", reason_code="REJECT_MODEL_MISREAD_DOCS", reviewer_id="second")
    item = queue.get(item_id)
    assert (item.decision, item.reviewer_id) == ("accept", "first")  # the first decision stands
    assert sum(e["event_type"] == "HUMAN_DECISION" for e in audit.iter_all()) == 1


@pytest.mark.parametrize("code", ["NOT_A_CODE", "", "accept_as_advised", "MODEL_TIMEOUT", None])
def test_an_unknown_reason_code_is_refused_and_the_item_stays_open(queue, audit, code):
    item_id = queue.enqueue(decision())
    with pytest.raises(InvalidDecision, match="reason code"):
        queue.decide(item_id, decision="accept", reason_code=code, reviewer_id="r1")
    assert queue.get(item_id).status == "open" and audit.verify().entries == 1  # engine-only codes are not reviewer codes


@pytest.mark.parametrize("verdict", ["approve", "ACCEPT", "", None, "sanction"])
def test_only_accept_modify_or_reject_is_a_decision(queue, verdict):
    item_id = queue.enqueue(decision())
    with pytest.raises(InvalidDecision, match="decision"):
        queue.decide(item_id, decision=verdict, reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    assert queue.get(item_id).status == "open"


@pytest.mark.parametrize("reviewer", ["", "   ", None])
def test_a_named_reviewer_is_required(queue, reviewer):
    item_id = queue.enqueue(decision())
    with pytest.raises(InvalidDecision, match="reviewer_id"):
        queue.decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id=reviewer)
    assert queue.get(item_id).status == "open"


def test_an_unknown_item_is_not_found(queue):
    with pytest.raises(ItemNotFound):
        queue.decide(999, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    with pytest.raises(ItemNotFound):
        queue.get(999)
    assert issubclass(ItemNotFound, LookupError) and issubclass(InvalidDecision, ValueError)


def test_reason_codes_come_from_the_policy_it_was_given(tmp_db, audit, tmp_path):
    from test_policy_catalog import policy_variant

    policy = policy_variant(tmp_path, lambda d: d["reason_codes"]["credit_review"].pop("MODIFY_TERMS"))
    strict = HumanQueue(tmp_db, audit, policy)
    item_id = strict.enqueue(decision())
    with pytest.raises(InvalidDecision):
        strict.decide(item_id, decision="modify", reason_code="MODIFY_TERMS", reviewer_id="r1")
    strict.decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")


def test_every_reviewer_code_in_the_shipped_policy_is_accepted(queue):
    codes = load_policy().human_reason_codes()
    for code in codes:
        queue.decide(queue.enqueue(decision()), decision="accept", reason_code=code, reviewer_id="r1")


# ------------------------------------------------------------------------------------------------ failures and concurrency


class ExplodingAudit:
    def append(self, **fields):
        raise RuntimeError("audit disk full")


def test_enqueue_leaves_no_orphan_item_if_the_audit_write_fails(tmp_db, audit):
    broken = HumanQueue(tmp_db, ExplodingAudit())
    with pytest.raises(RuntimeError, match="disk full"):
        broken.enqueue(decision())
    assert broken.list(status=None) == []


def test_a_decision_the_audit_log_could_not_record_does_not_stand(tmp_db, audit):
    good = HumanQueue(tmp_db, audit)
    item_id = good.enqueue(decision())
    with pytest.raises(RuntimeError, match="disk full"):
        HumanQueue(tmp_db, ExplodingAudit()).decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    item = good.get(item_id)
    assert (item.status, item.decision, item.reviewer_id, item.decided_ts) == ("open", None, None, None)
    good.decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")  # and it can still be decided
    assert audit.verify().ok


def test_two_reviewers_deciding_the_same_item_at_once_exactly_one_wins(queue, audit):
    item_id = queue.enqueue(decision(file_id="F000123"))
    workers, barrier, results = 16, threading.Barrier(16), []

    def reviewer(n: int) -> None:
        barrier.wait()
        try:
            queue.decide(item_id, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id=f"reviewer-{n}")
            results.append("won")
        except ItemAlreadyDecided:
            results.append("lost")

    threads = [threading.Thread(target=reviewer, args=(n,)) for n in range(workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(results) == ["lost"] * (workers - 1) + ["won"]
    decided = [e for e in audit.trail("F000123") if e["event_type"] == "HUMAN_DECISION"]
    assert len(decided) == 1 and decided[0]["reviewer_id"] == queue.get(item_id).reviewer_id
    assert audit.verify().ok


def test_concurrent_enqueue_and_decide_across_many_items(tmp_db, audit):
    queue = HumanQueue(tmp_db, audit)
    n_items, n_threads = 60, 8
    ids: list[int] = []
    lock = threading.Lock()

    def produce(start: int) -> None:
        for i in range(start, n_items, n_threads):
            new_id = queue.enqueue(decision(file_id=f"F{i:06d}", closeness=i / n_items))
            with lock:
                ids.append(new_id)

    threads = [threading.Thread(target=produce, args=(s,)) for s in range(n_threads)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(ids)) == n_items and len(queue.list("credit_review")) == n_items

    errors: list[BaseException] = []

    def review(chunk: list[int]) -> None:
        try:
            for item_id in chunk:
                queue.decide(item_id, decision="modify", reason_code="MODIFY_TERMS", reviewer_id=f"r{item_id % 5}")
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    chunks = [ids[i::n_threads] for i in range(n_threads)]
    threads = [threading.Thread(target=review, args=(c,)) for c in chunks]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert errors == []
    stats = queue.stats()
    assert (stats["open"], stats["decided"], stats["total"]) == (0, n_items, n_items)
    result = audit.verify()
    assert result.ok and result.entries == 2 * n_items  # one QUEUED and one HUMAN_DECISION per item, chain intact


def test_state_survives_reopening_the_database(tmp_db, audit):
    first = HumanQueue(tmp_db, audit)
    item_id = first.enqueue(decision(closeness=0.4))
    reopened = HumanQueue(tmp_db, AuditLog(tmp_db))
    assert [i.id for i in reopened.list("credit_review")] == [item_id]
    reopened.decide(item_id, decision="reject", reason_code="REJECT_MODEL_MISREAD_DOCS", reviewer_id="r1")
    assert first.get(item_id).status == "decided"


def test_the_audit_log_stays_append_only_next_to_the_mutable_queue(queue, audit, tmp_db):
    queue.decide(queue.enqueue(decision()), decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    with closing(sqlite3.connect(tmp_db, isolation_level=None)) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE audit_log SET reviewer_id = 'someone else'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_log")
    assert audit.verify().ok


# ------------------------------------------------------------------------------------------------ stats


def test_stats_summarise_the_queue(queue):
    empty = queue.stats()
    assert empty["total"] == 0 and empty["oldest_open_ts"] is None and set(empty["by_queue"]) == set(QUEUES)
    a = queue.enqueue(decision(file_id="a"))
    b = queue.enqueue(decision(file_id="b", outcome=Outcome.FRAUD_INVESTIGATION))
    queue.enqueue(decision(file_id="c", outcome=Outcome.DEFICIENCY_NOTICE))
    queue.decide(a, decision="accept", reason_code="ACCEPT_AS_ADVISED", reviewer_id="r1")
    queue.decide(b, decision="reject", reason_code="FRAUD_CLEARED", reviewer_id="r2")
    stats = queue.stats()
    assert (stats["total"], stats["open"], stats["decided"]) == (3, 1, 2)
    assert stats["by_queue"]["credit_review"] == {"open": 0, "decided": 1}
    assert stats["by_queue"]["fraud_investigation"] == {"open": 0, "decided": 1}
    assert stats["by_queue"]["deficiency_ops"] == {"open": 1, "decided": 0}
    assert stats["decisions"] == {"accept": 1, "modify": 0, "reject": 1}
    assert stats["reason_codes"] == {"ACCEPT_AS_ADVISED": 1, "FRAUD_CLEARED": 1}
    assert stats["oldest_open_ts"] == queue.get(queue.list("deficiency_ops")[0].id).created_ts
    json.dumps(stats)  # JSON-ready for the API health page
