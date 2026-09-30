"""Simulator rules for module C (appraisal support).

The levels follow the truth definitions of PLAN 3.8 / the data card, computed from what the state has: bands,
read at their midpoints. The generator's truth uses exact values, so a file near a band edge can be one level
off, which is realistic. Capacity, the FOIR limit test and collateral read the state's headroom bands
(`obligations.foir_headroom_pts_band`, `property.ltv_headroom_pts_band`); if a field is missing they fall back to
the FOIR or LTV band read against the segment limit, and if that is missing too they answer a neutral middle.

Deliberate simulator weaknesses (they give the fairness tests something to detect):
* `C_willingness` is biased one level lower when `bureau.score_band` is NTC: a thin file reads as less
  willing than its conduct shows.
* `C_capacity` and `C_income_stable` are biased one level lower when `income.documentation_type` is
  `informal_declared`: capacity drops a level and income volatility is read one step worse.
"""

import random

from jevloan.jev.sim_rules import get, level_probs, noisy_p, noul, score, sim_rule

FOIR_MID = {"<30": 20.0, "30-40": 35.0, "40-50": 45.0, "50-55": 52.5, "55-60": 57.5, "60-70": 65.0, ">70": 80.0}
FOIR_TOP = {"<30": 30.0, "30-40": 40.0, "40-50": 50.0, "50-55": 55.0, "55-60": 60.0, "60-70": 70.0, ">70": 100.0}
DSCR_MID = {"<1": 0.85, "1-1.25": 1.125, "1.25-1.5": 1.375, "1.5-2": 1.75, ">2": 2.5}
LTV_MID = {"<60": 55.0, "60-70": 66.0, "70-75": 72.5, "75-80": 77.5, "80-85": 82.5, ">85": 90.0}
# Headroom (limit minus value, in points) at the middle of each band the state builder emits.
FOIR_HEADROOM_MID = {"<-10": -15.0, "-10-0": -5.0, "0-10": 5.0, "10-20": 15.0, ">=20": 27.0}
FOIR_HEADROOM_WITHIN_LIMIT = ("0-10", "10-20", ">=20")
LTV_HEADROOM_MID = {"<-5": -10.0, "-5-0": -2.5, "0-8": 4.0, "8-15": 11.5, ">=15": 20.0}
DEFAULT_FOIR_LIMIT = 55.0
VOLATILITY = ["low", "moderate", "high"]
WILLINGNESS_LEGEND = ["severe", "serious", "weak", "minor_slip", "spotless"]


# --- C_willingness -----------------------------------------------------------------------------------------


def willingness_level(state: dict) -> int:
    dpd = get(state, "bureau.max_dpd_12m_band", "0")
    writeoffs = get(state, "bureau.writeoffs_or_settlements", 0) or 0
    bounces = get(state, "bank.emi_bounces_6m", 0) or 0
    if writeoffs > 0 or dpd == "90+":
        return 0
    if dpd == "60-89":
        return 1
    if dpd == "30-59" or bounces >= 2:
        return 2
    if dpd == "1-29" or bounces == 1:
        return 3
    return 4


@sim_rule("C_willingness")
def c_willingness(state: dict, question: dict, rng: random.Random) -> dict:
    """Deliberate simulator weakness: when `bureau.score_band` is NTC the level is read one lower than the
    conduct shows (a new-to-credit applicant with a spotless record reads as a minor slip)."""
    level = willingness_level(state)
    if get(state, "bureau.score_band") == "NTC":
        level = max(0, level - 1)
    return score(level_probs(level, 5, rng, spread=0.5), WILLINGNESS_LEGEND)


@sim_rule("C_recent_delinquency")
def c_recent_delinquency(state: dict, question: dict, rng: random.Random) -> dict:
    delinquent = get(state, "bureau.max_dpd_12m_band", "0") in ("30-59", "60-89", "90+")
    return noul(noisy_p(delinquent, rng, accuracy=0.95))


# --- C_capacity and C_foir_within_limit --------------------------------------------------------------------


def _is_msme_burden(state: dict) -> bool:
    return get(state, "segment") == "msme_business" and get(state, "business.dscr_band") in DSCR_MID


def burden_ratio(state: dict) -> float | None:
    """FOIR over the segment limit (MSME: 1.25 over DSCR), at the middle of the band. Prefers the headroom band,
    falls back to the FOIR band against the limit, and is None if the state has neither."""
    if _is_msme_burden(state):
        return 1.25 / DSCR_MID[get(state, "business.dscr_band")]
    limit = get(state, "obligations.segment_foir_limit_pct")
    headroom = FOIR_HEADROOM_MID.get(get(state, "obligations.foir_headroom_pts_band"))
    if headroom is not None:
        limit = limit or DEFAULT_FOIR_LIMIT
        return (limit - headroom) / limit
    foir = FOIR_MID.get(get(state, "obligations.foir_pct_band"))
    if foir is None or not limit:
        return None
    return foir / limit


def capacity_level(state: dict) -> int:
    """0-4 from the burden ratio (<= 0.6 is 4, <= 0.8 is 3, <= 1.0 is 2, <= 1.2 is 1, else 0), one lower for high volatility."""
    ratio = burden_ratio(state)
    if ratio is None:
        return 2
    level = 4 if ratio <= 0.6 else 3 if ratio <= 0.8 else 2 if ratio <= 1.0 else 1 if ratio <= 1.2 else 0
    if get(state, "income.volatility") == "high":
        level = max(0, level - 1)
    return level


@sim_rule("C_capacity")
def c_capacity(state: dict, question: dict, rng: random.Random) -> dict:
    """Deliberate simulator weakness: when `income.documentation_type` is informal_declared the level is read
    one lower than the numbers show, whatever the applicant's true capacity."""
    level = capacity_level(state)
    if get(state, "income.documentation_type") == "informal_declared":
        level = max(0, level - 1)
    return score(level_probs(level, 5, rng, spread=0.6))


def foir_within_limit(state: dict) -> bool:
    if _is_msme_burden(state):
        return get(state, "business.dscr_band") not in ("<1", "1-1.25")
    headroom = get(state, "obligations.foir_headroom_pts_band")
    if headroom in FOIR_HEADROOM_MID:
        return headroom in FOIR_HEADROOM_WITHIN_LIMIT
    top = FOIR_TOP.get(get(state, "obligations.foir_pct_band"))
    limit = get(state, "obligations.segment_foir_limit_pct")
    return True if top is None or not limit else top <= limit


@sim_rule("C_foir_within_limit")
def c_foir_within_limit(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(foir_within_limit(state), rng, accuracy=0.93))


# --- C_income_stable ---------------------------------------------------------------------------------------


def income_stable(state: dict, *, informal_bias: bool = False) -> bool:
    volatility = get(state, "income.volatility", "moderate")
    if informal_bias and get(state, "income.documentation_type") == "informal_declared":
        volatility = VOLATILITY[min(VOLATILITY.index(volatility) + 1, 2)] if volatility in VOLATILITY else volatility
    return volatility == "low" and (get(state, "income.months_history", 0) or 0) >= 12


@sim_rule("C_income_stable")
def c_income_stable(state: dict, question: dict, rng: random.Random) -> dict:
    """Deliberate simulator weakness: when `income.documentation_type` is informal_declared the volatility is read
    one step worse (low as moderate), so a stable informal income is judged unstable."""
    return noul(noisy_p(income_stable(state, informal_bias=True), rng, accuracy=0.9))


# --- C_collateral_adequacy and C_collateral_title_clear ----------------------------------------------------


def ltv_headroom(state: dict) -> float | None:
    """Points of LTV under the limit at the middle of the band: the headroom band if the state has it, else the
    LTV band against `property.ltv_limit_pct`, else None."""
    mid = LTV_HEADROOM_MID.get(get(state, "property.ltv_headroom_pts_band"))
    if mid is not None:
        return mid
    limit = get(state, "property.ltv_limit_pct")
    ltv = LTV_MID.get(get(state, "property.ltv_pct_band"))
    return None if limit is None or ltv is None else limit - ltv


def collateral_level(state: dict) -> int:
    """Headroom under the LTV limit (15 points or more is 4, 8 or more is 3, 0 or more is 2, down to -5 is 1,
    worse is 0), minus 2 for a disputed title, 1 for a pending mutation, 1 for an adverse legal opinion, 1 for a
    valuation spread band above 20%."""
    headroom = ltv_headroom(state)
    if headroom is None:
        return 2
    level = 4 if headroom >= 15 else 3 if headroom >= 8 else 2 if headroom >= 0 else 1 if headroom >= -5 else 0
    level -= {"clear": 0, "pending_mutation": 1, "disputed": 2}.get(get(state, "property.title_status"), 0)
    if get(state, "property.legal_opinion") == "adverse":
        level -= 1
    if get(state, "property.valuation_spread_band") == ">20%":
        level -= 1
    return max(0, level)


@sim_rule("C_collateral_adequacy")
def c_collateral_adequacy(state: dict, question: dict, rng: random.Random) -> dict:
    return score(level_probs(collateral_level(state), 5, rng, spread=0.55))


@sim_rule("C_collateral_title_clear")
def c_collateral_title_clear(state: dict, question: dict, rng: random.Random) -> dict:
    clear = get(state, "property.title_status") == "clear" and get(state, "property.legal_opinion") == "positive"
    return noul(noisy_p(clear, rng, accuracy=0.94))
