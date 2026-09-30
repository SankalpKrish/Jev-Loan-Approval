"""Simulator rules for `jevloan jev hello`. Also a small worked example of the rule helpers."""

import random

from jevloan.jev.sim_rules import choice, get, level_probs, noisy_p, noul, score, sim_rule

_FOIR_BAND_UPPER = {"<30": 30, "30-40": 40, "40-50": 50, "50-55": 55, "55-60": 60, "60-70": 70, ">70": 100}


def _foir_within_limit(state: dict) -> bool:
    upper = _FOIR_BAND_UPPER.get(get(state, "obligations.foir_pct_band"), 100)
    return upper <= get(state, "obligations.segment_foir_limit_pct", 0)


@sim_rule("hello_foir_within_limit")
def foir_within_limit(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(_foir_within_limit(state), rng))


@sim_rule("hello_primary_weakness")
def primary_weakness(state: dict, question: dict, rng: random.Random) -> dict:
    if get(state, "bureau.max_dpd_12m_band", "0") != "0":
        truth = "repayment_history"
    elif not _foir_within_limit(state):
        truth = "debt_burden"
    elif get(state, "income.documentation_type") != "salary_slip":
        truth = "income_documentation"
    else:
        truth = "none_material"
    peak = rng.uniform(0.6, 0.9)
    others = [option for option in question["criteria"] if option != truth]
    return choice({truth: peak, **{option: (1 - peak) / len(others) for option in others}})


@sim_rule("hello_repayment_conduct")
def repayment_conduct(state: dict, question: dict, rng: random.Random) -> dict:
    bounces = get(state, "bank.emi_bounces_6m", 0) + get(state, "bank.min_balance_breaches_6m", 0)
    if get(state, "bureau.max_dpd_12m_band", "0") == "0" and bounces == 0:
        level = 2
    elif get(state, "bureau.max_dpd_12m_band", "0") in ("0", "1-29") and bounces <= 2:
        level = 1
    else:
        level = 0
    return score(level_probs(level, len(question["criteria"]), rng))
