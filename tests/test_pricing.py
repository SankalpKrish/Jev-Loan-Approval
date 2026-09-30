"""The pricing formula: traceable line by line, exact, clamped visibly, and refused for any band a human has not cleared."""

import inspect
import json
from decimal import Decimal

import pytest

from jevloan.data.schema import PRODUCT_BY_SEGMENT, SEGMENTS
from jevloan.finance import apr_from_components, emi
from jevloan.policy.config import load_pricing
from jevloan.policy.pricing import PricingError, PricingResult, price

PRICING = load_pricing()
BUREAU_BANDS = ["800+", "750-799", "700-749", "650-699", "NTC", "600-649", "<600"]


def quote(**overrides) -> PricingResult:
    args = dict(segment="salaried_personal", product="personal_loan_unsecured", band="pass", composite=0.9,
                bureau_score_band="750-799", principal_inr=500_000, tenure_months=36, pricing=PRICING)
    return price(**{**args, **overrides})


def dsum(values) -> Decimal:
    return sum((Decimal(str(v)) for v in values), Decimal(0))


def test_the_worked_example():
    r = quote()
    assert r.rate_pct == 11.75  # 8.75 benchmark + 3.00 spread + 0.00 premium (composite >= 0.85) + 0.00 bureau (750-799)
    assert [(i["name"], i["value_pct"]) for i in r.line_items] == [
        ("benchmark_rate", 8.75), ("segment_spread", 3.0), ("risk_premium", 0.0), ("bureau_adjustment", 0.0)]
    assert r.formula_version == "pricing-2026.09-v1" and r.pre_clamp_rate_pct == 11.75
    assert r.processing_fee_inr == 7500  # 1.5% of 5,00,000
    assert r.emi_inr == round(emi(500_000, 11.75, 36))
    assert r.apr_pct == pytest.approx(apr_from_components(500_000, 7500, r.emi_inr, 36))


@pytest.mark.parametrize("segment", SEGMENTS)
@pytest.mark.parametrize("band,composite", [("pass", 0.95), ("pass", 0.75), ("borderline_approved_by_human", 0.6)])
@pytest.mark.parametrize("bureau", BUREAU_BANDS)
def test_line_items_sum_exactly_to_the_rate(segment, band, composite, bureau):
    r = quote(segment=segment, product=PRODUCT_BY_SEGMENT[segment], band=band, composite=composite, bureau_score_band=bureau)
    assert dsum(i["value_pct"] for i in r.line_items) == Decimal(str(r.rate_pct))  # exact, not approximately
    assert {"benchmark_rate", "segment_spread", "risk_premium", "bureau_adjustment"} <= {i["name"] for i in r.line_items}
    assert all(i["source"] for i in r.line_items)
    assert PRICING.floor_pct[segment] <= r.rate_pct <= PRICING.cap_pct[segment]


def test_line_items_add_up_for_awkward_decimals():
    cfg = PRICING.model_copy(update={"benchmark_rate_pct": 8.7, "segment_spread_pct": {**PRICING.segment_spread_pct, "salaried_personal": 3.1}})
    r = quote(pricing=cfg, bureau_score_band="700-749")  # 8.7 + 3.1 + 0.0 + 0.25: not exactly representable in binary floats
    assert dsum(i["value_pct"] for i in r.line_items) == Decimal(str(r.rate_pct)) == Decimal("12.05")


@pytest.mark.parametrize("segment,band,composite,bureau,expected", [
    ("salaried_personal", "borderline_approved_by_human", 0.6, "750-799", 13.0),
    ("self_employed", "pass", 0.72, "650-699", 8.75 + 3.75 + 0.5 + 0.5),
    ("msme_business", "pass", 0.9, "NTC", 8.75 + 3.0 + 0.0 + 0.75),
    ("secured_home", "pass", 0.9, "800+", 8.75),
    ("secured_home", "pass", 0.72, "700-749", 9.75),
])
def test_known_rates(segment, band, composite, bureau, expected):
    r = quote(segment=segment, product=PRODUCT_BY_SEGMENT[segment], band=band, composite=composite, bureau_score_band=bureau)
    assert r.rate_pct == pytest.approx(expected)


def test_risk_premium_tiers_switch_exactly_at_their_composite():
    premium = lambda c: next(i["value_pct"] for i in quote(composite=c).line_items if i["name"] == "risk_premium")  # noqa: E731
    assert premium(1.0) == 0.0 and premium(0.85) == 0.0
    assert premium(0.8499) == 0.5 and premium(0.70) == 0.5
    with pytest.raises(PricingError, match="below every premium tier"):
        quote(composite=0.6999)  # a "pass" this low is inconsistent with the policy bands: refuse, do not guess


@pytest.mark.parametrize("bureau,adjustment", [("800+", -0.25), ("750-799", 0.0), ("700-749", 0.25), ("650-699", 0.5), ("NTC", 0.75),
                                               ("600-649", 1.0), ("<600", 1.0), ("something new", 1.0)])
def test_bureau_adjustment_and_default(bureau, adjustment):
    item = next(i for i in quote(bureau_score_band=bureau).line_items if i["name"] == "bureau_adjustment")
    assert item["value_pct"] == adjustment
    assert "default" in item["source"] if bureau in ("600-649", "<600", "something new") else bureau in item["source"]


# ------------------------------------------------------------------------------------------------ clamps


def test_floor_clamp_is_its_own_line_item_and_the_items_still_sum():
    cfg = PRICING.model_copy(update={"floor_pct": {**PRICING.floor_pct, "salaried_personal": 12.5}})
    r = quote(pricing=cfg, bureau_score_band="800+")  # 8.75 + 3.0 + 0.0 - 0.25 = 11.5 before the clamp
    assert r.pre_clamp_rate_pct == 11.5 and r.rate_pct == 12.5
    assert r.line_items[-1] == {"name": "floor_clamp", "value_pct": 1.0, "source": "floor_pct[salaried_personal]"}
    assert dsum(i["value_pct"] for i in r.line_items[:-1]) == Decimal("11.5")  # the four formula terms sum to the pre-clamp rate
    assert dsum(i["value_pct"] for i in r.line_items) == Decimal("12.5")


def test_cap_clamp_is_its_own_line_item_and_the_items_still_sum():
    cfg = PRICING.model_copy(update={"cap_pct": {**PRICING.cap_pct, "salaried_personal": 13.5}})
    r = quote(pricing=cfg, band="borderline_approved_by_human", composite=0.6, bureau_score_band="<600")  # 14.0 before the clamp
    assert r.pre_clamp_rate_pct == 14.0 and r.rate_pct == 13.5
    assert r.line_items[-1] == {"name": "cap_clamp", "value_pct": -0.5, "source": "cap_pct[salaried_personal]"}
    assert dsum(i["value_pct"] for i in r.line_items) == Decimal("13.5")


def test_no_clamp_item_when_nothing_is_clamped():
    assert all("clamp" not in i["name"] for i in quote().line_items)
    cfg = PRICING.model_copy(update={"floor_pct": {**PRICING.floor_pct, "salaried_personal": 11.75}})
    assert all("clamp" not in i["name"] for i in quote(pricing=cfg).line_items)  # exactly on the floor is not a clamp


def test_the_clamped_rate_is_what_the_emi_and_apr_use():
    cfg = PRICING.model_copy(update={"cap_pct": {**PRICING.cap_pct, "salaried_personal": 12.0}})
    r = quote(pricing=cfg, band="borderline_approved_by_human", composite=0.6, bureau_score_band="<600")
    assert r.rate_pct == 12.0 and r.emi_inr == round(emi(500_000, 12.0, 36))


# ------------------------------------------------------------------------------------------------ APR, fee and EMI


@pytest.mark.parametrize("segment", SEGMENTS)
def test_apr_is_at_least_the_rate_when_there_is_a_fee(segment):
    r = quote(segment=segment, product=PRODUCT_BY_SEGMENT[segment], principal_inr=2_500_000, tenure_months=120)
    assert PRICING.processing_fee_pct[segment] > 0 and r.processing_fee_inr > 0
    assert r.apr_pct > r.rate_pct


def test_apr_matches_the_rate_when_there_is_no_fee():
    cfg = PRICING.model_copy(update={"processing_fee_pct": {s: 0.0 for s in SEGMENTS}})
    r = quote(pricing=cfg)
    assert r.processing_fee_inr == 0
    assert r.apr_pct == pytest.approx(r.rate_pct, abs=0.01)  # the EMI is rounded to the rupee, hence the small slack


def test_fee_rounds_half_up_to_the_rupee():
    cfg = PRICING.model_copy(update={"processing_fee_pct": {**PRICING.processing_fee_pct, "salaried_personal": 1.0}})
    assert quote(pricing=cfg, principal_inr=250).processing_fee_inr == 3  # 2.5 -> 3 (banker's rounding would say 2)
    assert quote(pricing=cfg, principal_inr=249).processing_fee_inr == 2


def test_pricing_is_deterministic_and_serialisable():
    assert quote() == quote()
    decoded = json.loads(json.dumps(quote().to_dict()))
    assert decoded["rate_pct"] == 11.75 and decoded["formula_version"] == "pricing-2026.09-v1" and len(decoded["line_items"]) == 4


# ------------------------------------------------------------------------------------------------ what may not be priced


@pytest.mark.parametrize("band", ["borderline", "fail", "decline", "human_review", "PASS", "proceed", "", "borderline_approved", None])
def test_any_band_but_the_two_cleared_ones_is_refused(band):
    with pytest.raises(PricingError, match="cannot be priced"):
        quote(band=band)


def test_the_model_never_sets_price():
    params = inspect.signature(price).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    # Everything here is bank policy, the file's own facts, or the engine's band and composite. Nothing takes a model answer.
    assert set(params) == {"segment", "product", "band", "composite", "bureau_score_band", "principal_inr", "tenure_months", "pricing"}


@pytest.mark.parametrize("overrides,match", [
    (dict(segment="crypto", product="x"), "unknown segment"),
    (dict(product="home_loan"), "does not belong"),
    (dict(composite=1.2), "composite"),
    (dict(composite=-0.1), "composite"),
    (dict(principal_inr=0), "positive"),
    (dict(tenure_months=0), "positive"),
])
def test_inputs_that_do_not_fit_the_formula_are_refused(overrides, match):
    with pytest.raises(PricingError, match=match):
        quote(**overrides)


def test_pricing_errors_are_value_errors():
    assert issubclass(PricingError, ValueError)
