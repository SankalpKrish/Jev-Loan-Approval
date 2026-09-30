"""Banding helpers and the blocks every appraisal state shares (PLAN section 3.7).

Everything the model sees is a band, an enum, a small integer or redacted text. This module is the one place that
says where the band edges are: ``BAND_TABLES`` is table-driven, ``VOCABULARY`` lists every label a band (or an
enum the state builders emit) can take, and the question packs may rely on both.

Edge conventions, chosen so that a policy limit lines up with a band edge:

* "left closed" bands are ``[lo, hi)``: money, ages, tenure, DSCR (a *minimum* of 1.25 means 1.25 is in
  ``1.25-1.5``), volatility (``cv < 0.25`` is low, ``cv >= 0.35`` is high) and the two headroom bands (a headroom of
  exactly 0 means the value sits *on* its limit, which is within it, so 0 is in ``0-10`` / ``0-8``);
* "right closed" bands are ``(lo, hi]``: FOIR, LTV and the GST-to-bank ratio, whose limits are *maximums*
  (a FOIR of exactly 55.0 against a limit of 55 sits in ``50-55``, not ``55-60``).

Only *observed* fields are read here (verified income, EMIs, the declared bureau score, the valuation figures).
``meta`` and ``labels`` are never touched; in particular ``meta.true_bureau_score`` is never used, so a file in
the ``pincode_bureau_thin`` disparity subset shows ``NTC`` exactly as the application shows it.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from jevloan import finance
from jevloan.data.schema import LoanFile, grid_row_for

STATE_SCHEMA = "jevloan.state.v1"
STAGES = ("appraisal", "sanction_docs", "monitoring")
REFERENCE_YEAR = 2026  # the year ages are measured to (the synthetic book is as of 14 Sep 2026)

# Segment policy (PLAN 3.7): FOIR limits in percent. MSME is judged on DSCR; 80 percent FOIR is DSCR 1.25.
SEGMENT_FOIR_LIMIT_PCT = {"salaried_personal": 55, "self_employed": 50, "msme_business": 80, "secured_home": 60}
# Months of bank statement each segment must show.
MONTHS_REQUIRED = {"salaried_personal": 6, "secured_home": 6, "self_employed": 12, "msme_business": 12}

_ROUND = 6  # kills float fuzz (54.9999999999 vs 55.0) without moving a value across a band edge in practice


# ------------------------------------------------------------------------------------------------ band tables


@dataclass(frozen=True)
class BandTable:
    """``labels`` has one more entry than ``edges``: labels[0] is below edges[0], labels[-1] at or above edges[-1]."""

    name: str
    edges: tuple[float, ...]
    labels: tuple[str, ...]
    right_closed: bool = False  # True: (lo, hi]; False: [lo, hi)

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.edges) + 1:
            raise ValueError(f"{self.name}: {len(self.labels)} labels for {len(self.edges)} edges")
        if list(self.edges) != sorted(self.edges):
            raise ValueError(f"{self.name}: edges must be ascending")

    def label(self, value: float) -> str:
        value = round(float(value), _ROUND)
        i = bisect.bisect_left(self.edges, value) if self.right_closed else bisect.bisect_right(self.edges, value)
        return self.labels[i]


def _table(name: str, edges: list[float], labels: list[str], *, right_closed: bool = False) -> BandTable:
    return BandTable(name, tuple(edges), tuple(labels), right_closed)


BAND_TABLES: dict[str, BandTable] = {
    t.name: t
    for t in (
        # rupee amounts (loan, turnover, bank credits, property value)
        _table("amount", [1e5, 3e5, 5e5, 1e6, 2.5e6, 5e6, 1e7, 2e7, 5e7],
               ["<1L", "1-3L", "3-5L", "5-10L", "10-25L", "25-50L", "50L-1Cr", "1-2Cr", "2-5Cr", ">5Cr"]),
        # rupees per month (income, EMI, average credits, average balance)
        _table("monthly", [1e4, 2.5e4, 5e4, 7.5e4, 1e5, 2e5, 5e5],
               ["<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L"]),
        _table("foir_pct", [30, 40, 50, 55, 60, 70], ["<30", "30-40", "40-50", "50-55", "55-60", "60-70", ">70"],
               right_closed=True),
        _table("ltv_pct", [60, 70, 75, 80, 85], ["<60", "60-70", "70-75", "75-80", "80-85", ">85"], right_closed=True),
        _table("dscr", [1, 1.25, 1.5, 2], ["<1", "1-1.25", "1.25-1.5", "1.5-2", ">2"]),
        _table("gst_to_bank_ratio", [0.5, 0.8, 1.2, 2], ["<0.5", "0.5-0.8", "0.8-1.2", "1.2-2", ">2"], right_closed=True),
        _table("dpd", [1, 30, 60, 90], ["0", "1-29", "30-59", "60-89", "90+"]),
        _table("bureau_score", [600, 650, 700, 750, 800], ["<600", "600-649", "650-699", "700-749", "750-799", "800+"]),
        _table("age", [20, 25, 30, 35, 40, 45, 50, 55, 60, 65],
               ["<20", "20-24", "25-29", "30-34", "35-39", "40-44", "45-49", "50-54", "55-59", "60-64", "65+"]),
        _table("vintage_years", [1, 2, 5, 10], ["<1y", "1-2y", "2-5y", "5-10y", ">10y"]),  # years in job or business
        _table("history_length", [6, 12, 36, 84], ["<6m", "6-12m", "1-3y", "3-7y", ">7y"]),  # months of bureau history
        _table("phone_vintage", [3, 12, 36], ["<3m", "3-12m", "1-3y", ">3y"]),  # months
        _table("cash_deposit_share", [0.05, 0.15, 0.30], ["<5%", "5-15%", "15-30%", ">30%"]),
        _table("card_utilization", [0.10, 0.30, 0.50, 0.75], ["<10%", "10-30%", "30-50%", "50-75%", ">75%"]),
        _table("valuation_spread", [0.05, 0.10, 0.20], ["<5%", "5-10%", "10-20%", ">20%"]),
        _table("address_shared", [1, 3], ["0", "1-2", "3+"]),
        # coefficient of variation; the edges are the C_income_stable truth (cv < 0.25) and the capacity deduction (cv >= 0.35)
        _table("volatility", [0.25, 0.35], ["low", "moderate", "high"]),
        # segment FOIR limit minus FOIR, in percentage points (non-MSME); C_capacity is read straight off these bands
        _table("foir_headroom_pts", [-10, 0, 10, 20], ["<-10", "-10-0", "0-10", "10-20", ">=20"]),
        # LTV limit minus LTV, in percentage points (home loans); C_collateral_adequacy is read straight off these bands
        _table("ltv_headroom_pts", [-5, 0, 8, 15], ["<-5", "-5-0", "0-8", "8-15", ">=15"]),
    )
}

NTC = "NTC"
VOCABULARY: dict[str, tuple[str, ...]] = {name: t.labels for name, t in BAND_TABLES.items()}
VOCABULARY["bureau_score"] = (NTC, *BAND_TABLES["bureau_score"].labels)
VOCABULARY["documentation_type"] = ("salary_slip", "itr", "gst_and_bank", "informal_declared")
VOCABULARY["email_domain_type"] = ("corporate", "free", "disposable")
VOCABULARY["title_status"] = ("clear", "disputed", "pending_mutation")
VOCABULARY["legal_opinion"] = ("positive", "adverse", "pending")
VOCABULARY["balance_change"] = ("rising", "flat", "falling_10_40", "falling_40_plus")


# ------------------------------------------------------------------------------------------------ band functions


def amount_band(inr: float) -> str:
    """A rupee amount (a loan, a turnover, a property value): ``<1L`` ... ``>5Cr``."""
    return BAND_TABLES["amount"].label(inr)


def monthly_band(inr: float) -> str:
    """A rupee-per-month amount (income, EMI, credits, balance): ``<10k`` ... ``>5L``."""
    return BAND_TABLES["monthly"].label(inr)


def foir_band(pct: float) -> str:
    return BAND_TABLES["foir_pct"].label(pct)


def ltv_band(pct: float) -> str:
    return BAND_TABLES["ltv_pct"].label(pct)


def dscr_band(ratio: float) -> str:
    return BAND_TABLES["dscr"].label(ratio)


def gst_ratio_band(ratio: float) -> str:
    return BAND_TABLES["gst_to_bank_ratio"].label(ratio)


def dpd_band(days: int) -> str:
    return BAND_TABLES["dpd"].label(days)


def bureau_score_band(score: int | None) -> str:
    """``NTC`` for a missing score (new to credit), else ``<600`` ... ``800+``."""
    return NTC if score is None else BAND_TABLES["bureau_score"].label(score)


def age_band(dob_year: int, reference_year: int = REFERENCE_YEAR) -> str:
    """Five-year age band from the declared birth year: ``25-29``, ``30-34`` ..."""
    return BAND_TABLES["age"].label(reference_year - dob_year)


def vintage_years_band(years: float) -> str:
    """Tenure in a job or vintage of a business: ``<1y`` ... ``>10y``."""
    return BAND_TABLES["vintage_years"].label(years)


def history_length_band(months: int) -> str:
    return BAND_TABLES["history_length"].label(months)


def phone_vintage_band(months: int) -> str:
    return BAND_TABLES["phone_vintage"].label(months)


def cash_share_band(fraction: float) -> str:
    return BAND_TABLES["cash_deposit_share"].label(fraction)


def card_utilization_band(fraction: float) -> str:
    return BAND_TABLES["card_utilization"].label(fraction)


def valuation_spread_band(value_a: float, value_b: float) -> str:
    """Gap between the two valuations as a share of the higher one: ``<5%`` ... ``>20%``."""
    hi, lo = max(value_a, value_b), min(value_a, value_b)
    return BAND_TABLES["valuation_spread"].label(0.0 if hi <= 0 else (hi - lo) / hi)


def address_shared_band(n: int) -> str:
    return BAND_TABLES["address_shared"].label(n)


def volatility_label(cv: float) -> str:
    """``low`` (cv < 0.25), ``moderate`` (cv < 0.35), else ``high``."""
    return BAND_TABLES["volatility"].label(cv)


def foir_headroom_band(headroom_pts: float) -> str:
    """Segment FOIR limit minus FOIR, in points: ``<-10``, ``-10-0``, ``0-10``, ``10-20``, ``>=20``."""
    return BAND_TABLES["foir_headroom_pts"].label(headroom_pts)


def ltv_headroom_band(headroom_pts: float) -> str:
    """LTV limit minus LTV, in points: ``<-5``, ``-5-0``, ``0-8``, ``8-15``, ``>=15``."""
    return BAND_TABLES["ltv_headroom_pts"].label(headroom_pts)


def balance_change_band(balances: list[int]) -> str:
    """The average balance of the last two months against the first two (six months, oldest first):
    ``falling_40_plus`` if last <= 0.6 x first, else ``falling_10_40`` if <= 0.9 x, else ``rising`` if >= 1.1 x,
    else ``flat``. Compared as integers (10 x last against 6 / 9 / 11 x first) so no float fuzz decides a tie."""
    first, last = balances[0] + balances[1], balances[-2] + balances[-1]
    if first <= 0:
        return "flat"
    if 10 * last <= 6 * first:
        return "falling_40_plus"
    if 10 * last <= 9 * first:
        return "falling_10_40"
    if 10 * last >= 11 * first:
        return "rising"
    return "flat"


# ------------------------------------------------------------------------------------------------ observed ratios


def foir_pct(file: LoanFile) -> float:
    """(existing EMI + proposed EMI) / verified monthly income, in percent, from observed fields."""
    o = file.obligations
    return finance.foir(o.existing_emi_inr + o.proposed_emi_inr, file.income.verified_monthly_income_inr)


def dscr_ratio(file: LoanFile) -> float:
    """Annual cash accruals over annual debt service (the business segments' verified income is monthly cash
    accruals). No debt service at all is unlimited cover, so it lands in the top band."""
    o = file.obligations
    return finance.dscr(12 * file.income.verified_monthly_income_inr, 12 * (o.existing_emi_inr + o.proposed_emi_inr))


def foir_headroom_pts(file: LoanFile) -> float | None:
    """Segment FOIR limit minus FOIR, in points (None for MSME, which is judged on DSCR)."""
    if file.segment == "msme_business":
        return None
    return SEGMENT_FOIR_LIMIT_PCT[file.segment] - foir_pct(file)


def ltv_headroom_pts(file: LoanFile) -> float | None:
    """LTV limit minus LTV, in points (None if the file has no property)."""
    if file.property is None:
        return None
    return ltv_limit_pct(file) - file.property.ltv


def gst_to_bank_ratio(file: LoanFile) -> float | None:
    g = file.gst
    if g is None or g.bank_credits_12m_inr <= 0:
        return None
    return g.turnover_12m_inr / g.bank_credits_12m_inr


def ltv_limit_pct(file: LoanFile) -> float | None:
    """The home-loan LTV limit of the grid row for this loan (80, or 75 for a large ticket); None if not home."""
    if file.property is None:
        return None
    return grid_row_for(file.application.product, file.application.loan_amount_inr)["max_ltv_pct"]


# ------------------------------------------------------------------------------------------------ shared blocks
# Each returns one top-level block of the appraisal state. The segment builders pick the ones that apply.


def application_block(file: LoanFile) -> dict:
    a = file.application
    return {
        "product": a.product,
        "loan_amount_band": amount_band(a.loan_amount_inr),
        "tenure_months": a.tenure_months,
        "purpose": a.purpose,
        "employment_type": a.employment_type,
        "years_in_job_or_business_band": vintage_years_band(a.years_in_job_or_business),
        "declared_monthly_income_band": monthly_band(a.declared_monthly_income_inr),
        "applicant_age_band": age_band(file.applicant.dob_year),
        "city_tier": a.city_tier,
    }


def bureau_block(file: LoanFile) -> dict:
    b = file.bureau
    return {
        "score_band": bureau_score_band(b.score),  # the declared score; meta.true_bureau_score is never read
        "active_loans": b.active_loans,
        "max_dpd_12m_band": dpd_band(b.max_dpd_12m),
        "enquiries_6m": b.enquiries_6m,
        "writeoffs_or_settlements": b.writeoffs_or_settlements,
        "history_length_band": history_length_band(b.history_months),
    }


def income_block(file: LoanFile) -> dict:
    i = file.income
    return {
        "verified_monthly_income_band": monthly_band(i.verified_monthly_income_inr),
        "volatility": volatility_label(i.volatility_cv),
        "months_history": i.months_history,
        "documentation_type": i.documentation_type,
    }


def obligations_block(file: LoanFile) -> dict:
    o = file.obligations
    block = {
        "existing_emi_band": monthly_band(o.existing_emi_inr),
        "proposed_emi_band": monthly_band(o.proposed_emi_inr),
        "foir_pct_band": foir_band(foir_pct(file)),
        "segment_foir_limit_pct": SEGMENT_FOIR_LIMIT_PCT[file.segment],
        "credit_card_utilization_band": card_utilization_band(o.credit_card_utilization),
    }
    headroom = foir_headroom_pts(file)
    if headroom is not None:  # MSME is judged on business.dscr_band instead
        block["foir_headroom_pts_band"] = foir_headroom_band(headroom)
    return block


def bank_block(file: LoanFile, salary_narration_org_token: str | None) -> dict:
    b = file.bank
    return {
        "months_covered": b.months_covered,
        "months_required": MONTHS_REQUIRED[file.segment],
        "most_recent_month_age": b.most_recent_month_age,
        "salary_credits_months": b.salary_credits_months,
        "salary_narration_org_token": salary_narration_org_token,
        "avg_monthly_credits_band": monthly_band(b.avg_monthly_credits_inr),
        "emi_bounces_6m": b.emi_bounces_6m,
        "cash_deposit_share_band": cash_share_band(b.cash_deposit_share),
        "min_balance_breaches_6m": b.min_balance_breaches_6m,
    }


def gst_block(file: LoanFile) -> dict | None:
    g = file.gst
    if g is None:
        return None
    ratio = gst_to_bank_ratio(file)
    return {
        "filings_on_time_12m": g.filings_on_time_12m,
        "months_filed": g.months_filed,
        "gst_turnover_band_12m": amount_band(g.turnover_12m_inr),
        "bank_credits_band_12m": amount_band(g.bank_credits_12m_inr),
        "gst_to_bank_ratio_band": None if ratio is None else gst_ratio_band(ratio),
    }


def business_block(file: LoanFile) -> dict:
    return {
        "dscr_band": dscr_band(dscr_ratio(file)),
        "vintage_years_band": vintage_years_band(file.application.years_in_job_or_business),
    }


def property_block(file: LoanFile) -> dict | None:
    p = file.property
    if p is None:
        return None
    return {
        "property_type": p.property_type,
        "market_value_band": amount_band(p.market_value_inr),
        "ltv_pct_band": ltv_band(p.ltv),
        "ltv_limit_pct": ltv_limit_pct(file),
        "ltv_headroom_pts_band": ltv_headroom_band(ltv_headroom_pts(file)),
        "title_status": p.title_status,
        "legal_opinion": p.legal_opinion,
        "valuation_spread_band": valuation_spread_band(p.market_value_inr, p.valuation_2_inr),
    }


def identity_block(file: LoanFile) -> dict:
    i = file.identity
    return {
        "pan_aadhaar_linked": i.pan_aadhaar_linked,
        "phone_vintage_band": phone_vintage_band(i.phone_vintage_months),
        "email_domain_type": i.email_domain_type,
        "address_shared_with_other_apps_band": address_shared_band(i.address_shared_with_other_apps),
        "bureau_history_consistent_with_age": i.bureau_history_vs_age_consistent,
    }
