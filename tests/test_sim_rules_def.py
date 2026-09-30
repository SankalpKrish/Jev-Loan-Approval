"""Simulator rules for modules D, E and F (PLAN 3.4): registered, read the state only, point the right way on
clear-cut hand-built states (PLAN 3.7 shapes), and track the book's truth on real built states."""

import copy
import inspect
import random
import statistics
from pathlib import Path

import pytest

from jevloan.data.generator import generate_book
from jevloan.data.schema import grid_row_for, load_disclosures
from jevloan.jev import sim_rules
from jevloan.jev.sim_rules import d_triage as sim_d
from jevloan.jev.sim_rules import e_memo_kfs as sim_e
from jevloan.jev.sim_rules import f_monitoring as sim_f
from jevloan.modules import base
from jevloan.modules.base import questions_for

sim_rules.load_all()
base.load_all()

DISCLOSURES = load_disclosures()
COVENANT_IDS = ["dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current"]
DOUBT = {
    "D_doubt_income_documentation": "income_documentation",
    "D_doubt_repayment_history": "repayment_history",
    "D_doubt_debt_burden": "debt_burden",
    "D_doubt_stability": "employment_or_business_stability",
    "D_doubt_collateral": "collateral",
    "D_doubt_thin_file": "bureau_thin_file",
}
DEF_QIDS = sorted(q for q in base.REGISTRY if q[0] in "DEF")
SEEDS = range(60)


def merge(state: dict, patch: dict) -> dict:
    out = copy.deepcopy(state)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        elif value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def answer(qid: str, stage: str, segment: str, state: dict, seed: int | str = 0) -> dict:
    wire = questions_for(stage, segment, state)[qid]
    return sim_rules.lookup(qid)(state, wire, random.Random(f"{seed}:{qid}"))


def mean_p(qid: str, stage: str, segment: str, state: dict) -> float:
    return statistics.mean(answer(qid, stage, segment, state, seed)["noul"] for seed in SEEDS)


def mean_level(qid: str, segment: str, state: dict) -> float:
    return statistics.mean(answer(qid, "appraisal", segment, state, seed)["score"] for seed in SEEDS)


YES, NO = 0.7, 0.3  # a clear-cut state must average above / below these over the seeds


# --- registration and isolation -------------------------------------------------------------------------------


@pytest.mark.parametrize("qid", DEF_QIDS)
def test_every_d_e_f_question_has_a_rule(qid):
    assert sim_rules.lookup(qid) is not None


def test_disclosure_and_covenant_questions_are_served_by_globs():
    assert "E_disclosure_*" in sim_rules.REGISTRY and "F_covenant_*" in sim_rules.REGISTRY
    assert sim_rules.lookup("E_disclosures_complete") is not sim_rules.lookup("E_disclosure_apr")


@pytest.mark.parametrize("module", [sim_d, sim_e, sim_f])
def test_rules_read_the_state_and_the_question_only(module):
    source = Path(inspect.getsourcefile(module)).read_text()
    for forbidden in ("jevloan.data", "jevloan.state", "labels", "question_truth", "pii_inventory"):
        assert forbidden not in source, f"{module.__name__} mentions {forbidden}"


def test_a_label_in_the_state_is_ignored():
    state = sanction_state()
    poisoned = merge(state, {"labels": {"memo_defects": ["apr_math_wrong"]}, "meta": {"seed": 1}})
    for qid in ("E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete", "E_disclosure_apr"):
        assert answer(qid, "sanction_docs", "salaried_personal", state) == answer(qid, "sanction_docs", "salaried_personal", poisoned)


def test_answers_are_deterministic_given_the_seed():
    state = sanction_state()
    for qid in ("E_memo_matches_grid", "E_disclosure_penal_charges"):
        assert answer(qid, "sanction_docs", "salaried_personal", state, 5) == answer(qid, "sanction_docs", "salaried_personal", state, 5)


# --- E: hand-built sanction_docs states -----------------------------------------------------------------------


def sanction_state(product="personal_loan_unsecured", principal=1_500_000) -> dict:
    row = grid_row_for(product, principal)
    conditions = [c["text"] for c in row["required_conditions"]]
    sections = " ".join(f"{i}. {d['heading']}: stated." for i, d in enumerate(DISCLOSURES, start=1))
    return {
        "schema": "jevloan.state.v1",
        "stage": "sanction_docs",
        "segment": "salaried_personal",
        "sanction_memo": {
            "text": "Sanction memo (proposed). Applicant [APPLICANT]. Amount 10-25L, tenure 48 months, rate 10.50% p.a. reducing. Conditions: "
            + " ".join(f"{i}. {c}." for i, c in enumerate(conditions, start=1)),
            "product": product,
            "ticket_band": row["band"],
            "tenure_months": row["max_tenure_months"] - 12,
            "conditions": conditions,
        },
        "kfs": {
            "text": f"Key Fact Statement. Borrower [APPLICANT]. rate 10.50%. {sections}",
            "apr_stated_pct": 11.42,
            "apr_recomputed_pct": 11.44,
            "rate_pct": 10.5,
            "tenure_months": row["max_tenure_months"] - 12,
        },
        "policy_grid_row": {
            "product": row["product"],
            "band": row["band"],
            "max_tenure_months": row["max_tenure_months"],
            "required_conditions": [{"id": c["id"], "text": c["text"]} for c in row["required_conditions"]],
        },
        "required_disclosures": [{"id": d["id"], "heading": d["heading"]} for d in DISCLOSURES],
    }


def drop_heading(state: dict, disclosure_id: str) -> dict:
    heading = next(d["heading"] for d in DISCLOSURES if d["id"] == disclosure_id)
    kept = [d for d in DISCLOSURES if d["id"] != disclosure_id]
    sections = " ".join(f"{i}. {d['heading']}: stated." for i, d in enumerate(kept, start=1))
    assert heading not in sections
    return merge(state, {"kfs": {"text": f"Key Fact Statement. Borrower [APPLICANT]. rate 10.50%. {sections}"}})


E_QIDS = ["E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete", *[f"E_disclosure_{d['id']}" for d in DISCLOSURES]]


def e_means(state: dict) -> dict[str, float]:
    return {qid: mean_p(qid, "sanction_docs", "salaried_personal", state) for qid in E_QIDS}


def test_a_clean_memo_and_kfs_are_answered_yes_to_every_e_question():
    means = e_means(sanction_state())
    assert all(p > YES for p in means.values()), means


@pytest.mark.parametrize("product,principal", [("home_loan", 9_000_000), ("msme_term_loan", 30_000_000), ("business_loan_self_employed", 3_000_000)])
def test_a_clean_memo_is_matched_against_the_row_of_its_own_product(product, principal):
    assert mean_p("E_memo_matches_grid", "sanction_docs", "salaried_personal", sanction_state(product, principal)) > YES


def test_memo_tenure_over_the_grid_maximum_is_answered_no():
    state = sanction_state()
    state["sanction_memo"]["tenure_months"] = state["policy_grid_row"]["max_tenure_months"] + 12
    means = e_means(state)
    assert means["E_memo_matches_grid"] < NO
    assert means["E_rate_math_correct"] > YES and means["E_disclosures_complete"] > YES


def test_memo_tenure_equal_to_the_maximum_is_allowed():
    state = sanction_state()
    state["sanction_memo"]["tenure_months"] = state["policy_grid_row"]["max_tenure_months"]
    assert mean_p("E_memo_matches_grid", "sanction_docs", "salaried_personal", state) > YES


def test_a_missing_required_condition_is_answered_no():
    state = sanction_state("personal_loan_unsecured", 1_500_000)
    guarantor = next(c["text"] for c in state["policy_grid_row"]["required_conditions"] if c["id"] == "guarantor")
    state["sanction_memo"]["conditions"] = [c for c in state["sanction_memo"]["conditions"] if c != guarantor]
    state["sanction_memo"]["text"] = state["sanction_memo"]["text"].replace(guarantor, "")
    assert mean_p("E_memo_matches_grid", "sanction_docs", "salaried_personal", state) < NO


def test_extra_conditions_and_reworded_conditions_still_match():
    state = sanction_state()
    conditions = state["sanction_memo"]["conditions"]
    state["sanction_memo"]["conditions"] = [conditions[0].lower(), *conditions[1:], "Quarterly review of the account"]
    assert mean_p("E_memo_matches_grid", "sanction_docs", "salaried_personal", state) > YES


def test_a_plain_rate_stated_as_the_apr_is_answered_no():
    state = merge(sanction_state(), {"kfs": {"apr_stated_pct": 10.5, "apr_recomputed_pct": 11.63}})
    assert mean_p("E_rate_math_correct", "sanction_docs", "salaried_personal", state) < NO


@pytest.mark.parametrize("stated,recomputed", [(12.9, 12.1), (9.87, 10.9)])
def test_an_apr_half_a_point_or_more_away_is_answered_no(stated, recomputed):
    state = merge(sanction_state(), {"kfs": {"apr_stated_pct": stated, "apr_recomputed_pct": recomputed}})
    assert mean_p("E_rate_math_correct", "sanction_docs", "salaried_personal", state) < NO


def test_an_apr_that_is_higher_than_the_rate_because_of_fees_is_fine():
    state = merge(sanction_state(), {"kfs": {"apr_stated_pct": 12.31, "apr_recomputed_pct": 12.32, "rate_pct": 10.5}})
    assert mean_p("E_rate_math_correct", "sanction_docs", "salaried_personal", state) > YES


def test_a_memo_rate_that_differs_from_the_kfs_rate_is_answered_no():
    state = sanction_state()
    state["sanction_memo"]["text"] = state["sanction_memo"]["text"].replace("rate 10.50%", "rate 11.25%")
    assert mean_p("E_rate_math_correct", "sanction_docs", "salaried_personal", state) < NO


@pytest.mark.parametrize("disclosure_id", [d["id"] for d in DISCLOSURES if d["id"] != "apr"])
def test_a_missing_kfs_heading_is_found_by_its_own_question_and_by_the_aggregate(disclosure_id):
    means = e_means(drop_heading(sanction_state(), disclosure_id))
    assert means[f"E_disclosure_{disclosure_id}"] < NO
    assert means["E_disclosures_complete"] < NO
    others = [p for qid, p in means.items() if qid.startswith("E_disclosure_") and qid != f"E_disclosure_{disclosure_id}"]
    assert all(p > YES for p in others)
    assert means["E_memo_matches_grid"] > YES and means["E_rate_math_correct"] > YES


def test_a_disclosure_question_without_its_heading_tells_the_reader_nothing():
    state = sanction_state()
    wire = questions_for("sanction_docs", "salaried_personal", state)["E_disclosure_apr"]
    wire["instructions"].pop("data")
    assert sim_rules.lookup("E_disclosure_apr")(state, wire, random.Random(1))["noul"] == 0.5


# --- F: hand-built monitoring states --------------------------------------------------------------------------

BALANCE_STEADY = ["50-75k"] * 6
CHANGE_BANDS = ("rising", "flat", "falling_10_40", "falling_40_plus")


def dpd_band(days: int) -> str:
    return "0" if days < 1 else "1-29" if days < 30 else "30-59" if days < 60 else "60-89" if days < 90 else "90+"


def monitoring_state(*, days=None, bounced=None, partial=None, balance=None, change="flat", covenants=None) -> dict:
    """A monitoring state in the PLAN 3.7 shape: `dpd_days` next to `dpd_band`, and `loan.balance_change_band`."""
    days, bounced = days or [0] * 6, bounced or [False] * 6
    partial, balance = partial or [False] * 6, balance or BALANCE_STEADY
    return {
        "schema": "jevloan.state.v1",
        "stage": "monitoring",
        "segment": "msme_business" if covenants else "salaried_personal",
        "loan": {
            "product": "personal_loan_unsecured", "loan_amount_band": "5-10L", "tenure_months": 48, "months_since_disbursal": 6,
            "balance_change_band": change,
        },
        "repayment": [
            {
                "m": i + 1, "dpd_days": days[i], "dpd_band": dpd_band(days[i]), "emi_bounced": bounced[i],
                "partial_payment": partial[i], "avg_balance_band": balance[i],
            }
            for i in range(6)
        ],
        "covenants": covenants or [],
    }


def without_new_fields(state: dict) -> dict:
    """The same state as an older builder wrote it: no `dpd_days`, no `loan.balance_change_band`."""
    old = copy.deepcopy(state)
    old["loan"].pop("balance_change_band", None)
    for month in old["repayment"]:
        month.pop("dpd_days", None)
    return old


def flags(*months: int) -> list[bool]:
    return [i + 1 in months for i in range(6)]


def ews_means(state: dict) -> dict[str, float]:
    qids = ["F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_partial_payments", "F_ews_balance_stress"]
    return {qid: mean_p(qid, "monitoring", state["segment"], state) for qid in qids}


def test_a_calm_repayment_history_raises_no_early_warning():
    assert all(p < NO for p in ews_means(monitoring_state()).values())


DPD_YES = [
    [0, 0, 12, 35, 58, 84],  # one month at 30 or more
    [0, 0, 4, 11, 18, 25],  # higher than the month before in each of the last four, never reaching 30
    [0, 0, 3, 6, 9, 9],  # exactly three rises in the last four
    [0, 0, 0, 0, 32, 0],  # a single month at 30 or more is enough
    [30, 0, 0, 0, 0, 0],  # 30 exactly, in the first month
]
DPD_NO = [
    [0] * 6,
    [0, 5, 0, 5, 0, 5],  # up and down, below 30
    [0, 3, 6, 6, 6, 9],  # two rises in the last four
    [8] * 6,  # equal is not higher
    [29, 29, 29, 29, 29, 29],  # just under 30 and flat
    [20, 15, 10, 5, 0, 0],  # falling
    [1, 2, 3, 3, 3, 3],  # rises only in the first entries
]


@pytest.mark.parametrize("days", DPD_YES)
def test_rising_or_serious_delinquency_is_answered_yes(days):
    means = ews_means(monitoring_state(days=days))
    assert means["F_ews_dpd_rising"] > YES
    assert means["F_ews_emi_bounces"] < NO and means["F_ews_partial_payments"] < NO and means["F_ews_balance_stress"] < NO


@pytest.mark.parametrize("days", DPD_NO)
def test_mild_flat_or_falling_delinquency_is_answered_no(days):
    assert ews_means(monitoring_state(days=days))["F_ews_dpd_rising"] < NO


def test_the_dpd_signal_is_exactly_the_generators_truth_on_random_month_lists():
    from jevloan.data.risk import ews_truth
    from jevloan.data.schema import RepaymentMonth

    rng = random.Random(3)
    for _ in range(500):
        days = [0] * 6
        for i in range(1, 6):
            days[i] = max(0, days[i - 1] + rng.choice([-5, 0, 0, 2, 4, 7, 12, 20])) if rng.random() < 0.7 else rng.choice([0, 3, 9])
        months = [
            RepaymentMonth(m=i + 1, dpd=d, emi_bounced=False, partial_payment=False, avg_balance_inr=50_000)
            for i, d in enumerate(days)
        ]
        state = monitoring_state(days=days)
        assert sim_f.dpd_rising(state["repayment"]) == ews_truth(months)["F_ews_dpd_rising"], days


def test_without_dpd_days_the_signal_falls_back_to_the_bands():
    old = without_new_fields(monitoring_state(days=[0, 0, 12, 35, 58, 84]))
    assert mean_p("F_ews_dpd_rising", "monitoring", "salaried_personal", old) > YES
    old = without_new_fields(monitoring_state(days=[0, 5, 0, 5, 0, 5]))
    assert mean_p("F_ews_dpd_rising", "monitoring", "salaried_personal", old) < NO


def test_two_bounced_emis_are_a_warning_and_one_is_not():
    assert ews_means(monitoring_state(bounced=flags(2, 5)))["F_ews_emi_bounces"] > YES
    assert ews_means(monitoring_state(bounced=flags(1, 2, 6)))["F_ews_emi_bounces"] > YES
    assert ews_means(monitoring_state(bounced=flags(4)))["F_ews_emi_bounces"] < NO


def test_two_partial_payments_are_a_warning_and_one_is_not():
    assert ews_means(monitoring_state(partial=flags(3, 4)))["F_ews_partial_payments"] > YES
    assert ews_means(monitoring_state(partial=flags(3)))["F_ews_partial_payments"] < NO


def test_bounces_and_partial_payments_are_kept_apart():
    means = ews_means(monitoring_state(bounced=flags(2, 5)))
    assert means["F_ews_partial_payments"] < NO
    means = ews_means(monitoring_state(partial=flags(2, 5)))
    assert means["F_ews_emi_bounces"] < NO


FALLING = ["75k-1L", "75k-1L", "50-75k", "50-75k", "25-50k", "10-25k"]


def test_only_falling_40_plus_is_a_balance_warning():
    for change in CHANGE_BANDS:
        p = mean_p("F_ews_balance_stress", "monitoring", "salaried_personal", monitoring_state(change=change, balance=FALLING))
        assert (p > YES) if change == "falling_40_plus" else (p < NO), (change, p)


def test_the_balance_answer_follows_the_change_band_and_not_the_month_bands():
    """The truth is a function of `loan.balance_change_band`, so the month bands are only background."""
    steady_bands = monitoring_state(change="falling_40_plus", balance=BALANCE_STEADY)
    assert mean_p("F_ews_balance_stress", "monitoring", "salaried_personal", steady_bands) > YES
    falling_bands = monitoring_state(change="falling_10_40", balance=FALLING)
    assert mean_p("F_ews_balance_stress", "monitoring", "salaried_personal", falling_bands) < NO


@pytest.mark.parametrize(
    "balance,expected_yes",
    [
        (FALLING, True),
        (["1-2L", "1-2L", "75k-1L", "50-75k", "50-75k", "25-50k"], True),
        (BALANCE_STEADY, False),
        (["25-50k", "25-50k", "50-75k", "50-75k", "75k-1L", "75k-1L"], False),  # a rise
        (["50-75k", "50-75k", "10-25k", "50-75k", "50-75k", "50-75k"], False),  # a dip that recovers
    ],
)
def test_without_the_change_band_the_signal_falls_back_to_the_month_bands(balance, expected_yes):
    old = without_new_fields(monitoring_state(balance=balance))
    p = mean_p("F_ews_balance_stress", "monitoring", "salaried_personal", old)
    assert (p > YES) if expected_yes else (p < NO)


def covenant_entry(covenant_id: str, **patch) -> dict:
    entry = copy.deepcopy(next(c for c in COVENANTS_OK if c["covenant_id"] == covenant_id))
    return {**entry, **patch}


COVENANTS_OK = [
    {"covenant_id": "dscr_min_1_25", "required": 1.25, "reported_value_band": "1.25-1.5",
     "evidence_text": "[ORG_A] compliance certificate signed by [PERSON_2]: DSCR for the last audited year is 1.31 against the required minimum 1.25."},
    {"covenant_id": "stock_statement_monthly", "required": 6, "reported_value_band": 6,
     "evidence_text": "Stock statements were received on time for 6 of the last 6 months (due by the 10th of each month)."},
    {"covenant_id": "no_unapproved_borrowing", "required": 0, "reported_value_band": 0,
     "evidence_text": "Bureau and auditor check found no new borrowing beyond the sanctioned facilities."},
    {"covenant_id": "insurance_current", "required": True, "reported_value_band": True,
     "evidence_text": "Insurance on hypothecated stock is current until Feb 2027."},
]
COVENANTS_BREACHED = {
    "dscr_min_1_25": covenant_entry(
        "dscr_min_1_25", reported_value_band="1-1.25",
        evidence_text="[ORG_A] compliance certificate signed by [PERSON_2]: DSCR for the last audited year is 1.10 against the required minimum 1.25."),
    "stock_statement_monthly": covenant_entry(
        "stock_statement_monthly", reported_value_band=4,
        evidence_text="Stock statements were received on time for 4 of the last 6 months (due by the 10th of each month)."),
    "no_unapproved_borrowing": covenant_entry(
        "no_unapproved_borrowing", reported_value_band=2,
        evidence_text="Bureau and auditor check found 2 new loan facilities of Rs [10-25L] taken without the bank's consent."),
    "insurance_current": covenant_entry(
        "insurance_current", reported_value_band=False,
        evidence_text="Insurance on hypothecated stock expired on Jun 2026; renewal not evidenced."),
}


@pytest.mark.parametrize("covenant_id", COVENANT_IDS)
def test_a_complied_covenant_is_answered_yes_and_only_that_one(covenant_id):
    state = monitoring_state(covenants=copy.deepcopy(COVENANTS_OK))
    assert mean_p(f"F_covenant_{covenant_id}", "monitoring", "msme_business", state) > YES


@pytest.mark.parametrize("covenant_id", COVENANT_IDS)
def test_a_breached_covenant_is_answered_no_and_the_others_stay_yes(covenant_id):
    covenants = [COVENANTS_BREACHED[c["covenant_id"]] if c["covenant_id"] == covenant_id else c for c in COVENANTS_OK]
    state = monitoring_state(covenants=copy.deepcopy(covenants))
    assert mean_p(f"F_covenant_{covenant_id}", "monitoring", "msme_business", state) < NO
    for other in COVENANT_IDS:
        if other != covenant_id:
            assert mean_p(f"F_covenant_{other}", "monitoring", "msme_business", state) > YES


@pytest.mark.parametrize("covenant_id", COVENANT_IDS)
def test_a_covenant_is_still_read_from_its_band_when_the_evidence_text_says_nothing(covenant_id):
    entries = [{**COVENANTS_BREACHED[covenant_id], "evidence_text": ""}]
    breached = monitoring_state(covenants=entries)
    wire = base.REGISTRY[f"F_covenant_{covenant_id}"].to_wire(breached)
    p = statistics.mean(sim_rules.lookup(f"F_covenant_{covenant_id}")(breached, wire, random.Random(s))["noul"] for s in SEEDS)
    assert p < NO


def test_a_covenant_that_is_not_in_the_state_gives_no_information():
    state = monitoring_state(covenants=[COVENANTS_OK[0]])
    wire = base.REGISTRY["F_covenant_insurance_current"].to_wire(state)
    assert sim_rules.lookup("F_covenant_insurance_current")(state, wire, random.Random(1))["noul"] == 0.5


# --- D: hand-built appraisal states ---------------------------------------------------------------------------

CLEAN = {
    "schema": "jevloan.state.v1",
    "stage": "appraisal",
    "segment": "salaried_personal",
    "application": {
        "product": "personal_loan_unsecured", "employment_type": "salaried", "years_in_job_or_business_band": "5-10y",
        "declared_monthly_income_band": "50-75k",
    },
    "bureau": {
        "score_band": "750-799", "active_loans": 1, "max_dpd_12m_band": "0", "enquiries_6m": 1,
        "writeoffs_or_settlements": 0, "history_length_band": "3-7y",
    },
    "income": {"verified_monthly_income_band": "50-75k", "volatility": "low", "months_history": 24, "documentation_type": "salary_slip"},
    "obligations": {
        "existing_emi_band": "<10k", "proposed_emi_band": "10-25k", "foir_pct_band": "30-40", "segment_foir_limit_pct": 55,
        "credit_card_utilization_band": "<10%",
    },
    "bank": {"emi_bounces_6m": 0},
}

HOME = {
    "segment": "secured_home",
    "application": {"product": "home_loan"},
    "obligations": {"segment_foir_limit_pct": 60},
    "property": {"ltv_pct_band": "<60", "ltv_limit_pct": 80, "title_status": "clear", "legal_opinion": "positive", "valuation_spread_band": "<5%"},
}
MSME = {
    "segment": "msme_business",
    "application": {"product": "msme_term_loan", "employment_type": "business_owner", "years_in_job_or_business_band": ">10y"},
    "obligations": {"segment_foir_limit_pct": 80, "foir_pct_band": "40-50"},
    "income": {"volatility": "low"},
    "business": {"dscr_band": "1.5-2", "vintage_years_band": ">10y"},
    "gst": {"gst_to_bank_ratio_band": "0.8-1.2"},
}

# a ladder of files from clearly sanctionable down to clearly not, each a step worse than the one before
LADDER = [
    ("clearly_sanctionable", {}),
    ("probably_sanctionable", {"bureau": {"score_band": "700-749"}, "obligations": {"foir_pct_band": "40-50"}, "income": {"volatility": "moderate"}}),
    ("on_the_boundary", {"bureau": {"score_band": "650-699"}, "obligations": {"foir_pct_band": "50-55"}, "income": {"volatility": "moderate"}}),
    ("probably_not", {"bureau": {"score_band": "600-649"}, "obligations": {"foir_pct_band": "55-60"}}),
    ("clearly_not", {"bureau": {"score_band": "<600", "max_dpd_12m_band": "90+", "writeoffs_or_settlements": 1}, "obligations": {"foir_pct_band": ">70"}}),
]


def test_closeness_falls_step_by_step_from_clearly_sanctionable_to_clearly_not():
    levels = [mean_level("D_closeness", "salaried_personal", merge(CLEAN, patch)) for _, patch in LADDER]
    assert levels == sorted(levels, reverse=True) and len(set(levels)) == len(levels), levels
    assert levels[0] > 3.3 and levels[-1] < 0.7


@pytest.mark.parametrize(
    "name,segment,patch",
    [
        ("home, clean", "secured_home", HOME),
        ("msme, clean", "msme_business", MSME),
    ],
)
def test_closeness_is_high_for_a_clean_home_or_msme_file(name, segment, patch):
    assert mean_level("D_closeness", segment, merge(CLEAN, patch)) > 3.0, name


@pytest.mark.parametrize(
    "name,segment,patch",
    [
        ("home, disputed title", "secured_home", merge(HOME, {"property": {"title_status": "disputed"}})),
        ("home, ltv far over", "secured_home", merge(HOME, {"property": {"ltv_pct_band": ">85"}})),
        ("msme, dscr under 1", "msme_business", merge(MSME, {"business": {"dscr_band": "<1"}})),
        ("msme, business under a year", "msme_business", merge(MSME, {"business": {"vintage_years_band": "<1y"}})),
    ],
)
def test_closeness_is_low_when_one_segment_specific_factor_fails_clearly(name, segment, patch):
    assert mean_level("D_closeness", segment, merge(CLEAN, patch)) < 1.3, name


def test_a_new_to_credit_applicant_is_close_only_for_a_salaried_loan_with_good_income():
    ntc = {"bureau": {"score_band": "NTC", "history_length_band": "<6m", "active_loans": 0}, "income": {"verified_monthly_income_band": "75k-1L"}}
    salaried = merge(CLEAN, ntc)
    not_salaried = merge(merge(CLEAN, {"segment": "self_employed"}), ntc)
    assert mean_level("D_closeness", "salaried_personal", salaried) > mean_level("D_closeness", "self_employed", not_salaried) + 1.5


def test_closeness_answers_are_valid_five_level_scores():
    a = answer("D_closeness", "appraisal", "salaried_personal", CLEAN)
    assert a["type"] == "score" and len(a["probabilities"]) == 5 and abs(sum(a["probabilities"].values()) - 1) < 1e-3


# one factor is clearly the weakest and the rest are comfortable
DOUBT_CASES = {
    "D_doubt_repayment_history": ("salaried_personal", {"bureau": {"score_band": "600-649", "max_dpd_12m_band": "60-89"}, "bank": {"emi_bounces_6m": 3}}),
    "D_doubt_debt_burden": ("salaried_personal", {"obligations": {"foir_pct_band": ">70", "credit_card_utilization_band": ">75%"}}),
    "D_doubt_stability": (
        "salaried_personal",
        {"income": {"volatility": "high", "months_history": 4}, "application": {"years_in_job_or_business_band": "<1y"}},
    ),
    "D_doubt_income_documentation": ("salaried_personal", {"application": {"declared_monthly_income_band": "1-2L"}}),
    "D_doubt_collateral": ("secured_home", merge(HOME, {"property": {"ltv_pct_band": "80-85", "title_status": "disputed", "legal_opinion": "adverse"}})),
    "D_doubt_thin_file": ("salaried_personal", {"bureau": {"score_band": "NTC", "history_length_band": "<6m", "active_loans": 0}}),
}


@pytest.mark.parametrize("target", sorted(DOUBT_CASES))
def test_the_weakest_factor_is_the_main_doubt_and_the_others_are_not(target):
    segment, patch = DOUBT_CASES[target]
    state = merge(CLEAN, patch)
    for qid in DOUBT:
        if qid == "D_doubt_collateral" and segment != "secured_home":
            continue
        p = mean_p(qid, "appraisal", segment, state)
        if qid == target:
            assert p > YES, (qid, p)
        else:
            assert p < NO, (target, qid, p)


def test_a_score_of_thin_file_is_not_read_as_bad_conduct():
    segment, patch = DOUBT_CASES["D_doubt_thin_file"]
    state = merge(CLEAN, patch)
    assert mean_p("D_doubt_repayment_history", "appraisal", segment, state) < NO


def test_documentation_type_alone_is_not_income_doubt():
    state = merge(CLEAN, {"income": {"documentation_type": "informal_declared"}})
    assert mean_p("D_doubt_income_documentation", "appraisal", "salaried_personal", state) < NO


def test_a_gst_mismatch_is_income_documentation_doubt_on_an_msme_file():
    state = merge(CLEAN, merge(MSME, {"gst": {"gst_to_bank_ratio_band": ">2"}}))
    assert mean_p("D_doubt_income_documentation", "appraisal", "msme_business", state) > YES


# --- truth signals on real built states -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    """300 book files and their three built states (real W2-state output)."""
    build_state = pytest.importorskip("jevloan.state").build_state
    book = generate_book(300, 7)
    return [(f, {stage: build_state(f, stage) for stage in ("appraisal", "sanction_docs", "monitoring")}) for f in book]


def _agreement(pairs) -> float:
    pairs = list(pairs)
    return sum(t == s for t, s in pairs) / len(pairs)


def test_e_signals_match_the_book_truth_before_any_noise_is_added(real):
    for qid, fn in (("E_memo_matches_grid", sim_e.memo_matches_grid), ("E_rate_math_correct", sim_e.rate_math_correct), ("E_disclosures_complete", sim_e.disclosures_complete)):
        assert _agreement((f.labels.question_truth[qid], fn(states["sanction_docs"])) for f, states in real) >= 0.99, qid
    pairs = [
        (f.labels.question_truth[f"E_disclosure_{d['id']}"], sim_e.heading_present(states["sanction_docs"], d["heading"]))
        for f, states in real
        for d in DISCLOSURES
    ]
    assert _agreement(pairs) >= 0.995


def test_f_signals_match_the_book_truth_before_any_noise_is_added(real):
    def months(states):
        return sim_f._months(states["monitoring"])

    assert _agreement((f.labels.ews_truth["F_ews_emi_bounces"], sum(m["emi_bounced"] for m in months(s)) >= 2) for f, s in real) == 1.0
    assert _agreement((f.labels.ews_truth["F_ews_partial_payments"], sum(m["partial_payment"] for m in months(s)) >= 2) for f, s in real) == 1.0
    # with `dpd_days` and `loan.balance_change_band` in the state the two signals are exact; an older state is banded
    has_days = all("dpd_days" in m for _, s in real for m in months(s))
    has_change = all("balance_change_band" in s["monitoring"]["loan"] for _, s in real)
    dpd = _agreement((f.labels.ews_truth["F_ews_dpd_rising"], sim_f.dpd_rising(months(s))) for f, s in real)
    balance = _agreement((f.labels.ews_truth["F_ews_balance_stress"], sim_f.balance_stress(s["monitoring"])) for f, s in real)
    assert dpd >= (1.0 if has_days else 0.93)
    assert balance >= (1.0 if has_change else 0.92)
    covenant_pairs = [
        (c["covenant_id"] not in f.labels.covenant_breaches, sim_f.covenant_complied(c["covenant_id"], c))
        for f, s in real
        for c in s["monitoring"]["covenants"]
    ]
    assert covenant_pairs and _agreement(covenant_pairs) >= 0.99


def test_d_signals_track_the_book_truth_before_any_noise_is_added(real):
    weakness = _agreement((f.labels.primary_weakness, sim_d.primary_weakness(s["appraisal"])[0]) for f, s in real)
    levels = [(f.labels.closeness_level, sim_d.closeness_level(s["appraisal"])) for f, s in real]
    assert weakness >= 0.68
    assert sum(abs(round(level) - truth) <= 1 for truth, level in levels) / len(levels) >= 0.9
    assert sum(round(level) == truth for truth, level in levels) / len(levels) >= 0.6
    # a higher answer goes with a higher truth: the mean signal rises with the true level
    by_truth = [statistics.mean(level for truth, level in levels if truth == t) for t in range(5)]
    assert by_truth == sorted(by_truth) and by_truth[4] - by_truth[0] > 2.5


def test_the_noisy_answers_agree_with_the_truth_at_roughly_the_target_rate(real):
    hits: dict[str, list[bool]] = {}
    within = []
    top = []
    for f, states in real:
        truth = f.labels.question_truth
        given: dict[str, float] = {}
        for stage, state in states.items():
            for qid, wire in questions_for(stage, f.segment, state).items():
                if qid[0] not in "DEF":
                    continue
                a = sim_rules.lookup(qid)(state, wire, random.Random(f"{f.file_id}:{qid}"))
                if qid == "D_closeness":
                    level = max(range(5), key=lambda k: a["probabilities"][str(k)])
                    within.append(abs(level - truth[qid]) <= 1)
                    continue
                if qid.startswith("F_ews_"):
                    t = f.labels.ews_truth[qid]
                elif qid.startswith("F_covenant_"):
                    t = qid.removeprefix("F_covenant_") not in f.labels.covenant_breaches
                else:
                    t = truth[qid]
                key = "E_disclosure_*" if qid.startswith("E_disclosure_") else qid
                hits.setdefault(key, []).append((a["noul"] >= 0.5) == bool(t))
                if qid in DOUBT:
                    given[qid] = a["noul"]
        top.append(bool(truth[max(given, key=given.get)]))
    for qid, results in hits.items():
        floor = 0.80 if qid.startswith("D_doubt") else 0.85
        assert sum(results) / len(results) >= floor, (qid, sum(results) / len(results))
    assert sum(within) / len(within) >= 0.88
    assert sum(top) / len(top) >= 0.55  # picking the top doubt from six noisy Nouls is harder than any one of them
