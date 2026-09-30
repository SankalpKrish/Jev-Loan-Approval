"""Module C: appraisal support (PLAN section 3.8). Three Scores (5 levels) and four Nouls.

The policy engine combines these with per-segment weights from YAML, so each question is one dimension:
willingness (conduct on existing obligations), capacity (debt burden and income stability) and, for a home
loan, collateral. Levels are tied to the band vocabulary of the state and are written worst to best.

FOIR, DSCR and LTV were computed in code (decision D2). For the two comparisons against a limit the state also
carries the answer, as a headroom band: `obligations.foir_headroom_pts_band` (segment FOIR limit minus FOIR, in
points) and `property.ltv_headroom_pts_band` (LTV limit minus LTV, in points). The rubric reads those, so Jev
never has to compare a band with a limit. An MSME file is judged on `business.dscr_band` instead. Volatility is
low below 0.25 (the stable cut) and high from 0.35 (the capacity deduction), so `income.volatility` lines up with
both truths. The tables below are the levels the bands map to; `tests/test_modules_abc.py` and
`tests/test_sim_rules_abc.py` check them against the state builder's vocabulary and the truth formula.
"""

from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register, score_wire

FOIR_HEADROOM_BAND_ORDER = ["<-10", "-10-0", "0-10", "10-20", ">=20"]
DSCR_BAND_ORDER = ["<1", "1-1.25", "1.25-1.5", "1.5-2", ">2"]
LTV_HEADROOM_BAND_ORDER = ["<-5", "-5-0", "0-8", "8-15", ">=15"]
VALUATION_SPREAD_BAND_ORDER = ["<5%", "5-10%", "10-20%", ">20%"]

# Capacity level (0-4) before the high-volatility deduction: from the headroom of the FOIR under its segment limit
# (every segment but MSME) or from the DSCR band (MSME). A limit of 50, 55 or 60 lines up with these bands to within
# one level; the generator's truth uses the exact FOIR.
CAPACITY_LEVEL_BY_FOIR_HEADROOM_BAND = {"<-10": 0, "-10-0": 1, "0-10": 2, "10-20": 3, ">=20": 4}
CAPACITY_LEVEL_BY_DSCR_BAND = {"<1": 0, "1-1.25": 1, "1.25-1.5": 2, "1.5-2": 3, ">2": 4}

# Collateral base level (0-4) before the defect deductions, from the headroom of the LTV under its limit. The band
# edges are the truth's own thresholds (15, 8, 0 and -5 points).
COLLATERAL_BASE_BY_LTV_HEADROOM_BAND = {"<-5": 0, "-5-0": 1, "0-8": 2, "8-15": 3, ">=15": 4}


def _capacity_text(level: int) -> str:
    headroom = [band for band in FOIR_HEADROOM_BAND_ORDER if CAPACITY_LEVEL_BY_FOIR_HEADROOM_BAND[band] == level]
    dscr = [band for band in DSCR_BAND_ORDER if CAPACITY_LEVEL_BY_DSCR_BAND[band] == level]
    return (
        f"`obligations.foir_headroom_pts_band` is {' or '.join(headroom)}, or, for segment msme_business, "
        f"`business.dscr_band` is {' or '.join(dscr)}."
    )


_WILLINGNESS = score_wire(
    "How clean is the applicant's conduct on existing obligations?",
    focus=(
        "Use `bureau.max_dpd_12m_band`, `bureau.writeoffs_or_settlements` and `bank.emi_bounces_6m` only. Ignore "
        "income, debt burden and collateral. Pick the worst level that any of the three fields shows."
    ),
    refer_to=["`bureau.max_dpd_12m_band`", "`bureau.writeoffs_or_settlements`", "`bank.emi_bounces_6m`"],
    levels=[
        {
            "level": "severe",
            "what": "`bureau.writeoffs_or_settlements` is 1 or more, or `bureau.max_dpd_12m_band` is 90+.",
            "not_for": "A max_dpd_12m_band of 60-89 with no write-off or settlement.",
            "examples": [
                "writeoffs_or_settlements 1 with max_dpd_12m_band 0",
                "max_dpd_12m_band 90+ with writeoffs_or_settlements 0",
                "writeoffs_or_settlements 2 with max_dpd_12m_band 30-59",
            ],
        },
        {
            "level": "serious",
            "what": "`bureau.max_dpd_12m_band` is 60-89 and `bureau.writeoffs_or_settlements` is 0.",
            "not_for": "A 90+ band or any write-off (severe), and a 30-59 band (weak).",
            "examples": [
                "max_dpd_12m_band 60-89 with writeoffs_or_settlements 0 and emi_bounces_6m 0",
                "max_dpd_12m_band 60-89 with emi_bounces_6m 3",
            ],
        },
        {
            "level": "weak",
            "what": (
                "`bureau.max_dpd_12m_band` is 30-59, or `bank.emi_bounces_6m` is 2 or more, with "
                "`bureau.writeoffs_or_settlements` 0 and a band below 60-89."
            ),
            "not_for": "A band of 60-89 or 90+, and any write-off.",
            "examples": [
                "max_dpd_12m_band 30-59",
                "max_dpd_12m_band 0 with emi_bounces_6m 2",
                "max_dpd_12m_band 1-29 with emi_bounces_6m 3",
            ],
        },
        {
            "level": "minor_slip",
            "what": (
                "`bureau.max_dpd_12m_band` is 1-29, or `bank.emi_bounces_6m` is exactly 1; "
                "`bureau.writeoffs_or_settlements` is 0 and `bank.emi_bounces_6m` is at most 1."
            ),
            "not_for": "A band of 30-59 or worse, 2 or more bounces, and any write-off.",
            "examples": [
                "max_dpd_12m_band 1-29 with emi_bounces_6m 0",
                "max_dpd_12m_band 0 with emi_bounces_6m 1",
            ],
        },
        {
            "level": "spotless",
            "what": (
                "`bureau.max_dpd_12m_band` is 0, `bank.emi_bounces_6m` is 0 and "
                "`bureau.writeoffs_or_settlements` is 0."
            ),
            "not_for": "Any days past due, any bounce, and any write-off. A thin bureau file with these values is still spotless.",
            "examples": [
                "max_dpd_12m_band 0, emi_bounces_6m 0, writeoffs_or_settlements 0",
                "bureau score_band NTC with max_dpd_12m_band 0, emi_bounces_6m 0, writeoffs_or_settlements 0",
            ],
        },
    ],
)

_RECENT_DELINQUENCY = noul_wire(
    "Is `bureau.max_dpd_12m_band` 30-59, 60-89 or 90+?",
    focus="Use `bureau.max_dpd_12m_band` only. Bounces and write-offs do not change the answer.",
    refer_to=["`bureau.max_dpd_12m_band`"],
    true_what="`bureau.max_dpd_12m_band` is 30-59, 60-89 or 90+.",
    true_examples=["max_dpd_12m_band 30-59", "max_dpd_12m_band 60-89", "max_dpd_12m_band 90+"],
    false_what="`bureau.max_dpd_12m_band` is 0 or 1-29.",
    false_not_for="A 30-59 band, and any band worse than that. Write-offs and bounces with a band of 0 or 1-29 are a no.",
    false_examples=["max_dpd_12m_band 0", "max_dpd_12m_band 1-29", "max_dpd_12m_band 0 with emi_bounces_6m 3"],
)

_CAPACITY = score_wire(
    "How much room does the applicant's income leave for the proposed EMI?",
    focus=(
        "Segment msme_business is judged by `business.dscr_band`. Every other segment is judged by "
        "`obligations.foir_headroom_pts_band`, which is the segment FOIR limit minus the FOIR, in points, already "
        "worked out: a band of 0-10 means the FOIR is under the limit by up to 10 points, and a band below 0 means "
        "the FOIR is over the limit. Find the level from the matching band in the level descriptions, then lower the "
        "level by one when `income.volatility` is high (level 0 stays level 0)."
    ),
    data={
        "foir_headroom_pts_band_order": FOIR_HEADROOM_BAND_ORDER,
        "dscr_band_order": DSCR_BAND_ORDER,
        "note": "A higher headroom band is a lighter debt burden. A higher DSCR band is a lighter one too.",
    },
    refer_to=[
        "`segment`",
        "`obligations.foir_headroom_pts_band`",
        "`business.dscr_band`",
        "`income.volatility`",
    ],
    levels=[
        {
            "level": "over_stretched",
            "what": _capacity_text(0),
            "not_for": "A headroom band of -10-0, which is over the limit by less than 10 points (over_limit).",
            "examples": [
                "foir_headroom_pts_band <-10",
                "foir_headroom_pts_band -10-0 with income.volatility high, which drops from over_limit",
                "segment msme_business with dscr_band <1",
            ],
        },
        {
            "level": "over_limit",
            "what": _capacity_text(1),
            "not_for": "A headroom band of 0-10 or better (at_limit or better), and a band below -10 (over_stretched).",
            "examples": [
                "foir_headroom_pts_band -10-0",
                "foir_headroom_pts_band 0-10 with income.volatility high, which drops from at_limit",
                "segment msme_business with dscr_band 1-1.25",
            ],
        },
        {
            "level": "at_limit",
            "what": _capacity_text(2),
            "not_for": "A headroom band below 0 (over_limit), and a band of 10-20 or more (comfortable or strong).",
            "examples": [
                "foir_headroom_pts_band 0-10",
                "foir_headroom_pts_band 10-20 with income.volatility high, which drops from comfortable",
                "segment msme_business with dscr_band 1.25-1.5",
            ],
        },
        {
            "level": "comfortable",
            "what": _capacity_text(3),
            "not_for": "A headroom band of 0-10 (at_limit), and a band of >=20 with low volatility (strong).",
            "examples": [
                "foir_headroom_pts_band 10-20",
                "foir_headroom_pts_band >=20 with income.volatility high, which drops from strong",
                "segment msme_business with dscr_band 1.5-2",
            ],
        },
        {
            "level": "strong",
            "what": _capacity_text(4),
            "not_for": "Any band in the level descriptions above. High `income.volatility` removes this level.",
            "examples": [
                "foir_headroom_pts_band >=20 with income.volatility low",
                "foir_headroom_pts_band >=20 with income.volatility moderate",
                "segment msme_business with dscr_band >2",
            ],
        },
    ],
)

_FOIR_WITHIN_LIMIT = noul_wire(
    "Is the applicant's debt burden within the segment limit?",
    focus=(
        "For segment msme_business, the burden is within the limit when `business.dscr_band` is 1.25-1.5, 1.5-2 or "
        ">2. For every other segment, `obligations.foir_headroom_pts_band` is the segment FOIR limit minus the FOIR, "
        "in points, already worked out: the burden is within the limit when the band is 0-10, 10-20 or >=20."
    ),
    refer_to=["`segment`", "`business.dscr_band`", "`obligations.foir_headroom_pts_band`"],
    true_what=(
        "`obligations.foir_headroom_pts_band` is 0-10, 10-20 or >=20, or, for msme_business, the DSCR band is "
        "1.25-1.5, 1.5-2 or >2."
    ),
    true_examples=[
        "foir_headroom_pts_band 0-10",
        "foir_headroom_pts_band >=20",
        "segment msme_business with dscr_band 1.5-2",
    ],
    false_what=(
        "`obligations.foir_headroom_pts_band` is -10-0 or <-10, or, for msme_business, the DSCR band is <1 or 1-1.25."
    ),
    false_not_for="A headroom band of 0-10, which is on or under the limit and is within it.",
    false_examples=[
        "foir_headroom_pts_band -10-0",
        "foir_headroom_pts_band <-10",
        "segment msme_business with dscr_band 1-1.25",
    ],
)

_INCOME_STABLE = noul_wire(
    "Is `income.volatility` low, and is `income.months_history` 12 or more?",
    focus="Both parts must hold. Use `income.volatility` and `income.months_history` only.",
    refer_to=["`income.volatility`", "`income.months_history`"],
    true_what="`income.volatility` is low and `income.months_history` is 12 or more.",
    true_examples=[
        "volatility low with months_history 36",
        "volatility low with months_history 12",
        "volatility low with months_history 120",
    ],
    false_what="`income.volatility` is moderate or high, or `income.months_history` is under 12.",
    false_not_for="Volatility low with months_history of 12 or more. That is a yes.",
    false_examples=[
        "volatility moderate with months_history 48",
        "volatility high with months_history 24",
        "volatility low with months_history 8",
    ],
)

_COLLATERAL_ADEQUACY = score_wire(
    "How adequate is the property as collateral for the loan?",
    focus=(
        "Start from `property.ltv_headroom_pts_band`, which is the LTV limit minus the LTV, in points, already worked "
        "out. Far below the limit means >=15. Below the limit means 8-15. At the limit means 0-8. Just above the limit "
        "means -5-0. Well above the limit means <-5. Then look for defects. A minor defect is `property.title_status` "
        "pending_mutation, `property.legal_opinion` adverse, or `property.valuation_spread_band` >20%. A disputed "
        "`property.title_status` is a serious defect."
    ),
    data={"ltv_headroom_pts_band_order": LTV_HEADROOM_BAND_ORDER, "valuation_spread_band_order": VALUATION_SPREAD_BAND_ORDER},
    refer_to=[
        "`property.ltv_headroom_pts_band`",
        "`property.title_status`",
        "`property.legal_opinion`",
        "`property.valuation_spread_band`",
    ],
    levels=[
        {
            "level": "inadequate",
            "what": (
                "The headroom band is <-5; or -5-0 with any defect; or 0-8 with a serious defect or two minor defects; "
                "or 8-15 with a serious defect and a minor defect."
            ),
            "not_for": "A headroom band of 0-8 or better with clear title and a positive legal opinion.",
            "examples": [
                "ltv_headroom_pts_band <-5",
                "ltv_headroom_pts_band -5-0 with legal_opinion adverse",
                "ltv_headroom_pts_band 0-8 with title_status disputed",
            ],
        },
        {
            "level": "weak",
            "what": (
                "The headroom band is -5-0 with no defect; or 0-8 with one minor defect; or 8-15 with a serious defect "
                "or two minor defects; or >=15 with a serious defect and a minor defect."
            ),
            "not_for": "A headroom band of 0-8 with no defect (marginal).",
            "examples": [
                "ltv_headroom_pts_band -5-0, title_status clear, legal_opinion positive",
                "ltv_headroom_pts_band 0-8 with title_status pending_mutation",
                "ltv_headroom_pts_band 8-15 with title_status disputed",
            ],
        },
        {
            "level": "marginal",
            "what": (
                "The headroom band is 0-8 with no defect; or 8-15 with one minor defect; or >=15 with a serious defect "
                "or two minor defects."
            ),
            "not_for": "A headroom band of >=15 with clear title, a positive opinion and a small spread (strong).",
            "examples": [
                "ltv_headroom_pts_band 0-8, title_status clear, legal_opinion positive",
                "ltv_headroom_pts_band 8-15 with valuation_spread_band >20%",
                "ltv_headroom_pts_band >=15 with title_status disputed",
            ],
        },
        {
            "level": "adequate",
            "what": "The headroom band is 8-15 with no defect; or >=15 with exactly one minor defect.",
            "not_for": "A defect-free band of >=15 (strong), and a disputed title (marginal or worse).",
            "examples": [
                "ltv_headroom_pts_band 8-15, title_status clear, legal_opinion positive",
                "ltv_headroom_pts_band >=15 with legal_opinion adverse",
                "ltv_headroom_pts_band 8-15, valuation_spread_band 10-20%, title_status clear, legal_opinion positive",
            ],
        },
        {
            "level": "strong",
            "what": (
                "The headroom band is >=15, `property.title_status` is clear, `property.legal_opinion` is positive, and "
                "`property.valuation_spread_band` is <5%, 5-10% or 10-20%."
            ),
            "not_for": "Any defect, and any headroom band below 15.",
            "examples": [
                "ltv_headroom_pts_band >=15, title_status clear, legal_opinion positive, valuation_spread_band <5%",
                "ltv_headroom_pts_band >=15, title_status clear, legal_opinion positive, valuation_spread_band 5-10%",
                "ltv_headroom_pts_band >=15, title_status clear, legal_opinion positive, valuation_spread_band 10-20%",
            ],
        },
    ],
)

_TITLE_CLEAR = noul_wire(
    "Is `property.title_status` clear, and is `property.legal_opinion` positive?",
    focus="Both parts must hold. Use `property.title_status` and `property.legal_opinion` only.",
    refer_to=["`property.title_status`", "`property.legal_opinion`"],
    true_what="`property.title_status` is clear and `property.legal_opinion` is positive.",
    true_examples=[
        "title_status clear with legal_opinion positive",
        "title_status clear, legal_opinion positive, ltv_pct_band 75-80",
    ],
    false_what="`property.title_status` is disputed or pending_mutation, or `property.legal_opinion` is adverse or pending.",
    false_not_for="A clear title with a positive legal opinion, whatever the LTV band or valuation spread.",
    false_examples=[
        "title_status disputed",
        "title_status pending_mutation with legal_opinion positive",
        "title_status clear with legal_opinion pending",
    ],
)


def _q(qid, qtype, wire, polarity, reason, segments=ALL_SEGMENTS) -> QuestionDef:
    return register(
        QuestionDef(
            qid=qid,
            module="C",
            stage="appraisal",
            qtype=qtype,
            segments=frozenset(segments),
            risk_polarity=polarity,
            routing_relevant=True,
            reason_text=reason,
            wire=wire,
        )
    )


_q("C_willingness", "score", _WILLINGNESS, "ordinal_high_is_good", "Conduct on existing obligations is weak: delinquency, write-offs or EMI bounces")
_q("C_recent_delinquency", "noul", _RECENT_DELINQUENCY, "yes_is_bad", "Worst delinquency in the last 12 months was 30 or more days past due")
_q("C_capacity", "score", _CAPACITY, "ordinal_high_is_good", "Income leaves little room for the proposed EMI: debt burden at or over the segment limit, or unstable income")
_q("C_foir_within_limit", "noul", _FOIR_WITHIN_LIMIT, "yes_is_good", "Debt burden is above the segment limit")
_q("C_income_stable", "noul", _INCOME_STABLE, "yes_is_good", "Income is volatile or has a short history")
_q(
    "C_collateral_adequacy",
    "score",
    _COLLATERAL_ADEQUACY,
    "ordinal_high_is_good",
    "Collateral is weak: high loan-to-value, title or legal opinion problems, or a wide valuation spread",
    {"secured_home"},
)
_q(
    "C_collateral_title_clear",
    "noul",
    _TITLE_CLEAR,
    "yes_is_good",
    "Property title is not clear or the legal opinion is not positive",
    {"secured_home"},
)
