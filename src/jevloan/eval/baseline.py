"""Rules-only answers for the evaluation baseline.

This baseline consumes the same already-built stage state and question wires as Jev. It never reads a LoanFile,
labels, simulator randomness, or noisy answer helpers. Every output has the same wire answer shape used by the
gateway so the ordinary policy engine can score it.
"""

from __future__ import annotations

from typing import Any

from jevloan.jev.sim_rules import a_readiness, b_fraud, c_appraisal, d_triage, e_memo_kfs, f_monitoring
from jevloan.modules.base import catalog

_DOUBT_CATEGORY = {
    "D_doubt_income_documentation": "income_documentation",
    "D_doubt_repayment_history": "repayment_history",
    "D_doubt_debt_burden": "debt_burden",
    "D_doubt_stability": "employment_or_business_stability",
    "D_doubt_collateral": "collateral",
    "D_doubt_thin_file": "bureau_thin_file",
}


def _require_bool(qid: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{qid}: deterministic evidence is incomplete (expected a boolean, got {value!r})")
    return value


def _truth(state: dict, qid: str, question: dict) -> bool:
    if qid == "A_income_proof_current":
        return bool(a_readiness.income_proof_current(state)[0])
    if qid == "A_address_proof_valid":
        return a_readiness.address_proof_valid(state)
    if qid == "A_statements_cover_months":
        covered = a_readiness.get(state, "bank.months_covered")
        required = a_readiness.get(state, "bank.months_required")
        if covered is None or required is None:
            raise ValueError(f"{qid}: statement coverage or required months is missing")
        return covered >= required
    if qid == "A_fields_cohere":
        gap = a_readiness.income_band_gap(state)
        incoherent = (
            (gap is not None and gap >= 2)
            or a_readiness.tenure_past_superannuation(state)
            or a_readiness.experience_too_long(state)
        )
        return not incoherent
    if qid == "B_identity_coheres":
        return b_fraud.identity_coheres(state)
    if qid == "B_salary_matches_employer":
        return b_fraud.salary_matches_employer(state)
    if qid == "B_gst_bank_consistent":
        ratio = a_readiness.get(state, "gst.gst_to_bank_ratio_band")
        if ratio is None:
            raise ValueError(f"{qid}: gst ratio band is missing from an asked question")
        return ratio not in ("<0.5", ">2")
    if qid == "B_synthetic_identity_signals":
        return b_fraud.synthetic_identity(state)
    if qid == "C_recent_delinquency":
        return a_readiness.get(state, "bureau.max_dpd_12m_band", "0") in ("30-59", "60-89", "90+")
    if qid == "C_foir_within_limit":
        return c_appraisal.foir_within_limit(state)
    if qid == "C_income_stable":
        return c_appraisal.income_stable(state)
    if qid == "C_collateral_title_clear":
        return (
            a_readiness.get(state, "property.title_status") == "clear"
            and a_readiness.get(state, "property.legal_opinion") == "positive"
        )
    if qid == "D_doubt_collateral" and a_readiness.get(state, "property") is None:
        raise ValueError(f"{qid}: question was asked without a property state")
    if qid in _DOUBT_CATEGORY:
        primary, _lead = d_triage.primary_weakness(state)
        return primary == _DOUBT_CATEGORY[qid]
    if qid == "E_memo_matches_grid":
        return _require_bool(qid, e_memo_kfs.memo_matches_grid(state))
    if qid == "E_rate_math_correct":
        return _require_bool(qid, e_memo_kfs.rate_math_correct(state))
    if qid == "E_disclosures_complete":
        return _require_bool(qid, e_memo_kfs.disclosures_complete(state))
    if qid.startswith("E_disclosure_"):
        heading = a_readiness.get(question, "instructions.data.section_heading")
        if not isinstance(heading, str) or not heading:
            raise ValueError(f"{qid}: question wire is missing its disclosure heading")
        return e_memo_kfs.heading_present(state, heading)
    if qid == "F_ews_dpd_rising":
        return f_monitoring.dpd_rising(f_monitoring._months(state))
    if qid == "F_ews_emi_bounces":
        return sum(bool(month.get("emi_bounced")) for month in f_monitoring._months(state)) >= 2
    if qid == "F_ews_partial_payments":
        return sum(bool(month.get("partial_payment")) for month in f_monitoring._months(state)) >= 2
    if qid == "F_ews_balance_stress":
        return f_monitoring.balance_stress(state)
    if qid.startswith("F_covenant_"):
        covenant_id = a_readiness.get(question, "instructions.data.covenant_id")
        if not isinstance(covenant_id, str) or not covenant_id:
            raise ValueError(f"{qid}: question wire is missing its covenant id")
        entries = [
            item for item in a_readiness.get(state, "covenants", []) or []
            if isinstance(item, dict) and item.get("covenant_id") == covenant_id
        ]
        if not entries:
            raise ValueError(f"{qid}: covenant {covenant_id!r} is absent from the state")
        return _require_bool(qid, f_monitoring.covenant_complied(covenant_id, entries[0]))
    raise ValueError(f"no deterministic rules-only baseline is registered for question {qid!r}")


def _score(qid: str, state: dict, question: dict) -> int:
    if qid == "C_willingness":
        return c_appraisal.willingness_level(state)
    if qid == "C_capacity":
        return c_appraisal.capacity_level(state)
    if qid == "C_collateral_adequacy":
        return c_appraisal.collateral_level(state)
    if qid == "D_closeness":
        value = d_triage.closeness_level(state)
        return max(0, min(4, int(round(value))))
    raise ValueError(f"no deterministic score baseline is registered for question {qid!r}")


def rule_answers(state: dict, questions: dict[str, dict]) -> dict[str, dict]:
    """Return a deterministic wire-format baseline answer for every supplied question.

    Nouls have exact probabilities of 0 or 1. Scores use the deterministic integer level and a one-hot
    probability vector. Unknown qids, mismatched question types and incomplete evidence raise explicitly.
    """
    if not isinstance(state, dict):
        raise TypeError("state must be a dict")
    if not isinstance(questions, dict):
        raise TypeError("questions must be a dict keyed by qid")
    definitions = catalog()
    answers: dict[str, dict] = {}
    for qid, wire in questions.items():
        qdef = definitions.get(qid)
        if qdef is None:
            raise ValueError(f"no deterministic rules-only baseline is registered for question {qid!r}")
        if not isinstance(wire, dict) or wire.get("type") != qdef.qtype:
            raise ValueError(f"{qid}: question wire type does not match catalogue type {qdef.qtype!r}")
        if qdef.qtype == "noul":
            p = 1.0 if _truth(state, qid, wire) else 0.0
            answers[qid] = {"type": "noul", "noul": p, "derived_confidence": 1.0}
        elif qdef.qtype == "score":
            value = _score(qid, state, wire)
            levels = wire.get("criteria")
            if not isinstance(levels, list) or len(levels) < 2 or not 0 <= value < len(levels):
                raise ValueError(f"{qid}: deterministic score {value!r} does not fit the question's score levels")
            answers[qid] = {
                "type": "score",
                "score": value,
                "probabilities": {str(index): float(index == value) for index in range(len(levels))},
                "confidence": 1.0,
            }
        else:
            raise ValueError(f"{qid}: unsupported baseline question type {qdef.qtype!r}")
    return answers
