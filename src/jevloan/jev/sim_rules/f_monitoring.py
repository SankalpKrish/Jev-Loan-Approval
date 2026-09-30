"""Simulator rules for module F: early warnings and covenants (PLAN sections 3.4, 3.8).

Each rule reads only the monitoring state: the `repayment` month list (bands and flags) and the `covenants` entries.
The simulator hands a rule the question but not its id, so `F_covenant_*` reads the covenant it was asked about from
`instructions.data.covenant_id`, which is the same thing real Jev is told.

The DPD rule reads `repayment[*].dpd_days` and the balance rule reads `loan.balance_change_band`, so both are exact
comparisons on what the state shows. When a state lacks those fields (an older state), the rules fall back to the bands
and are less exact, as a real reader would be: a rise inside one DPD band is invisible, and a balance fall is judged from
band midpoints.
"""

from __future__ import annotations

import random
import re

from jevloan.jev.sim_rules import get, noisy_p, noul, sim_rule
from jevloan.jev.sim_rules.d_triage import band_mid, band_range


def _months(state: dict) -> list[dict]:
    entries = [m for m in get(state, "repayment", []) or [] if isinstance(m, dict)]
    return sorted(entries, key=lambda m: m.get("m", 0))


def _band_days(band) -> float:
    """Lower edge of a DPD band in days (`0` -> 0, `1-29` -> 1, `30-59` -> 30, `90+` -> 90)."""
    span = band_range(str(band))
    if span is not None:
        return span[0]
    found = re.search(r"\d+", str(band))
    return float(found.group()) if found else 0.0


def _days(months: list[dict]) -> list[float]:
    """Days past due per month: the `dpd_days` integers when every entry has one, else the lower edge of each
    `dpd_band` (a state without `dpd_days` hides a rise inside one band)."""
    if months and all(isinstance(m.get("dpd_days"), (int, float)) and not isinstance(m.get("dpd_days"), bool) for m in months):
        return [float(m["dpd_days"]) for m in months]
    return [_band_days(m.get("dpd_band", "0")) for m in months]


def dpd_rising(months: list[dict]) -> bool:
    """Any month at 30 days or more, or a strict rise over the previous month in at least three of the last four."""
    days = _days(months)
    if any(d >= 30 for d in days):
        return True
    rises = sum(1 for i in range(max(1, len(days) - 4), len(days)) if days[i] > days[i - 1])
    return rises >= 3


_CHANGE_BANDS = ("rising", "flat", "falling_10_40", "falling_40_plus")


def balance_stress(state: dict) -> bool:
    """`loan.balance_change_band == falling_40_plus`. A state without that field falls back to comparing the midpoints
    of the `avg_balance_band` of the last two months with the first two (coarser, so less exact)."""
    change = get(state, "loan.balance_change_band")
    if change in _CHANGE_BANDS:
        return change == "falling_40_plus"
    mids = [band_mid(m.get("avg_balance_band")) for m in _months(state)]
    if len(mids) < 4 or any(mid is None for mid in mids):
        return False
    first, last = (mids[0] + mids[1]) / 2, (mids[-2] + mids[-1]) / 2
    return first > 0 and last <= 0.6 * first


@sim_rule("F_ews_dpd_rising")
def f_ews_dpd_rising(state: dict, question: dict, rng: random.Random) -> dict:
    exact = all(isinstance(m.get("dpd_days"), (int, float)) for m in _months(state))
    return noul(noisy_p(dpd_rising(_months(state)), rng, accuracy=0.93 if exact else 0.90))


@sim_rule("F_ews_emi_bounces")
def f_ews_emi_bounces(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(sum(bool(m.get("emi_bounced")) for m in _months(state)) >= 2, rng, accuracy=0.93))


@sim_rule("F_ews_partial_payments")
def f_ews_partial_payments(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(sum(bool(m.get("partial_payment")) for m in _months(state)) >= 2, rng, accuracy=0.93))


@sim_rule("F_ews_balance_stress")
def f_ews_balance_stress(state: dict, question: dict, rng: random.Random) -> dict:
    exact = get(state, "loan.balance_change_band") in _CHANGE_BANDS
    return noul(noisy_p(balance_stress(state), rng, accuracy=0.93 if exact else 0.90))


# --------------------------------------------------------------------------------------------- covenants

_NUMBER = r"(\d+(?:\.\d+)?)"


def _truthy(value) -> bool | None:
    text = str(value).strip().lower()
    if text in {"true", "yes", "current", "1"}:
        return True
    if text in {"false", "no", "lapsed", "expired", "not current", "0"}:
        return False
    return None


def _at_least(reported, required, evidence: str, pattern: str) -> bool | None:
    """Complied when the reported value is at or above `required` (DSCR, months of stock statements)."""
    found = re.search(pattern, evidence)
    if found:
        return float(found.group(1)) >= float(required)
    span = band_range(reported)
    if span is None or not isinstance(required, (int, float)) or isinstance(required, bool):
        return None
    if span[0] >= required:
        return True
    return False if span[1] <= required else None


def covenant_complied(covenant_id: str, entry: dict) -> bool | None:
    """Whether the covenant is complied with, read from `evidence_text` first and then from the reported band."""
    evidence = str(entry.get("evidence_text") or "").lower()
    reported, required = entry.get("reported_value_band"), entry.get("required")
    if covenant_id == "dscr_min_1_25":
        return _at_least(reported, required, evidence, rf"\bis {_NUMBER}\b")
    if covenant_id == "stock_statement_monthly":
        return _at_least(reported, required, evidence, rf"\bfor {_NUMBER} of the last")
    if covenant_id == "no_unapproved_borrowing":
        if re.search(r"\bno new borrowing\b|\bnothing new\b", evidence):
            return True
        found = re.search(rf"\bfound {_NUMBER} new", evidence)
        if found:
            return float(found.group(1)) == 0
        if isinstance(reported, str) and (reported.startswith(">") or reported.endswith("+")):
            return False
        span = band_range(reported)
        return None if span is None else span[1] <= 0
    if covenant_id == "insurance_current":
        if re.search(r"\bexpired\b|\blapsed\b|\bnot evidenced\b|\bnot current\b", evidence):
            return False
        if re.search(r"\bis current\b", evidence):
            return True
        return _truthy(reported)
    return None


@sim_rule("F_covenant_*")
def f_covenant(state: dict, question: dict, rng: random.Random) -> dict:
    covenant_id = get(question, "instructions.data.covenant_id")
    entries = [c for c in get(state, "covenants", []) or [] if isinstance(c, dict) and c.get("covenant_id") == covenant_id]
    if not covenant_id or not entries:
        return noul(0.5)
    complied = covenant_complied(covenant_id, entries[0])
    return noul(0.5 if complied is None else noisy_p(complied, rng, accuracy=0.93))
