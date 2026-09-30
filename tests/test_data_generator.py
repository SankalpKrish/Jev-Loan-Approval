from collections import Counter

import pytest

from jevloan.data import risk
from jevloan.data.documents import MAX_DOC_WORDS, MAX_DOCS, MAX_KFS_WORDS, MAX_MEMO_WORDS
from jevloan.data.generator import GENERATOR_VERSION, generate_book, generate_file
from jevloan.data.schema import applicable_qids, question_type
from test_data_common import book_300, book_2000, book_2000_seconds


def test_same_seed_gives_an_identical_book_and_other_seeds_differ():
    a = generate_book(120, 5)
    b = generate_book(120, 5)
    assert [f.model_dump_json() for f in a] == [f.model_dump_json() for f in b]
    c = generate_book(120, 6)
    assert [f.model_dump_json() for f in a] != [f.model_dump_json() for f in c]


def test_each_file_depends_only_on_its_own_index_and_seed():
    book = book_300()
    for i in (0, 1, 57, 299):
        assert generate_file(i, 11).model_dump_json() == book[i].model_dump_json()
    # a smaller book is a prefix of a larger one
    assert [f.model_dump_json() for f in generate_book(25, 11)] == [f.model_dump_json() for f in book[:25]]


def test_two_thousand_files_take_well_under_a_minute():
    assert book_2000_seconds() < 60


def test_file_ids_meta_and_segments():
    book = book_2000()
    assert [f.file_id for f in book[:3]] == ["F000001", "F000002", "F000003"]
    assert len({f.file_id for f in book}) == 2000
    assert {f.meta.generator_version for f in book} == {GENERATOR_VERSION}
    assert {f.meta.seed for f in book} == {7}
    seg = Counter(f.segment for f in book)
    assert seg == {"salaried_personal": 600, "self_employed": 400, "msme_business": 500, "secured_home": 500}
    prefix = Counter(f.segment for f in book[:200])  # any prefix is representative
    assert prefix["salaried_personal"] == 60 and prefix["msme_business"] == 50


def test_base_rates_n2000_seed7():
    book = book_2000()
    n = len(book)
    rate = lambda pred: sum(1 for f in book if pred(f)) / n  # noqa: E731
    assert 0.55 <= rate(lambda f: f.labels.sanctionable) <= 0.65
    assert 0.04 <= rate(lambda f: f.labels.fraud) <= 0.07
    for kind, target in [("identity_mismatch", 0.015), ("salary_pattern_mismatch", 0.015), ("gst_bank_mismatch", 0.015), ("synthetic_identity", 0.010)]:
        assert abs(rate(lambda f: f.labels.fraud_type == kind) - target) <= 0.008, kind
    assert 0.12 <= rate(lambda f: bool(f.labels.missing_items)) <= 0.18
    assert 0.17 <= rate(lambda f: bool(f.labels.memo_defects)) <= 0.23
    outcomes = Counter(f.labels.outcome_12m for f in book)
    assert 0.03 <= outcomes["defaults"] / n <= 0.09 and outcomes["slips"] > outcomes["defaults"] * 0.8 and outcomes["repays"] / n > 0.8


def test_fraud_types_only_appear_where_they_can():
    for f in book_2000():
        t = f.labels.fraud_type
        assert f.labels.fraud == (t is not None)
        if t == "salary_pattern_mismatch":
            assert f.application.employment_type == "salaried" and f.segment in ("salaried_personal", "secured_home")
        if t == "gst_bank_mismatch":
            assert f.gst is not None


def test_segment_blocks_are_present_only_where_they_belong():
    for f in book_2000():
        assert (f.property is not None) == (f.segment == "secured_home")
        if f.segment == "msme_business":
            assert f.gst is not None and len(f.post_disbursal.covenants) == 4
        else:
            assert f.post_disbursal.covenants == [] and f.labels.covenant_breaches == []
        assert (f.application.employer_name is not None) == (f.application.employment_type == "salaried")
        assert (f.application.business_name is not None) == (f.application.employment_type != "salaried")
        assert (f.bank.salary_narration_employer is not None) == (f.application.employment_type == "salaried")
        assert f.application.product in {
            "salaried_personal": {"personal_loan_unsecured"}, "self_employed": {"business_loan_self_employed"},
            "msme_business": {"msme_term_loan"}, "secured_home": {"home_loan"},
        }[f.segment]


def test_every_applicable_question_has_a_truth_and_nothing_else_does():
    for f in book_2000():
        qids = applicable_qids(f)
        truth = f.labels.question_truth
        assert list(truth) == qids, f.file_id
        for q in qids:
            v = truth[q]
            if question_type(q) == "score":
                assert isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 4, (f.file_id, q, v)
            else:
                assert isinstance(v, bool), (f.file_id, q, v)
        assert risk.question_truth(f) == truth


def test_question_truth_follows_the_label_definitions():
    for f in book_2000():
        lab, t = f.labels, f.labels.question_truth
        assert t["A_income_proof_current"] == ("income_proof" not in lab.missing_items)
        assert t["A_address_proof_valid"] == ("address_proof" not in lab.missing_items)
        assert t["A_statements_cover_months"] == ("statements" not in lab.missing_items)
        assert t["A_fields_cohere"] == ("fields_coherence" not in lab.missing_items)
        assert t["B_identity_coheres"] == (lab.fraud_type not in ("identity_mismatch", "synthetic_identity"))
        assert t["B_synthetic_identity_signals"] == (lab.fraud_type == "synthetic_identity")
        assert t["C_recent_delinquency"] == (f.bureau.max_dpd_12m >= 30)
        assert t["C_income_stable"] == (f.income.volatility_cv < 0.25 and f.income.months_history >= 12)
        assert t["D_closeness"] == lab.closeness_level
        assert t["D_doubt_repayment_history"] == (lab.primary_weakness == "repayment_history")
        assert sum(t[q] for q in t if q.startswith("D_doubt_")) == 1
        assert t["E_memo_matches_grid"] == ("memo_condition_mismatch" not in lab.memo_defects)
        assert t["E_rate_math_correct"] == ("apr_math_wrong" not in lab.memo_defects)
        assert t["E_disclosures_complete"] == (not any(d.startswith("disclosure_missing:") for d in lab.memo_defects))
        for d in ("apr", "cooling_off_period", "penal_charges"):
            assert t[f"E_disclosure_{d}"] == (f"disclosure_missing:{d}" not in lab.memo_defects)
        for k, v in lab.ews_truth.items():
            assert t[k] == v
        if f.segment == "msme_business":
            for c in ("dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current"):
                assert t[f"F_covenant_{c}"] == (c not in lab.covenant_breaches)
        if f.property:
            assert t["C_collateral_title_clear"] == (f.property.title_status == "clear" and f.property.legal_opinion == "positive")


def test_foir_truth_uses_dscr_for_msme_and_segment_limits_otherwise():
    for f in book_2000():
        foir = risk.true_foir_pct(f)
        if f.segment == "msme_business":
            assert f.labels.question_truth["C_foir_within_limit"] == (risk.true_dscr(f) >= 1.25)
            assert risk.true_dscr(f) == pytest.approx(100 / foir)
        else:
            limit = {"salaried_personal": 55, "self_employed": 50, "secured_home": 60}[f.segment]
            assert f.labels.question_truth["C_foir_within_limit"] == (foir <= limit)


def test_arithmetic_of_the_file_is_consistent():
    from jevloan import finance

    for f in book_2000():
        a = f.application
        assert a.tenure_months > 0 and a.loan_amount_inr > 0
        if f.sanction_memo.tenure_months == a.tenure_months:  # no tenure defect in the memo
            assert f.obligations.proposed_emi_inr == round(finance.emi(a.loan_amount_inr, f.sanction_memo.rate_pct, a.tenure_months))
        assert f.kfs.emi_inr == round(finance.emi(f.kfs.principal_inr, f.kfs.rate_pct, f.kfs.tenure_months))
        assert f.kfs.principal_inr == a.loan_amount_inr and f.kfs.rate_pct == f.sanction_memo.rate_pct
        assert f.kfs.tenure_months == f.sanction_memo.tenure_months
        if f.property:
            p = f.property
            assert p.ltv == pytest.approx(round(100 * a.loan_amount_inr / min(p.market_value_inr, p.valuation_2_inr), 1))
        if f.gst:
            assert f.gst.bank_credits_12m_inr == f.bank.avg_monthly_credits_inr * 12
            assert 0 <= f.gst.filings_on_time_12m <= f.gst.months_filed <= 12


def test_document_limits_from_the_state_budget():
    for f in book_2000():
        assert 3 <= len(f.documents) <= MAX_DOCS, f.file_id
        assert [d.doc_id for d in f.documents] == [f"{f.file_id}-D{i}" for i in range(1, len(f.documents) + 1)]
        for d in f.documents:
            assert len(d.text.split()) <= MAX_DOC_WORDS, (f.file_id, d.doc_type)
        assert len(f.sanction_memo.text.split()) <= MAX_MEMO_WORDS
        assert len(f.kfs.text.split()) <= MAX_KFS_WORDS
    words = [len(d.text.split()) for f in book_2000() for d in f.documents]
    assert sum(words) / len(words) < 60  # 40-60 words is the target


def test_documents_use_only_the_allowed_types_and_scripts():
    allowed = {"salary_slip", "form16", "itr", "bank_statement_header", "address_proof_utility_bill", "address_proof_rent_agreement",
               "address_proof_passport", "pan_card_text", "employer_letter", "gst_return_summary", "business_registration",
               "property_title", "valuation_report"}
    seen = Counter()
    for f in book_2000():
        for d in f.documents:
            assert d.doc_type in allowed
            seen[d.doc_type] += 1
    assert set(seen) == allowed


def test_book_of_300_has_every_segment_and_some_of_every_defect_kind():
    book = book_300()
    assert {f.segment for f in book} == {"salaried_personal", "self_employed", "msme_business", "secured_home"}
    assert any(f.labels.missing_items for f in book) and any(f.labels.memo_defects for f in book)
