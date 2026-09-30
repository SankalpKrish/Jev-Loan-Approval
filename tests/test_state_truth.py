"""The state carries what the question truths are computed from: C_capacity, C_collateral_adequacy, C_income_stable,
C_foir_within_limit and F_ews_balance_stress can be read off the state fields (PLAN 3.8) and agree exactly with
``labels.question_truth`` (the data module's truth uses the same band edges)."""

from __future__ import annotations

from test_state_common import appraisal, book_400, files_where, states_400


CAPACITY_BY_HEADROOM = {">=20": 4, "10-20": 3, "0-10": 2, "-10-0": 1, "<-10": 0}
CAPACITY_BY_DSCR = {">2": 4, "1.5-2": 3, "1.25-1.5": 2, "1-1.25": 1, "<1": 0}
COLLATERAL_BY_HEADROOM = {">=15": 4, "8-15": 3, "0-8": 2, "-5-0": 1, "<-5": 0}


def capacity_from_state(s: dict) -> int:
    if "foir_headroom_pts_band" in s["obligations"]:
        level = CAPACITY_BY_HEADROOM[s["obligations"]["foir_headroom_pts_band"]]
    else:
        level = CAPACITY_BY_DSCR[s["business"]["dscr_band"]]
    return max(0, level - 1) if s["income"]["volatility"] == "high" else level


def collateral_from_state(s: dict) -> int:
    p = s["property"]
    level = COLLATERAL_BY_HEADROOM[p["ltv_headroom_pts_band"]]
    level -= {"clear": 0, "pending_mutation": 1, "disputed": 2}[p["title_status"]]
    level -= p["legal_opinion"] == "adverse"
    level -= p["valuation_spread_band"] == ">20%"
    return max(0, level)


def _disagreements(files, from_state, qid) -> list[tuple[str, int, int]]:
    return [(f.file_id, from_state(appraisal(f)), f.labels.question_truth[qid]) for f in files
            if from_state(appraisal(f)) != f.labels.question_truth[qid]]


def test_c_capacity_can_be_read_off_the_state():
    assert not (bad := _disagreements(book_400(), capacity_from_state, "C_capacity")), bad[:5]


def test_c_capacity_covers_every_level_in_both_bases():
    seen = {(f.segment == "msme_business", capacity_from_state(appraisal(f))) for f in book_400()}
    assert {lvl for msme, lvl in seen if not msme} == {0, 1, 2, 3, 4}
    assert {lvl for msme, lvl in seen if msme} >= {0, 2, 3, 4}


def test_c_collateral_adequacy_can_be_read_off_the_state():
    files = files_where(lambda f: f.segment == "secured_home")
    assert not (bad := _disagreements(files, collateral_from_state, "C_collateral_adequacy")), bad[:5]
    assert {collateral_from_state(appraisal(f)) for f in files} >= {0, 1, 2, 3, 4}


def test_f_ews_balance_stress_matches_the_truth():
    for f in book_400():
        s = states_400()[(f.file_id, "monitoring")]
        assert (s["loan"]["balance_change_band"] == "falling_40_plus") == f.labels.question_truth["F_ews_balance_stress"], f.file_id


def test_c_income_stable_is_the_low_volatility_band_with_a_year_of_history():
    for f in book_400():
        inc = appraisal(f)["income"]
        assert (inc["volatility"] == "low" and inc["months_history"] >= 12) == f.labels.question_truth["C_income_stable"], f.file_id


def test_c_foir_within_limit_matches_a_headroom_at_or_above_zero():
    for f in files_where(lambda f: f.segment != "msme_business"):
        s = appraisal(f)
        within = s["obligations"]["foir_headroom_pts_band"] in ("0-10", "10-20", ">=20")
        assert within == f.labels.question_truth["C_foir_within_limit"], f.file_id
    for f in files_where(lambda f: f.segment == "msme_business"):
        assert (appraisal(f)["business"]["dscr_band"] not in ("<1", "1-1.25")) == f.labels.question_truth["C_foir_within_limit"], f.file_id


def test_whole_2000_book_truths_can_be_read_off_the_banded_blocks():
    """No redaction needed for the numeric blocks, so check every file of the full book, not just 400."""
    from jevloan.data.generator import generate_book
    from jevloan.state import base

    mismatches = []
    for f in generate_book(2000, 7):
        s = {"obligations": base.obligations_block(f), "income": base.income_block(f)}
        if f.segment == "msme_business":
            s["business"] = base.business_block(f)
        if f.property is not None:
            s["property"] = base.property_block(f)
        truth = f.labels.question_truth
        if capacity_from_state(s) != truth["C_capacity"]:
            mismatches.append((f.file_id, "C_capacity"))
        if f.property is not None and collateral_from_state(s) != truth["C_collateral_adequacy"]:
            mismatches.append((f.file_id, "C_collateral_adequacy"))
        if (s["income"]["volatility"] == "low" and s["income"]["months_history"] >= 12) != truth["C_income_stable"]:
            mismatches.append((f.file_id, "C_income_stable"))
        months = sorted(f.post_disbursal.months, key=lambda m: m.m)
        stress = base.balance_change_band([m.avg_balance_inr for m in months]) == "falling_40_plus"
        if stress != truth["F_ews_balance_stress"]:
            mismatches.append((f.file_id, "F_ews_balance_stress"))
    assert mismatches == [], mismatches[:10]
