import math
import random
from collections import Counter

import pytest

from jevloan.data import risk
from jevloan.data.schema import RepaymentMonth
from test_data_common import book_2000


def _months(dpd=None, bounced=None, partial=None, bal=None):
    dpd = dpd or [0] * 6
    bounced = bounced or [False] * 6
    partial = partial or [False] * 6
    bal = bal or [100_000] * 6
    return [RepaymentMonth(m=i + 1, dpd=dpd[i], emi_bounced=bounced[i], partial_payment=partial[i], avg_balance_inr=bal[i]) for i in range(6)]


def test_risk_function_is_a_sigmoid_of_named_coefficients():
    assert risk.risk_pd({}) == pytest.approx(1 / (1 + math.exp(-risk.COEFFICIENTS["intercept"])))
    x = {"foir": 1.0, "max_dpd": 2.0}
    z = risk.COEFFICIENTS["intercept"] + risk.COEFFICIENTS["foir"] + 2 * risk.COEFFICIENTS["max_dpd"]
    assert risk.risk_pd(x) == pytest.approx(1 / (1 + math.exp(-z)))
    assert 0.002 <= risk.risk_pd({"foir": -50}) and risk.risk_pd({"foir": 50, "max_dpd": 50}) <= 0.95


def test_every_extracted_feature_has_a_coefficient_and_signs_make_sense():
    f = book_2000()[0]
    feats = risk.extract_features(f)
    assert set(feats) <= set(risk.COEFFICIENTS)
    assert risk.COEFFICIENTS["bureau_score"] < 0 and risk.COEFFICIENTS["business_vintage"] < 0
    for k in ("foir", "max_dpd", "emi_bounces", "writeoff", "ltv", "income_volatility", "gst_inconsistency", "fraud_flag", "ntc"):
        assert risk.COEFFICIENTS[k] > 0, k
    assert set(risk.FEATURE_CATEGORY) <= set(risk.COEFFICIENTS)


def test_worse_features_raise_pd_on_a_real_file():
    f = next(f for f in book_2000() if f.segment == "salaried_personal" and f.bureau.score and f.bureau.max_dpd_12m == 0)
    base = risk.pd_for_file(f)
    worse = f.model_copy(deep=True)
    worse.bureau.max_dpd_12m = 45
    assert risk.pd_for_file(worse) > base
    worse = f.model_copy(deep=True)
    worse.bureau.score = 600
    assert risk.pd_for_file(worse) > base
    worse = f.model_copy(deep=True)
    worse.obligations.existing_emi_inr += 30_000
    assert risk.pd_for_file(worse) > base


def test_stored_labels_are_what_the_risk_module_computes():
    for f in book_2000():
        ok, failed = risk.synthetic_credit_decision(f)
        assert f.labels.sanctionable == ok, f.file_id
        assert ok == (not failed)
        assert f.labels.risk_pd == pytest.approx(round(risk.pd_for_file(f), 6))
        assert f.labels.primary_weakness == risk.primary_weakness(f)
        assert f.labels.closeness_level == risk.closeness_level(f)
        assert ("fraud" in failed) == f.labels.fraud


def test_each_policy_rule_can_fail_a_sanctionable_file():
    for seg in ("salaried_personal", "self_employed", "msme_business", "secured_home"):
        f = next(f for f in book_2000() if f.segment == seg and f.labels.sanctionable and f.labels.closeness_level == 4)
        assert risk.synthetic_credit_decision(f) == (True, [])

        def broken(**edits):
            g = f.model_copy(deep=True)
            for path, value in edits.items():
                obj, _, attr = path.rpartition("__")
                target = g
                for part in obj.split("__"):
                    target = getattr(target, part)
                setattr(target, attr, value)
            return risk.synthetic_credit_decision(g)

        assert "max_dpd_60_plus" in broken(bureau__max_dpd_12m=60)[1]
        assert "max_dpd_60_plus" not in broken(bureau__max_dpd_12m=59)[1]
        assert "writeoff_or_settlement" in broken(bureau__writeoffs_or_settlements=1)[1]
        assert "bureau_below_650" in broken(bureau__score=649)[1]
        assert "bureau_below_650" not in broken(bureau__score=650)[1]
        capacity_rule = "dscr_below_1_25" if seg == "msme_business" else "foir_above_limit"
        assert capacity_rule in broken(obligations__existing_emi_inr=10**9)[1]
        assert "fraud" in broken(labels__fraud=True)[1]
        if seg in ("self_employed", "msme_business"):
            assert "business_vintage_below_2y" in broken(application__years_in_job_or_business=1.9)[1]
        if seg == "secured_home":
            assert "title_not_clear" in broken(property__title_status="disputed")[1]
            assert "ltv_above_limit" in broken(property__ltv=95.0)[1]


def test_ntc_is_allowed_only_for_salaried_with_income_of_50k():
    sal = next(f for f in book_2000() if f.segment == "salaried_personal" and f.labels.sanctionable and f.labels.closeness_level == 4)
    g = sal.model_copy(deep=True)
    g.bureau.score = None
    g.income.verified_monthly_income_inr = 60_000
    g.obligations.existing_emi_inr = 0
    g.obligations.proposed_emi_inr = 10_000
    g.application.declared_monthly_income_inr = 60_000
    assert "ntc_not_eligible" not in risk.synthetic_credit_decision(g)[1]
    g.income.verified_monthly_income_inr = 49_000
    g.application.declared_monthly_income_inr = 49_000
    assert "ntc_not_eligible" in risk.synthetic_credit_decision(g)[1]
    biz = next(f for f in book_2000() if f.segment == "self_employed" and f.labels.sanctionable).model_copy(deep=True)
    biz.bureau.score = None
    assert "ntc_not_eligible" in risk.synthetic_credit_decision(biz)[1]


def test_closeness_is_consistent_with_sanctionable():
    book = book_2000()
    for f in book:
        lvl = f.labels.closeness_level
        assert 0 <= lvl <= 4
        if lvl >= 3:
            assert f.labels.sanctionable, f.file_id
        if lvl <= 1:
            assert not f.labels.sanctionable, f.file_id
        if f.labels.fraud:
            assert lvl == 0
    counts = Counter(f.labels.closeness_level for f in book)
    assert all(counts[k] > 60 for k in range(5)), counts  # every level is well populated


def test_primary_weakness_is_one_of_the_six_and_collateral_only_for_home():
    six = {"income_documentation", "repayment_history", "debt_burden", "employment_or_business_stability", "collateral", "bureau_thin_file"}
    seen = Counter()
    for f in book_2000():
        assert f.labels.primary_weakness in six
        if f.property is None:
            assert f.labels.primary_weakness != "collateral"
        seen[f.labels.primary_weakness] += 1
    assert set(seen) == six and min(seen.values()) >= 50


def test_thin_file_weakness_follows_new_to_credit():
    for f in book_2000():
        if f.labels.primary_weakness == "bureau_thin_file":
            assert risk.effective_bureau_score(f) is None


def test_draw_outcome_frequencies():
    rng = random.Random(0)
    n = 40_000
    c = Counter(risk.draw_outcome(0.10, rng) for _ in range(n))
    assert c["defaults"] / n == pytest.approx(0.10, abs=0.01)
    assert c["slips"] / n == pytest.approx(0.15, abs=0.01)  # 1.5 * pd
    c = Counter(risk.draw_outcome(0.40, rng) for _ in range(n))
    assert c["slips"] / n == pytest.approx(0.30, abs=0.012)  # capped at 0.3
    assert c["defaults"] / n == pytest.approx(0.40, abs=0.012)


def test_outcomes_in_the_book_follow_pd():
    book = book_2000()
    lo = [f for f in book if f.labels.risk_pd < 0.03]
    hi = [f for f in book if f.labels.risk_pd > 0.15]
    assert len(lo) > 100 and len(hi) > 50
    assert sum(f.labels.outcome_12m == "defaults" for f in hi) / len(hi) > 3 * sum(f.labels.outcome_12m == "defaults" for f in lo) / len(lo)


def test_ews_definitions_on_hand_built_months():
    assert not any(risk.ews_truth(_months()).values())
    # DPD rising in months 3, 4, 5 (three of the last four) without reaching 30
    r = risk.ews_truth(_months(dpd=[0, 0, 5, 9, 14, 14]))
    assert r["F_ews_dpd_rising"]
    # only two rises
    assert not risk.ews_truth(_months(dpd=[0, 0, 5, 9, 9, 9]))["F_ews_dpd_rising"]
    # reaching 30 anywhere counts
    assert risk.ews_truth(_months(dpd=[0, 30, 0, 0, 0, 0]))["F_ews_dpd_rising"]
    assert not risk.ews_truth(_months(dpd=[0, 29, 0, 0, 0, 0]))["F_ews_dpd_rising"]
    b = [False, True, False, False, False, False]
    assert not risk.ews_truth(_months(bounced=b))["F_ews_emi_bounces"]
    b[4] = True
    assert risk.ews_truth(_months(bounced=b))["F_ews_emi_bounces"]
    assert not risk.ews_truth(_months(partial=[True, False, False, False, False, False]))["F_ews_partial_payments"]
    assert risk.ews_truth(_months(partial=[True, False, False, False, False, True]))["F_ews_partial_payments"]
    # last two months average at least 40 percent below the first two
    assert risk.ews_truth(_months(bal=[100, 100, 90, 90, 60, 60]))["F_ews_balance_stress"]
    assert not risk.ews_truth(_months(bal=[100, 100, 90, 90, 61, 61]))["F_ews_balance_stress"]


def test_ews_truth_in_the_book_matches_its_repayment_months_and_outcome():
    book = book_2000()
    for f in book:
        assert f.labels.ews_truth == risk.ews_truth(f.post_disbursal.months)
        assert [m.m for m in f.post_disbursal.months] == [1, 2, 3, 4, 5, 6]
    def rate(outcome, key):
        sub = [f for f in book if f.labels.outcome_12m == outcome]
        return sum(f.labels.ews_truth[key] for f in sub) / len(sub)
    for key in ("F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_balance_stress"):
        assert rate("defaults", key) > rate("slips", key) > rate("repays", key) - 0.001
    assert rate("defaults", "F_ews_dpd_rising") > 0.7 and rate("repays", "F_ews_dpd_rising") < 0.02
    d = [f for f in book if f.labels.outcome_12m == "defaults"]
    assert sum(any(m.dpd >= 60 for m in f.post_disbursal.months) for f in d) / len(d) > 0.4  # defaults escalate


def test_willingness_capacity_and_collateral_levels():
    book = book_2000()
    for f in book:
        assert 0 <= risk.willingness_level(f) <= 4 and 0 <= risk.capacity_level(f) <= 4
        if f.property:
            assert 0 <= risk.collateral_level(f) <= 4
    clean = next(f for f in book if f.bureau.max_dpd_12m == 0 and f.bank.emi_bounces_6m == 0 and f.bureau.writeoffs_or_settlements == 0)
    assert risk.willingness_level(clean) == 4
    bad = clean.model_copy(deep=True)
    bad.bureau.writeoffs_or_settlements = 1
    assert risk.willingness_level(bad) == 0
    bad = clean.model_copy(deep=True)
    bad.bureau.max_dpd_12m = 65
    assert risk.willingness_level(bad) == 1
    bad.bureau.max_dpd_12m = 35
    assert risk.willingness_level(bad) == 2
    bad.bureau.max_dpd_12m = 5
    assert risk.willingness_level(bad) == 3
