"""Module D: exception triage (PLAN sections 1.1 D7, 3.8; spec section 5).

Asked in the `appraisal` stage, in the same call as A, B and C and on the same banded state, but speculatively: it
never gates anything (`routing_relevant=False`). It orders the human credit-review queue and supplies the reason.

* `D_closeness`: a 5-level Score, from "clearly not sanctionable" to "clearly sanctionable". Truth is
  `labels.closeness_level`: the smallest margin over the sanction rules (conduct, burden, stability, collateral,
  and the risk estimate) decides the level, so the rubric tells Jev the file is only as close as its weakest factor.
* `D_doubt_*`: one Noul per factor, asking whether that factor is *the main* source of doubt. Truth is
  `labels.primary_weakness`. Every one of them carries the same definition of "main" (the weakest factor,
  measured against its own limit), so the six answers compete on the same scale.

Wording follows docs/vendor/typesafe/model-jaggedness_jev-1.13.md: literal conditions, one judgment per Noul, state
fields named in backticks, no arithmetic left to the model (the state already holds the bands).
"""

from __future__ import annotations

from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register, score_wire

_HOME_ONLY = frozenset({"secured_home"})

_MISSING_BLOCK = (
    "Use only the blocks that are present in `state`. A block that is missing (for example `property` on a salaried "
    "loan, or `business` on a home loan) does not apply to this file and is not a weakness."
)

# --------------------------------------------------------------------------------------------- D_closeness

CLOSENESS_LEVELS = [
    {
        "level": "clearly_not_sanctionable",
        "what": (
            "At least one factor is far beyond its limit, so no strength elsewhere can make up for it. Hard failures "
            "are a write-off or settlement in `bureau.writeoffs_or_settlements`, a `bureau.max_dpd_12m_band` of `90+`, "
            "a `bureau.score_band` of `<600`, a `bureau.score_band` of `NTC` on any loan other than a salaried "
            "personal loan, `obligations.foir_pct_band` of `>70`, `business.dscr_band` of `<1`, "
            "`property.ltv_pct_band` of `>85`, or a `property.title_status` other than `clear`."
        ),
        "not_for": (
            "A file whose weakest factor is only just past its limit. A `bureau.max_dpd_12m_band` of `60-89` or a "
            "`bureau.score_band` of `600-649` on its own belongs one level up."
        ),
        "examples": [
            "`bureau.writeoffs_or_settlements` is 1 and `bureau.max_dpd_12m_band` is `90+`.",
            "`obligations.foir_pct_band` is `>70` for a personal loan, so the burden is far above `obligations.segment_foir_limit_pct`.",
            "`property.title_status` is `disputed` on a home loan, whatever the other bands say.",
        ],
    },
    {
        "level": "probably_not_sanctionable",
        "what": (
            "One factor is beyond its limit, but only narrowly, and nothing else is strong enough to offset it: "
            "a `bureau.max_dpd_12m_band` of `60-89`, a `bureau.score_band` of `600-649`, an "
            "`obligations.foir_pct_band` one band above `obligations.segment_foir_limit_pct`, a `business.dscr_band` "
            "of `1-1.25`, a `property.ltv_pct_band` of `80-85`, or a `business.vintage_years_band` under two years."
        ),
        "not_for": (
            "A file with a hard failure such as a write-off or a `90+` band (that is the level below), and a file "
            "where every factor is inside its limit (that is the level above)."
        ),
        "examples": [
            "`bureau.max_dpd_12m_band` is `60-89`, `bureau.score_band` is `700-749` and `obligations.foir_pct_band` is `30-40`.",
            "`business.dscr_band` is `1-1.25` on an MSME loan while conduct in `bureau` is clean.",
            "`obligations.foir_pct_band` is `60-70` while `obligations.segment_foir_limit_pct` is 55.",
        ],
    },
    {
        "level": "on_the_boundary",
        "what": (
            "No single hard failure, but the file sits right at a limit or several factors are weak at the same time, "
            "so a small change would decide the outcome: for example a `bureau.score_band` of `650-699` together with "
            "an `obligations.foir_pct_band` at the `obligations.segment_foir_limit_pct`, or `income.volatility` of "
            "`high` together with a `bureau.max_dpd_12m_band` of `30-59`."
        ),
        "not_for": (
            "A file that fails one limit clearly (a level below), and a file whose factors are all comfortably "
            "inside their limits (a level above)."
        ),
        "examples": [
            "`bureau.score_band` is `650-699` and `obligations.foir_pct_band` is `50-55` with `obligations.segment_foir_limit_pct` of 55.",
            "`income.volatility` is `high`, `bank.emi_bounces_6m` is 2 and `bureau.max_dpd_12m_band` is `30-59`, yet no limit is clearly broken.",
            "`property.ltv_pct_band` is `75-80` with `property.ltv_limit_pct` of 80 and `property.legal_opinion` is `pending`.",
        ],
    },
    {
        "level": "probably_sanctionable",
        "what": (
            "Every factor is inside its limit. One or two factors are close to a limit, for example a "
            "`bureau.score_band` of `700-749`, an `obligations.foir_pct_band` of `40-50`, a "
            "`bureau.max_dpd_12m_band` of `1-29`, or an `income.volatility` of `moderate`, but none is beyond it."
        ),
        "not_for": (
            "A file with any factor beyond its limit (a level below), and a file where every factor is comfortable "
            "with room to spare (the level above)."
        ),
        "examples": [
            "`bureau.score_band` is `700-749`, `obligations.foir_pct_band` is `40-50` and `bureau.max_dpd_12m_band` is `0`.",
            "`bureau.max_dpd_12m_band` is `1-29`, `income.volatility` is `moderate`, and all other bands are inside their limits.",
            "`business.dscr_band` is `1.25-1.5` on an MSME loan with clean conduct in `bureau`.",
        ],
    },
    {
        "level": "clearly_sanctionable",
        "what": (
            "Every factor is comfortably inside its limit: `bureau.max_dpd_12m_band` of `0`, no write-off or settlement, "
            "a `bureau.score_band` of `750-799` or `800+`, an `obligations.foir_pct_band` of `<30` or `30-40`, "
            "`income.volatility` of `low`, and on a home loan a low `property.ltv_pct_band` with `property.title_status` of `clear`."
        ),
        "not_for": (
            "A file where any factor is close to its limit or beyond it, however good the other factors are."
        ),
        "examples": [
            "`bureau.score_band` is `800+`, `bureau.max_dpd_12m_band` is `0`, `obligations.foir_pct_band` is `<30` and `income.volatility` is `low`.",
            "`business.dscr_band` is `>2`, `business.vintage_years_band` is long and `bureau.writeoffs_or_settlements` is 0.",
            "`property.ltv_pct_band` is `<60`, `property.title_status` is `clear` and `bureau.score_band` is `750-799`.",
        ],
    },
]

CLOSENESS_WIRE = score_wire(
    "How close is this file to being sanctionable under the bank's credit policy? Judge it on four things together: "
    "conduct (`bureau` and `bank`), repayment burden (`obligations`, and `business.dscr_band` for a business loan), "
    "stability (`income`, `business.vintage_years_band`) and, on a home loan, collateral (`property`).",
    refer_to=[
        "`bureau.max_dpd_12m_band`",
        "`bureau.writeoffs_or_settlements`",
        "`bureau.score_band`",
        "`bank.emi_bounces_6m`",
        "`obligations.foir_pct_band`",
        "`obligations.segment_foir_limit_pct`",
        "`business.dscr_band`",
        "`income.volatility`",
        "`income.months_history`",
        "`business.vintage_years_band`",
        "`property.ltv_pct_band`",
        "`property.ltv_limit_pct`",
        "`property.title_status`",
        "`property.legal_opinion`",
    ],
    focus=(
        "The file is only as close as its weakest factor allows: one factor beyond its limit outweighs strength "
        "elsewhere. Read each band as written and compare it with the limit shown next to it; do not recalculate. "
        + _MISSING_BLOCK
    ),
    levels=CLOSENESS_LEVELS,
)

register(
    QuestionDef(
        qid="D_closeness",
        module="D",
        stage="appraisal",
        qtype="score",
        segments=ALL_SEGMENTS,
        risk_polarity="ordinal_high_is_good",
        routing_relevant=False,
        reason_text="How close the file is to the sanction boundary (orders the credit review queue, nearest to sanctionable first)",
        wire=CLOSENESS_WIRE,
    )
)

# --------------------------------------------------------------------------------------------- D_doubt_*

_MAIN_MEANS = (
    "'Main' means weakest. The factors are: conduct (repayment history), burden (debt burden), stability, income "
    "documentation, collateral (home loans only) and thin file (little or no credit history). Answer yes only if this "
    "factor is weaker, measured against its own limit, than each of the other factors. If this factor is weak but "
    "another factor is weaker, answer no. If every factor is comfortably inside its limit, answer yes only for the "
    "factor that sits closest to its limit. " + _MISSING_BLOCK
)

# one entry per factor: the question, the state fields, and the rubric for each side
_FACTORS: list[dict] = [
    {
        "qid": "D_doubt_income_documentation",
        "segments": ALL_SEGMENTS,
        "question": (
            "Is a gap between declared and verified income, or a mismatch between GST turnover and bank credits, "
            "the main source of doubt about this file?"
        ),
        "refer_to": [
            "`application.declared_monthly_income_band`",
            "`income.verified_monthly_income_band`",
            "`income.documentation_type`",
            "`gst.gst_to_bank_ratio_band`",
        ],
        "true_what": (
            "The income the applicant declared is not backed by the verified income, or the GST turnover and the bank "
            "credits do not agree, and this is weaker than every other factor: `application.declared_monthly_income_band` "
            "is a higher band than `income.verified_monthly_income_band`, or `gst.gst_to_bank_ratio_band` is `<0.5`, "
            "`0.5-0.8`, `1.2-2` or `>2`."
        ),
        "true_examples": [
            "`application.declared_monthly_income_band` is `75k-1L` but `income.verified_monthly_income_band` is `50-75k`, and conduct and burden are comfortable.",
            "`gst.gst_to_bank_ratio_band` is `>2` on an MSME loan while `bureau.max_dpd_12m_band` is `0`.",
        ],
        "false_what": (
            "The declared and verified income bands are the same and `gst.gst_to_bank_ratio_band` is `0.8-1.2` or absent, "
            "or another factor is weaker than the income evidence."
        ),
        "false_not_for": (
            "`income.documentation_type` on its own. A documentation type such as `informal_declared` is not doubt by "
            "itself; look at whether declared and verified income agree and whether GST and bank credits agree."
        ),
        "false_examples": [
            "`income.documentation_type` is `informal_declared` but `application.declared_monthly_income_band` equals `income.verified_monthly_income_band` and `obligations.foir_pct_band` is `>70`.",
            "`gst.gst_to_bank_ratio_band` is `0.8-1.2` and the weakest factor is `bureau.max_dpd_12m_band` of `60-89`.",
        ],
        "reason": "Main doubt is income documentation: declared income is not matched by verified income, or GST and bank credits disagree",
    },
    {
        "qid": "D_doubt_repayment_history",
        "segments": ALL_SEGMENTS,
        "question": (
            "Is the applicant's repayment conduct (delinquency, write-offs, bureau score and bounced EMIs) the main "
            "source of doubt about this file?"
        ),
        "refer_to": [
            "`bureau.max_dpd_12m_band`",
            "`bureau.writeoffs_or_settlements`",
            "`bureau.score_band`",
            "`bank.emi_bounces_6m`",
        ],
        "true_what": (
            "Payment conduct is the weakest factor: a write-off or settlement in `bureau.writeoffs_or_settlements`, a "
            "`bureau.max_dpd_12m_band` worse than `0`, a low `bureau.score_band` (`<600`, `600-649` or `650-699`), or "
            "`bank.emi_bounces_6m` of 1 or more, and no other factor is weaker."
        ),
        "true_examples": [
            "`bureau.max_dpd_12m_band` is `60-89` and `bank.emi_bounces_6m` is 3, while `obligations.foir_pct_band` is `30-40`.",
            "`bureau.writeoffs_or_settlements` is 1 and every other block looks comfortable.",
        ],
        "false_what": (
            "Conduct is clean (`bureau.max_dpd_12m_band` is `0`, no write-off, no bounced EMI, a good `bureau.score_band`), "
            "or another factor is weaker than the conduct record."
        ),
        "false_not_for": (
            "A `bureau.score_band` of `NTC`. No score means little history, which belongs to the thin file question, "
            "not to bad conduct."
        ),
        "false_examples": [
            "`bureau.max_dpd_12m_band` is `1-29` but `obligations.foir_pct_band` is `>70`, so the burden is weaker.",
            "`bureau.score_band` is `NTC` and `bureau.max_dpd_12m_band` is `0`.",
        ],
        "reason": "Main doubt is repayment history: delinquency, write-off, low bureau score or bounced EMIs",
    },
    {
        "qid": "D_doubt_debt_burden",
        "segments": ALL_SEGMENTS,
        "question": (
            "Is the repayment burden (existing plus proposed EMI against income, card use, or business debt service "
            "cover) the main source of doubt about this file?"
        ),
        "refer_to": [
            "`obligations.foir_pct_band`",
            "`obligations.segment_foir_limit_pct`",
            "`obligations.credit_card_utilization_band`",
            "`business.dscr_band`",
        ],
        "true_what": (
            "The burden is the weakest factor: `obligations.foir_pct_band` is at or above "
            "`obligations.segment_foir_limit_pct`, `obligations.credit_card_utilization_band` is high, or "
            "`business.dscr_band` is `<1` or `1-1.25`, and no other factor is weaker."
        ),
        "true_examples": [
            "`obligations.foir_pct_band` is `60-70` with `obligations.segment_foir_limit_pct` of 55 and `bureau.max_dpd_12m_band` is `0`.",
            "`business.dscr_band` is `<1` on an MSME loan while conduct in `bureau` is clean.",
        ],
        "false_what": (
            "The burden is comfortably below the limit (`obligations.foir_pct_band` is `<30` or `30-40`, "
            "`business.dscr_band` is `1.5-2` or `>2`), or another factor is weaker than the burden."
        ),
        "false_not_for": "A missing `business` block on a personal or home loan. It only means the DSCR does not apply.",
        "false_examples": [
            "`obligations.foir_pct_band` is `30-40` and the weakest factor is `bureau.writeoffs_or_settlements` of 1.",
            "`obligations.foir_pct_band` is `50-55` but `income.volatility` is `high` and `bank.emi_bounces_6m` is 3.",
        ],
        "reason": "Main doubt is debt burden: EMI obligations are at or above the limit for the segment",
    },
    {
        "qid": "D_doubt_stability",
        "segments": ALL_SEGMENTS,
        "question": (
            "Is unstable income, or a short job or business history, the main source of doubt about this file?"
        ),
        "refer_to": [
            "`income.volatility`",
            "`income.months_history`",
            "`application.years_in_job_or_business_band`",
            "`business.vintage_years_band`",
        ],
        "true_what": (
            "Stability is the weakest factor: `income.volatility` is `high`, `income.months_history` is short, or "
            "`application.years_in_job_or_business_band` or `business.vintage_years_band` is a short band, and no "
            "other factor is weaker."
        ),
        "true_examples": [
            "`income.volatility` is `high` and `business.vintage_years_band` is under two years, with `bureau.max_dpd_12m_band` of `0`.",
            "`application.years_in_job_or_business_band` is a short band and `income.volatility` is `moderate`, while burden and conduct are comfortable.",
        ],
        "false_what": (
            "Income is steady (`income.volatility` is `low`) with a long job or business history, or another factor is "
            "weaker than the stability of income."
        ),
        "false_not_for": (
            "A high burden or a poor bureau record when income itself is steady. Those belong to the burden and "
            "conduct questions."
        ),
        "false_examples": [
            "`income.volatility` is `low`, `income.months_history` is long and the weakest factor is `obligations.foir_pct_band` of `>70`.",
            "`income.volatility` is `high` but `bureau.writeoffs_or_settlements` is 1, so conduct is weaker.",
        ],
        "reason": "Main doubt is stability: income is volatile or the job or business history is short",
    },
    {
        "qid": "D_doubt_collateral",
        "segments": _HOME_ONLY,
        "question": (
            "Is the property (loan-to-value, title or legal opinion) the main source of doubt about this home loan?"
        ),
        "refer_to": [
            "`property.ltv_pct_band`",
            "`property.ltv_limit_pct`",
            "`property.title_status`",
            "`property.legal_opinion`",
        ],
        "true_what": (
            "The collateral is the weakest factor: `property.ltv_pct_band` is at or above `property.ltv_limit_pct`, "
            "`property.title_status` is `disputed` or `pending_mutation`, or `property.legal_opinion` is `adverse` or "
            "`pending`, and no other factor is weaker."
        ),
        "true_examples": [
            "`property.title_status` is `disputed` while `bureau.max_dpd_12m_band` is `0` and `obligations.foir_pct_band` is `30-40`.",
            "`property.ltv_pct_band` is `80-85` with `property.ltv_limit_pct` of 80 and the applicant's other bands are comfortable.",
        ],
        "false_what": (
            "The property is sound (`property.ltv_pct_band` is well under `property.ltv_limit_pct`, `property.title_status` "
            "is `clear`, `property.legal_opinion` is `positive`), or another factor is weaker than the property."
        ),
        "false_not_for": "The applicant's own conduct, burden or income. Those are separate factors.",
        "false_examples": [
            "`property.ltv_pct_band` is `<60`, `property.title_status` is `clear` and `bureau.max_dpd_12m_band` is `60-89`.",
            "`property.title_status` is `clear` and the weakest factor is `obligations.foir_pct_band` of `>70`.",
        ],
        "reason": "Main doubt is collateral: loan-to-value, title or legal opinion on the property is weak",
    },
    {
        "qid": "D_doubt_thin_file",
        "segments": ALL_SEGMENTS,
        "question": (
            "Is a thin credit file (`bureau.score_band` of `NTC`, a first-time borrower with little or no bureau "
            "history) the main source of doubt about this file?"
        ),
        "refer_to": [
            "`bureau.score_band`",
            "`bureau.history_length_band`",
            "`bureau.active_loans`",
        ],
        "true_what": (
            "`bureau.score_band` is `NTC` (no bureau score), usually with a short `bureau.history_length_band`, and no "
            "other factor is weaker than the missing history."
        ),
        "true_examples": [
            "`bureau.score_band` is `NTC`, `bureau.active_loans` is 0 and `obligations.foir_pct_band` is `30-40`.",
            "`bureau.score_band` is `NTC` with a short `bureau.history_length_band` and no delinquency in `bureau.max_dpd_12m_band`.",
        ],
        "false_what": (
            "`bureau.score_band` shows a score (`<600` up to `800+`), or the file has no score but another factor is "
            "clearly weaker."
        ),
        "false_not_for": (
            "A low score. A `bureau.score_band` of `<600` is a poor record, not a missing one, and belongs to the "
            "repayment conduct question."
        ),
        "false_examples": [
            "`bureau.score_band` is `600-649` and `bureau.max_dpd_12m_band` is `30-59`.",
            "`bureau.score_band` is `NTC` but `obligations.foir_pct_band` is `>70`, so the burden is weaker.",
        ],
        "reason": "Main doubt is a thin file: little or no credit history to judge conduct from",
    },
]

for _f in _FACTORS:
    register(
        QuestionDef(
            qid=_f["qid"],
            module="D",
            stage="appraisal",
            qtype="noul",
            segments=_f["segments"],
            risk_polarity="yes_is_bad",
            routing_relevant=False,
            reason_text=_f["reason"],
            wire=noul_wire(
                _f["question"],
                refer_to=_f["refer_to"],
                focus=_MAIN_MEANS,
                true_what=_f["true_what"],
                true_examples=_f["true_examples"],
                false_what=_f["false_what"],
                false_not_for=_f["false_not_for"],
                false_examples=_f["false_examples"],
            ),
        )
    )
