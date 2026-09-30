"""The documents must contain the evidence each truth label rests on, in both directions: a file has the label
if and only if its documents show it."""

import re

import pytest

from jevloan import finance
from jevloan.data.risk import AS_OF, STATEMENTS_REQUIRED
from jevloan.data.schema import grid_row_for, load_disclosures
from test_data_common import DATE_PAT, amounts, ascii_digits, book_2000, parse_date


def doc(f, *types):
    return next((d for d in f.documents if d.doc_type in types), None)


def dob_of(f):
    m = re.search(r"Date of birth: " + DATE_PAT, doc(f, "pan_card_text").text)
    return parse_date(m[1])


# ------------------------------------------------------------------------------------------------ module A


def test_stale_or_absent_income_proof_iff_income_proof_missing():
    for f in book_2000():
        d = doc(f, "salary_slip", "itr")
        stale_or_absent = d is None or d.month_age >= 3
        assert stale_or_absent == ("income_proof" in f.labels.missing_items), f.file_id
        if d is not None:
            assert (d.doc_type == "salary_slip") == (f.application.employment_type == "salaried")
    assert any(doc(f, "salary_slip", "itr") is None for f in book_2000())  # the 'absent' variant occurs
    assert any((doc(f, "salary_slip") or doc(f, "itr")) and (doc(f, "salary_slip", "itr").month_age >= 3) for f in book_2000())


def _address_problem(f, d) -> str | None:
    text = ascii_digits(d.text)
    if "not a billing statement" in text or "plain paper" in text or "address page is missing" in text:
        return "wrong_type"
    if d.doc_type == "address_proof_utility_bill" and d.month_age >= 3:
        return "expired"
    if d.doc_type == "address_proof_rent_agreement":
        start, end = [parse_date(x) for x in re.findall(DATE_PAT, text)[:2]]
        assert 0 < (end - start).days < 400
        if end < AS_OF:
            return "expired"
    if d.doc_type == "address_proof_passport":
        if parse_date(re.search(r"expires " + DATE_PAT, text)[1]) < AS_OF:
            return "expired"
    if f.applicant.address.pincode not in text:
        return "mismatched"
    if d.script == "latin" and f.applicant.address.line1.lower() not in text.lower():
        return "mismatched"
    return None


def test_invalid_address_proof_iff_address_proof_missing():
    kinds = set()
    for f in book_2000():
        d = next(d for d in f.documents if d.doc_type.startswith("address_proof"))
        problem = _address_problem(f, d)
        assert (problem is not None) == ("address_proof" in f.labels.missing_items), (f.file_id, problem, d.text)
        if problem:
            kinds.add(problem)
    assert kinds == {"expired", "wrong_type", "mismatched"}


def test_short_statements_iff_statements_missing():
    for f in book_2000():
        required = STATEMENTS_REQUIRED[f.segment]
        assert required == (6 if f.segment in ("salaried_personal", "secured_home") else 12)
        assert (f.bank.months_covered < required) == ("statements" in f.labels.missing_items), f.file_id
        bank_doc = doc(f, "bank_statement_header")
        assert f"({f.bank.months_covered} months)" in bank_doc.text
        assert bank_doc.month_age == f.bank.most_recent_month_age


def _incoherence(f) -> list[str]:
    reasons = []
    if f.application.declared_monthly_income_inr / f.income.verified_monthly_income_inr >= 1.5:
        reasons.append("income_off_type")
    age = AS_OF.year - f.applicant.dob_year
    if f.application.employment_type == "salaried":
        remaining = (f.applicant.dob_year + 60 - AS_OF.year) * 12 + (dob_of(f).month - AS_OF.month)
        if f.application.tenure_months > remaining + 6:
            reasons.append("tenure_past_retirement")
    if f.application.years_in_job_or_business > age - 16:
        reasons.append("experience_impossible")
    return reasons


def test_incoherent_fields_iff_fields_coherence_missing():
    seen = set()
    for f in book_2000():
        reasons = _incoherence(f)
        assert bool(reasons) == ("fields_coherence" in f.labels.missing_items), (f.file_id, reasons)
        seen.update(reasons)
    assert seen == {"income_off_type", "tenure_past_retirement", "experience_impossible"}


def test_employer_letter_shows_the_superannuation_and_start_evidence():
    for f in book_2000():
        letter = doc(f, "employer_letter")
        if letter is None:
            continue
        m = re.search(r"since ([A-Z][a-z]{2}) (\d{4})", letter.text)
        remaining = int(re.search(r"superannuation: (\d+) months", letter.text)[1])
        joined_year = int(m[2])
        if "fields_coherence" in f.labels.missing_items and "tenure_past_retirement" in _incoherence(f):
            assert f.application.tenure_months > remaining
        elif "fields_coherence" not in f.labels.missing_items:
            assert f.application.tenure_months <= remaining + 1
            assert joined_year >= f.applicant.dob_year + 17


# ------------------------------------------------------------------------------------------------ module B


def test_salary_pattern_mismatch_shows_two_different_employers():
    mismatches = [f for f in book_2000() if f.labels.fraud_type == "salary_pattern_mismatch"]
    assert len(mismatches) >= 15
    for f in mismatches:
        slip, bank = doc(f, "salary_slip"), doc(f, "bank_statement_header")
        assert slip is not None
        employer, narration = f.application.employer_name, f.bank.salary_narration_employer
        assert employer != narration
        assert employer.lower() in slip.text.lower() and narration.lower() in bank.text.lower()
        assert employer.lower() not in bank.text.lower()
        assert narration in f.pii_inventory.org_names and employer in f.pii_inventory.org_names
    for f in book_2000():
        if f.application.employment_type == "salaried" and f.labels.fraud_type != "salary_pattern_mismatch":
            assert f.bank.salary_narration_employer == f.application.employer_name
            assert f.application.employer_name.lower() in doc(f, "bank_statement_header").text.lower()


def _pan_card(f):
    m = re.search(r"Name: (.+?)\. PAN: (\w{10})\. Date of birth: " + DATE_PAT, doc(f, "pan_card_text").text)
    return m[1], m[2].upper(), parse_date(m[3])


def test_identity_mismatch_shows_a_different_pan_name_or_birth_year_across_documents():
    for f in book_2000():
        name, pan, dob = _pan_card(f)
        differs = []
        if name.lower() != f.applicant.name.lower():
            differs.append("name")
        if pan != f.applicant.pan:
            differs.append("pan")
        if dob.year != f.applicant.dob_year:
            differs.append("dob")
        assert bool(differs) == (f.labels.fraud_type == "identity_mismatch"), (f.file_id, differs)
        if f.labels.fraud_type == "identity_mismatch":
            if "pan" in differs:  # the other documents carry the applicant's own PAN
                other = [d for d in f.documents if d.doc_type in ("salary_slip", "itr")]
                assert all(f.applicant.pan.lower() in d.text.lower() for d in other)
                assert pan in f.pii_inventory.pans
            if "name" in differs:
                assert name.title() in f.pii_inventory.person_names or name in f.pii_inventory.person_names
            if "dob" in differs:
                assert abs(dob.year - f.applicant.dob_year) >= 6


def test_synthetic_identity_files_have_a_thin_new_templated_footprint():
    synthetic = [f for f in book_2000() if f.labels.fraud_type == "synthetic_identity"]
    assert len(synthetic) >= 10
    for f in synthetic:
        signals = [
            f.identity.phone_vintage_months < 3, f.identity.email_domain_type == "disposable",
            not f.identity.bureau_history_vs_age_consistent, f.identity.address_shared_with_other_apps >= 3,
            not f.identity.pan_aadhaar_linked,
        ]
        assert sum(signals) >= 2, (f.file_id, signals)
        assert f.bureau.score is None and f.bureau.history_months <= 3
        assert f.application.years_in_job_or_business < 1.1
        assert all("Auto-generated document; no signature required." in d.text for d in f.documents)
    for f in book_2000():
        if f.labels.fraud_type != "synthetic_identity":
            assert not any("Auto-generated document" in d.text for d in f.documents)
    clean = [f for f in book_2000() if not f.labels.fraud]
    assert sum(f.identity.email_domain_type == "disposable" for f in clean) / len(clean) < 0.03
    assert sum(f.identity.phone_vintage_months < 3 for f in clean) / len(clean) < 0.05


def test_gst_bank_mismatch_shows_turnover_far_from_bank_credits():
    mismatches = [f for f in book_2000() if f.labels.fraud_type == "gst_bank_mismatch"]
    assert len(mismatches) >= 15
    for f in book_2000():
        if f.gst is None:
            continue
        ratio = f.gst.turnover_12m_inr / f.gst.bank_credits_12m_inr
        if f.labels.fraud_type == "gst_bank_mismatch":
            assert ratio >= 2.0 or ratio <= 0.5, f.file_id
        else:
            assert 0.8 <= ratio <= 1.25, f.file_id
        assert f.gst.turnover_12m_inr in amounts(doc(f, "gst_return_summary").text)
        bank_doc = doc(f, "bank_statement_header")
        if f.bank.months_covered >= 12:
            assert f.gst.bank_credits_12m_inr in amounts(bank_doc.text)
        assert f.gst.gstin in doc(f, "gst_return_summary").text


# ------------------------------------------------------------------------------------------------ module E


def test_memo_condition_mismatch_iff_tenure_over_grid_or_a_required_condition_missing():
    hits = {"tenure": 0, "condition": 0}
    for f in book_2000():
        m = f.sanction_memo
        row = grid_row_for(f.application.product, f.application.loan_amount_inr)
        assert m.ticket_band == row["band"] and m.product == f.application.product
        required = [c["text"] for c in row["required_conditions"]]
        over = m.tenure_months > row["max_tenure_months"]
        missing = [t for t in required if t not in m.conditions]
        assert (over or bool(missing)) == ("memo_condition_mismatch" in f.labels.memo_defects), f.file_id
        assert all(c in m.text for c in m.conditions)
        assert f"{m.tenure_months} months" in m.text and f"{m.rate_pct:.2f}%" in m.text
        assert f.application.tenure_months <= row["max_tenure_months"]  # the request itself is within the grid
        hits["tenure"] += over
        hits["condition"] += bool(missing)
    assert hits["tenure"] > 20 and hits["condition"] > 20


def test_apr_math_wrong_iff_stated_apr_is_off_by_half_a_point_or_is_the_plain_rate():
    plain = off = 0
    for f in book_2000():
        k = f.kfs
        true_apr = finance.apr_from_components(k.principal_inr, k.fees_inr, k.emi_inr, k.tenure_months)
        diff = abs(k.apr_stated_pct - true_apr)
        wrong = "apr_math_wrong" in f.labels.memo_defects
        assert wrong == (diff >= 0.5), (f.file_id, diff)
        if not wrong:
            assert diff <= 0.05
        else:
            if k.apr_stated_pct == round(k.rate_pct, 2) and true_apr - k.rate_pct >= 0.5:
                plain += 1
            else:
                off += 1
        assert f"{k.apr_stated_pct:.2f}%" in k.text
        assert k.fees_inr > 0 and true_apr > k.rate_pct
    assert plain >= 5 and off >= 5  # both variants occur


def test_missing_disclosure_iff_its_section_is_absent_from_the_kfs():
    heads = {d["id"]: d["heading"] for d in load_disclosures()}
    missing_seen = set()
    for f in book_2000():
        k = f.kfs
        for did, heading in heads.items():
            present = f"{heading}:" in k.text
            assert present == (did in k.disclosures_present), (f.file_id, did)
            assert present == (f"disclosure_missing:{did}" not in f.labels.memo_defects), (f.file_id, did)
            if not present:
                missing_seen.add(did)
        assert "apr" in k.disclosures_present  # the APR section is never dropped
    assert missing_seen == set(heads) - {"apr"}


# ------------------------------------------------------------------------------------------------ module F


def test_covenant_breaches_are_visible_in_reported_values_and_track_the_outcome():
    book = [f for f in book_2000() if f.segment == "msme_business"]
    rates = {o: [0, 0] for o in ("repays", "slips", "defaults")}
    for f in book:
        by_id = {c.covenant_id: c for c in f.post_disbursal.covenants}
        assert list(by_id) == ["dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current"]
        breaches = set()
        c = by_id["dscr_min_1_25"]
        assert c.required == 1.25 and isinstance(c.reported_value, float)
        if c.reported_value < 1.25:
            breaches.add(c.covenant_id)
        assert f"{c.reported_value:.2f}" in c.evidence_text
        c = by_id["stock_statement_monthly"]
        assert c.required == 6
        if c.reported_value < 6:
            breaches.add(c.covenant_id)
        assert f"{c.reported_value} of the last 6" in c.evidence_text
        c = by_id["no_unapproved_borrowing"]
        assert c.required == 0
        if c.reported_value > 0:
            breaches.add(c.covenant_id)
        assert ("without the bank's consent" in c.evidence_text) == (c.reported_value > 0)
        c = by_id["insurance_current"]
        assert c.required is True and isinstance(c.reported_value, bool)
        if not c.reported_value:
            breaches.add(c.covenant_id)
        assert ("expired on" in c.evidence_text) == (not c.reported_value)
        assert breaches == set(f.labels.covenant_breaches), f.file_id
        rates[f.labels.outcome_12m][0] += len(breaches)
        rates[f.labels.outcome_12m][1] += 1
    per_file = {o: n / max(1, k) for o, (n, k) in rates.items()}
    assert per_file["defaults"] > per_file["slips"] > per_file["repays"]


def test_repayment_months_look_like_their_outcome():
    repays = [f for f in book_2000() if f.labels.outcome_12m == "repays"]
    assert all(max(m.dpd for m in f.post_disbursal.months) <= 9 for f in repays)  # noise, never a real delinquency
    clean = sum(all(m.dpd == 0 and not m.emi_bounced and not m.partial_payment for m in f.post_disbursal.months) for f in repays)
    assert clean / len(repays) > 0.5
    defaults = [f for f in book_2000() if f.labels.outcome_12m == "defaults"]
    assert sum(max(m.dpd for m in f.post_disbursal.months) >= 30 for f in defaults) / len(defaults) > 0.85


@pytest.mark.parametrize("seg,doc_types", [
    ("salaried_personal", {"salary_slip", "bank_statement_header", "pan_card_text", "employer_letter"}),
    ("msme_business", {"itr", "gst_return_summary", "bank_statement_header", "business_registration", "pan_card_text"}),
    ("secured_home", {"bank_statement_header", "pan_card_text", "property_title", "valuation_report"}),
])
def test_each_segment_carries_its_characteristic_documents(seg, doc_types):
    files = [f for f in book_2000() if f.segment == seg and f.labels.missing_items == []]
    assert files
    for f in files:
        assert doc_types <= {d.doc_type for d in f.documents} | ({"salary_slip"} if f.application.employment_type == "salaried" else set()) | {"itr"}
