"""Loan arithmetic. Code does all the maths (PLAN D2); Jev only ever sees the results as bands.

Conventions, stated once so nobody has to guess:

* Rates are annual percentages (``12.0`` means 12% p.a.), never fractions.
* ``foir`` and ``ltv`` return **percentages** (``55.0`` means 55%), because the state schema and the
  sanction grid express limits in percent. ``dscr`` returns a plain ratio (``1.25``).
* Money is in rupees; nothing here rounds unless the docstring says so.
"""

from __future__ import annotations

import math

APR_TOLERANCE = 1e-10  # on the *monthly* rate; the bisection actually narrows well below this


def emi(principal: float, annual_rate_pct: float, n_months: int) -> float:
    """Equated monthly instalment on a reducing-balance loan, paid in arrears.

    ``EMI = P * r * (1+r)^n / ((1+r)^n - 1)`` with ``r = annual_rate_pct / 1200``.
    A zero rate returns ``principal / n_months``. Example: 5,00,000 at 12% over 36 months is 16,607.15.
    """
    if n_months <= 0:
        raise ValueError("n_months must be positive")
    if principal < 0:
        raise ValueError("principal must not be negative")
    r = annual_rate_pct / 1200.0
    if r == 0:
        return principal / n_months
    growth = math.exp(n_months * math.log1p(r))
    return principal * r * growth / (growth - 1.0)


def _pv_of_annuity(payment: float, r: float, n_months: int) -> float:
    """Present value of ``n_months`` level payments at monthly rate ``r`` (numerically safe near r = 0)."""
    if abs(r) < 1e-12:
        return payment * n_months
    return payment * (-math.expm1(-n_months * math.log1p(r))) / r


def apr_from_components(principal: float, upfront_fees: float, emi: float, n_months: int) -> float:
    """Annual Percentage Rate as the nominal annualised IRR of the borrower's cash flows, in percent.

    The borrower receives ``principal - upfront_fees`` today and pays ``emi`` at the end of each of the next
    ``n_months`` months. We solve for the monthly IRR ``r`` in::

        principal - upfront_fees = sum_{t=1..n} emi / (1 + r)^t

    by bisection (tolerance 1e-10 on ``r``; the present value is strictly decreasing in ``r``, so the root is
    unique) and return ``APR = 12 * r * 100``.

    Convention: this is the *nominal* annualised IRR (monthly IRR times 12), which is what the illustration in
    RBI's Key Fact Statement format uses. It is NOT the effective annual rate ``(1+r)^12 - 1``. With no fees
    and ``emi`` computed at rate ``R``, the APR is exactly ``R``; upfront fees push it above ``R``.
    """
    net = principal - upfront_fees
    if n_months <= 0:
        raise ValueError("n_months must be positive")
    if net <= 0:
        raise ValueError("upfront fees must be smaller than the principal")
    if emi <= 0:
        raise ValueError("emi must be positive")

    lo, hi = -0.5, 1.0  # monthly rate; wide enough for any real loan (hi is 100% per month)
    while _pv_of_annuity(emi, lo, n_months) < net and lo > -0.999999:  # emi*n < net: a negative-rate loan
        lo = (lo - 1.0) / 2.0
    while _pv_of_annuity(emi, hi, n_months) > net:
        hi *= 2.0
        if hi > 1e6:
            raise ValueError("APR does not converge for these inputs")
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if _pv_of_annuity(emi, mid, n_months) > net:
            lo = mid
        else:
            hi = mid
        if hi - lo < APR_TOLERANCE * 1e-3:
            break
    return 12.0 * ((lo + hi) / 2.0) * 100.0


def foir(total_emi: float, monthly_income: float) -> float:
    """Fixed obligations to income ratio, in percent: ``100 * total_emi / monthly_income``."""
    if monthly_income <= 0:
        raise ValueError("monthly_income must be positive")
    return 100.0 * total_emi / monthly_income


def ltv(loan: float, value: float) -> float:
    """Loan to value ratio, in percent: ``100 * loan / value``."""
    if value <= 0:
        raise ValueError("value must be positive")
    return 100.0 * loan / value


def dscr(annual_cash_accruals: float, annual_debt_service: float) -> float:
    """Debt service coverage ratio: annual cash accruals divided by annual debt service (a plain ratio).
    No debt service at all means unlimited cover, returned as ``inf``."""
    if annual_debt_service < 0:
        raise ValueError("annual_debt_service must not be negative")
    if annual_debt_service == 0:
        return math.inf
    return annual_cash_accruals / annual_debt_service
