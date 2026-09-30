"""docs/DATA_CARD.md must say what the code does: the coefficient table, the base rates and the question list."""

import re

import pytest

from jevloan.config import resolve_path
from jevloan.data.commands import compute_stats
from jevloan.data.risk import COEFFICIENTS, FOIR_LIMIT_PCT, PD_CUTOFF
from jevloan.data.schema import EWS_IDS, MSME_COVENANT_IDS, disclosure_ids
from test_data_common import book_2000

CARD = resolve_path("docs/DATA_CARD.md").read_text(encoding="utf-8")


def test_coefficient_table_matches_the_code():
    section = CARD.split("## 3. The risk function")[1].split("## 4.")[0]
    rows = dict(re.findall(r"^\| `(\w+)` \| (-?\d+\.\d+) \|", section, flags=re.M))
    assert set(rows) == set(COEFFICIENTS)
    for name, value in COEFFICIENTS.items():
        assert float(rows[name]) == pytest.approx(value), name


def test_policy_limits_in_the_card_match_the_code():
    assert "0.08 salaried, 0.10 self-employed, 0.10 MSME, 0.05 home" in CARD
    assert (PD_CUTOFF["salaried_personal"], PD_CUTOFF["self_employed"], PD_CUTOFF["msme_business"], PD_CUTOFF["secured_home"]) == (0.08, 0.10, 0.10, 0.05)
    assert "at most 55% salaried, 50% self-employed, 60% home" in CARD
    assert (FOIR_LIMIT_PCT["salaried_personal"], FOIR_LIMIT_PCT["self_employed"], FOIR_LIMIT_PCT["secured_home"]) == (55.0, 50.0, 60.0)


def test_measured_base_rates_in_the_card_match_the_book():
    s = compute_stats(book_2000())
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    expected = [
        pct(s["sanctionable"]), pct(s["fraud"]), pct(s["missing_any"]), pct(s["memo_defect_any"]),
        pct(s["fraud_types"]["identity_mismatch"]), pct(s["fraud_types"]["salary_pattern_mismatch"]),
        pct(s["fraud_types"]["gst_bank_mismatch"]), pct(s["fraud_types"]["synthetic_identity"]),
        pct(s["outcomes"]["repays"]), pct(s["outcomes"]["slips"]), pct(s["outcomes"]["defaults"]),
        pct(s["memo_defect_kinds"]["memo_condition_mismatch"]), pct(s["memo_defect_kinds"]["apr_math_wrong"]),
        pct(s["memo_defect_kinds"]["disclosure_missing"]),
        *(pct(s["sanctionable_by_segment"][k]) for k in ("salaried_personal", "self_employed", "msme_business", "secured_home")),
        f"{s['mean_pd']:.4f}", f"({s['disparity']['total']} files)", pct(s["disparity"]["total"] / s["n"]),
        *(pct(s["missing_items"][k]) for k in ("income_proof", "address_proof", "statements", "fields_coherence")),
    ]
    for text in expected:
        assert text in CARD, f"{text} is not in DATA_CARD.md; regenerate the numbers"
    for k, v in s["disparity"].items():
        if k != "total":
            assert f"| {v} (" in CARD, k


def test_every_question_family_and_covenant_is_documented():
    for qid in ("A_income_proof_current", "B_synthetic_identity_signals", "C_willingness", "C_capacity", "C_collateral_adequacy",
                "D_closeness", "E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete", "E_disclosure_<id>",
                "F_covenant_<id>", "F_ews_dpd_rising"):
        assert qid in CARD, qid
    for cid in MSME_COVENANT_IDS:
        assert cid in CARD
    for did in EWS_IDS:
        assert did.replace("F_ews_", "") in CARD
    assert len(disclosure_ids()) == 8 and "the 8 disclosures" in CARD
    for section in ("Purpose, and what this data must never be used for", "The risk function", "The synthetic credit policy",
                    "Engineered disparities", "Known unrealistic aspects", "Base rates, measured"):
        assert section in CARD


def test_truth_alignment_edges_in_the_card_match_the_code():
    from jevloan.data import risk

    section = CARD.split("### Truth alignment")[1].split("### `labels.question_truth`")[0]
    fmt = lambda edges: ", ".join(f"{e:g}" for e in edges)  # noqa: E731
    rows = {
        "FOIR_HEADROOM_EDGES": fmt(risk.FOIR_HEADROOM_EDGES), "DSCR_EDGES": fmt(risk.DSCR_EDGES),
        "LTV_HEADROOM_EDGES": fmt(risk.LTV_HEADROOM_EDGES),
        "VOLATILITY_STABLE_CV": f"{risk.VOLATILITY_STABLE_CV:g} and {risk.VOLATILITY_HIGH_CV:g}",
        "VALUATION_SPREAD_HIGH": f"{100 * risk.VALUATION_SPREAD_HIGH:g}%",
        "BALANCE_STRESS_LAST_OVER_FIRST": "{} / {}".format(*risk.BALANCE_STRESS_LAST_OVER_FIRST),
    }
    for constant, text in rows.items():
        line = next(l for l in section.splitlines() if constant in l)
        assert f"| {text} |" in line, (constant, text, line)
    assert "6 places" in section and "left closed" in section


def test_truth_definitions_and_level_distributions_in_the_card_match_the_book():
    book = book_2000()
    for text in ("`foir_headroom_pts_band`", "`ltv_headroom_pts_band`", "`business.dscr_band`", "falling_40_plus",
                 "`dpd_days`", "`valuation_spread_band` `>20%`", "under 0.25", "0.35 or more"):
        assert text in CARD, text

    def dist(q, files):
        n = len(files)
        return ", ".join(f"{100 * sum(f.labels.question_truth[q] == k for f in files) / n:.1f}%" for k in range(5))

    home = [f for f in book if f.property]
    for q, files in (("C_capacity", book), ("C_collateral_adequacy", home), ("C_willingness", book)):
        assert dist(q, files) in CARD, (q, dist(q, files))


def test_alias_convention_is_described_in_the_card():
    for text in ("pii_inventory.aliases", "Latin line1 of the address\n  it renders", "Never aliased", "second PAN"):
        assert text in CARD, text
