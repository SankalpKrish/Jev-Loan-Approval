"""Shared support for the policy tests: a hand-built question catalogue (PLAN section 3.8), answer builders, a
call-result builder and a policy-variant loader. Plus a few tests that the support itself matches the shipped policy,
so the other policy tests cannot drift away from the YAML they exercise."""

import copy
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from jevloan.data.schema import SEGMENTS, load_disclosures
from jevloan.jev.gateway import JevCallResult
from jevloan.policy.config import Policy, load_policy
from jevloan.policy.engine import PolicyEngine

POLICY_YAML = Path(__file__).resolve().parents[1] / "config" / "policy" / "policy_v1.yaml"


@dataclass(frozen=True)
class Q:
    qid: str
    module: str
    qtype: str
    risk_polarity: str
    routing_relevant: bool
    reason_text: str


def _q(qid: str, module: str, qtype: str, polarity: str, routing: bool = True) -> Q:
    return Q(qid, module, qtype, polarity, routing, f"RUBRIC[{qid}]")


def build_catalog() -> dict[str, Q]:
    """Every question of section 3.8 (the E_disclosure_* ones from the shipped KFS file), keyed by qid."""
    rows = [
        *[_q(q, "A", "noul", "yes_is_good") for q in (
            "A_income_proof_current", "A_address_proof_valid", "A_statements_cover_months", "A_fields_cohere")],
        _q("B_identity_coheres", "B", "noul", "yes_is_good"),
        _q("B_salary_matches_employer", "B", "noul", "yes_is_good"),
        _q("B_gst_bank_consistent", "B", "noul", "yes_is_good"),
        _q("B_synthetic_identity_signals", "B", "noul", "yes_is_bad"),
        _q("C_willingness", "C", "score", "ordinal_high_is_good"),
        _q("C_recent_delinquency", "C", "noul", "yes_is_bad"),
        _q("C_capacity", "C", "score", "ordinal_high_is_good"),
        _q("C_foir_within_limit", "C", "noul", "yes_is_good"),
        _q("C_income_stable", "C", "noul", "yes_is_good"),
        _q("C_collateral_adequacy", "C", "score", "ordinal_high_is_good"),
        _q("C_collateral_title_clear", "C", "noul", "yes_is_good"),
        _q("D_closeness", "D", "score", "ordinal_high_is_good", routing=False),
        *[_q(q, "D", "noul", "yes_is_bad", routing=False) for q in (
            "D_doubt_income_documentation", "D_doubt_repayment_history", "D_doubt_debt_burden", "D_doubt_stability",
            "D_doubt_collateral", "D_doubt_thin_file")],
        _q("E_memo_matches_grid", "E", "noul", "yes_is_good"),
        _q("E_rate_math_correct", "E", "noul", "yes_is_good"),
        _q("E_disclosures_complete", "E", "noul", "yes_is_good"),
        *[_q(f"E_disclosure_{d['id']}", "E", "noul", "yes_is_good", routing=False) for d in load_disclosures()],
        *[_q(q, "F", "noul", "yes_is_bad") for q in (
            "F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_partial_payments", "F_ews_balance_stress")],
        *[_q(f"F_covenant_{c}", "F", "noul", "yes_is_good") for c in (
            "dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current")],
    ]
    return {q.qid: q for q in rows}


def noul(p: float) -> dict:
    return {"type": "noul", "noul": p, "derived_confidence": abs(2 * p - 1)}


def score(level: float, *, levels: int = 5, confidence: float = 0.9) -> dict:
    return {
        "type": "score", "score": level, "confidence": confidence,
        "probabilities": {str(i): 1 / levels for i in range(levels)},
        "legend": {str(i): f"level {i}" for i in range(levels)},
    }


def make_call(answers: dict | None, **overrides) -> JevCallResult:
    fields = dict(
        ok=True, answers=answers, model_version="sim-jev-0.1", input_tokens=1000, latency_ms=50.0,
        request_ts="2026-09-29T10:00:00.000Z", response_ts="2026-09-29T10:00:00.050Z", failure=None, failure_detail=None,
        state_hash="0" * 64, audit_seq=1,
    )
    return JevCallResult(**{**fields, **overrides})


def failed_call(kind: str) -> JevCallResult:
    return make_call(None, ok=False, model_version=None, input_tokens=None, response_ts=None, failure=kind, failure_detail="test")


# ---- answer sets. Goodness: score level/4; yes_is_good p; yes_is_bad 1 - p.

_B_BY_SEGMENT = {
    "salaried_personal": ("B_identity_coheres", "B_salary_matches_employer", "B_synthetic_identity_signals"),
    "self_employed": ("B_identity_coheres", "B_gst_bank_consistent", "B_synthetic_identity_signals"),
    "msme_business": ("B_identity_coheres", "B_gst_bank_consistent", "B_synthetic_identity_signals"),
    "secured_home": ("B_identity_coheres", "B_salary_matches_employer", "B_synthetic_identity_signals"),
}


def appraisal_answers(segment: str = "salaried_personal") -> dict[str, dict]:
    """A clean file: ready, no fraud, strong C (composite 0.97 or better) and a D triage that would say 'close'."""
    answers = {q: noul(0.97) for q in ("A_income_proof_current", "A_address_proof_valid", "A_statements_cover_months", "A_fields_cohere")}
    for q in _B_BY_SEGMENT[segment]:
        answers[q] = noul(0.03 if q == "B_synthetic_identity_signals" else 0.97)
    answers |= {
        "C_willingness": score(4), "C_capacity": score(4), "C_foir_within_limit": noul(0.97),
        "C_income_stable": noul(0.97), "C_recent_delinquency": noul(0.03),
    }
    if segment == "secured_home":
        answers |= {"C_collateral_adequacy": score(4), "C_collateral_title_clear": noul(0.97)}
    answers["D_closeness"] = score(3)
    for q in ("D_doubt_income_documentation", "D_doubt_repayment_history", "D_doubt_debt_burden", "D_doubt_stability", "D_doubt_thin_file"):
        answers[q] = noul(0.1)
    if segment == "secured_home":
        answers["D_doubt_collateral"] = noul(0.1)
    return answers


def with_c_goodness(answers: dict, goodness: float, segment: str = "salaried_personal") -> dict:
    """Set every C answer to the same goodness (0, .25, .5, .75 or 1 for scores; confident Nouls need <=.25 or >=.75)."""
    out = dict(answers)
    for qid in load_policy().modules.C.segments[segment].weights:
        if qid in ("C_willingness", "C_capacity", "C_collateral_adequacy"):
            out[qid] = score(goodness * 4)
        elif qid == "C_recent_delinquency":
            out[qid] = noul(1 - goodness)  # yes_is_bad
        else:
            out[qid] = noul(goodness)
    return out


def sanction_answers(p: float = 0.97) -> dict[str, dict]:
    answers = {q: noul(p) for q in ("E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete")}
    answers |= {f"E_disclosure_{d['id']}": noul(p) for d in load_disclosures()}
    return answers


def sanction_state(stated: float = 12.5, recomputed: float = 12.5) -> dict:
    return {"schema": "jevloan.state.v1", "stage": "sanction_docs", "kfs": {"apr_stated_pct": stated, "apr_recomputed_pct": recomputed}}


def monitoring_answers(ews: int = 0, breached: int = 0, *, p_bad: float = 0.9, p_ok: float = 0.1) -> dict[str, dict]:
    """`ews` early warnings firing and `breached` covenants breached; everything else is calm and compliant."""
    ews_ids = ["F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_partial_payments", "F_ews_balance_stress"]
    cov_ids = ["F_covenant_dscr_min_1_25", "F_covenant_stock_statement_monthly", "F_covenant_no_unapproved_borrowing", "F_covenant_insurance_current"]
    answers = {q: noul(p_bad if i < ews else p_ok) for i, q in enumerate(ews_ids)}
    answers |= {q: noul(1 - p_bad if i < breached else 0.95) for i, q in enumerate(cov_ids)}
    return answers


def engine(policy: Policy | None = None, catalog: dict | None = None) -> PolicyEngine:
    return PolicyEngine(policy or load_policy(), catalog or build_catalog())


def policy_variant(tmp_path: Path, mutate) -> Policy:
    """The shipped policy with `mutate(dict)` applied, written to a temp file and loaded through load_policy()."""
    data = copy.deepcopy(yaml.safe_load(POLICY_YAML.read_text()))
    mutate(data)
    path = tmp_path / "policy_variant.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return load_policy(path)


# ------------------------------------------------------------------------------------------------ support self-checks


def test_catalog_covers_every_weighted_question_in_the_shipped_policy():
    catalog, policy = build_catalog(), load_policy()
    for segment in SEGMENTS:
        assert set(policy.modules.C.segments[segment].weights) <= set(catalog)
    assert set(policy.modules.A.missing_item_names) == {q for q, v in catalog.items() if v.module == "A"}


def test_clean_answer_sets_are_confident_passes():
    engine_ = engine()
    for segment in SEGMENTS:
        decision = engine_.decide(
            file_id="F1", segment=segment, stage="appraisal", call=make_call(appraisal_answers(segment)), state={}
        )
        assert decision.outcome == "PROCEED_TO_SANCTIONING_AUTHORITY", (segment, decision.reason_codes)
        assert decision.composite > 0.95 and decision.band == "pass"


def test_policy_variant_round_trips(tmp_path):
    variant = policy_variant(tmp_path, lambda d: d["modules"]["F"].update(warning_threshold_p=0.7))
    assert variant.modules.F.warning_threshold_p == 0.7
    with pytest.raises(Exception):
        policy_variant(tmp_path, lambda d: d["decline"].update(require_human_confirmation=False))
