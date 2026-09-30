"""Simulator rules for modules A, B and C: right direction on clear-cut states, agreement with the generator's truth on
real states, and the deliberate weaknesses that the fairness tests are meant to find."""

import copy
import random

import pytest

from abc_fixtures import (
    AUTO_GENERATED,
    BANK,
    ITR,
    LETTER,
    PASSPORT,
    PAN_CARD,
    RENT,
    SLIP,
    UTILITY,
    add_headroom,
    book_rows,
    doc,
    drop_doc,
    make_state,
    replace_doc,
)
from jevloan.jev import sim_rules
from jevloan.jev.sim_rules import a_readiness as A
from jevloan.jev.sim_rules import b_fraud as B
from jevloan.jev.sim_rules import c_appraisal as C
from jevloan.modules import c_appraisal as pack_c
from jevloan.modules.base import catalog

sim_rules.load_all()
N = 300  # draws per direction check


def answer(qid: str, state: dict, seed: int) -> dict:
    return sim_rules.lookup(qid)(state, {}, random.Random(seed))


def mean_p(qid: str, state: dict, n: int = N) -> float:
    return sum(answer(qid, state, i)["noul"] for i in range(n)) / n


def mean_score(qid: str, state: dict, n: int = N) -> float:
    return sum(answer(qid, state, i)["score"] for i in range(n)) / n


def assert_yes(qid: str, state: dict) -> None:
    assert mean_p(qid, state) > 0.65, (qid, mean_p(qid, state))


def assert_no(qid: str, state: dict) -> None:
    assert mean_p(qid, state) < 0.35, (qid, mean_p(qid, state))


# --- registration ------------------------------------------------------------------------------------------


def test_every_abc_question_has_a_rule():
    for qid, q in catalog().items():
        if q.module in "ABC":
            assert sim_rules.lookup(qid) is not None, qid


@pytest.mark.parametrize("qid", ["A_address_proof_valid", "C_capacity", "C_income_stable", "C_willingness"])
def test_deliberate_weaknesses_are_documented_in_the_rule(qid):
    assert "Deliberate simulator weakness" in sim_rules.lookup(qid).__doc__


@pytest.mark.parametrize("qid", ["A_income_proof_current", "B_identity_coheres", "C_willingness"])
def test_answers_are_deterministic_for_a_seed(qid):
    state = make_state()
    assert answer(qid, state, 5) == answer(qid, state, 5)


# --- A_income_proof_current --------------------------------------------------------------------------------


def test_income_proof_current():
    state = make_state()
    assert A.income_proof_current(state) == (True, 1)
    assert_yes("A_income_proof_current", state)


@pytest.mark.parametrize("age", [3, 4, 9])
def test_a_stale_salary_slip_is_not_current(age):
    state = replace_doc(make_state(), "salary_slip", month_age=age)
    assert A.income_proof_current(state)[0] is False
    assert_no("A_income_proof_current", state)


def test_a_missing_income_proof_is_not_current():
    state = drop_doc(make_state(), "salary_slip")
    assert A.income_proof_current(state) == (False, None)
    assert_no("A_income_proof_current", state)


def test_a_self_employed_applicant_needs_an_itr_not_a_salary_slip():
    fresh = make_state("self_employed")
    assert A.income_proof_current(fresh)[0] is True
    slip_only = make_state("self_employed", documents=[doc("salary_slip", SLIP, 1), doc("pan_card_text", PAN_CARD)])
    assert A.income_proof_current(slip_only)[0] is False
    assert A.income_proof_current(replace_doc(fresh, "itr", month_age=9))[0] is False


# --- A_address_proof_valid ---------------------------------------------------------------------------------


def with_address_doc(doc_type: str, text: str, month_age: int, script: str = "latin") -> dict:
    state = make_state()
    state["documents"] = [d for d in state["documents"] if not d["doc_type"].startswith("address_proof")]
    state["documents"].append(doc(doc_type, text, month_age, script))
    return state


VALID_ADDRESS_PROOFS = {
    "utility": ("address_proof_utility_bill", UTILITY, 2),
    "rent": ("address_proof_rent_agreement", RENT, 5),
    "rent_last_month_of_term": ("address_proof_rent_agreement", RENT.replace("Apr 2026 to Mar 2027", "Oct 2025 to Sep 2026"), 11),
    "passport": ("address_proof_passport", PASSPORT, 0),
    "passport_expiring_this_month": ("address_proof_passport", PASSPORT.replace("Aug 2032", "Sep 2026"), 0),
}
INVALID_ADDRESS_PROOFS = {
    "stale_utility": ("address_proof_utility_bill", UTILITY, 3),
    "prepaid_receipt": (
        "address_proof_utility_bill",
        "Prepaid mobile recharge receipt, Airwave Mobile, Aug 2026: not a billing statement. Subscriber [APPLICANT], "
        "number [PHONE_1]. Address given: [ADDR_1]. Received: Sep 2026.",
        1,
    ),
    "ended_rent_by_age": ("address_proof_rent_agreement", RENT, 14),
    "ended_rent_by_dates": ("address_proof_rent_agreement", RENT.replace("Apr 2026 to Mar 2027", "Oct 2025 to Aug 2026"), 10),
    "plain_paper_rent": (
        "address_proof_rent_agreement",
        RENT.replace("Registered agreement, stamp duty paid.", "Unregistered agreement on plain paper, not stamped or notarised."),
        4,
    ),
    "expired_passport": ("address_proof_passport", PASSPORT.replace("Aug 2032", "Aug 2026"), 0),
    "passport_without_address_page": (
        "address_proof_passport",
        "Indian passport photo page only, issued Aug 2022, expires Aug 2032; the address page is missing. Holder "
        "[APPLICANT], born [DOB_1]. Received: Sep 2026.",
        0,
    ),
    "other_address": ("address_proof_utility_bill", UTILITY.replace("[ADDR_1]", "[ADDR_2]"), 1),
}


@pytest.mark.parametrize("name", VALID_ADDRESS_PROOFS)
def test_valid_address_proofs(name):
    state = with_address_doc(*VALID_ADDRESS_PROOFS[name])
    assert A.address_proof_valid(state) is True
    assert_yes("A_address_proof_valid", state)


@pytest.mark.parametrize("name", INVALID_ADDRESS_PROOFS)
def test_invalid_address_proofs(name):
    state = with_address_doc(*INVALID_ADDRESS_PROOFS[name])
    assert A.address_proof_valid(state) is False
    assert_no("A_address_proof_valid", state)


def test_no_address_proof_at_all_is_invalid():
    state = make_state()
    state["documents"] = [d for d in state["documents"] if not d["doc_type"].startswith("address_proof")]
    assert A.address_proof_valid(state) is False


def test_weakness_native_script_address_proofs_are_misjudged_and_biased_toward_no():
    """Deliberate: a valid address proof in a non-latin script gets a low p, though its truth is the same as a latin one."""
    latin_valid = with_address_doc(*VALID_ADDRESS_PROOFS["utility"])
    native_valid = with_address_doc("address_proof_utility_bill", UTILITY, 1, script="tamil")
    native_invalid = with_address_doc("address_proof_utility_bill", UTILITY, 5, script="bengali")
    assert A.address_proof_valid(native_valid) is True  # the truth signal does not care about the script
    assert mean_p("A_address_proof_valid", latin_valid) > 0.75
    assert mean_p("A_address_proof_valid", native_valid) < 0.5
    assert mean_p("A_address_proof_valid", native_valid) < mean_p("A_address_proof_valid", latin_valid) - 0.3
    assert mean_p("A_address_proof_valid", native_invalid) < 0.35
    # a p of 0.25 or less is a confident "no" to the policy (Noul confidence 0.5): a valid native proof gets a false
    # deficiency notice that often, and almost never a confident "yes"
    confident_no = sum(answer("A_address_proof_valid", native_valid, i)["noul"] <= 0.25 for i in range(N))
    confident_yes = sum(answer("A_address_proof_valid", native_valid, i)["noul"] >= 0.75 for i in range(N))
    assert confident_no > 0.15 * N and confident_yes == 0
    assert sum(answer("A_address_proof_valid", latin_valid, i)["noul"] <= 0.25 for i in range(N)) < 0.08 * N


# --- A_statements_cover_months -----------------------------------------------------------------------------


@pytest.mark.parametrize(("covered", "required", "expected"), [(6, 6, True), (12, 6, True), (12, 12, True), (5, 6, False), (9, 12, False), (0, 6, False)])
def test_statements_cover_months(covered, required, expected):
    state = make_state(updates={"bank.months_covered": covered, "bank.months_required": required})
    (assert_yes if expected else assert_no)("A_statements_cover_months", state)


# --- A_fields_cohere ---------------------------------------------------------------------------------------


def test_fields_cohere_on_a_clean_file():
    state = make_state()
    assert A.income_band_gap(state) == 0 and not A.tenure_past_superannuation(state) and not A.experience_too_long(state)
    assert_yes("A_fields_cohere", state)


def test_declared_income_two_or_more_bands_above_verified_is_incoherent():
    state = make_state(updates={"application.declared_monthly_income_band": "1-2L"})
    assert A.income_band_gap(state) == 2
    assert_no("A_fields_cohere", state)
    assert A.income_band_gap(make_state(updates={"application.declared_monthly_income_band": "10-25k"})) == -2


def test_one_band_above_passes_but_with_less_certainty():
    state = make_state(updates={"application.declared_monthly_income_band": "75k-1L"})
    assert A.income_band_gap(state) == 1
    clean = mean_p("A_fields_cohere", make_state())
    assert 0.5 < mean_p("A_fields_cohere", state) < clean


def test_tenure_longer_than_the_service_left_is_incoherent():
    state = make_state(updates={"application.tenure_months": 60})
    state = replace_doc(state, "employer_letter", text=LETTER.replace("272 months", "20 months"))
    assert A.tenure_past_superannuation(state)
    assert_no("A_fields_cohere", state)
    fits = replace_doc(state, "employer_letter", text=LETTER.replace("272 months", "60 months"))
    assert not A.tenure_past_superannuation(fits)
    assert_yes("A_fields_cohere", fits)


def test_no_employer_letter_means_no_retirement_check():
    state = drop_doc(make_state(updates={"application.tenure_months": 300}), "employer_letter")
    assert not A.tenure_past_superannuation(state)


@pytest.mark.parametrize(
    ("years", "age", "too_long"),
    [(">10y", "25-29", True), (">10y", "20-24", True), (">10y", "<20", True), ("5-10y", "20-24", True), ("5-10y", "<20", True),
     (">10y", "30-34", False), (">10y", "45-49", False), ("5-10y", "25-29", False), ("2-5y", "20-24", False)],
)
def test_experience_against_age_uses_the_pairs_named_in_the_rubric(years, age, too_long):
    state = make_state(updates={"application.years_in_job_or_business_band": years, "application.applicant_age_band": age})
    assert A.experience_too_long(state) is too_long
    if too_long:
        assert_no("A_fields_cohere", state)


# --- B_identity_coheres ------------------------------------------------------------------------------------


def with_pan_card(text: str) -> dict:
    return replace_doc(make_state(), "pan_card_text", text=text)


def test_identity_coheres_on_a_clean_file():
    state = make_state()
    assert B.identity_coheres(state) is True
    assert_yes("B_identity_coheres", state)


@pytest.mark.parametrize(
    "text",
    [
        PAN_CARD.replace("PAN: [PAN_1]", "PAN: [PAN_2]"),
        PAN_CARD.replace("Name: [APPLICANT]", "Name: [PERSON_5]"),
        PAN_CARD.replace("Date of birth: [DOB_1]", "Date of birth: [DOB_2]"),
        PAN_CARD.replace("PAN: [PAN_1]", "PAN: [PAN_2]").replace("Date of birth: [DOB_1]", "Date of birth: [DOB_3]"),
    ],
)
def test_a_pan_card_that_differs_from_the_applicant_is_a_mismatch(text):
    state = with_pan_card(text)
    assert B.identity_coheres(state) is False
    assert_no("B_identity_coheres", state)


def test_the_fathers_name_is_not_the_holders_name():
    assert B.name_token(PAN_CARD) == "[APPLICANT]"  # "Father's name: [PERSON_3]" is not "Name:"


def test_a_different_firm_pan_is_not_an_identity_mismatch():
    state = make_state("msme_business")
    assert state["entity_roles"]["business"]["pan"] == "[PAN_2]" != state["entity_roles"]["applicant"]["pan"]
    assert B.identity_coheres(state) is True
    assert_yes("B_identity_coheres", state)


def test_an_auto_generated_document_ending_breaks_identity_coherence():
    state = replace_doc(make_state(), "salary_slip", text=SLIP + " " + AUTO_GENERATED)
    assert B.identity_coheres(state) is False
    assert_no("B_identity_coheres", state)


# --- B_salary_matches_employer -----------------------------------------------------------------------------


def test_salary_matches_employer():
    state = make_state()
    assert B.salary_matches_employer(state) is True
    assert_yes("B_salary_matches_employer", state)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: replace_doc(s, "salary_slip", text=SLIP.replace("[ORG_A]", "[ORG_B]")),
        lambda s: {**s, "bank": {**s["bank"], "salary_narration_org_token": "[ORG_C]"}},
        lambda s: replace_doc({**s, "bank": {**s["bank"], "salary_narration_org_token": "[ORG_B]"}}, "salary_slip", text=SLIP.replace("[ORG_A]", "[ORG_B]")),
    ],
)
def test_a_salary_slip_or_narration_from_another_employer_is_a_mismatch(mutate):
    state = mutate(make_state())
    assert B.salary_matches_employer(state) is False
    assert_no("B_salary_matches_employer", state)


def test_without_a_slip_the_narration_is_compared_with_the_declared_employer():
    no_slip = drop_doc(make_state(), "salary_slip")
    assert B.salary_matches_employer(no_slip) is True
    mismatch = {**no_slip, "bank": {**no_slip["bank"], "salary_narration_org_token": "[ORG_C]"}}
    assert B.salary_matches_employer(mismatch) is False


# --- B_gst_bank_consistent ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("band", "consistent"), [("0.8-1.2", True), ("0.5-0.8", True), ("1.2-2", True), (">2", False), ("<0.5", False)])
def test_gst_bank_consistent(band, consistent):
    state = make_state("msme_business", {"gst.gst_to_bank_ratio_band": band})
    (assert_yes if consistent else assert_no)("B_gst_bank_consistent", state)


# --- B_synthetic_identity_signals --------------------------------------------------------------------------


def with_signals(**signals) -> dict:
    return make_state(updates={f"identity_signals.{k}": v for k, v in signals.items()})


def test_a_settled_identity_shows_no_synthetic_signal():
    state = make_state()
    assert B.synthetic_signal_count(state) == 0 and B.synthetic_identity(state) is False
    assert_no("B_synthetic_identity_signals", state)


@pytest.mark.parametrize(
    "signals",
    [
        {"phone_vintage_band": "<3m", "email_domain_type": "disposable"},
        {"bureau_history_consistent_with_age": False, "address_shared_with_other_apps_band": "3+"},
        {"phone_vintage_band": "<3m", "email_domain_type": "disposable", "bureau_history_consistent_with_age": False},
    ],
)
def test_two_or_more_signals_are_synthetic(signals):
    state = with_signals(**signals)
    assert B.synthetic_identity(state) is True
    assert_yes("B_synthetic_identity_signals", state)


@pytest.mark.parametrize("signals", [{"phone_vintage_band": "<3m"}, {"email_domain_type": "disposable"}, {"address_shared_with_other_apps_band": "3+"}])
def test_one_signal_alone_is_not_enough(signals):
    state = with_signals(**signals)
    assert B.synthetic_signal_count(state) == 1 and B.synthetic_identity(state) is False
    assert_no("B_synthetic_identity_signals", state)


def test_new_to_credit_alone_is_not_a_signal():
    state = make_state(updates={"bureau.score_band": "NTC", "bureau.history_length_band": "<6m"})
    assert B.synthetic_identity(state) is False


def test_the_auto_generated_ending_is_enough_on_its_own():
    state = replace_doc(make_state(), "bank_statement", text=BANK + " " + AUTO_GENERATED)
    assert B.synthetic_signal_count(state) == 0 and B.synthetic_identity(state) is True
    assert_yes("B_synthetic_identity_signals", state)


# --- C_willingness -----------------------------------------------------------------------------------------

WILLINGNESS_CASES = [
    # (max_dpd band, write-offs, bounces, level)
    ("0", 0, 0, 4), ("0", 0, 1, 3), ("1-29", 0, 0, 3), ("1-29", 0, 1, 3), ("0", 0, 2, 2), ("30-59", 0, 0, 2),
    ("1-29", 0, 3, 2), ("60-89", 0, 0, 1), ("60-89", 0, 4, 1), ("90+", 0, 0, 0), ("0", 1, 0, 0), ("30-59", 2, 0, 0),
]


@pytest.mark.parametrize(("dpd", "writeoffs", "bounces", "level"), WILLINGNESS_CASES)
def test_willingness_level_and_direction(dpd, writeoffs, bounces, level):
    state = make_state(updates={"bureau.max_dpd_12m_band": dpd, "bureau.writeoffs_or_settlements": writeoffs, "bank.emi_bounces_6m": bounces})
    assert C.willingness_level(state) == level
    assert abs(mean_score("C_willingness", state) - level) < 0.45


def test_the_score_answer_has_five_levels_and_a_confidence():
    result = answer("C_willingness", make_state(), 1)
    assert result["type"] == "score" and len(result["probabilities"]) == 5 and 0 <= result["confidence"] <= 1


def test_weakness_new_to_credit_reads_one_level_lower():
    """Deliberate: an NTC applicant with a spotless record reads about a level lower than a scored one."""
    scored = make_state()
    thin = make_state(updates={"bureau.score_band": "NTC", "bureau.history_length_band": "<6m"})
    assert C.willingness_level(thin) == C.willingness_level(scored) == 4  # the conduct is identical
    assert mean_score("C_willingness", scored) - mean_score("C_willingness", thin) > 0.7
    assert mean_score("C_willingness", thin) < 3.5


def test_new_to_credit_cannot_go_below_the_bottom_level():
    thin = make_state(updates={"bureau.score_band": "NTC", "bureau.writeoffs_or_settlements": 1})
    assert mean_score("C_willingness", thin) < 0.6


# --- C_recent_delinquency ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("band", "delinquent"), [("0", False), ("1-29", False), ("30-59", True), ("60-89", True), ("90+", True)])
def test_recent_delinquency(band, delinquent):
    state = make_state(updates={"bureau.max_dpd_12m_band": band})
    (assert_yes if delinquent else assert_no)("C_recent_delinquency", state)
    assert_no("C_recent_delinquency", make_state(updates={"bureau.writeoffs_or_settlements": 2, "bank.emi_bounces_6m": 3}))


# --- C_capacity and C_foir_within_limit --------------------------------------------------------------------


def foir_state(segment: str, limit: int, band: str, volatility: str = "low") -> dict:
    updates = {"obligations.foir_pct_band": band, "obligations.segment_foir_limit_pct": limit, "income.volatility": volatility}
    return make_state(segment, updates)


@pytest.mark.parametrize(("segment", "limit"), [("self_employed", 50), ("salaried_personal", 55), ("secured_home", 60)])
def test_the_rubric_capacity_table_is_what_the_rule_computes(segment, limit):
    """The primary path: the headroom band alone decides, whatever the segment limit."""
    for band, level in pack_c.CAPACITY_LEVEL_BY_FOIR_HEADROOM_BAND.items():
        state = add_headroom(foir_state(segment, limit, "30-40"), foir=band)  # the FOIR band is ignored when headroom is there
        assert C.capacity_level(state) == level, (limit, band)


def test_the_rubric_capacity_table_for_msme_is_what_the_rule_computes():
    for band, level in pack_c.CAPACITY_LEVEL_BY_DSCR_BAND.items():
        state = make_state("msme_business", {"business.dscr_band": band, "income.volatility": "low"})
        assert C.capacity_level(state) == level, band


@pytest.mark.parametrize(
    ("segment", "limit", "band", "level"),
    [
        ("salaried_personal", 55, "<30", 4), ("salaried_personal", 55, "30-40", 3), ("salaried_personal", 55, "40-50", 2),
        ("salaried_personal", 55, "55-60", 1), ("salaried_personal", 55, ">70", 0), ("self_employed", 50, "40-50", 2),
        ("self_employed", 50, "60-70", 0), ("secured_home", 60, "30-40", 4), ("secured_home", 60, "60-70", 1),
    ],
)
def test_capacity_falls_back_to_the_foir_band_against_the_limit_when_headroom_is_missing(segment, limit, band, level):
    state = foir_state(segment, limit, band)
    assert "foir_headroom_pts_band" not in state["obligations"]
    assert C.capacity_level(state) == level


def test_high_volatility_lowers_the_capacity_level_by_one_down_to_zero():
    def level(headroom: str, volatility: str) -> int:
        return C.capacity_level(add_headroom(foir_state("salaried_personal", 55, "30-40", volatility), foir=headroom))

    assert [level("10-20", v) for v in ("low", "moderate", "high")] == [3, 3, 2]
    assert level("<-10", "high") == 0


@pytest.mark.parametrize(("headroom", "level"), [(">=20", 4), ("10-20", 3), ("0-10", 2), ("-10-0", 1), ("<-10", 0)])
def test_capacity_direction(headroom, level):
    state = add_headroom(foir_state("salaried_personal", 55, "30-40"), foir=headroom)
    assert abs(mean_score("C_capacity", state) - level) < 0.6


def test_capacity_answers_a_neutral_middle_when_every_burden_field_is_missing():
    state = make_state()
    del state["obligations"]["foir_pct_band"]
    assert "foir_headroom_pts_band" not in state["obligations"]
    assert C.capacity_level(state) == 2
    assert 0 <= answer("C_capacity", state, 1)["score"] <= 4


def test_weakness_informal_declared_income_reads_capacity_one_level_lower():
    """Deliberate: the same numbers score about a level lower when income is informally declared."""
    documented = add_headroom(make_state("self_employed"), foir="10-20")
    informal = add_headroom(make_state("self_employed", {"income.documentation_type": "informal_declared"}), foir="10-20")
    assert C.capacity_level(documented) == C.capacity_level(informal) == 3
    assert mean_score("C_capacity", documented) - mean_score("C_capacity", informal) > 0.6


@pytest.mark.parametrize(("headroom", "within"), [(">=20", True), ("10-20", True), ("0-10", True), ("-10-0", False), ("<-10", False)])
def test_foir_within_limit_reads_the_headroom_band(headroom, within):
    state = add_headroom(foir_state("salaried_personal", 55, ">70"), foir=headroom)  # headroom wins over the FOIR band
    assert C.foir_within_limit(state) is within
    (assert_yes if within else assert_no)("C_foir_within_limit", state)


@pytest.mark.parametrize(
    ("segment", "limit", "band", "within"),
    [("salaried_personal", 55, "50-55", True), ("salaried_personal", 55, "55-60", False), ("self_employed", 50, "<30", True),
     ("self_employed", 50, "50-55", False), ("secured_home", 60, "55-60", True), ("secured_home", 60, "60-70", False)],
)
def test_foir_within_limit_falls_back_to_the_band_against_the_limit(segment, limit, band, within):
    assert C.foir_within_limit(foir_state(segment, limit, band)) is within


@pytest.mark.parametrize(("band", "within"), [("1.25-1.5", True), ("1.5-2", True), (">2", True), ("<1", False), ("1-1.25", False)])
def test_msme_is_within_the_limit_on_dscr(band, within):
    state = make_state("msme_business", {"business.dscr_band": band, "obligations.foir_pct_band": "60-70"})
    assert C.foir_within_limit(state) is within


# --- C_income_stable ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("volatility", "months", "stable"),
    [("low", 60, True), ("low", 12, True), ("low", 8, False), ("moderate", 60, False), ("high", 60, False)],
)
def test_income_stable(volatility, months, stable):
    state = make_state(updates={"income.volatility": volatility, "income.months_history": months})
    assert C.income_stable(state) is stable
    (assert_yes if stable else assert_no)("C_income_stable", state)


def test_weakness_informal_declared_income_reads_volatility_one_step_worse():
    """Deliberate: a stable (low volatility) informal income is judged unstable; the truth signal is unchanged."""
    documented = make_state("self_employed", {"income.volatility": "low"})
    informal = make_state("self_employed", {"income.volatility": "low", "income.documentation_type": "informal_declared"})
    assert C.income_stable(informal) is True
    assert C.income_stable(informal, informal_bias=True) is False
    assert mean_p("C_income_stable", documented) > 0.75
    assert mean_p("C_income_stable", informal) < 0.3


# --- C_collateral_adequacy and C_collateral_title_clear ----------------------------------------------------


def home_state(limit: int, band: str, title: str = "clear", opinion: str = "positive", spread: str = "<5%") -> dict:
    return make_state(
        "secured_home",
        {
            "property.ltv_limit_pct": limit,
            "property.ltv_pct_band": band,
            "property.title_status": title,
            "property.legal_opinion": opinion,
            "property.valuation_spread_band": spread,
        },
    )


@pytest.mark.parametrize("limit", [75, 80])
def test_the_rubric_collateral_table_is_what_the_rule_computes(limit):
    """The primary path: the headroom band alone sets the base level, whatever the limit or the LTV band."""
    for band, level in pack_c.COLLATERAL_BASE_BY_LTV_HEADROOM_BAND.items():
        state = add_headroom(home_state(limit, "70-75"), ltv=band)
        assert C.collateral_level(state) == level, (limit, band)


@pytest.mark.parametrize(
    ("limit", "band", "level"),
    [(80, "<60", 4), (80, "60-70", 3), (80, "70-75", 2), (80, "75-80", 2), (80, "80-85", 1), (80, ">85", 0),
     (75, "<60", 4), (75, "60-70", 3), (75, "70-75", 2), (75, "75-80", 1), (75, "80-85", 0), (75, ">85", 0)],
)
def test_collateral_falls_back_to_the_ltv_band_against_the_limit_when_headroom_is_missing(limit, band, level):
    state = home_state(limit, band)
    assert "ltv_headroom_pts_band" not in state["property"]
    assert C.collateral_level(state) == level


@pytest.mark.parametrize(
    ("kwargs", "level"),
    [
        (dict(title="pending_mutation"), 3), (dict(title="disputed"), 2), (dict(opinion="adverse"), 3), (dict(opinion="pending"), 4),
        (dict(spread="10-20%"), 4), (dict(spread=">20%"), 3), (dict(title="disputed", opinion="adverse"), 1),
        (dict(title="disputed", opinion="adverse", spread=">20%"), 0),
    ],
)
def test_collateral_defects_lower_the_level(kwargs, level):
    assert C.collateral_level(add_headroom(home_state(80, "<60", **kwargs), ltv=">=15")) == level


def test_collateral_level_never_goes_below_zero():
    assert C.collateral_level(add_headroom(home_state(75, ">85", "disputed", "adverse", ">20%"), ltv="<-5")) == 0


@pytest.mark.parametrize(("headroom", "level"), [(">=15", 4), ("8-15", 3), ("0-8", 2), ("-5-0", 1), ("<-5", 0)])
def test_collateral_adequacy_direction(headroom, level):
    assert abs(mean_score("C_collateral_adequacy", add_headroom(home_state(80, "70-75"), ltv=headroom)) - level) < 0.6


def test_collateral_answers_a_neutral_middle_when_every_ltv_field_is_missing():
    state = make_state("secured_home")
    del state["property"]["ltv_pct_band"]
    assert "ltv_headroom_pts_band" not in state["property"]
    assert C.collateral_level(state) == 2
    assert 0 <= answer("C_collateral_adequacy", state, 1)["score"] <= 4


@pytest.mark.parametrize(("title", "opinion", "clear"), [("clear", "positive", True), ("clear", "pending", False), ("clear", "adverse", False), ("disputed", "positive", False), ("pending_mutation", "positive", False)])
def test_title_clear(title, opinion, clear):
    state = home_state(80, "60-70", title, opinion)
    (assert_yes if clear else assert_no)("C_collateral_title_clear", state)


# --- agreement with the generator's truth on real states ---------------------------------------------------


def truth_pairs(qid: str, fn) -> list[tuple]:
    return [(row["truth"][qid], fn(row["state"])) for row in book_rows() if qid in row["truth"]]


def agreement(pairs: list[tuple]) -> float:
    return sum(bool(t) == bool(v) for t, v in pairs) / len(pairs)


def volatility_edges_are_aligned() -> bool:
    from jevloan.state.base import BAND_TABLES

    return tuple(BAND_TABLES["volatility"].edges) == (0.25, 0.35)


@pytest.mark.parametrize(
    ("qid", "signal", "minimum"),
    [
        ("A_income_proof_current", lambda s: A.income_proof_current(s)[0], 1.0),
        ("A_address_proof_valid", A.address_proof_valid, 1.0),
        ("A_statements_cover_months", lambda s: s["bank"]["months_covered"] >= s["bank"]["months_required"], 1.0),
        ("A_fields_cohere", lambda s: not ((A.income_band_gap(s) or 0) >= 2 or A.tenure_past_superannuation(s) or A.experience_too_long(s)), 0.95),
        ("B_identity_coheres", B.identity_coheres, 1.0),
        ("B_salary_matches_employer", B.salary_matches_employer, 1.0),
        ("B_gst_bank_consistent", lambda s: s["gst"]["gst_to_bank_ratio_band"] not in ("<0.5", ">2"), 1.0),
        ("B_synthetic_identity_signals", B.synthetic_identity, 0.98),
        ("C_recent_delinquency", lambda s: s["bureau"]["max_dpd_12m_band"] in ("30-59", "60-89", "90+"), 1.0),
        ("C_foir_within_limit", C.foir_within_limit, 1.0),
        ("C_collateral_title_clear", lambda s: s["property"]["title_status"] == "clear" and s["property"]["legal_opinion"] == "positive", 1.0),
    ],
)
def test_the_truth_signals_agree_with_the_generator_on_real_states(qid, signal, minimum):
    pairs = truth_pairs(qid, signal)
    assert len(pairs) >= 40
    assert agreement(pairs) >= minimum, (qid, agreement(pairs))


def test_willingness_signal_matches_the_generator_exactly():
    assert all(t == v for t, v in truth_pairs("C_willingness", C.willingness_level))


def test_capacity_and_collateral_levels_are_within_one_of_the_generator():
    for qid, fn, exact_min in (("C_capacity", C.capacity_level, 0.65), ("C_collateral_adequacy", C.collateral_level, 0.6)):
        pairs = truth_pairs(qid, fn)
        assert all(abs(t - v) <= 1 for t, v in pairs if not (qid == "C_capacity" and abs(t - v) == 2)), qid
        assert sum(t == v for t, v in pairs) / len(pairs) >= exact_min, qid


def test_income_stable_agrees_with_the_generator_as_far_as_the_volatility_bands_allow():
    """The truth is cv < 0.25. With the state's edges at 0.25 and 0.35 the signal is exact; with the older edges the
    'moderate' band straddles the cut and agreement is about 82%."""
    agree = agreement(truth_pairs("C_income_stable", C.income_stable))
    assert agree >= (0.98 if volatility_edges_are_aligned() else 0.75), agree


def test_the_noisy_answers_agree_with_the_truth_about_as_often_as_designed():
    """The full rules, noise included, over the book slice: 85 to 95 percent agreement per Noul on average."""
    total = correct = 0
    for row in book_rows():
        for qid, truth in row["truth"].items():
            if qid[0] not in "ABC" or not isinstance(truth, bool):
                continue
            p = answer(qid, row["state"], hash((qid, row["file_id"])) & 0xFFFF)["noul"]
            total += 1
            correct += (p >= 0.5) == truth
    assert 0.85 <= correct / total <= 0.99, correct / total


def test_scores_stay_calibrated_enough_to_be_within_one_level_most_of_the_time():
    for qid in ("C_willingness", "C_capacity"):
        within = [abs(answer(qid, row["state"], i)["score"] - row["truth"][qid]) <= 1 for i, row in enumerate(book_rows())]
        assert sum(within) / len(within) >= 0.85, qid


def test_the_rules_do_not_modify_the_state():
    state = make_state("secured_home")
    frozen = copy.deepcopy(state)
    for qid in ("A_address_proof_valid", "B_identity_coheres", "C_capacity", "C_collateral_adequacy", "C_income_stable"):
        answer(qid, state, 1)
    assert state == frozen


@pytest.mark.parametrize("segment", ["salaried_personal", "self_employed", "msme_business", "secured_home"])
def test_every_rule_answers_a_state_that_lacks_optional_blocks(segment):
    """A state with the optional blocks stripped still gets an answer from every A/B/C rule (the graceful fallback)."""
    state = make_state(segment)
    for block in ("gst", "business", "property"):
        state.pop(block, None)
    state["documents"] = []
    for qid, q in catalog().items():
        if q.module in "ABC":
            result = answer(qid, state, 3)
            assert result["type"] == q.qtype
