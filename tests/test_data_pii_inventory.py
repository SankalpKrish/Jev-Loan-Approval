"""Every raw identifier embedded anywhere in a file must be in its pii_inventory."""

import re

from jevloan.data.documents import indian_group
from test_data_common import PAN_RE, ascii_digits, book_2000, digit_runs, unexplained_pii


def _all_texts(f):
    yield "memo", f.sanction_memo.text
    yield "kfs", f.kfs.text
    for d in f.documents:
        yield d.doc_type, d.text
    for c in f.post_disbursal.covenants:
        yield "covenant:" + c.covenant_id, c.evidence_text


def test_every_identifier_in_every_text_is_in_the_inventory():
    problems = []
    for f in book_2000():
        for where, text in _all_texts(f):
            problems += [f"{f.file_id} {where}: {p}" for p in unexplained_pii(text, f.pii_inventory)]
    assert not problems, problems[:10]


def test_structured_identifiers_are_in_the_inventory_with_the_applicant_first():
    for f in book_2000():
        inv, a = f.pii_inventory, f.applicant
        assert inv.person_names[0] == a.name
        if a.name_native:
            assert inv.person_names[1] == a.name_native
        assert inv.org_names[0] == (f.application.employer_name or f.application.business_name)
        assert a.pan in inv.pans and inv.pans[0] == a.pan
        assert a.aadhaar in inv.aadhaars and inv.aadhaars[0] == a.aadhaar
        assert a.phone in inv.phones and inv.phones[0] == a.phone
        assert a.email in inv.emails and inv.emails[0] == a.email
        assert f.bank.account_number in inv.account_numbers
        assert a.address.line1 in inv.address_lines and a.address.pincode in inv.pincodes
        if f.co_applicant:
            assert f.co_applicant.name in inv.person_names and f.co_applicant.pan in inv.pans
        if f.gst:
            assert f.gst.gstin[2:12] in inv.pans
        if f.bank.salary_narration_employer:
            assert f.bank.salary_narration_employer in inv.org_names


def test_inventory_formats_are_canonical_and_free_of_duplicates():
    for f in book_2000():
        inv = f.pii_inventory
        for field in ("person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines", "pincodes"):
            values = getattr(inv, field)
            assert len(values) == len(set(values)), (f.file_id, field)
        assert all(re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p) for p in inv.pans)
        assert all(re.fullmatch(r"[2-9]\d{11}", p) for p in inv.aadhaars)
        assert all(re.fullmatch(r"[6-9]\d{9}", p) for p in inv.phones)
        assert all(re.fullmatch(r"\d{11,16}", p) for p in inv.account_numbers)
        assert all(re.fullmatch(r"[1-9]\d{5}", p) for p in inv.pincodes)
        assert all(e == e.lower() for e in inv.emails)


def test_every_person_and_org_in_the_inventory_is_actually_written_somewhere():
    for f in book_2000():
        blob = "\n".join(t for _, t in _all_texts(f)).lower()
        structured = " ".join([f.applicant.name, f.applicant.name_native or "", f.application.employer_name or "",
                               f.application.business_name or "", f.co_applicant.name if f.co_applicant else "",
                               f.bank.salary_narration_employer or ""]).lower()
        canonical_only = set(f.pii_inventory.aliases.values())  # e.g. the romanised name of a native-script landlord
        for name in f.pii_inventory.person_names + f.pii_inventory.org_names:
            assert name.lower() in blob or name.lower() in structured or name in canonical_only, (f.file_id, name)


def test_documents_really_contain_pii_in_varied_formats():
    book = book_2000()
    texts = [t for f in book for _, t in _all_texts(f)]
    blob = "\n".join(texts)
    assert re.search(r"\+91 \d{5} \d{5}", blob) and re.search(r"(?<!\d)\d{5}-\d{5}(?!\d)", blob) and re.search(r"(?<!\d)\d{10}(?!\d)", blob)
    assert re.search(r"(?<!\d)[2-9]\d{3} \d{4} \d{4}(?!\d)", blob)  # spaced Aadhaar
    assert re.search(r"XXXX XXXX \d{4}", blob)  # masked Aadhaar
    assert re.search(r"Rs\. \d{1,2},\d{2},\d{3}(?!\d)", blob)  # Indian grouping, the form named in the brief
    assert "₹" in blob and "INR " in blob and re.search(r"Rs \d[\d,]*/-", blob)
    assert re.search(r"[A-Z]{5}\d{4}[A-Z]", blob) and PAN_RE.search(blob.lower())  # upper and lower case PANs
    assert re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", blob)
    assert re.search(r"\b(Mr\.|Mrs\.|Ms\.|Shri|Smt\.) [A-Z]", blob)
    assert re.search(r"\b[A-Z]\. [A-Z][a-z]+", blob)  # initials
    assert any(re.search(r"[஀-௿]", t) for t in texts) and any(re.search(r"[ঀ-৿]", t) for t in texts)
    assert any(re.search(r"[ऀ-ॿ]", t) for t in texts)
    # native-script digits appear in some native documents, and their canonical ASCII form is in the inventory
    native_digit_files = [f for f in book if any(re.search(r"[௦-௯০-৯०-९]", d.text) for d in f.documents)]
    for f in native_digit_files:
        pins = re.findall(r"[௦-௯০-৯०-९]{6}", "\n".join(d.text for d in f.documents))
        assert pins and all(ascii_digits(p) in f.pii_inventory.pincodes for p in pins)


def test_no_unexplained_long_digit_runs_even_after_gate_style_compaction():
    """A gate that joins digit runs across single separators must find only known identifiers: dates, amounts and
    pincodes may never glue together into something that looks like an account number or phone."""
    for f in book_2000():
        for where, text in _all_texts(f):
            for run in digit_runs(text):
                assert len(run) < 9 or run in f.pii_inventory.aadhaars + f.pii_inventory.account_numbers or any(
                    run.endswith(p) for p in f.pii_inventory.phones
                ), (f.file_id, where, run)


def test_indian_grouping():
    assert indian_group(145_000) == "1,45,000"
    assert indian_group(999) == "999" and indian_group(1_000) == "1,000"
    assert indian_group(12_345_678) == "1,23,45,678"
    assert indian_group(50_000_000) == "5,00,00,000"


def test_every_extra_identifier_in_the_inventory_is_written_in_some_text():
    """Nothing is recorded that was not written (structured application fields aside), so the redactor's known
    entities and the leak scan are exact."""
    for f in book_2000():
        inv, a = f.pii_inventory, f.applicant
        blob = "\n".join(t for _, t in _all_texts(f))
        runs = set(digit_runs(blob))
        upper = blob.upper()
        structured_pans = {a.pan} | ({f.co_applicant.pan} if f.co_applicant else set()) | ({f.gst.gstin[2:12]} if f.gst else set())
        for pan in inv.pans:
            assert pan in structured_pans or pan in upper, (f.file_id, pan)
        for ph in inv.phones:
            assert ph == a.phone or any(r.endswith(ph) for r in runs), (f.file_id, ph)
        for aad in inv.aadhaars:
            assert aad == a.aadhaar or aad in runs, (f.file_id, aad)
        for acct in inv.account_numbers:
            assert acct == f.bank.account_number or acct in runs, (f.file_id, acct)
        for mail in inv.emails:
            assert mail == a.email or mail in blob, (f.file_id, mail)
        for line in inv.address_lines:
            # an alias's canonical form (the Latin line1 of a native-script address) need not be written verbatim
            assert line == a.address.line1 or line in inv.aliases.values() or line.lower() in blob.lower(), (f.file_id, line)
        for pin in inv.pincodes:
            assert pin == a.address.pincode or pin in runs, (f.file_id, pin)
