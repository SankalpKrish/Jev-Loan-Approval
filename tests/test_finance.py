import math

import pytest

from jevloan import finance


def test_emi_hand_computed_5_lakh_12pct_36_months():
    # 500000 * 0.01 * 1.01^36 / (1.01^36 - 1) = 16607.1544...
    assert finance.emi(500_000, 12, 36) == pytest.approx(16_607.15, abs=0.005)


def test_emi_other_hand_computed_values():
    assert finance.emi(1_200_000, 0, 12) == pytest.approx(100_000.0)  # zero rate
    # 10,00,000 at 9% over 240 months is the classic 8,997.26
    assert finance.emi(1_000_000, 9, 240) == pytest.approx(8_997.26, abs=0.01)
    # one month at 12%: principal plus one month of interest
    assert finance.emi(100_000, 12, 1) == pytest.approx(101_000.0)


def test_emi_rejects_bad_input():
    with pytest.raises(ValueError):
        finance.emi(100_000, 12, 0)
    with pytest.raises(ValueError):
        finance.emi(-1, 12, 12)


def test_apr_equals_rate_when_there_are_no_fees():
    for principal, rate, n in [(500_000, 12, 36), (2_500_000, 9.5, 240), (75_000, 18, 12), (1_000_000, 0, 24)]:
        e = finance.emi(principal, rate, n)
        assert finance.apr_from_components(principal, 0, e, n) == pytest.approx(rate, abs=1e-6)


def test_apr_hand_computed_one_month_loan_with_a_fee():
    # borrower gets 99,000 today and repays 101,000 a month later: r = 101000/99000 - 1 = 2.0202%/month
    # APR = 12 * 2.020202... = 24.2424...
    assert finance.apr_from_components(100_000, 1_000, 101_000, 1) == pytest.approx(24.2424, abs=1e-4)


def test_apr_hand_computed_two_month_loan_with_a_fee():
    # net 90,000; two payments of 50,000. Solve 90000 = 50000/(1+r) + 50000/(1+r)^2 with x = 1/(1+r):
    # 50000 x^2 + 50000 x - 90000 = 0  ->  x = (-1 + sqrt(1 + 4*1.8)) / 2
    x = (-1 + math.sqrt(1 + 4 * 1.8)) / 2
    expected = 12 * (1 / x - 1) * 100
    assert finance.apr_from_components(100_000, 10_000, 50_000, 2) == pytest.approx(expected, abs=1e-6)


def test_fees_push_apr_above_the_rate_and_more_fee_means_more_apr():
    principal, rate, n = 500_000, 12, 36
    e = finance.emi(principal, rate, n)
    apr_1 = finance.apr_from_components(principal, 5_000, e, n)  # 1% fee
    apr_2 = finance.apr_from_components(principal, 10_000, e, n)  # 2% fee
    assert rate < apr_1 < apr_2
    # a 1% upfront fee on a 3-year loan adds roughly 0.6 to 0.7 percentage points
    assert 0.5 < apr_1 - rate < 0.9


def test_fees_matter_less_on_long_tenures():
    rate = 9.0
    short = finance.apr_from_components(1_000_000, 10_000, finance.emi(1_000_000, rate, 36), 36) - rate
    long_ = finance.apr_from_components(1_000_000, 10_000, finance.emi(1_000_000, rate, 240), 240) - rate
    assert long_ < short


def test_apr_satisfies_its_defining_equation():
    principal, fees, n = 3_000_000, 45_000, 180
    e = finance.emi(principal, 8.75, n)
    apr = finance.apr_from_components(principal, fees, e, n)
    r = apr / 1200
    pv = sum(e / (1 + r) ** t for t in range(1, n + 1))
    assert pv == pytest.approx(principal - fees, rel=1e-9)


def test_apr_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        finance.apr_from_components(100_000, 100_000, 5_000, 24)
    with pytest.raises(ValueError):
        finance.apr_from_components(100_000, 0, 0, 24)
    with pytest.raises(ValueError):
        finance.apr_from_components(100_000, 0, 5_000, 0)


def test_foir_ltv_dscr():
    assert finance.foir(27_500, 50_000) == pytest.approx(55.0)
    assert finance.ltv(4_000_000, 5_000_000) == pytest.approx(80.0)
    assert finance.dscr(2_500_000, 2_000_000) == pytest.approx(1.25)
    assert finance.dscr(100, 0) == math.inf
    with pytest.raises(ValueError):
        finance.foir(1, 0)
    with pytest.raises(ValueError):
        finance.ltv(1, 0)
    with pytest.raises(ValueError):
        finance.dscr(1, -1)
