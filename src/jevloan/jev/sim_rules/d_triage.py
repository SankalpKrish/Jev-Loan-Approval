"""Simulator rules for module D: closeness and the main doubt (PLAN section 3.4, 3.8).

The rules read the banded appraisal state only, the same thing real Jev sees. They stand in for a reader that
judges "how close to sanctionable" and "what is the weakest factor" from bands, so each rule rebuilds a rough
version of the sanction margins and the risk contributions from band midpoints (the constants mirror the
risk function documented in docs/DATA_CARD.md; the per-file truth is never read). Coarse bands and the pd
estimate make the answer noisy in the way a real model is: the level is usually right or one off, and the
main-doubt factor is usually right unless two factors are close.

Band vocabulary follows PLAN 3.7 where it is fixed. Where it is not (card utilisation, history length,
vintage), the generic parser reads any `a-b`, `<a`, `>a`, `a+` band with `k`/`L`/`Cr`/`%`/`y`/`m` units and a missing
field falls back to the typical value, so a change of vocabulary degrades the sim rather than breaking it.
"""

from __future__ import annotations

import math
import random
import re

from jevloan.jev.sim_rules import get, level_probs, noisy_p, noul, score, sim_rule

# --------------------------------------------------------------------------------------------- band reading

_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_X = {"k": 1e3, "L": 1e5, "Cr": 1e7, "%": 1.0, "y": 1.0, "m": 1.0, None: 1.0}
_UNITS = "k|L|Cr|%|y|m"
_BAND = re.compile(rf"^\s*(<|>)?\s*{_NUM}\s*({_UNITS})?\s*(?:-\s*{_NUM}\s*({_UNITS})?)?\s*(\+)?\s*$")

BUREAU_SCORE = {"<600": 570.0, "600-649": 625.0, "650-699": 675.0, "700-749": 725.0, "750-799": 775.0, "800+": 825.0}
DPD_DAYS = {"0": 0.0, "1-29": 15.0, "30-59": 45.0, "60-89": 75.0, "90+": 100.0}
FOIR_PCT = {"<30": 24.0, "30-40": 35.0, "40-50": 45.0, "50-55": 52.5, "55-60": 57.5, "60-70": 64.5, ">70": 80.0}
LTV_PCT = {"<60": 55.0, "60-70": 65.0, "70-75": 72.5, "75-80": 77.5, "80-85": 82.5, ">85": 90.0}
DSCR = {"<1": 0.88, "1-1.25": 1.13, "1.25-1.5": 1.37, "1.5-2": 1.75, ">2": 2.3}
VOLATILITY_CV = {"low": 0.10, "moderate": 0.22, "high": 0.40}
VINTAGE_YEARS = {"<1y": 0.6, "1-2y": 1.5, "2-5y": 3.4, "5-10y": 7.2, ">10y": 14.0}


def band_range(band) -> tuple[float, float] | None:
    """(low, high) of a band such as `1-3L`, `50L-1Cr`, `<10k`, `>5L`, `90+`, `0.5-0.8`; None if unreadable."""
    if isinstance(band, bool):
        return None
    if isinstance(band, (int, float)):
        return float(band), float(band)
    if not isinstance(band, str):
        return None
    match = _BAND.match(band)
    if not match:
        return None
    sign, first, unit_first, second, unit_second, plus = match.groups()
    if second is not None:  # "1-3L" states the unit once, at the end; "50L-1Cr" states both
        return float(first) * _UNIT_X[unit_first or unit_second], float(second) * _UNIT_X[unit_second or unit_first]
    value = float(first) * _UNIT_X[unit_first]
    if sign == "<":
        return 0.6 * value, value
    if sign == ">" or plus:
        return value, 1.25 * value
    return value, value


def band_mid(band, default: float | None = None) -> float | None:
    span = band_range(band)
    return default if span is None else (span[0] + span[1]) / 2


def _lookup(table: dict[str, float], band, default: float | None = None) -> float | None:
    """Midpoint from a fixed vocabulary, else from the generic parser, else `default`."""
    if isinstance(band, str) and band in table:
        return table[band]
    return band_mid(band, default)


# --------------------------------------------------------------------------------------------- the risk function

_COEF = {
    "intercept": -3.90, "seg_self_employed": 0.15, "seg_msme_business": -0.10, "seg_secured_home": -0.90,
    "bureau_score": -0.80, "ntc": 0.55, "foir": 0.60, "income_volatility": 0.30, "max_dpd": 0.55, "emi_bounces": 0.35,
    "writeoff": 0.90, "ltv": 0.50, "title_risk": 0.60, "business_vintage": -0.35, "gst_inconsistency": 0.35,
    "income_gap": 0.30, "card_utilization": 0.25,
}
_FOIR_CENTRE = {"salaried_personal": 0.40, "self_employed": 0.38, "msme_business": 0.60, "secured_home": 0.45}
_VOL_CENTRE = {"salaried": 0.07, "self_employed": 0.28, "business_owner": 0.30}
_VOL_SCALE = {"salaried": 0.08, "self_employed": 0.15, "business_owner": 0.15}
_VINTAGE_CENTRE = {"salaried_personal": 5.0, "self_employed": 6.0, "msme_business": 9.0, "secured_home": 6.0}
_PD_CUTOFF = {"salaried_personal": 0.08, "self_employed": 0.10, "msme_business": 0.10, "secured_home": 0.05}
_FOIR_LIMIT = {"salaried_personal": 55.0, "self_employed": 50.0, "msme_business": 80.0, "secured_home": 60.0}
_CATEGORY = {
    "bureau_score": "repayment_history", "max_dpd": "repayment_history", "emi_bounces": "repayment_history",
    "writeoff": "repayment_history", "ntc": "bureau_thin_file", "foir": "debt_burden", "card_utilization": "debt_burden",
    "income_volatility": "employment_or_business_stability", "business_vintage": "employment_or_business_stability",
    "ltv": "collateral", "title_risk": "collateral", "gst_inconsistency": "income_documentation",
    "income_gap": "income_documentation",
}
_CATEGORY_ORDER = (
    "repayment_history", "debt_burden", "employment_or_business_stability", "income_documentation", "collateral",
    "bureau_thin_file",
)
_FALLBACK = ("bureau_score", "foir", "card_utilization", "income_volatility", "business_vintage", "ltv")


def _segment(state: dict) -> str:
    return get(state, "segment", "salaried_personal")


def _employment(state: dict) -> str:
    kind = get(state, "application.employment_type")
    if kind in _VOL_CENTRE:
        return kind
    return "salaried" if _segment(state) == "salaried_personal" else "business_owner"


def _foir_pct(state: dict) -> float | None:
    dscr = _lookup(DSCR, get(state, "business.dscr_band"))
    if _segment(state) == "msme_business" and dscr:
        return 100.0 / dscr  # for the business segments income is cash accruals, so DSCR = 1 / FOIR
    return _lookup(FOIR_PCT, get(state, "obligations.foir_pct_band"))


def _years(state: dict) -> float | None:
    for path in ("business.vintage_years_band", "application.years_in_job_or_business_band"):
        years = _lookup(VINTAGE_YEARS, get(state, path))
        if years is not None:
            return years
    return None


def _score(state: dict) -> float | None:
    """The reported bureau score (band midpoint); None for a file with no score."""
    band = get(state, "bureau.score_band")
    if band == "NTC" or band is None:
        return None
    return BUREAU_SCORE.get(band) or band_mid(band)


def _features(state: dict) -> dict[str, float]:
    """The standardised feature vector of the risk function, read from bands."""
    segment, employment = _segment(state), _employment(state)
    score_value = _score(state)
    foir = _foir_pct(state)
    dpd = _lookup(DPD_DAYS, get(state, "bureau.max_dpd_12m_band", "0"), 0.0)
    cv = _lookup(VOLATILITY_CV, get(state, "income.volatility"), _VOL_CENTRE[employment])
    years = _years(state)
    util = band_mid(get(state, "obligations.credit_card_utilization_band"), 0.35)
    if util > 1.5:  # a percentage band
        util /= 100.0
    x = {
        "seg_self_employed": float(segment == "self_employed"),
        "seg_msme_business": float(segment == "msme_business"),
        "seg_secured_home": float(segment == "secured_home"),
        "bureau_score": 0.0 if score_value is None else (score_value - 725.0) / 60.0,
        "ntc": float(score_value is None and get(state, "bureau.score_band") == "NTC"),
        "foir": 0.0 if foir is None else (foir / 100.0 - _FOIR_CENTRE[segment]) / 0.15,
        "income_volatility": (cv - _VOL_CENTRE[employment]) / _VOL_SCALE[employment],
        "max_dpd": min(dpd, 120.0) / 30.0,
        "emi_bounces": min(float(get(state, "bank.emi_bounces_6m", 0) or 0), 4.0) / 2.0,
        "writeoff": float((get(state, "bureau.writeoffs_or_settlements", 0) or 0) > 0),
        "ltv": 0.0,
        "title_risk": 0.0,
        "business_vintage": 0.0 if years is None else (min(years, 15.0) - _VINTAGE_CENTRE[segment]) / 5.0,
        "gst_inconsistency": 0.0,
        "income_gap": 0.0,
        "card_utilization": (util - 0.35) / 0.2,
    }
    if get(state, "property") is not None:
        ltv = _lookup(LTV_PCT, get(state, "property.ltv_pct_band"))
        if ltv is not None:
            x["ltv"] = max(-3.0, min(4.0, (ltv - 65.0) / 12.0))
        title = {"clear": 0.0, "pending_mutation": 0.5, "disputed": 1.0}.get(get(state, "property.title_status", "clear"), 0.0)
        x["title_risk"] = title + (0.5 if get(state, "property.legal_opinion") == "adverse" else 0.0)
    ratio = band_mid(get(state, "gst.gst_to_bank_ratio_band"))
    if ratio:
        x["gst_inconsistency"] = min(3.0, max(0.0, abs(math.log(ratio)) - 0.12) / 0.25)
    declared = band_mid(get(state, "application.declared_monthly_income_band"))
    verified = band_mid(get(state, "income.verified_monthly_income_band"))
    if declared and verified:
        x["income_gap"] = min(3.0, max(0.0, declared / verified - 1.0 - 0.05) / 0.15)
    return x


def _pd(x: dict[str, float]) -> float:
    z = _COEF["intercept"] + sum(_COEF[k] * v for k, v in x.items() if k in _COEF and k != "intercept")
    return min(0.95, max(0.002, 1.0 / (1.0 + math.exp(-z))))


def weakness_scores(state: dict) -> dict[str, float]:
    """Risk-increasing contribution per weakness category (0 when a category does not push risk up)."""
    x = _features(state)
    categories = [c for c in _CATEGORY_ORDER if c != "collateral" or get(state, "property") is not None]
    positive = dict.fromkeys(categories, 0.0)
    for feature, category in _CATEGORY.items():
        contribution = _COEF[feature] * x[feature]
        if category in positive and contribution > 0:
            positive[category] += contribution
    if max(positive.values()) >= 0.05:
        return positive
    # nothing pushes risk up materially: the category whose features sit closest to the risky end is the weakness
    raw = dict.fromkeys(categories, 0.0)
    present = dict.fromkeys(categories, False)
    for feature in _FALLBACK:
        category = _CATEGORY[feature]
        if category in raw and not (feature == "bureau_score" and get(state, "bureau.score_band") == "NTC"):
            raw[category] += _COEF[feature] * x[feature]
            present[category] = True
    candidates = [c for c in categories if present[c]] or categories
    floor = min(raw[c] for c in candidates)
    return {c: (raw[c] - floor) * 0.01 if c in candidates else 0.0 for c in categories}  # only the ranking matters here


def primary_weakness(state: dict) -> tuple[str, float]:
    """(category, lead over the runner-up). A small lead means the file is a coin toss between two factors."""
    scores = weakness_scores(state)
    ranked = sorted(scores, key=lambda c: (-round(scores[c], 9), _CATEGORY_ORDER.index(c)))
    lead = scores[ranked[0]] - (scores[ranked[1]] if len(ranked) > 1 else 0.0)
    return ranked[0], lead


# --------------------------------------------------------------------------------------------- closeness


def margins(state: dict) -> dict[str, float]:
    """Signed distance to each sanction rule, in the units of the generator's margins (positive passes, about 1 is
    comfortable). The smallest one decides the closeness level."""
    segment = _segment(state)
    m: dict[str, float] = {}
    dscr = _lookup(DSCR, get(state, "business.dscr_band"))
    if segment == "msme_business" and dscr:
        m["dscr"] = (dscr - 1.25) / 0.35
    else:
        foir = _lookup(FOIR_PCT, get(state, "obligations.foir_pct_band"))
        limit = get(state, "obligations.segment_foir_limit_pct", _FOIR_LIMIT[segment])
        if foir is not None:
            m["foir"] = (limit - foir) / 10.0
    score_value = _score(state)
    if score_value is None:
        if get(state, "bureau.score_band") == "NTC":
            income = band_range(get(state, "income.verified_monthly_income_band"))
            eligible = segment == "salaried_personal" and income is not None and income[0] >= 50_000
            m["bureau"] = min(2.0, ((income[0] - 50_000) / 25_000 + 0.5)) if eligible else -2.0
    else:
        m["bureau"] = (score_value - 650.0) / 40.0
    m["dpd"] = (60.0 - _lookup(DPD_DAYS, get(state, "bureau.max_dpd_12m_band", "0"), 0.0)) / 20.0
    m["writeoff"] = 2.0 if not (get(state, "bureau.writeoffs_or_settlements", 0) or 0) else -2.5
    if get(state, "property") is not None:
        ltv = _lookup(LTV_PCT, get(state, "property.ltv_pct_band"))
        if ltv is not None:
            m["ltv"] = (get(state, "property.ltv_limit_pct", 80.0) - ltv) / 6.0
        m["title"] = 2.0 if get(state, "property.title_status", "clear") == "clear" else -2.5
    if segment in ("self_employed", "msme_business") or (segment == "secured_home" and _employment(state) != "salaried"):
        years = _years(state)
        if years is not None:
            m["vintage"] = (years - 2.0) / 1.5
    m["pd"] = (_PD_CUTOFF[segment] - _pd(_features(state))) / (0.5 * _PD_CUTOFF[segment])
    return m


# knots: (smallest margin, closeness level). Level edges of the generator sit at -1.0, -0.3, 0.3 and 1.0.
_KNOTS = [(-2.0, 0.0), (-1.0, 0.5), (-0.3, 1.5), (0.3, 2.5), (1.0, 3.5), (1.8, 4.0)]


def closeness_level(state: dict) -> float:
    """A fractional level in [0, 4] from the smallest margin (the file is only as close as its weakest factor)."""
    worst = min(margins(state).values(), default=0.0)
    if worst <= _KNOTS[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(_KNOTS, _KNOTS[1:], strict=False):
        if worst <= x1:
            return y0 + (y1 - y0) * (worst - x0) / (x1 - x0)
    return 4.0


@sim_rule("D_closeness")
def d_closeness(state: dict, question: dict, rng: random.Random) -> dict:
    return score(level_probs(closeness_level(state), 5, rng, spread=0.4))


# --------------------------------------------------------------------------------------------- D_doubt_*

_DOUBT_CATEGORY = {
    "D_doubt_income_documentation": "income_documentation",
    "D_doubt_repayment_history": "repayment_history",
    "D_doubt_debt_burden": "debt_burden",
    "D_doubt_stability": "employment_or_business_stability",
    "D_doubt_collateral": "collateral",
    "D_doubt_thin_file": "bureau_thin_file",
}


def _doubt_rule(category: str):
    def rule(state: dict, question: dict, rng: random.Random) -> dict:
        main, lead = primary_weakness(state)
        accuracy = 0.86 + 0.09 * min(1.0, lead / 0.3)  # a coin toss between two factors is read less reliably
        return noul(noisy_p(main == category, rng, accuracy=accuracy))

    return rule


for _qid, _category in _DOUBT_CATEGORY.items():
    sim_rule(_qid)(_doubt_rule(_category))
