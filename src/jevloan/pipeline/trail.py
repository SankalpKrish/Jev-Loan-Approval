"""Audit-trail completeness checks, including repeated attempts of one stage."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from jevloan.policy.outcomes import Outcome

_GATEWAY_EVENTS = {"MODEL_CALL", "MODEL_FAILURE", "PII_BLOCK"}


def _json_field(row: dict, key: str) -> Any:
    value = row.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def trail_complete(audit, file_id: str, stages) -> tuple[bool, list[str]]:
    """Check a file's ordered audit protocol for each requested stage attempt.

    ``stages`` is an ordered sequence and may contain a stage more than once. Every occurrence requires a
    separate gateway result and policy outcome; queue/pricing records are required from that attempt's decision.
    """
    required = list(stages)
    trail = audit.trail(file_id)
    problems: list[str] = []
    known_stages = {"appraisal", "sanction_docs", "monitoring"}
    for stage in required:
        if stage not in known_stages:
            problems.append(f"unknown required stage {stage!r}")

    by_stage: dict[str, list[dict]] = defaultdict(list)
    for row in trail:
        if row.get("stage") in known_stages:
            by_stage[row["stage"]].append(row)

    for stage, needed in Counter(s for s in required if s in known_stages).items():
        rows = by_stage[stage]
        calls = [r for r in rows if r.get("event_type") in _GATEWAY_EVENTS]
        outcomes = [r for r in rows if r.get("event_type") == "POLICY_OUTCOME"]
        queues = [r for r in rows if r.get("event_type") == "QUEUED"]
        prices = [r for r in rows if r.get("event_type") == "PRICING"]
        if len(calls) != needed:
            problems.append(f"{stage}: expected {needed} gateway result(s), found {len(calls)}")
        if len(outcomes) != needed:
            problems.append(f"{stage}: expected {needed} POLICY_OUTCOME event(s), found {len(outcomes)}")

        expected_queues: list[tuple[int, dict]] = []
        expected_prices: list[tuple[int, dict]] = []
        paired = min(len(calls), len(outcomes))
        for index in range(paired):
            call, outcome_row = calls[index], outcomes[index]
            if call["seq"] >= outcome_row["seq"]:
                problems.append(f"{stage} attempt {index + 1}: POLICY_OUTCOME does not follow its gateway result")
            detail = _json_field(outcome_row, "detail_json")
            decision = detail.get("decision") if isinstance(detail, dict) else None
            queue = decision.get("queue") if isinstance(decision, dict) else None
            outcome = outcome_row.get("policy_outcome")
            if queue is not None:
                expected_queues.append((outcome_row["seq"], {"queue": queue, "attempt": index + 1}))
            if outcome == Outcome.PROCEED_TO_SANCTIONING_AUTHORITY.value:
                expected_prices.append((outcome_row["seq"], {"attempt": index + 1}))

        if len(queues) != len(expected_queues):
            problems.append(f"{stage}: expected {len(expected_queues)} QUEUED event(s), found {len(queues)}")
        for index, (queue_row, (after_seq, info)) in enumerate(zip(queues, expected_queues, strict=False)):
            qdetail = _json_field(queue_row, "detail_json")
            actual_queue = qdetail.get("queue") if isinstance(qdetail, dict) else None
            if queue_row["seq"] <= after_seq:
                problems.append(f"{stage} attempt {info['attempt']}: QUEUED does not follow POLICY_OUTCOME")
            if info["attempt"] < len(outcomes) and queue_row["seq"] >= outcomes[info["attempt"]]["seq"]:
                problems.append(f"{stage} attempt {info['attempt']}: QUEUED is after the next POLICY_OUTCOME")
            if actual_queue != info["queue"]:
                problems.append(f"{stage} attempt {info['attempt']}: expected queue {info['queue']!r}, got {actual_queue!r}")

        if len(prices) != len(expected_prices):
            problems.append(f"{stage}: expected {len(expected_prices)} PRICING event(s), found {len(prices)}")
        for price_row, (after_seq, info) in zip(prices, expected_prices, strict=False):
            if price_row["seq"] <= after_seq:
                problems.append(f"{stage} attempt {info['attempt']}: PRICING does not follow POLICY_OUTCOME")
            if info["attempt"] < len(outcomes) and price_row["seq"] >= outcomes[info["attempt"]]["seq"]:
                problems.append(f"{stage} attempt {info['attempt']}: PRICING is after the next POLICY_OUTCOME")

        # An outcome cannot be considered complete if the model/policy attempt stream interleaves incorrectly.
        for attempt in range(paired):
            call = calls[attempt]
            outcome_row = outcomes[attempt]
            later_call = calls[attempt + 1]["seq"] if attempt + 1 < len(calls) else float("inf")
            if outcome_row["seq"] >= later_call:
                problems.append(f"{stage} attempt {attempt + 1}: next gateway call preceded POLICY_OUTCOME")

    return not problems, problems
