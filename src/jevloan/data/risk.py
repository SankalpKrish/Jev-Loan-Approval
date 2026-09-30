"""The documented synthetic risk function, credit policy and label definitions.

Everything a label depends on lives here so that docs/DATA_CARD.md can describe it in one place:

* ``COEFFICIENTS`` and ``risk_pd``: ``pd = sigmoid(intercept + sum(beta_j * x_j))`` over standardised features.
* ``synthetic_credit_decision``: the synthetic credit policy applied to the TRUE features.
* ``primary_weakness`` and ``closeness_level``: how far, and in what way, a file is from sanctionable.
* ``draw_outcome``: the 12-month outcome drawn from the pd.
* ``ews_truth``: early-warning truths computed from six months of repayment.
* ``question_truth``: the truth of every applicable section 3.8 question.

The "true" features are the structured fields of the file, except that a file in the ``pincode_bureau_thin``
disparity subset has its bureau score hidden (reported as new-to-credit) while ``meta.true_bureau_score`` keeps the
real one. Labels always use the real one, which is what makes that disparity label-independent.
"""

from __future__ import annotations

import bisect
import math
import random
from datetime import date

from jevloan import finance
from jevloan.data.schema import (
    EWS_IDS,
    MSME_COVENANT_IDS,
    LoanFile,
    RepaymentMonth,
    applicable_qids,
    disclosure_ids,
    grid_row_for,
)

# The date the whole synthetic book is "as of": application date, document stamps, ages.
AS_OF = date(2026, 9, 14)

# ------------------------------------------------------------------------------------------------ policy constants

PD_CUTOFF = {"salaried_personal": 0.08, "self_employed": 0.10, "msme_business": 0.10, "secured_home": 0.05}
# FOIR limits in percent. MSME is judged on DSCR >= 1.25; because the MSME 'income' field is monthly cash
# accruals, DSCR = 1 / FOIR and DSCR >= 1.25 is the same as FOIR <= 80 percent.
FOIR_LIMIT_PCT = {"salaried_personal": 55.0, "self_employed": 50.0, "msme_business": 80.0, "secured_home": 60.0}
DSCR_MIN = 1.25
MIN_BUREAU_SCORE = 650
MAX_DPD_EXCLUSIVE = 60  # sanctionable needs max_dpd_12m < 60
NTC_MIN_MONTHLY_INCOME = 50_000
MIN_BUSINESS_VINTAGE_YEARS = 2.0
RETIREMENT_AGE = {"salaried": 60, "self_employed": 70, "business_owner": 70}
STATEMENTS_REQUIRED = {"salaried_personal": 6, "secured_home": 6, "self_employed": 12, "msme_business": 12}

# ------------------------------------------------------------------------------------------------ truth-alignment edges
# PLAN 3.8, truth-alignment rule: every per-question truth is a function of what the state shows, with thresholds on
# band edges. The edges below MUST match the band tables in ``jevloan.state.base`` (BAND_TABLES: "foir_headroom_pts",
# "ltv_headroom_pts", "dscr", "volatility", "valuation_spread"). The data layer does not import from ``state/``, so they
# are repeated here once, by name; tests/test_data_risk.py compares them to the state tables and rebuilds the truths
# from real states. Every band is left closed, [lo, hi), and values are rounded to ``BAND_ROUND`` places first, as
# state.base does, so no float fuzz decides a tie.
BAND_ROUND = 6
FOIR_HEADROOM_EDGES = (-10.0, 0.0, 10.0, 20.0)  # segment FOIR limit minus FOIR, points: <-10, -10-0, 0-10, 10-20, >=20
LTV_HEADROOM_EDGES = (-5.0, 0.0, 8.0, 15.0)  # LTV limit minus LTV, points: <-5, -5-0, 0-8, 8-15, >=15
DSCR_EDGES = (1.0, 1.25, 1.5, 2.0)  # <1, 1-1.25, 1.25-1.5, 1.5-2, >2 (2.0 itself is in the top band)
VOLATILITY_STABLE_CV = 0.25  # cv below this is band "low": the C_income_stable threshold
VOLATILITY_HIGH_CV = 0.35  # cv at or above this is band "high": costs C_capacity one level
VALUATION_SPREAD_HIGH = 0.20  # spread at or above this is band ">20%": costs C_collateral_adequacy one level
BALANCE_STRESS_LAST_OVER_FIRST = (6, 10)  # last-two-month mean <= 6/10 x first-two-month mean (>= 40% below)

# ------------------------------------------------------------------------------------------------ the risk function

COEFFICIENTS: dict[str, float] = {
    "intercept": -3.90,  # log-odds of a 12-month default for a typical salaried file with every x = 0
    "seg_self_employed": 0.15,
    "seg_msme_business": -0.10,
    "seg_secured_home": -0.90,  # secured lending defaults less
    "bureau_score": -0.80,  # per 60 points above 725
    "ntc": 0.55,  # new to credit (real, not the disparity artefact)
    "foir": 0.60,  # per 0.15 above the segment centre
    "income_volatility": 0.30,  # per unit of standardised coefficient of variation
    "max_dpd": 0.55,  # per 30 days of worst delinquency in 12 months
    "emi_bounces": 0.35,  # per 2 bounces in 6 months
    "writeoff": 0.90,  # any write-off or settlement
    "ltv": 0.50,  # per 12 points above 65 percent (home loans)
    "title_risk": 0.60,  # disputed title 1.0, pending mutation 0.5, adverse legal opinion +0.5
    "business_vintage": -0.35,  # per 5 years above the segment centre
    "gst_inconsistency": 0.35,  # GST turnover vs bank credits outside the 0.89-1.13 band
    "income_gap": 0.30,  # declared income above verified income by more than 5 percent
    "card_utilization": 0.25,  # per 0.2 above 0.35
    "fraud_flag": 1.20,  # a fraud file is much less likely to repay
}

FOIR_CENTRE = {"salaried_personal": 0.40, "self_employed": 0.38, "msme_business": 0.60, "secured_home": 0.45}
FOIR_SCALE = 0.15
VOLATILITY_CENTRE = {"salaried": 0.07, "self_employed": 0.28, "business_owner": 0.30}
VOLATILITY_SCALE = {"salaried": 0.08, "self_employed": 0.15, "business_owner": 0.15}
VINTAGE_CENTRE = {"salaried_personal": 5.0, "self_employed": 6.0, "msme_business": 9.0, "secured_home": 6.0}

# feature -> the primary-weakness category its positive contribution counts towards
FEATURE_CATEGORY = {
    "bureau_score": "repayment_history",
    "max_dpd": "repayment_history",
    "emi_bounces": "repayment_history",
    "writeoff": "repayment_history",
    "ntc": "bureau_thin_file",
    "foir": "debt_burden",
    "card_utilization": "debt_burden",
    "income_volatility": "employment_or_business_stability",
    "business_vintage": "employment_or_business_stability",
    "ltv": "collateral",
    "title_risk": "collateral",
    "gst_inconsistency": "income_documentation",
    "income_gap": "income_documentation",
}
CATEGORY_ORDER = (
    "repayment_history", "debt_burden", "employment_or_business_stability", "income_documentation", "collateral",
    "bureau_thin_file",
)
_FALLBACK_FEATURES = ("bureau_score", "foir", "card_utilization", "income_volatility", "business_vintage", "ltv")
_MATERIAL_CONTRIBUTION = 0.05


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def risk_pd(features: dict[str, float]) -> float:
    """12-month default probability: ``sigmoid(intercept + sum(COEFFICIENTS[k] * features[k]))``, clipped to
    [0.002, 0.95]. Features not supplied count as 0 (the typical value)."""
    z = COEFFICIENTS["intercept"] + sum(COEFFICIENTS[k] * v for k, v in features.items() if k != "intercept")
    return min(0.95, max(0.002, sigmoid(z)))


# ------------------------------------------------------------------------------------------------ true features


def total_emi(file: LoanFile) -> int:
    return file.obligations.existing_emi_inr + file.obligations.proposed_emi_inr


def true_foir_pct(file: LoanFile) -> float:
    """(existing + proposed EMI) / verified monthly income, in percent."""
    return finance.foir(total_emi(file), file.income.verified_monthly_income_inr)


def true_dscr(file: LoanFile) -> float:
    """Annual cash accruals over annual debt service. Verified monthly income is monthly cash accruals for the
    business segments, so this equals 1 / FOIR."""
    return finance.dscr(12 * file.income.verified_monthly_income_inr, 12 * total_emi(file))


def effective_bureau_score(file: LoanFile) -> int | None:
    """The score labels are computed from: the real one for a file whose reported score is hidden."""
    if file.meta.disparity_subset == "pincode_bureau_thin" and file.meta.true_bureau_score is not None:
        return file.meta.true_bureau_score
    return file.bureau.score


def ltv_limit_pct(file: LoanFile) -> float | None:
    if file.property is None:
        return None
    return grid_row_for(file.application.product, file.application.loan_amount_inr)["max_ltv_pct"]


def age_at_application(file: LoanFile) -> int:
    return AS_OF.year - file.applicant.dob_year


def needs_vintage_rule(file: LoanFile) -> bool:
    return file.segment in ("self_employed", "msme_business") or (
        file.segment == "secured_home" and file.application.employment_type == "self_employed"
    )


def extract_features(file: LoanFile) -> dict[str, float]:
    """Standardised feature vector x (0 = typical) for ``risk_pd``."""
    seg, emp = file.segment, file.application.employment_type
    score = effective_bureau_score(file)
    x: dict[str, float] = {
        "seg_self_employed": 1.0 if seg == "self_employed" else 0.0,
        "seg_msme_business": 1.0 if seg == "msme_business" else 0.0,
        "seg_secured_home": 1.0 if seg == "secured_home" else 0.0,
        "bureau_score": 0.0 if score is None else (score - 725) / 60.0,
        "ntc": 1.0 if score is None else 0.0,
        "foir": (true_foir_pct(file) / 100.0 - FOIR_CENTRE[seg]) / FOIR_SCALE,
        "income_volatility": (file.income.volatility_cv - VOLATILITY_CENTRE[emp]) / VOLATILITY_SCALE[emp],
        "max_dpd": min(file.bureau.max_dpd_12m, 120) / 30.0,
        "emi_bounces": min(file.bank.emi_bounces_6m, 4) / 2.0,
        "writeoff": 1.0 if file.bureau.writeoffs_or_settlements > 0 else 0.0,
        "ltv": 0.0,
        "title_risk": 0.0,
        "business_vintage": (min(file.application.years_in_job_or_business, 15.0) - VINTAGE_CENTRE[seg]) / 5.0,
        "gst_inconsistency": 0.0,
        "income_gap": 0.0,
        "card_utilization": (file.obligations.credit_card_utilization - 0.35) / 0.2,
        "fraud_flag": 1.0 if file.labels.fraud else 0.0,
    }
    if file.property is not None:
        x["ltv"] = max(-3.0, min(4.0, (file.property.ltv - 65.0) / 12.0))
        x["title_risk"] = {"clear": 0.0, "pending_mutation": 0.5, "disputed": 1.0}[file.property.title_status] + (
            0.5 if file.property.legal_opinion == "adverse" else 0.0
        )
    if file.gst is not None and file.gst.bank_credits_12m_inr > 0:
        ratio = file.gst.turnover_12m_inr / file.gst.bank_credits_12m_inr
        x["gst_inconsistency"] = min(3.0, max(0.0, abs(math.log(ratio)) - 0.12) / 0.25)
    gap = file.application.declared_monthly_income_inr / file.income.verified_monthly_income_inr - 1.0
    x["income_gap"] = min(3.0, max(0.0, gap - 0.05) / 0.15)
    return x


def pd_for_file(file: LoanFile) -> float:
    return risk_pd(extract_features(file))


# ------------------------------------------------------------------------------------------------ the credit policy


def _ntc_eligible(file: LoanFile) -> bool:
    return file.segment == "salaried_personal" and file.income.verified_monthly_income_inr >= NTC_MIN_MONTHLY_INCOME


def synthetic_credit_decision(file: LoanFile) -> tuple[bool, list[str]]:
    """The synthetic credit policy on the TRUE features. Returns ``(sanctionable, failed_rules)``.

    Sanctionable only when: not fraud; FOIR within the segment limit (MSME: DSCR >= 1.25); bureau >= 650 (new to
    credit only for a salaried personal loan with income >= Rs. 50,000 a month); max DPD in 12 months < 60; no
    write-offs or settlements; for home loans LTV within the grid limit and a clear title; business vintage >= 2
    years (business segments and self-employed home applicants); and ``risk_pd`` below the segment cutoff.
    """
    failed: list[str] = []
    if file.labels.fraud:
        failed.append("fraud")
    if file.segment == "msme_business":
        if true_dscr(file) < DSCR_MIN:
            failed.append("dscr_below_1_25")
    elif true_foir_pct(file) > FOIR_LIMIT_PCT[file.segment]:
        failed.append("foir_above_limit")
    score = effective_bureau_score(file)
    if score is None:
        if not _ntc_eligible(file):
            failed.append("ntc_not_eligible")
    elif score < MIN_BUREAU_SCORE:
        failed.append("bureau_below_650")
    if file.bureau.max_dpd_12m >= MAX_DPD_EXCLUSIVE:
        failed.append("max_dpd_60_plus")
    if file.bureau.writeoffs_or_settlements > 0:
        failed.append("writeoff_or_settlement")
    if file.property is not None:
        limit = ltv_limit_pct(file)
        if limit is not None and file.property.ltv > limit:
            failed.append("ltv_above_limit")
        if file.property.title_status != "clear":
            failed.append("title_not_clear")
    if needs_vintage_rule(file) and file.application.years_in_job_or_business < MIN_BUSINESS_VINTAGE_YEARS:
        failed.append("business_vintage_below_2y")
    if pd_for_file(file) >= PD_CUTOFF[file.segment]:
        failed.append("pd_above_cutoff")
    return (not failed), failed


# ------------------------------------------------------------------------------------------------ weakness, closeness


def primary_weakness(file: LoanFile) -> str:
    """The category with the largest risk-increasing contribution (``beta_j * x_j``), summed per category.
    Collateral counts only for home loans. If nothing pushes risk up materially, the category whose continuous
    features sit closest to the risky end is named instead."""
    x = extract_features(file)
    contrib = {k: COEFFICIENTS[k] * x[k] for k in FEATURE_CATEGORY}
    categories = [c for c in CATEGORY_ORDER if c != "collateral" or file.property is not None]
    pos = {c: 0.0 for c in categories}
    for k, v in contrib.items():
        cat = FEATURE_CATEGORY[k]
        if cat in pos and v > 0:
            pos[cat] += v
    best = max(categories, key=lambda c: (round(pos[c], 9), -CATEGORY_ORDER.index(c)))
    if pos[best] >= _MATERIAL_CONTRIBUTION:
        return best
    raw = {c: 0.0 for c in categories}
    present = {c: False for c in categories}
    for k in _FALLBACK_FEATURES:
        cat = FEATURE_CATEGORY[k]
        if cat in raw and not (k == "bureau_score" and effective_bureau_score(file) is None):
            raw[cat] += contrib[k]
            present[cat] = True
    candidates = [c for c in categories if present[c]] or categories
    return max(candidates, key=lambda c: (round(raw[c], 9), -CATEGORY_ORDER.index(c)))


def rule_margins(file: LoanFile) -> dict[str, float]:
    """Signed distance to each sanction rule in comparable units: positive passes, negative fails, about 1 is
    comfortable. The sanctionable boundary is where the smallest margin crosses 0."""
    m: dict[str, float] = {}
    if file.segment == "msme_business":
        m["dscr"] = (true_dscr(file) - DSCR_MIN) / 0.35
    else:
        m["foir"] = (FOIR_LIMIT_PCT[file.segment] - true_foir_pct(file)) / 10.0
    score = effective_bureau_score(file)
    if score is None:
        m["bureau"] = (
            min(2.0, (file.income.verified_monthly_income_inr - NTC_MIN_MONTHLY_INCOME) / 25_000) if _ntc_eligible(file) else -2.0
        )
    else:
        m["bureau"] = (score - MIN_BUREAU_SCORE) / 40.0
    m["dpd"] = (MAX_DPD_EXCLUSIVE - file.bureau.max_dpd_12m) / 20.0
    m["writeoff"] = 2.0 if file.bureau.writeoffs_or_settlements == 0 else -2.5
    if file.property is not None:
        limit = ltv_limit_pct(file) or 80.0
        m["ltv"] = (limit - file.property.ltv) / 6.0
        m["title"] = 2.0 if file.property.title_status == "clear" else -2.5
    if needs_vintage_rule(file):
        m["vintage"] = (file.application.years_in_job_or_business - MIN_BUSINESS_VINTAGE_YEARS) / 1.5
    cutoff = PD_CUTOFF[file.segment]
    m["pd"] = (cutoff - pd_for_file(file)) / (0.5 * cutoff)
    m["fraud"] = -3.0 if file.labels.fraud else 3.0
    return m


def closeness_level(file: LoanFile) -> int:
    """0 (clearly not sanctionable) to 4 (clearly sanctionable), from the smallest rule margin (which includes the
    ``pd_cutoff - risk_pd`` margin): >= 1.0 is 4, >= 0.3 is 3, within +-0.3 of the boundary is 2, >= -1.0 is 1,
    below that 0. Levels 3 and 4 are always sanctionable and 0 and 1 never are; level 2 straddles the boundary."""
    worst = min(rule_margins(file).values())
    if worst >= 1.0:
        return 4
    if worst >= 0.3:
        return 3
    if worst >= -0.3:
        return 2
    if worst >= -1.0:
        return 1
    return 0


# ------------------------------------------------------------------------------------------------ outcome


def draw_outcome(pd: float, rng: random.Random) -> str:
    """defaults with probability pd, slips with probability min(0.3, 1.5 * pd), repays otherwise."""
    u = rng.random()
    if u < pd:
        return "defaults"
    if u < pd + min(0.3, 1.5 * pd):
        return "slips"
    return "repays"


# ------------------------------------------------------------------------------------------------ early warnings


def ews_truth(months: list[RepaymentMonth]) -> dict[str, bool]:
    """Early-warning truths from six months of repayment.

    * F_ews_dpd_rising: DPD (the state's ``dpd_days``) strictly higher than the previous month in at least 3 of
      months 3-6, or any month at 30 or more.
    * F_ews_emi_bounces: 2 or more bounced EMIs.
    * F_ews_partial_payments: 2 or more partial payments.
    * F_ews_balance_stress: the mean balance of months 5-6 is at most 0.6 times the mean of months 1-2 (at least 40
      percent below), compared on integer sums so a tie is exact. This is exactly the state's
      ``loan.balance_change_band == "falling_40_plus"``.
    """
    by_m = sorted(months, key=lambda r: r.m)
    dpd = [r.dpd for r in by_m]
    rises = sum(1 for i in range(2, 6) if dpd[i] > dpd[i - 1])
    first = by_m[0].avg_balance_inr + by_m[1].avg_balance_inr
    last = by_m[4].avg_balance_inr + by_m[5].avg_balance_inr
    num, den = BALANCE_STRESS_LAST_OVER_FIRST
    return {
        "F_ews_dpd_rising": rises >= 3 or max(dpd) >= 30,
        "F_ews_emi_bounces": sum(r.emi_bounced for r in by_m) >= 2,
        "F_ews_partial_payments": sum(r.partial_payment for r in by_m) >= 2,
        "F_ews_balance_stress": first > 0 and den * last <= num * first,
    }


# ------------------------------------------------------------------------------------------------ question truth


def willingness_level(file: LoanFile) -> int:
    """C_willingness 0-4: 0 write-off or DPD >= 90; 1 DPD 60-89; 2 DPD 30-59 or 2+ bounces; 3 DPD 1-29 or one
    bounce; 4 spotless."""
    dpd, bounces = file.bureau.max_dpd_12m, file.bank.emi_bounces_6m
    if file.bureau.writeoffs_or_settlements > 0 or dpd >= 90:
        return 0
    if dpd >= 60:
        return 1
    if dpd >= 30 or bounces >= 2:
        return 2
    if dpd >= 1 or bounces == 1:
        return 3
    return 4


def band_level(edges: tuple[float, ...], value: float) -> int:
    """Index of the left-closed band [edges[i-1], edges[i]) that ``value`` falls in: 0 below the first edge, up to
    ``len(edges)`` at or above the last."""
    return bisect.bisect_right(edges, round(float(value), BAND_ROUND))


def foir_headroom_pts(file: LoanFile) -> float:
    """Segment FOIR limit minus true FOIR, in points, from observed fields (what the state's ``foir_headroom_pts_band``
    bands). Not defined for MSME, which is judged on DSCR."""
    return FOIR_LIMIT_PCT[file.segment] - true_foir_pct(file)


def ltv_headroom_pts(file: LoanFile) -> float:
    """LTV limit minus LTV, in points (home loans)."""
    assert file.property is not None
    return (ltv_limit_pct(file) or 80.0) - file.property.ltv


def foir_within_limit(file: LoanFile) -> bool:
    """C_foir_within_limit: MSME DSCR at least 1.25, otherwise FOIR within the segment limit (headroom at least 0)."""
    if file.segment == "msme_business":
        return round(true_dscr(file), BAND_ROUND) >= DSCR_MIN
    return round(foir_headroom_pts(file), BAND_ROUND) >= 0


def capacity_level(file: LoanFile) -> int:
    """C_capacity 0-4, read straight off state bands. Non-MSME: ``foir_headroom_pts_band`` (headroom = segment limit
    minus FOIR) >=20 is 4, 10-20 is 3, 0-10 is 2, -10-0 is 1, <-10 is 0. MSME: ``business.dscr_band`` >2 (2.0 or more) is
    4, 1.5-2 is 3, 1.25-1.5 is 2, 1-1.25 is 1, <1 is 0. Then one level is lost (floor 0) when volatility is high
    (``income.volatility`` band "high", cv >= 0.35)."""
    if file.segment == "msme_business":
        level = band_level(DSCR_EDGES, true_dscr(file))
    else:
        level = band_level(FOIR_HEADROOM_EDGES, foir_headroom_pts(file))
    if file.income.volatility_cv >= VOLATILITY_HIGH_CV:
        level = max(0, level - 1)
    return level


def income_stable(file: LoanFile) -> bool:
    """C_income_stable: volatility band "low" (cv < 0.25) and at least 12 months of income history."""
    return file.income.volatility_cv < VOLATILITY_STABLE_CV and file.income.months_history >= 12


def collateral_level(file: LoanFile) -> int:
    """C_collateral_adequacy 0-4 (home loans), read straight off state bands. Base from ``ltv_headroom_pts_band``
    (limit minus LTV): >=15 is 4, 8-15 is 3, 0-8 is 2, -5-0 is 1, <-5 is 0. Then minus 2 for a disputed title, 1 for a
    pending mutation, 1 for an adverse legal opinion, and 1 for ``valuation_spread_band`` ">20%" (spread of 20 percent
    or more); floored at 0."""
    prop = file.property
    assert prop is not None
    level = band_level(LTV_HEADROOM_EDGES, ltv_headroom_pts(file))
    level -= {"clear": 0, "pending_mutation": 1, "disputed": 2}[prop.title_status]
    if prop.legal_opinion == "adverse":
        level -= 1
    lo, hi = sorted((prop.market_value_inr, prop.valuation_2_inr))
    if hi > 0 and round((hi - lo) / hi, BAND_ROUND) >= VALUATION_SPREAD_HIGH:
        level -= 1
    return max(0, level)


def question_truth(file: LoanFile) -> dict[str, bool | int]:
    """Truth for every applicable section 3.8 question (bool for a Noul, int level for a Score). Needs the labels
    other than ``question_truth`` itself to be filled in already."""
    lab = file.labels
    doubt = {
        "D_doubt_income_documentation": "income_documentation",
        "D_doubt_repayment_history": "repayment_history",
        "D_doubt_debt_burden": "debt_burden",
        "D_doubt_stability": "employment_or_business_stability",
        "D_doubt_collateral": "collateral",
        "D_doubt_thin_file": "bureau_thin_file",
    }
    truth: dict[str, bool | int] = {
        "A_income_proof_current": "income_proof" not in lab.missing_items,
        "A_address_proof_valid": "address_proof" not in lab.missing_items,
        "A_statements_cover_months": "statements" not in lab.missing_items,
        "A_fields_cohere": "fields_coherence" not in lab.missing_items,
        "B_identity_coheres": lab.fraud_type not in ("identity_mismatch", "synthetic_identity"),
        "B_salary_matches_employer": lab.fraud_type != "salary_pattern_mismatch",
        "B_gst_bank_consistent": lab.fraud_type != "gst_bank_mismatch",
        "B_synthetic_identity_signals": lab.fraud_type == "synthetic_identity",
        "C_willingness": willingness_level(file),
        "C_recent_delinquency": file.bureau.max_dpd_12m >= 30,
        "C_capacity": capacity_level(file),
        "C_foir_within_limit": foir_within_limit(file),
        "C_income_stable": income_stable(file),
        "D_closeness": lab.closeness_level,
        "E_memo_matches_grid": "memo_condition_mismatch" not in lab.memo_defects,
        "E_rate_math_correct": "apr_math_wrong" not in lab.memo_defects,
        "E_disclosures_complete": not any(d.startswith("disclosure_missing:") for d in lab.memo_defects),
    }
    for qid, cat in doubt.items():
        truth[qid] = lab.primary_weakness == cat
    if file.property is not None:
        truth["C_collateral_adequacy"] = collateral_level(file)
        truth["C_collateral_title_clear"] = file.property.title_status == "clear" and file.property.legal_opinion == "positive"
    for d in disclosure_ids():
        truth[f"E_disclosure_{d}"] = f"disclosure_missing:{d}" not in lab.memo_defects
    for qid in EWS_IDS:
        truth[qid] = lab.ews_truth[qid]
    for cid in MSME_COVENANT_IDS:
        truth[f"F_covenant_{cid}"] = cid not in lab.covenant_breaches
    return {qid: truth[qid] for qid in applicable_qids(file)}
