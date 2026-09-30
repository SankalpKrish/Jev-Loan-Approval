"""PLAN 3.8 truth-alignment rule: every per-question truth is a function of what the state shows, with thresholds
on band edges. The edges are named constants in data/risk.py (the data layer does not import state/); these tests
pin their exact behaviour, compare them to the state's band tables, and rebuild the truths from state bands."""

import pytest

from jevloan.data import risk
from test_data_common import book_2000

FOIR_LEVEL = {">=20": 4, "10-20": 3, "0-10": 2, "-10-0": 1, "<-10": 0}
LTV_LEVEL = {">=15": 4, "8-15": 3, "0-8": 2, "-5-0": 1, "<-5": 0}
DSCR_LEVEL = {">2": 4, "1.5-2": 3, "1.25-1.5": 2, "1-1.25": 1, "<1": 0}


def pick(segment, **conds):
    return next(f for f in book_2000() if f.segment == segment and all(getattr(f.application, k, None) == v for k, v in conds.items()))


def with_foir(f, foir_pts, cv=0.10):
    """A copy whose FOIR is exactly ``foir_pts`` percent (income 1,00,000 a month, no existing EMI)."""
    g = f.model_copy(deep=True)
    g.income.verified_monthly_income_inr = 100_000
    g.obligations.existing_emi_inr = 0
    g.obligations.proposed_emi_inr = int(round(foir_pts * 1000))
    g.income.volatility_cv = cv
    return g


# ------------------------------------------------------------------------------------------------ constants


def test_named_edges_are_the_plan_values():
    assert risk.FOIR_HEADROOM_EDGES == (-10, 0, 10, 20)
    assert risk.LTV_HEADROOM_EDGES == (-5, 0, 8, 15)
    assert risk.DSCR_EDGES == (1, 1.25, 1.5, 2)
    assert (risk.VOLATILITY_STABLE_CV, risk.VOLATILITY_HIGH_CV, risk.VALUATION_SPREAD_HIGH) == (0.25, 0.35, 0.20)
    assert risk.BALANCE_STRESS_LAST_OVER_FIRST == (6, 10)


def test_edges_match_the_state_band_tables():
    base = pytest.importorskip("jevloan.state.base")
    tables = base.BAND_TABLES
    for name, edges in [("foir_headroom_pts", risk.FOIR_HEADROOM_EDGES), ("ltv_headroom_pts", risk.LTV_HEADROOM_EDGES),
                        ("dscr", risk.DSCR_EDGES), ("volatility", (risk.VOLATILITY_STABLE_CV, risk.VOLATILITY_HIGH_CV))]:
        assert tuple(tables[name].edges) == tuple(edges), name
        assert not tables[name].right_closed, name  # headroom, DSCR and volatility bands are left closed
    assert risk.VALUATION_SPREAD_HIGH == tables["valuation_spread"].edges[-1] and not tables["valuation_spread"].right_closed
    assert dict(base.SEGMENT_FOIR_LIMIT_PCT) == risk.FOIR_LIMIT_PCT
    assert tables["foir_headroom_pts"].labels == ("<-10", "-10-0", "0-10", "10-20", ">=20")
    assert tables["ltv_headroom_pts"].labels == ("<-5", "-5-0", "0-8", "8-15", ">=15")
    assert risk.BAND_ROUND == base._ROUND


# ------------------------------------------------------------------------------------------------ C_capacity


@pytest.mark.parametrize("segment,limit", [("salaried_personal", 55), ("self_employed", 50), ("secured_home", 60)])
def test_capacity_levels_switch_exactly_on_the_headroom_edges(segment, limit):
    f = pick(segment)
    cases = [(limit - 20, 4), (limit - 19.999, 3), (limit - 10, 3), (limit - 9.999, 2), (limit, 2), (limit + 0.001, 1),
             (limit + 10, 1), (limit + 10.001, 0), (limit + 30, 0), (limit - 40, 4)]
    for foir_pts, level in cases:
        assert risk.capacity_level(with_foir(f, foir_pts)) == level, (segment, foir_pts)
    # the yes/no question sits on the same edge: headroom 0 is on the limit, which is within it
    assert risk.foir_within_limit(with_foir(f, limit)) and not risk.foir_within_limit(with_foir(f, limit + 0.001))


def test_capacity_for_msme_switches_exactly_on_the_dscr_edges():
    f = pick("msme_business")

    def level(total_emi):
        g = f.model_copy(deep=True)
        g.income.verified_monthly_income_inr = 120_000
        g.obligations.existing_emi_inr, g.obligations.proposed_emi_inr = 0, total_emi
        g.income.volatility_cv = 0.10
        return risk.capacity_level(g), risk.foir_within_limit(g)

    assert level(60_000) == (4, True)  # DSCR 2.0 is in the top band
    assert level(60_001) == (3, True)
    assert level(80_000) == (3, True)  # 1.5
    assert level(80_001) == (2, True)
    assert level(96_000) == (2, True)  # 1.25, the covenant and policy minimum
    assert level(96_001) == (1, False)
    assert level(120_000) == (1, False)  # 1.0
    assert level(120_001) == (0, False)


def test_high_volatility_costs_one_capacity_level_at_exactly_0_35():
    f = pick("salaried_personal")
    for foir_pts, level in [(35, 4), (45, 3), (55, 2), (65, 1)]:
        assert risk.capacity_level(with_foir(f, foir_pts, cv=0.349)) == level
        assert risk.capacity_level(with_foir(f, foir_pts, cv=0.35)) == level - 1
    assert risk.capacity_level(with_foir(f, 80, cv=0.9)) == 0  # floored


# ------------------------------------------------------------------------------------------------ C_income_stable


def test_income_stable_edge_is_cv_0_25_and_12_months():
    f = pick("salaried_personal")

    def stable(cv, months):
        g = f.model_copy(deep=True)
        g.income.volatility_cv, g.income.months_history = cv, months
        return risk.question_truth(g)["C_income_stable"]

    assert stable(0.249, 12) is True
    assert stable(0.25, 12) is False
    assert stable(0.10, 11) is False
    assert stable(0.10, 240) is True


# ------------------------------------------------------------------------------------------------ C_collateral_adequacy


def home(**edits):
    f = pick("secured_home")
    g = f.model_copy(deep=True)
    g.property.title_status, g.property.legal_opinion = "clear", "positive"
    g.property.market_value_inr = g.property.valuation_2_inr = 10_000_000
    for k, v in edits.items():
        setattr(g.property, k, v)
    return g


def test_collateral_levels_switch_exactly_on_the_ltv_headroom_edges():
    limit = risk.ltv_limit_pct(home())
    for headroom, level in [(15, 4), (14.9, 3), (8, 3), (7.9, 2), (0, 2), (-0.1, 1), (-5, 1), (-5.1, 0), (-20, 0)]:
        assert risk.collateral_level(home(ltv=limit - headroom)) == level, headroom


def test_collateral_deductions():
    limit = risk.ltv_limit_pct(home())
    top = dict(ltv=limit - 20)
    assert risk.collateral_level(home(**top)) == 4
    assert risk.collateral_level(home(**top, title_status="pending_mutation")) == 3
    assert risk.collateral_level(home(**top, title_status="disputed")) == 2
    assert risk.collateral_level(home(**top, legal_opinion="adverse")) == 3
    assert risk.collateral_level(home(**top, title_status="disputed", legal_opinion="adverse")) == 1
    assert risk.collateral_level(home(ltv=limit + 10, title_status="disputed", legal_opinion="adverse")) == 0  # floored


def test_valuation_spread_deduction_applies_at_exactly_20_percent_not_15():
    limit = risk.ltv_limit_pct(home())
    top = dict(ltv=limit - 20)
    assert risk.collateral_level(home(**top, market_value_inr=1_000_000, valuation_2_inr=800_000)) == 3  # spread 0.20
    assert risk.collateral_level(home(**top, market_value_inr=800_000, valuation_2_inr=1_000_000)) == 3  # either way round
    assert risk.collateral_level(home(**top, market_value_inr=1_000_000, valuation_2_inr=800_001)) == 4  # 0.199999
    assert risk.collateral_level(home(**top, market_value_inr=1_000_000, valuation_2_inr=850_000)) == 4  # 15 percent: no longer counts
    assert risk.collateral_level(home(**top, market_value_inr=1_000_000, valuation_2_inr=700_000)) == 3


# ------------------------------------------------------------------------------------------------ F_ews_balance_stress


def test_balance_stress_is_last_two_at_most_0_6_of_first_two_on_integer_sums():
    from test_data_risk import _months

    assert risk.ews_truth(_months(bal=[100, 100, 90, 90, 60, 60]))["F_ews_balance_stress"]  # exactly 0.6: a tie is stress
    assert not risk.ews_truth(_months(bal=[100, 100, 90, 90, 60, 61]))["F_ews_balance_stress"]
    assert risk.ews_truth(_months(bal=[100, 101, 5, 5, 60, 60]))["F_ews_balance_stress"]  # 120 <= 0.6 * 201; the middle months do not count
    assert not risk.ews_truth(_months(bal=[100, 101, 5, 5, 60, 61]))["F_ews_balance_stress"]  # 121 > 120.6
    assert not risk.ews_truth(_months(bal=[100, 100, 1, 1, 61, 60]))["F_ews_balance_stress"]
    assert not risk.ews_truth(_months(bal=[0, 0, 0, 0, 0, 0]))["F_ews_balance_stress"]
    assert not risk.ews_truth(_months(bal=[10**9, 10**9, 1, 1, 1_200_000_001, 1_200_000_000]))["F_ews_balance_stress"]


# ------------------------------------------------------------------------------------------------ the rule itself


def _level_from_bands(file, base):
    """Everything below reads bands the state carries, never a raw feature."""
    if file.segment == "msme_business":
        level = DSCR_LEVEL[base.dscr_band(base.dscr_ratio(file))]
    else:
        level = FOIR_LEVEL[base.foir_headroom_band(base.foir_headroom_pts(file))]
    return max(0, level - 1) if base.volatility_label(file.income.volatility_cv) == "high" else level


def test_truths_are_functions_of_the_state_bands_for_every_file():
    base = pytest.importorskip("jevloan.state.base")
    for f in book_2000():
        t = f.labels.question_truth
        assert t["C_capacity"] == _level_from_bands(f, base), f.file_id
        stable = base.volatility_label(f.income.volatility_cv) == "low" and f.income.months_history >= 12
        assert t["C_income_stable"] == stable
        if f.segment == "msme_business":
            within = base.dscr_band(base.dscr_ratio(f)) not in ("<1", "1-1.25")
        else:
            within = base.foir_headroom_band(base.foir_headroom_pts(f)) not in ("<-10", "-10-0")
        assert t["C_foir_within_limit"] == within, f.file_id
        if f.property:
            level = LTV_LEVEL[base.ltv_headroom_band(base.ltv_headroom_pts(f))]
            level -= {"clear": 0, "pending_mutation": 1, "disputed": 2}[f.property.title_status]
            level -= f.property.legal_opinion == "adverse"
            level -= base.valuation_spread_band(f.property.market_value_inr, f.property.valuation_2_inr) == ">20%"
            assert t["C_collateral_adequacy"] == max(0, level), f.file_id
        balances = [m.avg_balance_inr for m in sorted(f.post_disbursal.months, key=lambda m: m.m)]
        assert t["F_ews_balance_stress"] == (base.balance_change_band(balances) == "falling_40_plus"), f.file_id


def test_truths_are_functions_of_real_state_output():
    """End to end on every 47th file (47 is coprime with the 20-file segment cycle): read only the appraisal and monitoring states, compute the truths, compare."""
    state = pytest.importorskip("jevloan.state")
    files = book_2000()[::47]
    assert {f.segment for f in files} == {"salaried_personal", "self_employed", "msme_business", "secured_home"}
    for f in files:
        a, m = state.build_state(f, "appraisal"), state.build_state(f, "monitoring")
        t = f.labels.question_truth
        if f.segment == "msme_business":
            level = DSCR_LEVEL[a["business"]["dscr_band"]]
            within = a["business"]["dscr_band"] not in ("<1", "1-1.25")
        else:
            level = FOIR_LEVEL[a["obligations"]["foir_headroom_pts_band"]]
            within = a["obligations"]["foir_headroom_pts_band"] not in ("<-10", "-10-0")
        high = a["income"]["volatility"] == "high"
        assert t["C_capacity"] == (max(0, level - 1) if high else level), f.file_id
        assert t["C_foir_within_limit"] == within
        assert t["C_income_stable"] == (a["income"]["volatility"] == "low" and a["income"]["months_history"] >= 12)
        assert t["C_recent_delinquency"] == (a["bureau"]["max_dpd_12m_band"] in ("30-59", "60-89", "90+"))
        if f.segment == "secured_home":
            p = a["property"]
            lv = LTV_LEVEL[p["ltv_headroom_pts_band"]] - {"clear": 0, "pending_mutation": 1, "disputed": 2}[p["title_status"]]
            lv -= p["legal_opinion"] == "adverse"
            lv -= p["valuation_spread_band"] == ">20%"
            assert t["C_collateral_adequacy"] == max(0, lv), f.file_id
            assert t["C_collateral_title_clear"] == (p["title_status"] == "clear" and p["legal_opinion"] == "positive")
        assert t["F_ews_balance_stress"] == (m["loan"]["balance_change_band"] == "falling_40_plus")
        days = [r["dpd_days"] for r in sorted(m["repayment"], key=lambda r: r["m"])]
        rising = sum(days[i] > days[i - 1] for i in range(2, 6)) >= 3 or max(days) >= 30
        assert t["F_ews_dpd_rising"] == rising, f.file_id
        assert t["F_ews_emi_bounces"] == (sum(r["emi_bounced"] for r in m["repayment"]) >= 2)
        assert t["F_ews_partial_payments"] == (sum(r["partial_payment"] for r in m["repayment"]) >= 2)
