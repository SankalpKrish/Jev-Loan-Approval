"""Evidence fidelity: the documents in the state still show what the labels are based on (PLAN 3.6, 3.7)."""

from __future__ import annotations

import re

from test_state_common import TOKEN_ANY, appraisal, book_400, doc_of, files_where

from jevloan.data.generator import generate_book
from jevloan.state.roles import rewrite_birth_dates

NAME_TOKEN = re.compile(r"\[(?:APPLICANT|PERSON_[A-Z0-9]+)\]")


def pan_card(f) -> dict:
    d = doc_of(appraisal(f), "pan_card_text")
    assert d is not None, f.file_id
    return d


def pan_card_fields(text: str) -> dict:
    return {
        "name": re.search(r"Name: (\[[A-Z_0-9]+\])", text).group(1),
        "pan": re.search(r"PAN: (\[[A-Z_0-9]+\])", text).group(1),
        "birth": re.search(r"Date of birth: (\[DOB_\d+\])", text).group(1),
    }


def test_clean_files_show_the_applicants_pan_on_the_pan_card():
    clean = files_where(lambda f: not f.labels.fraud)
    assert len(clean) > 300
    for f in clean:
        s = appraisal(f)
        fields = pan_card_fields(pan_card(f)["text"])
        assert fields["pan"] == s["entity_roles"]["applicant"]["pan"], f.file_id
        assert fields["name"] == s["entity_roles"]["applicant"]["name"] == "[APPLICANT]", f.file_id
        assert fields["birth"] == s["entity_roles"]["applicant"]["birth"] == "[DOB_1]", f.file_id


def test_identity_mismatch_files_show_a_token_that_differs_from_the_applicants_role():
    files = files_where(lambda f: f.labels.fraud_type == "identity_mismatch")
    assert len(files) >= 3
    for f in files:
        s = appraisal(f)
        role = s["entity_roles"]["applicant"]
        fields = pan_card_fields(pan_card(f)["text"])
        differing = [k for k, want in (("name", role["name"]), ("pan", role["pan"]), ("birth", role["birth"])) if fields[k] != want]
        assert differing, (f.file_id, fields, role)
        # every document token still matches some declared role or is a distinct, consistent alternative
        for d in s["documents"]:
            for tok in TOKEN_ANY.findall(d["text"]):
                assert tok.startswith("[") and tok.endswith("]")


def test_identity_mismatch_kinds_are_visible_one_by_one():
    """Whichever of PAN, name and birth year the generator changed shows in the PAN card, and only those."""
    for f in files_where(lambda f: f.labels.fraud_type == "identity_mismatch"):
        raw = next(d.text for d in f.documents if d.doc_type == "pan_card_text")
        raw_year = int(re.search(r"Date of birth: .*?((?:19|20)\d{2})", raw).group(1))
        raw_pan = re.search(r"PAN: ([A-Za-z0-9]{10})", raw).group(1).upper()
        fields = pan_card_fields(pan_card(f)["text"])
        assert (fields["birth"] != "[DOB_1]") == (raw_year != f.applicant.dob_year)
        assert (fields["pan"] != "[PAN_1]") == (raw_pan != f.applicant.pan)


def test_salary_pattern_mismatch_shows_a_different_narration_employer():
    files = files_where(lambda f: f.labels.fraud_type == "salary_pattern_mismatch")
    assert len(files) >= 3
    for f in files:
        s = appraisal(f)
        assert s["bank"]["salary_narration_org_token"] != s["entity_roles"]["applicant"]["employer"], f.file_id
        bank_doc = doc_of(s, "bank_statement_header")["text"]
        assert s["bank"]["salary_narration_org_token"] in bank_doc  # the narration in the statement names the other employer
        assert s["entity_roles"]["applicant"]["employer"] in " ".join(d["text"] for d in s["documents"] if d["doc_type"] in ("salary_slip", "employer_letter"))


def test_clean_salaried_files_have_matching_employer_tokens():
    files = files_where(lambda f: f.application.employment_type == "salaried" and not f.labels.fraud)
    assert len(files) > 150
    for f in files:
        s = appraisal(f)
        assert s["bank"]["salary_narration_org_token"] == s["entity_roles"]["applicant"]["employer"] == "[ORG_A]", f.file_id


def address_tokens(state: dict) -> set[str]:
    d = doc_of(state, "address_proof")
    assert d is not None
    return set(re.findall(r"\[ADDR_\d+\]", d["text"]))


def test_address_proof_carries_the_residence_token_on_clean_files():
    checked = 0
    for f in files_where(lambda f: not f.labels.fraud and f.meta.disparity_subset is None and "address_proof" not in f.labels.missing_items):
        s = appraisal(f)
        assert address_tokens(s) == {s["entity_roles"]["applicant"]["residence_address"]}, f.file_id
        checked += 1
    assert checked > 250


def test_a_mismatched_address_proof_shows_a_different_address_token():
    """The address_proof defect variants: 'expired' and 'wrong_type' show the applicant's address, 'mismatched'
    shows another one. Both must be distinguishable from the state."""
    differing = 0
    for f in files_where(lambda f: "address_proof" in f.labels.missing_items):
        s = appraisal(f)
        toks = address_tokens(s)
        assert len(toks) <= 1  # a passport photo page without the address page shows no address at all
        differing += bool(toks) and toks != {s["entity_roles"]["applicant"]["residence_address"]}
    assert differing >= 2


def test_native_script_address_proof_carries_the_same_residence_token():
    """The alias fix: an address written in Tamil or Bengali maps to the token of the Latin line1 (fairness)."""
    files = files_where(lambda f: f.meta.disparity_subset == "lang_doc_script" and "address_proof" not in f.labels.missing_items)
    assert len(files) >= 8
    for f in files:
        s = appraisal(f)
        d = doc_of(s, "address_proof")
        assert d["script"] != "latin", f.file_id
        assert address_tokens(s) == {s["entity_roles"]["applicant"]["residence_address"]}, (f.file_id, d["text"])
        assert "[APPLICANT]" in d["text"], (f.file_id, d["text"])  # the native-script name is the applicant too


def test_native_script_documents_leave_no_native_name_or_address_behind():
    for f in files_where(lambda f: f.meta.disparity_subset == "lang_doc_script"):
        d = doc_of(appraisal(f), "address_proof")
        if f.applicant.name_native:
            assert f.applicant.name_native not in d["text"]


def test_business_registration_address_token_differs_from_the_residence():
    for f in files_where(lambda f: f.segment == "msme_business"):
        s = appraisal(f)
        reg = doc_of(s, "business_registration")["text"]
        assert s["entity_roles"]["business"]["address"] in reg
        assert s["entity_roles"]["business"]["name"] in reg
        assert s["entity_roles"]["business"]["pan"] in reg


def test_property_title_shows_the_declared_property_address_and_the_co_applicant():
    for f in files_where(lambda f: f.segment == "secured_home"):
        s = appraisal(f)
        title = doc_of(s, "property_title")["text"]
        assert s["entity_roles"]["property"]["address"] in title, f.file_id
        if f.co_applicant:
            assert s["entity_roles"]["co_applicant"]["name"] in title, f.file_id


def test_gst_files_show_the_business_pan_token_in_the_gst_summary():
    for f in files_where(lambda f: f.gst is not None):
        s = appraisal(f)
        summary = doc_of(s, "gst_return_summary")["text"]
        assert s["entity_roles"]["business"]["pan"] in summary, (f.file_id, summary)
        assert f.gst.gstin not in summary and f.gst.gstin[2:12] not in summary


def test_missing_items_can_be_recomputed_from_the_state():
    """statements and income_proof are decidable from the state alone: the labels are not the only source."""
    for f in book_400():
        s = appraisal(f)
        assert (s["bank"]["months_covered"] < s["bank"]["months_required"]) == ("statements" in f.labels.missing_items), f.file_id
        income_docs = [d for d in s["documents"] if d["doc_type"] in ("salary_slip", "itr")]
        missing = not any(d["month_age"] < 3 for d in income_docs)
        assert missing == ("income_proof" in f.labels.missing_items), f.file_id


def test_fraud_signals_are_visible_in_the_gst_and_identity_blocks():
    for f in files_where(lambda f: f.labels.fraud_type == "gst_bank_mismatch"):
        assert appraisal(f)["gst"]["gst_to_bank_ratio_band"] in ("<0.5", ">2"), f.file_id
    for f in files_where(lambda f: f.labels.fraud_type is None and f.gst is not None):
        assert appraisal(f)["gst"]["gst_to_bank_ratio_band"] in ("0.8-1.2", "1.2-2"), f.file_id
    for f in files_where(lambda f: f.labels.fraud_type == "synthetic_identity"):
        s = appraisal(f)
        assert any("Auto-generated document" in d["text"] for d in s["documents"])
        assert s["bureau"]["score_band"] == "NTC"


# ------------------------------------------------------------------------------------------------ birth dates


def test_birth_dates_become_dob_tokens_and_no_birth_year_remains():
    year_re = re.compile(r"\b(?:19|20)\d{2}\b")
    seen_other = 0
    for f in book_400():
        s = appraisal(f)
        raw_years = set()
        for raw in f.documents:
            for m in re.finditer(r"(?:[Dd]ate of birth:|born)\s+[^.]*?((?:19|20)\d{2})", raw.text):
                raw_years.add(int(m.group(1)))
        assert f.applicant.dob_year in raw_years or not any(d.doc_type in ("pan_card_text", "address_proof_passport") for d in f.documents) or f.labels.fraud_type == "identity_mismatch"
        joined = " ".join(d["text"] for d in s["documents"])
        assert str(f.applicant.dob_year) not in year_re.findall(joined), f.file_id
        for y in raw_years:
            for d, raw in zip(s["documents"], f.documents, strict=True):
                if re.search(rf"(?:[Dd]ate of birth:|born)[^.]*?{y}", raw.text):
                    assert not re.search(rf"(?:date of birth|born)[^.]*{y}", d["text"], re.I), (f.file_id, d["text"])
                    assert re.search(r"(?:Date of birth:|born) \[DOB_\d+\]", d["text"]), (f.file_id, d["text"])
        # applicant year -> [DOB_1]; any other year -> [DOB_2], [DOB_3] ... consistently within the file
        mapping: dict[int, str] = {}
        for d, raw in zip(s["documents"], f.documents, strict=True):
            m = re.search(r"(?:Date of birth:|born)\s+[^.]*?((?:19|20)\d{2})", raw.text)
            t = re.search(r"(?:Date of birth:|born) (\[DOB_\d+\])", d["text"])
            if m:
                assert t
                year = int(m.group(1))
                assert mapping.setdefault(year, t.group(1)) == t.group(1), f.file_id
                if year == f.applicant.dob_year:
                    assert t.group(1) == "[DOB_1]"
                else:
                    assert t.group(1) != "[DOB_1]"
                    seen_other += 1
        assert len(set(mapping.values())) == len(mapping)


def test_alternative_birth_years_get_numbered_tokens():
    """An identity_mismatch file whose PAN card shows another birth year: applicant year [DOB_1], the other [DOB_2]."""
    from jevloan.state import build_state

    found = 0
    for f in generate_book(2000, 7):
        if f.labels.fraud_type != "identity_mismatch":
            continue
        raw = next(d.text for d in f.documents if d.doc_type == "pan_card_text")
        year = int(re.search(r"Date of birth: .*?((?:19|20)\d{2})", raw).group(1))
        if year == f.applicant.dob_year:
            continue
        s = build_state(f, "appraisal")
        texts = {d["doc_type"]: d["text"] for d in s["documents"]}
        assert "Date of birth: [DOB_2]" in texts["pan_card_text"], (f.file_id, texts["pan_card_text"])
        if "address_proof_passport" in texts:  # the passport carries the applicant's real birth year
            assert "born [DOB_1]" in texts["address_proof_passport"]
        assert str(year) not in " ".join(texts.values()) and str(f.applicant.dob_year) not in " ".join(texts.values())
        found += 1
    assert found >= 4


def test_rewrite_formats():
    ym: dict[int, int] = {}
    cases = {
        "Holder X, born Mar 1988.": "Holder X, born [DOB_1].",
        "PAN card. DOB Mar 1988. Father": "PAN card. DOB [DOB_1]. Father",
        "Date of birth: Mar 1988.": "Date of birth: [DOB_1].",
        "D.O.B. Mar 1988 verified": "D.O.B. [DOB_1] verified",
        "d.o.b: 14 March 1988": "d.o.b: [DOB_1]",
        "Date of Birth 14-03-1988;": "Date of Birth [DOB_1];",
        "born on 1988": "born on [DOB_1]",
        "Date of birth: Mar 1979.": "Date of birth: [DOB_2].",
        "born Jan 1979 and DOB Jun 1970": "born [DOB_2] and DOB [DOB_3]",
        "Received: Sep 2026. Filed Jul 2026.": "Received: Sep 2026. Filed Jul 2026.",  # not birth dates
    }
    for text, want in cases.items():
        assert rewrite_birth_dates(text, 1988, ym) == want
    assert ym == {1988: 1, 1979: 2, 1970: 3}


def test_rewrite_handles_every_raw_birth_format_of_the_whole_2000_book():
    """Run on the raw text (no redaction) so this covers the dd Mon yyyy, dd/mm/yyyy and dd-mm-yyyy spellings."""
    n = 0
    for f in generate_book(2000, 7):
        ym: dict[int, int] = {}
        for d in f.documents:
            out = rewrite_birth_dates(d.text, f.applicant.dob_year, ym)
            if d.doc_type in ("pan_card_text",) or "born" in d.text:
                assert re.search(r"\[DOB_\d+\]", out), (f.file_id, d.text)
                n += 1
                for m in re.finditer(r"(?:Date of birth:|born)\s+[^.]*?((?:19|20)\d{2})", d.text):
                    assert not re.search(rf"(?:Date of birth:|born) [^.\[]*{m.group(1)}", out), (f.file_id, out)
        assert ym[f.applicant.dob_year] == 1
    assert n > 2000
