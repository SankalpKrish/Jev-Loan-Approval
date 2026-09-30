"""Outcomes, queues and reason codes (PLAN section 3.10).

Three things live here because the engine, the queue and the pipeline all need them and none of them may
disagree: what a stage can decide, which human queue each decision lands in, and the codes that say why.
"""

from enum import StrEnum
from typing import Final


class Outcome(StrEnum):
    PROCEED_TO_SANCTIONING_AUTHORITY = "PROCEED_TO_SANCTIONING_AUTHORITY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DECLINE_RECOMMENDED = "DECLINE_RECOMMENDED"
    DEFICIENCY_NOTICE = "DEFICIENCY_NOTICE"
    FRAUD_INVESTIGATION = "FRAUD_INVESTIGATION"
    DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF = "DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF"
    DISBURSAL_BLOCKED = "DISBURSAL_BLOCKED"
    WATCHLIST_T0 = "WATCHLIST_T0"
    WATCHLIST_T1 = "WATCHLIST_T1"
    WATCHLIST_T2 = "WATCHLIST_T2"
    WATCHLIST_T3 = "WATCHLIST_T3"


class ReasonCode(StrEnum):
    """Codes the engine attaches to a decision. Every one is described in `system_reason_codes` of the policy."""

    # Failure routing: a failed call is always a HUMAN_REVIEW.
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_ERROR = "MODEL_ERROR"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UNREACHABLE = "MODEL_UNREACHABLE"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    PII_BLOCKED = "PII_BLOCKED"
    FORCED_HUMAN_REVIEW = "FORCED_HUMAN_REVIEW"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    # Appraisal.
    FRAUD_SIGNAL = "FRAUD_SIGNAL"
    READINESS_FAIL = "READINESS_FAIL"
    COMPOSITE_PASS = "COMPOSITE_PASS"
    COMPOSITE_BORDERLINE = "COMPOSITE_BORDERLINE"
    COMPOSITE_FAIL = "COMPOSITE_FAIL"
    NO_APPRAISAL_ANSWERS = "NO_APPRAISAL_ANSWERS"
    # Sanction documents.
    APR_MISMATCH_DETERMINISTIC = "APR_MISMATCH_DETERMINISTIC"
    APR_CHECK_UNAVAILABLE = "APR_CHECK_UNAVAILABLE"
    E_CHECK_FAILED = "E_CHECK_FAILED"
    MEMO_KFS_CONSISTENT = "MEMO_KFS_CONSISTENT"
    # Monitoring.
    WATCHLIST_T0 = "WATCHLIST_T0"
    WATCHLIST_T1 = "WATCHLIST_T1"
    WATCHLIST_T2 = "WATCHLIST_T2"
    WATCHLIST_T3 = "WATCHLIST_T3"


STAGES: Final = ("appraisal", "sanction_docs", "monitoring")

# What the gateway reports (JevCallResult.failure) -> the reason code on the HUMAN_REVIEW it produces.
FAILURE_REASON_CODE: Final[dict[str, ReasonCode]] = {
    "timeout": ReasonCode.MODEL_TIMEOUT,
    "api_error": ReasonCode.MODEL_ERROR,
    "rate_limited": ReasonCode.MODEL_RATE_LIMITED,
    "connection_error": ReasonCode.MODEL_UNREACHABLE,
    "circuit_open": ReasonCode.CIRCUIT_OPEN,
    "pii_blocked": ReasonCode.PII_BLOCKED,
    "forced_human": ReasonCode.FORCED_HUMAN_REVIEW,
}

CREDIT_REVIEW: Final = "credit_review"
FRAUD_INVESTIGATION_QUEUE: Final = "fraud_investigation"
DEFICIENCY_OPS: Final = "deficiency_ops"
DECLINE_CONFIRMATION: Final = "decline_confirmation"
DISBURSAL_CORRECTION: Final = "disbursal_correction"
WATCHLIST_REVIEW: Final = "watchlist_review"
QUEUES: Final = (
    CREDIT_REVIEW,
    FRAUD_INVESTIGATION_QUEUE,
    DEFICIENCY_OPS,
    DECLINE_CONFIRMATION,
    DISBURSAL_CORRECTION,
    WATCHLIST_REVIEW,
)

# Where a failed call lands, per stage.
STAGE_FAILURE_QUEUE: Final[dict[str, str]] = {
    "appraisal": CREDIT_REVIEW,
    "sanction_docs": DISBURSAL_CORRECTION,
    "monitoring": WATCHLIST_REVIEW,
}

# Outcomes whose queue never depends on anything else. HUMAN_REVIEW, DISBURSAL_BLOCKED and WATCHLIST_T1..T3
# also always queue, but which queue depends on the stage, so the engine picks it.
OUTCOME_QUEUE: Final[dict[Outcome, str | None]] = {
    Outcome.PROCEED_TO_SANCTIONING_AUTHORITY: None,
    Outcome.DECLINE_RECOMMENDED: DECLINE_CONFIRMATION,
    Outcome.DEFICIENCY_NOTICE: DEFICIENCY_OPS,
    Outcome.FRAUD_INVESTIGATION: FRAUD_INVESTIGATION_QUEUE,
    Outcome.DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF: None,
    Outcome.DISBURSAL_BLOCKED: DISBURSAL_CORRECTION,
    Outcome.WATCHLIST_T0: None,
    Outcome.WATCHLIST_T1: WATCHLIST_REVIEW,
    Outcome.WATCHLIST_T2: WATCHLIST_REVIEW,
    Outcome.WATCHLIST_T3: WATCHLIST_REVIEW,
}

# The C bands. Pricing also knows a fourth, "borderline_approved_by_human": a borderline file after a human said accept.
BAND_PASS: Final = "pass"
BAND_BORDERLINE: Final = "borderline"
BAND_FAIL: Final = "fail"
BAND_BORDERLINE_APPROVED: Final = "borderline_approved_by_human"
PRICEABLE_BANDS: Final = (BAND_PASS, BAND_BORDERLINE_APPROVED)
