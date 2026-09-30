import pytest
from pydantic import ValidationError

from jevloan.data import schema
from jevloan.data.schema import LoanFile, applicable_qids, grid_row_for, load_book, load_disclosures, load_grid, write_book
from test_data_common import book_300


def test_book_round_trip_through_jsonl(tmp_path):
    book = list(book_300())[:40]
    path = tmp_path / "nested" / "book.jsonl"
    assert write_book(book, path) == 40
    assert len(path.read_text(encoding="utf-8").splitlines()) == 40
    back = list(load_book(path))
    assert back == book
    assert all(b.model_dump() == a.model_dump() for a, b in zip(book, back))


def test_native_script_and_int_bool_values_survive_the_round_trip():
    native = [f for f in book_300() if f.meta.disparity_subset == "lang_doc_script"]
    assert native, "the n=300 book should contain a native-script file"
    f = native[0]
    again = LoanFile.model_validate_json(f.model_dump_json())
    assert again == f
    truth = again.labels.question_truth
    assert isinstance(truth["A_income_proof_current"], bool)
    assert isinstance(truth["C_willingness"], int) and not isinstance(truth["C_willingness"], bool)


def test_schema_rejects_unknown_fields_and_bad_enums():
    f = book_300()[0]
    data = f.model_dump()
    data["applicant"]["surprise"] = 1
    with pytest.raises(ValidationError):
        LoanFile.model_validate(data)
    data = f.model_dump()
    data["segment"] = "gig_worker"
    with pytest.raises(ValidationError):
        LoanFile.model_validate(data)


def test_top_level_fields_match_the_contract():
    assert list(LoanFile.model_fields) == [
        "file_id", "segment", "applicant", "co_applicant", "demographics", "application", "bureau", "income", "obligations",
        "bank", "gst", "property", "identity", "documents", "sanction_memo", "kfs", "post_disbursal", "labels", "meta",
        "pii_inventory",
    ]
    assert list(schema.Labels.model_fields) == [
        "sanctionable", "fraud", "fraud_type", "missing_items", "outcome_12m", "risk_pd", "primary_weakness",
        "closeness_level", "memo_defects", "ews_truth", "covenant_breaches", "question_truth",
    ]
    assert list(schema.PIIInventory.model_fields) == [
        "person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines", "pincodes",
        "aliases",
    ]
    assert list(schema.Bank.model_fields) == [
        "account_number", "months_covered", "most_recent_month_age", "salary_credits_months", "salary_narration_employer",
        "avg_monthly_credits_inr", "emi_bounces_6m", "cash_deposit_share", "min_balance_breaches_6m",
    ]


def test_grid_and_disclosures_load_with_versions():
    grid = load_grid()
    assert grid["version"] == "grid-v1"
    assert {r["product"] for r in grid["rows"]} == {
        "personal_loan_unsecured", "business_loan_self_employed", "msme_term_loan", "home_loan",
    }
    assert [r["band"] for r in grid["rows"] if r["product"] == "personal_loan_unsecured"] == ["<=5L", "5-10L", "10-25L", "25-40L"]
    assert [r["band"] for r in grid["rows"] if r["product"] == "business_loan_self_employed"] == ["<=10L", "10-50L", "50L-2Cr"]
    assert [r["band"] for r in grid["rows"] if r["product"] == "msme_term_loan"] == ["<=50L", "50L-2Cr", "2-5Cr"]
    assert [r["band"] for r in grid["rows"] if r["product"] == "home_loan"] == ["<=30L", "30-75L", ">75L"]
    for r in grid["rows"]:
        assert r["max_tenure_months"] > 0 and r["min_bureau_band"] and r["required_conditions"]
        assert all(c["id"] and c["text"] for c in r["required_conditions"])
        assert (r["max_ltv_pct"] is not None) == (r["product"] == "home_loan")
    assert [d["id"] for d in load_disclosures()] == [
        "apr", "total_cost_of_credit", "fees_and_charges_breakup", "cooling_off_period", "grievance_redressal_officer",
        "recovery_agent_policy", "repayment_schedule", "penal_charges",
    ]


def test_grid_band_boundaries_include_the_upper_bound():
    assert grid_row_for("personal_loan_unsecured", 500_000)["band"] == "<=5L"
    assert grid_row_for("personal_loan_unsecured", 500_001)["band"] == "5-10L"
    assert grid_row_for("home_loan", 7_500_000)["band"] == "30-75L"
    assert grid_row_for("home_loan", 90_000_000)["band"] == ">75L"
    assert grid_row_for("msme_term_loan", 50_000_000)["band"] == "2-5Cr"
    with pytest.raises(KeyError):
        grid_row_for("personal_loan_unsecured", 90_000_000)


def test_grid_encodes_the_conditions_the_brief_names():
    rows = {(r["product"], r["band"]): {c["id"] for c in r["required_conditions"]} for r in load_grid()["rows"]}
    assert "guarantor" not in rows[("personal_loan_unsecured", "5-10L")]
    assert "guarantor" in rows[("personal_loan_unsecured", "10-25L")]
    for band in ("<=30L", "30-75L", ">75L"):
        assert {"property_insurance", "equitable_mortgage"} <= rows[("home_loan", band)]
    assert "hypothecation_stock" not in rows[("msme_term_loan", "<=50L")]
    assert "hypothecation_stock" in rows[("msme_term_loan", "50L-2Cr")]
    for band in ("<=50L", "50L-2Cr", "2-5Cr"):
        assert "cgtmse_cover" in rows[("msme_term_loan", band)]


def test_applicable_qids_mirror_the_catalogue():
    base = schema.qids_for("salaried_personal", salaried=True, has_gst=False)
    assert "B_salary_matches_employer" in base and "B_gst_bank_consistent" not in base
    assert not any(q.startswith("F_covenant") for q in base)
    assert "C_collateral_adequacy" not in base and "D_doubt_collateral" not in base
    assert sum(q.startswith("E_disclosure_") for q in base) == 8
    home_sal = schema.qids_for("secured_home", salaried=True, has_gst=False)
    home_se = schema.qids_for("secured_home", salaried=False, has_gst=False)
    assert "B_salary_matches_employer" in home_sal and "B_salary_matches_employer" not in home_se
    assert {"C_collateral_adequacy", "C_collateral_title_clear", "D_doubt_collateral"} <= set(home_se)
    assert "B_gst_bank_consistent" in schema.qids_for("self_employed", salaried=False, has_gst=True)
    assert "B_gst_bank_consistent" not in schema.qids_for("self_employed", salaried=False, has_gst=False)
    msme = schema.qids_for("msme_business", salaried=False, has_gst=True)
    assert [q for q in msme if q.startswith("F_covenant")] == [f"F_covenant_{c}" for c in schema.MSME_COVENANT_IDS]
    assert len(msme) == len(set(msme))


def test_applicable_qids_use_the_files_own_attributes():
    for f in book_300():
        q = applicable_qids(f)
        assert ("B_salary_matches_employer" in q) == (f.application.employment_type == "salaried" and f.segment in ("salaried_personal", "secured_home"))
        assert ("B_gst_bank_consistent" in q) == (f.gst is not None)
        assert ("C_collateral_adequacy" in q) == (f.property is not None)
