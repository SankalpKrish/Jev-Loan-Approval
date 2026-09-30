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
