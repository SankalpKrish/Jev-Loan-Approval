"""Band helpers (state/base.py): vocabularies, edges, and agreement with the policy constants."""

from __future__ import annotations

import pytest
from test_state_common import book_400

from jevloan.data import risk
from jevloan.pii.redact import MONTHLY_BANDS
from jevloan.state import base, tokens
from jevloan.state.base import BAND_TABLES, VOCABULARY


def test_vocabularies_match_the_plan():
    assert VOCABULARY["amount"] == ("<1L", "1-3L", "3-5L", "5-10L", "10-25L", "25-50L", "50L-1Cr", "1-2Cr", "2-5Cr", ">5Cr")
    assert VOCABULARY["monthly"] == ("<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L")
    assert VOCABULARY["foir_pct"] == ("<30", "30-40", "40-50", "50-55", "55-60", "60-70", ">70")
    assert VOCABULARY["ltv_pct"] == ("<60", "60-70", "70-75", "75-80", "80-85", ">85")
    assert VOCABULARY["dscr"] == ("<1", "1-1.25", "1.25-1.5", "1.5-2", ">2")
    assert VOCABULARY["gst_to_bank_ratio"] == ("<0.5", "0.5-0.8", "0.8-1.2", "1.2-2", ">2")
    assert VOCABULARY["dpd"] == ("0", "1-29", "30-59", "60-89", "90+")
    assert VOCABULARY["bureau_score"] == ("NTC", "<600", "600-649", "650-699", "700-749", "750-799", "800+")
    assert VOCABULARY["phone_vintage"] == ("<3m", "3-12m", "1-3y", ">3y")
    assert VOCABULARY["valuation_spread"] == ("<5%", "5-10%", "10-20%", ">20%")
    assert VOCABULARY["address_shared"] == ("0", "1-2", "3+")
    assert VOCABULARY["volatility"] == ("low", "moderate", "high")
    assert VOCABULARY["foir_headroom_pts"] == ("<-10", "-10-0", "0-10", "10-20", ">=20")
    assert VOCABULARY["ltv_headroom_pts"] == ("<-5", "-5-0", "0-8", "8-15", ">=15")
    assert VOCABULARY["balance_change"] == ("rising", "flat", "falling_10_40", "falling_40_plus")
    assert BAND_TABLES["volatility"].edges == (0.25, 0.35)
    assert BAND_TABLES["foir_headroom_pts"].edges == (-10, 0, 10, 20) and BAND_TABLES["ltv_headroom_pts"].edges == (-5, 0, 8, 15)


@pytest.mark.parametrize("table", list(BAND_TABLES.values()), ids=lambda t: t.name)
def test_every_table_is_well_formed_and_reachable(table):
    assert len(table.labels) == len(table.edges) + 1 and len(set(table.labels)) == len(table.labels)
    probes = [table.edges[0] - 1] + list(table.edges) + [table.edges[-1] + 1]
    seen = {table.label(p) for p in probes}
    assert seen <= set(table.labels)
    assert table.label(table.edges[0] - 1) == table.labels[0] and table.label(table.edges[-1] + 1) == table.labels[-1]


def test_money_bands():
    assert base.amount_band(99_999) == "<1L" and base.amount_band(100_000) == "1-3L"
    assert base.amount_band(499_999) == "3-5L" and base.amount_band(500_000) == "5-10L"
    assert base.amount_band(10_000_000) == "1-2Cr" and base.amount_band(49_999_999) == "2-5Cr" and base.amount_band(50_000_000) == ">5Cr"
    assert base.monthly_band(9_999) == "<10k" and base.monthly_band(10_000) == "10-25k"
    assert base.monthly_band(74_999) == "50-75k" and base.monthly_band(75_000) == "75k-1L"
    assert base.monthly_band(499_999) == "2-5L" and base.monthly_band(500_000) == ">5L" and base.monthly_band(4_000_000) == ">5L"


def test_monthly_bands_agree_with_the_redactors_text_bands():
    for upper, label in MONTHLY_BANDS:
        assert base.monthly_band(upper - 1) == label


def test_limits_sit_on_the_right_edge_of_a_max_band():
    """FOIR and LTV limits are maximums: a value equal to the limit is within it, and stays in the lower band."""
    assert base.foir_band(55.0) == "50-55" and base.foir_band(55.01) == "55-60"
    assert base.foir_band(29.99) == "<30" and base.foir_band(70.0) == "60-70" and base.foir_band(70.01) == ">70"
    assert base.foir_band(54.999999999999) == "50-55"  # float fuzz around an edge
    assert base.ltv_band(80.0) == "75-80" and base.ltv_band(80.1) == "80-85" and base.ltv_band(75.0) == "70-75"
    assert base.ltv_band(59.9) == "<60" and base.ltv_band(85.1) == ">85"
    assert base.gst_ratio_band(1.2) == "0.8-1.2" and base.gst_ratio_band(1.21) == "1.2-2"
    assert base.gst_ratio_band(0.5) == "<0.5" and base.gst_ratio_band(2.0) == "1.2-2" and base.gst_ratio_band(2.1) == ">2"


def test_dscr_minimum_sits_on_the_left_edge():
    assert base.dscr_band(1.25) == "1.25-1.5" and base.dscr_band(1.2499) == "1-1.25"
    assert base.dscr_band(0.99) == "<1" and base.dscr_band(1.0) == "1-1.25" and base.dscr_band(2.0) == ">2" and base.dscr_band(1.5) == "1.5-2"
    assert base.dscr_band(float("inf")) == ">2"


def test_headroom_bands_are_left_closed_so_a_value_on_its_limit_is_within_it():
    f = base.foir_headroom_band
    assert [f(x) for x in (-50, -10.01, -10, -0.01, 0, 0.01, 9.99, 10, 19.99, 20, 45)] == [
        "<-10", "<-10", "-10-0", "-10-0", "0-10", "0-10", "0-10", "10-20", "10-20", ">=20", ">=20"]
    g = base.ltv_headroom_band
    assert [g(x) for x in (-30, -5.1, -5, -0.1, 0, 0.9, 7.9, 8, 14.9, 15, 40)] == [
        "<-5", "<-5", "-5-0", "-5-0", "0-8", "0-8", "0-8", "8-15", "8-15", ">=15", ">=15"]
    assert f(55 - 54.9999999999) == "0-10" and f(-1e-10) == "0-10"  # float fuzz around the limit does not flip the band


def test_headroom_agrees_with_the_right_closed_foir_and_ltv_bands_at_the_limit():
    """FOIR exactly on the limit is `50-55` (within) and headroom 0 (`0-10`, within); just over is `55-60` and `-10-0`."""
    assert base.foir_band(55.0) == "50-55" and base.foir_headroom_band(55 - 55.0) == "0-10"
    assert base.foir_band(55.01) == "55-60" and base.foir_headroom_band(55 - 55.01) == "-10-0"
    assert base.ltv_band(80.0) == "75-80" and base.ltv_headroom_band(80 - 80.0) == "0-8"
    assert base.ltv_band(80.1) == "80-85" and base.ltv_headroom_band(80 - 80.1) == "-5-0"


def test_balance_change_band():
    b = base.balance_change_band
    assert b([100, 100, 90, 80, 60, 60]) == "falling_40_plus"  # exactly 0.6 x first is a fall of 40 percent: in
    assert b([100, 100, 90, 80, 60, 61]) == "falling_10_40"
    assert b([100, 100, 100, 100, 90, 90]) == "falling_10_40"  # exactly 0.9 x first is in
    assert b([100, 100, 100, 100, 90, 91]) == "flat"
    assert b([100, 100, 100, 100, 100, 100]) == "flat"
    assert b([100, 100, 100, 100, 110, 110]) == "rising"  # exactly 1.1 x first is in
    assert b([100, 100, 100, 100, 109, 110]) == "flat"
    assert b([100, 20, 5000, 1, 40, 32]) == "falling_40_plus"  # only months 1-2 (60 on average) and 5-6 (36) count: 0.6 x
    assert b([1000, 1000, 1, 1, 1000, 1000]) == "flat" and b([1000, 1000, 9999, 9999, 500, 700]) == "falling_40_plus"
    assert b([0, 0, 1, 1, 5, 5]) == "flat"  # no first-months balance to compare with


def test_dpd_and_bureau_bands():
    assert [base.dpd_band(d) for d in (0, 1, 29, 30, 59, 60, 89, 90, 180)] == ["0", "1-29", "1-29", "30-59", "30-59", "60-89", "60-89", "90+", "90+"]
    assert base.bureau_score_band(None) == "NTC"
    assert [base.bureau_score_band(s) for s in (300, 599, 600, 649, 650, 699, 700, 749, 750, 799, 800, 900)] == [
        "<600", "<600", "600-649", "600-649", "650-699", "650-699", "700-749", "700-749", "750-799", "750-799", "800+", "800+"]


def test_age_bands_are_five_year_bands_from_the_birth_year_in_2026():
    assert base.age_band(1988) == "35-39"  # 38 in 2026
    assert base.age_band(2026 - 25) == "25-29" and base.age_band(2026 - 24) == "20-24" and base.age_band(2026 - 64) == "60-64"
    assert base.age_band(2026 - 70) == "65+" and base.age_band(2026 - 19) == "<20"
    assert base.age_band(1990, reference_year=2030) == "40-44"


def test_vintage_history_phone_cash_card_spread_shared_volatility():
    assert [base.vintage_years_band(y) for y in (0.3, 1.0, 1.9, 2.0, 4.9, 5.0, 9.9, 10.0, 30)] == ["<1y", "1-2y", "1-2y", "2-5y", "2-5y", "5-10y", "5-10y", ">10y", ">10y"]
    assert [base.history_length_band(m) for m in (0, 5, 6, 11, 12, 35, 36, 83, 84)] == ["<6m", "<6m", "6-12m", "6-12m", "1-3y", "1-3y", "3-7y", "3-7y", ">7y"]
    assert [base.phone_vintage_band(m) for m in (0, 2, 3, 11, 12, 35, 36, 120)] == ["<3m", "<3m", "3-12m", "3-12m", "1-3y", "1-3y", ">3y", ">3y"]
    assert [base.cash_share_band(x) for x in (0.0, 0.049, 0.05, 0.149, 0.15, 0.299, 0.30, 0.45)] == ["<5%", "<5%", "5-15%", "5-15%", "15-30%", "15-30%", ">30%", ">30%"]
    assert [base.card_utilization_band(x) for x in (0.0, 0.09, 0.10, 0.29, 0.30, 0.49, 0.50, 0.74, 0.75, 1.0)] == [
        "<10%", "<10%", "10-30%", "10-30%", "30-50%", "30-50%", "50-75%", "50-75%", ">75%", ">75%"]
    assert base.valuation_spread_band(1_000_000, 970_000) == "<5%" and base.valuation_spread_band(970_000, 1_000_000) == "<5%"
    assert base.valuation_spread_band(1_000_000, 900_000) == "10-20%" and base.valuation_spread_band(1_000_000, 700_000) == ">20%"
    assert base.valuation_spread_band(1_000_000, 930_000) == "5-10%"
    assert [base.address_shared_band(n) for n in (0, 1, 2, 3, 7)] == ["0", "1-2", "1-2", "3+", "3+"]
    # low is cv < 0.25 (the C_income_stable truth), high is cv >= 0.35 (the capacity deduction)
    assert [base.volatility_label(cv) for cv in (0.0, 0.249, 0.25, 0.349, 0.35, 0.9)] == ["low", "low", "moderate", "moderate", "high", "high"]


def test_policy_constants_agree_with_the_data_risk_module():
    assert base.SEGMENT_FOIR_LIMIT_PCT == {k: int(v) for k, v in risk.FOIR_LIMIT_PCT.items()}
    assert base.MONTHS_REQUIRED == risk.STATEMENTS_REQUIRED


def test_observed_ratios_match_the_risk_helpers_and_never_use_labels_or_meta():
    for f in book_400()[:120]:
        assert base.foir_pct(f) == pytest.approx(risk.true_foir_pct(f))
        assert base.dscr_ratio(f) == pytest.approx(risk.true_dscr(f))
        if f.property is not None:
            assert base.ltv_limit_pct(f) == risk.ltv_limit_pct(f)


def test_bureau_band_uses_the_declared_score_never_the_hidden_one():
    thin = [f for f in book_400() if f.meta.disparity_subset == "pincode_bureau_thin" and f.meta.true_bureau_score is not None]
    assert thin, "the 400-file book should contain hidden-score files"
    for f in thin:
        assert f.bureau.score is None
        assert base.bureau_block(f)["score_band"] == "NTC"


def test_estimate_tokens_is_ceil_of_canonical_length_over_2_2():
    import math

    from jevloan.canonical import canonical_json

    obj = {"b": [1, 2, 3], "a": "x" * 40}
    assert tokens.estimate_tokens(obj) == math.ceil(len(canonical_json(obj)) / 2.2)
    assert tokens.estimate_tokens("x" * 218) == 100  # 220 characters with the quotes
    assert tokens.estimate_tokens({}) == 1  # "{}" is 2 characters
    assert tokens.estimate_tokens({"k": "\u0b95" * 30}) == math.ceil(len('{"k":"' + "\u0b95" * 30 + '"}') / 2.2)  # characters, not bytes
