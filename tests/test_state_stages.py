"""sanction_docs and monitoring states (PLAN 3.7)."""

from __future__ import annotations

import re

import pytest
from test_state_common import book_400, files_where, states_400

from jevloan import finance
from jevloan.data.schema import grid_row_for, load_disclosures, load_grid
from jevloan.state import STATE_SCHEMA
from jevloan.state.base import VOCABULARY
from jevloan.state.stages import grid_row_block


def sanction(f) -> dict:
    return states_400()[(f.file_id, "sanction_docs")]


def monitoring(f) -> dict:
    return states_400()[(f.file_id, "monitoring")]


def test_sanction_docs_keys():
    for f in book_400():
        s = sanction(f)
        assert set(s) == {"schema", "stage", "segment", "sanction_memo", "kfs", "policy_grid_row", "required_disclosures"}
        assert s["schema"] == STATE_SCHEMA and s["stage"] == "sanction_docs" and s["segment"] == f.segment
        assert set(s["sanction_memo"]) == {"text", "product", "ticket_band", "tenure_months", "conditions"}
        assert set(s["kfs"]) == {"text", "apr_stated_pct", "apr_recomputed_pct", "rate_pct", "tenure_months"}
        assert set(s["policy_grid_row"]) - {"max_ltv_pct"} == {"product", "band", "max_tenure_months", "required_conditions"}


def test_apr_recomputed_from_the_kfs_components():
    wrong = ok = 0
    for f in book_400():
        s = sanction(f)
        k = f.kfs
        want = round(finance.apr_from_components(k.principal_inr, k.fees_inr, k.emi_inr, k.tenure_months), 2)
        assert s["kfs"]["apr_recomputed_pct"] == want
        assert s["kfs"]["apr_stated_pct"] == k.apr_stated_pct and s["kfs"]["rate_pct"] == k.rate_pct
        diff = abs(s["kfs"]["apr_recomputed_pct"] - s["kfs"]["apr_stated_pct"])
        if "apr_math_wrong" in f.labels.memo_defects:
            assert diff >= 0.45, (f.file_id, diff)  # 0.5 pp or the plain rate, less rounding
            wrong += 1
        else:
            assert diff <= 0.05, (f.file_id, diff)
            ok += 1
    assert wrong >= 10 and ok > 300


def test_policy_grid_row_is_the_grid_row_of_the_memo_product_and_principal():
    for f in book_400():
        s = sanction(f)
        row = grid_row_for(f.sanction_memo.product, f.kfs.principal_inr)
        g = s["policy_grid_row"]
        assert g["product"] == row["product"] and g["band"] == row["band"] == f.sanction_memo.ticket_band
        assert g["max_tenure_months"] == row["max_tenure_months"]
        assert g["required_conditions"] == [{"id": c["id"], "text": c["text"]} for c in row["required_conditions"]]
        assert ("max_ltv_pct" in g) == (row["max_ltv_pct"] is not None) == (f.segment == "secured_home")
        if "max_ltv_pct" in g:
            assert g["max_ltv_pct"] == row["max_ltv_pct"]


def test_grid_row_block_omits_ltv_for_non_home_products():
    assert "max_ltv_pct" not in grid_row_block("personal_loan_unsecured", 300_000)
    assert grid_row_block("home_loan", 8_000_000)["max_ltv_pct"] == 75
    assert grid_row_block("home_loan", 3_000_000)["band"] == "<=30L"
    with pytest.raises(KeyError):
        grid_row_block("gold_loan", 100_000)


def test_required_disclosures_come_from_the_kfs_policy_file():
    want = [{"id": d["id"], "heading": d["heading"]} for d in load_disclosures()]
    assert len(want) == 8
    for f in book_400():
        assert sanction(f)["required_disclosures"] == want


def test_memo_conditions_stay_word_for_word_comparable_with_the_grid():
    policy = {c["text"] for row in load_grid()["rows"] for c in row["required_conditions"]}
    for f in book_400():
        s = sanction(f)
        assert s["sanction_memo"]["conditions"] == f.sanction_memo.conditions or set(f.sanction_memo.conditions) - policy
        for cond in s["sanction_memo"]["conditions"]:
            assert cond in s["sanction_memo"]["text"], (f.file_id, cond)  # policy phrases such as "50 lakh" survive in the text too


def test_memo_condition_mismatch_is_decidable_from_the_state_alone():
    seen = 0
    for f in book_400():
        s = sanction(f)
        grid = s["policy_grid_row"]
        memo = s["sanction_memo"]
        too_long = memo["tenure_months"] > grid["max_tenure_months"]
        missing = any(c["text"] not in memo["conditions"] for c in grid["required_conditions"])
        assert (too_long or missing) == ("memo_condition_mismatch" in f.labels.memo_defects), f.file_id
        seen += too_long or missing
    assert seen >= 10


def test_missing_disclosures_are_decidable_from_the_kfs_text():
    seen = 0
    for f in book_400():
        s = sanction(f)
        for d in s["required_disclosures"]:
            present = d["heading"] in s["kfs"]["text"]
            assert present == (f"disclosure_missing:{d['id']}" not in f.labels.memo_defects), (f.file_id, d["id"])
            seen += not present
    assert seen >= 10


def test_memo_and_kfs_text_is_redacted():
    for f in book_400():
        s = sanction(f)
        for text in (s["sanction_memo"]["text"], s["kfs"]["text"]):
            assert f.file_id not in text
            assert "[APPLICANT]" in text
            assert not re.search(r"(?:Rs\.?|INR|₹)\s*\d", text)
            assert not re.search(r"\b(?:Mr|Mrs|Ms|Shri|Smt)\b", text)
            assert not re.search(r"\+\s?91|\(\s*91\s*\)", text)  # no country-code residue next to a phone token
        if "grievance_redressal_officer" in f.kfs.disclosures_present:
            # the officer is [PERSON_n], or [APPLICANT] when the generator happened to draw the applicant's own name
            assert re.search(r"Grievance redressal officer: \[(?:PERSON_\d+|APPLICANT)\], \[PHONE_\d+\], \[EMAIL_\d+\]", s["kfs"]["text"]), f.file_id


def test_monitoring_keys_and_bands():
    for f in book_400():
        s = monitoring(f)
        assert set(s) == {"schema", "stage", "segment", "loan", "repayment", "covenants"}
        assert s["stage"] == "monitoring" and s["segment"] == f.segment
        assert set(s["loan"]) == {"product", "loan_amount_band", "tenure_months", "months_since_disbursal", "balance_change_band"}
        assert s["loan"]["balance_change_band"] in VOCABULARY["balance_change"]
        assert s["loan"]["product"] == f.application.product and s["loan"]["tenure_months"] == f.application.tenure_months
        assert s["loan"]["months_since_disbursal"] == 6 and s["loan"]["loan_amount_band"] in VOCABULARY["amount"]
        assert len(s["repayment"]) == 6
        for row, m in zip(s["repayment"], f.post_disbursal.months, strict=True):
            assert set(row) == {"m", "dpd_days", "dpd_band", "emi_bounced", "partial_payment", "avg_balance_band"}
            assert row["m"] == m.m and row["emi_bounced"] == m.emi_bounced and row["partial_payment"] == m.partial_payment
            assert row["dpd_days"] == m.dpd and isinstance(row["dpd_days"], int) and 0 <= row["dpd_days"] < 1000
            assert row["dpd_band"] in VOCABULARY["dpd"] and row["avg_balance_band"] in VOCABULARY["monthly"]


def test_covenants_appear_only_for_msme():
    for f in book_400():
        cov = monitoring(f)["covenants"]
        if f.segment == "msme_business":
            assert [c["covenant_id"] for c in cov] == ["dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current"]
            for c in cov:
                assert set(c) == {"covenant_id", "required", "reported_value_band", "evidence_text"}
                assert c["evidence_text"] and f.file_id not in c["evidence_text"]
        else:
            assert cov == []


def test_covenant_breach_is_decidable_from_the_state():
    breaches = 0
    for f in files_where(lambda f: f.segment == "msme_business"):
        got = []
        for c in monitoring(f)["covenants"]:
            v, req = c["reported_value_band"], c["required"]
            if c["covenant_id"] == "dscr_min_1_25":
                assert v in VOCABULARY["dscr"] and req == 1.25
                breach = v in ("<1", "1-1.25")
            elif c["covenant_id"] == "stock_statement_monthly":
                breach = v < req
            elif c["covenant_id"] == "no_unapproved_borrowing":
                breach = v > req
            else:
                breach = v is not True
            if breach:
                got.append(c["covenant_id"])
        assert sorted(got) == sorted(f.labels.covenant_breaches), f.file_id
        breaches += len(got)
    assert breaches >= 10


def test_covenant_evidence_is_redacted_but_keeps_the_facts():
    for f in files_where(lambda f: f.segment == "msme_business"):
        cov = {c["covenant_id"]: c for c in monitoring(f)["covenants"]}
        assert "[ORG_A]" in cov["dscr_min_1_25"]["evidence_text"]
        assert re.search(r"required minimum 1\.25", cov["dscr_min_1_25"]["evidence_text"])
        assert re.search(r"\[PERSON_\d+\]", cov["dscr_min_1_25"]["evidence_text"])  # the CFO who signed it
        assert not re.search(r"(?:Rs\.?|INR|₹)\s*\d", " ".join(c["evidence_text"] for c in cov.values()))
        assert not re.search(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}", " ".join(c["evidence_text"] for c in cov.values()))


def test_repayment_ews_signals_survive_the_banding():
    """Delinquency onset is decidable from the DPD bands: any month at 30 days or more is visible as such."""
    order = list(VOCABULARY["dpd"])
    for f in book_400():
        bands = [r["dpd_band"] for r in monitoring(f)["repayment"]]
        assert (any(order.index(b) >= 2 for b in bands)) == any(m.dpd >= 30 for m in f.post_disbursal.months)


def test_dpd_band_and_dpd_days_agree():
    from jevloan.state.base import dpd_band

    for f in book_400():
        for row in monitoring(f)["repayment"]:
            assert row["dpd_band"] == dpd_band(row["dpd_days"])


def test_ews_dpd_rising_is_decidable_from_dpd_days():
    """PLAN 3.8: dpd_days strictly increases across at least 3 of the last 4 months, or any dpd_days >= 30."""
    n_true = 0
    for f in book_400():
        days = [r["dpd_days"] for r in monitoring(f)["repayment"]]
        rising = sum(1 for i in range(2, 6) if days[i] > days[i - 1])
        got = rising >= 3 or max(days) >= 30
        assert got == f.labels.ews_truth["F_ews_dpd_rising"], (f.file_id, days)
        n_true += got
    assert n_true >= 10


def test_balance_stress_is_exactly_the_falling_40_plus_band():
    n_true = 0
    for f in book_400():
        band = monitoring(f)["loan"]["balance_change_band"]
        assert (band == "falling_40_plus") == f.labels.ews_truth["F_ews_balance_stress"], (f.file_id, band)
        n_true += band == "falling_40_plus"
    assert n_true >= 10


def test_balance_change_band_follows_the_first_and_last_two_months():
    from fractions import Fraction

    for f in book_400():
        bal = [m.avg_balance_inr for m in sorted(f.post_disbursal.months, key=lambda m: m.m)]
        ratio = Fraction(bal[4] + bal[5], bal[0] + bal[1])  # exact: no float fuzz at the 0.6 / 0.9 / 1.1 edges
        want = ("falling_40_plus" if ratio <= Fraction(3, 5) else "falling_10_40" if ratio <= Fraction(9, 10)
                else "rising" if ratio >= Fraction(11, 10) else "flat")
        assert monitoring(f)["loan"]["balance_change_band"] == want, f.file_id
    assert {monitoring(f)["loan"]["balance_change_band"] for f in book_400()} == set(VOCABULARY["balance_change"])
