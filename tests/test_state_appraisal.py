"""Appraisal states: keys per segment, vocabularies, roles, documents, determinism (PLAN 3.7)."""

from __future__ import annotations

import json
import warnings

import pytest
from test_state_common import BARE_TOKEN, appraisal, book_400, files_where, states_400

from jevloan.canonical import canonical_json
from jevloan.data.schema import SEGMENTS
from jevloan.state import STAGES, STATE_SCHEMA, build_state, redactor_for
from jevloan.state import base as B
from jevloan.state.base import VOCABULARY
from jevloan.state.roles import MAX_DOC_WORDS, MAX_DOCUMENTS, StateBuildError, clean_text, entity_roles, truncate_words

COMMON = {"schema", "stage", "segment", "application", "bureau", "income", "obligations", "bank", "identity_signals", "entity_roles", "documents"}


def expected_keys(f) -> set[str]:
    keys = set(COMMON)
    if f.segment in ("self_employed", "msme_business"):
        keys.add("business")
        if f.gst is not None:
            keys.add("gst")
    if f.segment == "secured_home":
        keys.add("property")
    return keys


def test_public_api():
    assert STATE_SCHEMA == "jevloan.state.v1"
    assert STAGES == ("appraisal", "sanction_docs", "monitoring")
    f = book_400()[0]
    with pytest.raises(ValueError):
        build_state(f, "underwriting")


def test_each_segment_builds_with_exactly_the_plan_keys():
    seen = set()
    for f in book_400():
        s = appraisal(f)
        assert set(s) == expected_keys(f), (f.file_id, f.segment)
        assert s["schema"] == STATE_SCHEMA and s["stage"] == "appraisal" and s["segment"] == f.segment
        seen.add(f.segment)
    assert seen == set(SEGMENTS)


def test_block_key_sets_are_exactly_the_plan_fields():
    for f in book_400():
        s = appraisal(f)
        assert set(s["application"]) == {"product", "loan_amount_band", "tenure_months", "purpose", "employment_type",
                                         "years_in_job_or_business_band", "declared_monthly_income_band", "applicant_age_band", "city_tier"}
        assert set(s["bureau"]) == {"score_band", "active_loans", "max_dpd_12m_band", "enquiries_6m", "writeoffs_or_settlements", "history_length_band"}
        assert set(s["income"]) == {"verified_monthly_income_band", "volatility", "months_history", "documentation_type"}
        assert set(s["obligations"]) == {"existing_emi_band", "proposed_emi_band", "foir_pct_band", "segment_foir_limit_pct",
                                         "credit_card_utilization_band"} | (set() if f.segment == "msme_business" else {"foir_headroom_pts_band"})
        assert set(s["bank"]) == {"months_covered", "months_required", "most_recent_month_age", "salary_credits_months", "salary_narration_org_token",
                                  "avg_monthly_credits_band", "emi_bounces_6m", "cash_deposit_share_band", "min_balance_breaches_6m"}
        assert set(s["identity_signals"]) == {"pan_aadhaar_linked", "phone_vintage_band", "email_domain_type",
                                              "address_shared_with_other_apps_band", "bureau_history_consistent_with_age"}
        if "gst" in s:
            assert set(s["gst"]) == {"filings_on_time_12m", "months_filed", "gst_turnover_band_12m", "bank_credits_band_12m", "gst_to_bank_ratio_band"}
        if "business" in s:
            assert set(s["business"]) == {"dscr_band", "vintage_years_band"}
        if "property" in s:
            assert set(s["property"]) == {"property_type", "market_value_band", "ltv_pct_band", "ltv_limit_pct", "ltv_headroom_pts_band",
                                          "title_status", "legal_opinion", "valuation_spread_band"}
        assert set(s["entity_roles"]) == {"applicant", "co_applicant", "business", "property"}
        assert all(set(d) == {"doc_type", "month_age", "script", "text"} for d in s["documents"])


def test_band_values_are_inside_the_vocabularies():
    V = VOCABULARY
    for f in book_400():
        s = appraisal(f)
        assert s["application"]["loan_amount_band"] in V["amount"]
        assert s["application"]["declared_monthly_income_band"] in V["monthly"]
        assert s["application"]["years_in_job_or_business_band"] in V["vintage_years"]
        assert s["application"]["applicant_age_band"] in V["age"]
        assert s["bureau"]["score_band"] in V["bureau_score"]
        assert s["bureau"]["max_dpd_12m_band"] in V["dpd"]
        assert s["bureau"]["history_length_band"] in V["history_length"]
        assert s["income"]["verified_monthly_income_band"] in V["monthly"]
        assert s["income"]["volatility"] in V["volatility"]
        assert s["income"]["documentation_type"] in V["documentation_type"]
        o = s["obligations"]
        assert o["existing_emi_band"] in V["monthly"] and o["proposed_emi_band"] in V["monthly"]
        assert o["foir_pct_band"] in V["foir_pct"] and o["credit_card_utilization_band"] in V["card_utilization"]
        if f.segment != "msme_business":
            assert o["foir_headroom_pts_band"] in V["foir_headroom_pts"]
        assert s["bank"]["avg_monthly_credits_band"] in V["monthly"] and s["bank"]["cash_deposit_share_band"] in V["cash_deposit_share"]
        i = s["identity_signals"]
        assert i["phone_vintage_band"] in V["phone_vintage"] and i["email_domain_type"] in V["email_domain_type"]
        assert i["address_shared_with_other_apps_band"] in V["address_shared"]
        if "gst" in s:
            g = s["gst"]
            assert g["gst_turnover_band_12m"] in V["amount"] and g["bank_credits_band_12m"] in V["amount"]
            assert g["gst_to_bank_ratio_band"] in V["gst_to_bank_ratio"]
        if "business" in s:
            assert s["business"]["dscr_band"] in V["dscr"] and s["business"]["vintage_years_band"] in V["vintage_years"]
        if "property" in s:
            p = s["property"]
            assert p["market_value_band"] in V["amount"] and p["ltv_pct_band"] in V["ltv_pct"]
            assert p["title_status"] in V["title_status"] and p["legal_opinion"] in V["legal_opinion"]
            assert p["valuation_spread_band"] in V["valuation_spread"] and p["ltv_limit_pct"] in (75, 80)
            assert p["ltv_headroom_pts_band"] in V["ltv_headroom_pts"]


def test_bands_come_from_the_observed_fields():
    for f in book_400():
        s = appraisal(f)
        assert s["obligations"]["foir_pct_band"] == B.foir_band(100 * (f.obligations.existing_emi_inr + f.obligations.proposed_emi_inr) / f.income.verified_monthly_income_inr)
        assert s["obligations"]["segment_foir_limit_pct"] == {"salaried_personal": 55, "self_employed": 50, "msme_business": 80, "secured_home": 60}[f.segment]
        assert s["bureau"]["score_band"] == B.bureau_score_band(f.bureau.score)
        assert s["income"]["volatility"] == ("low" if f.income.volatility_cv < 0.25 else "moderate" if f.income.volatility_cv < 0.35 else "high")
        assert s["application"]["applicant_age_band"] == B.age_band(f.applicant.dob_year)
        if f.segment != "msme_business":
            limit = s["obligations"]["segment_foir_limit_pct"]
            foir = 100 * (f.obligations.existing_emi_inr + f.obligations.proposed_emi_inr) / f.income.verified_monthly_income_inr
            assert s["obligations"]["foir_headroom_pts_band"] == B.foir_headroom_band(limit - foir)
        if f.property is not None:
            assert s["property"]["ltv_pct_band"] == B.ltv_band(f.property.ltv)
            assert s["property"]["ltv_headroom_pts_band"] == B.ltv_headroom_band(s["property"]["ltv_limit_pct"] - f.property.ltv)
        if f.gst is not None:
            assert s["gst"]["gst_to_bank_ratio_band"] == B.gst_ratio_band(f.gst.turnover_12m_inr / f.gst.bank_credits_12m_inr)


def test_msme_capacity_is_dscr_and_the_limit_is_80():
    msme = files_where(lambda f: f.segment == "msme_business")
    assert msme
    for f in msme:
        s = appraisal(f)
        assert s["obligations"]["segment_foir_limit_pct"] == 80
        assert "foir_headroom_pts_band" not in s["obligations"]  # MSME is judged on DSCR
        assert s["business"]["dscr_band"] in VOCABULARY["dscr"]
        # DSCR is the reciprocal of FOIR here, so a FOIR under 80 percent is a DSCR of at least 1.25
        assert (s["business"]["dscr_band"] in ("<1", "1-1.25")) == (B.foir_pct(f) > 80)
        assert "gst" in s and "property" not in s


def test_bank_months_required():
    for f in book_400():
        b = appraisal(f)["bank"]
        assert b["months_required"] == (6 if f.segment in ("salaried_personal", "secured_home") else 12)
        assert b["months_covered"] == f.bank.months_covered
        assert b["salary_credits_months"] == f.bank.salary_credits_months


def test_salary_narration_token_is_none_for_non_salaried_applicants():
    for f in book_400():
        tok = appraisal(f)["bank"]["salary_narration_org_token"]
        if f.bank.salary_narration_employer is None:
            assert tok is None and f.application.employment_type != "salaried"
        else:
            assert BARE_TOKEN.fullmatch(tok)


def test_self_employed_gst_block_only_when_registered():
    for f in files_where(lambda f: f.segment == "self_employed"):
        assert ("gst" in appraisal(f)) == (f.gst is not None)
    assert files_where(lambda f: f.segment == "self_employed" and f.gst is None), "need a non-GST self-employed file"


def test_entity_roles_are_bare_tokens():
    checked = 0
    for f in book_400():
        roles = appraisal(f)["entity_roles"]
        a = roles["applicant"]
        assert a["name"] == "[APPLICANT]" and a["birth"] == "[DOB_1]"
        assert set(a) == {"name", "pan", "uid", "phone", "residence_address", "birth"} | ({"employer"} if f.application.employer_name else set())
        for key, value in a.items():
            assert BARE_TOKEN.fullmatch(value), (f.file_id, key, value)
        assert a["pan"].startswith("[PAN_") and a["uid"].startswith("[UID_") and a["phone"].startswith("[PHONE_") and a["residence_address"].startswith("[ADDR_")
        assert a["pan"] == "[PAN_1]" and a["uid"] == "[UID_1]" and a["phone"] == "[PHONE_1]" and a["residence_address"] == "[ADDR_1]"
        if f.application.employer_name:
            assert a["employer"] == "[ORG_A]"
        co = roles["co_applicant"]
        assert (co is None) == (f.co_applicant is None)
        if co:
            assert set(co) == {"name", "pan", "relation"} and co["relation"] == f.co_applicant.relation
            assert BARE_TOKEN.fullmatch(co["name"]) and BARE_TOKEN.fullmatch(co["pan"])
            assert co["name"] != "[APPLICANT]" and co["pan"] != a["pan"]
            checked += 1
        biz = roles["business"]
        assert (biz is None) == (not f.application.business_name)
        if biz:
            assert set(biz) == {"name", "pan", "address"}
            assert biz["name"] == "[ORG_A]" and BARE_TOKEN.fullmatch(biz["pan"])
            assert biz["address"] is None or (BARE_TOKEN.fullmatch(biz["address"]) and biz["address"] != a["residence_address"])
            if f.gst is None:
                assert biz["pan"] == a["pan"]  # a proprietorship files under the owner's PAN
        prop = roles["property"]
        assert (prop is None) == (f.property is None)
        if prop:
            assert BARE_TOKEN.fullmatch(prop["address"]) and prop["address"] != a["residence_address"]
    assert checked >= 20


def test_business_pan_is_the_gstin_pan_and_can_differ_from_the_applicants():
    differing = 0
    for f in files_where(lambda f: f.gst is not None):
        biz_pan = appraisal(f)["entity_roles"]["business"]["pan"]
        if f.gst.gstin[2:12] != f.applicant.pan:
            assert biz_pan != "[PAN_1]", f.file_id
            differing += 1
        else:
            assert biz_pan == "[PAN_1]", f.file_id
    assert differing >= 10


def test_business_and_property_addresses_are_present_whenever_a_document_shows_them():
    for f in book_400():
        roles = appraisal(f)["entity_roles"]
        if f.segment == "msme_business":
            assert roles["business"]["address"] is not None, f.file_id
        if f.segment == "self_employed":
            has_reg = any(d.doc_type == "business_registration" for d in f.documents)
            assert (roles["business"]["address"] is not None) == has_reg, f.file_id
        if f.segment == "secured_home":
            assert roles["property"]["address"] is not None, f.file_id


def test_documents_are_bounded_and_carry_only_the_plan_fields():
    for f in book_400():
        docs = appraisal(f)["documents"]
        assert 1 <= len(docs) <= MAX_DOCUMENTS and len(docs) == len(f.documents)
        for raw, d in zip(f.documents, docs, strict=True):
            assert d["doc_type"] == raw.doc_type and d["month_age"] == raw.month_age and d["script"] == raw.script
            assert len(d["text"].split()) <= MAX_DOC_WORDS
            assert d["text"] and d["text"] == " ".join(d["text"].split())


def test_no_document_needs_truncating():
    """The generator keeps every document under 70 words, and redaction only shortens them. If that ever stops being
    true a sentence of evidence may be cut, so say so loudly."""
    cut = []
    for f in book_400():
        r, year_map = redactor_for(f), {}
        for doc in f.documents[:MAX_DOCUMENTS]:
            full = clean_text(f, r, doc.text, year_map=year_map)
            if truncate_words(full, MAX_DOC_WORDS)[1]:
                cut.append((f.file_id, doc.doc_type))
    if cut:
        warnings.warn(f"truncation dropped text from {len(cut)} documents (evidence may be lost): {cut[:5]}", stacklevel=1)


def test_truncation_keeps_whole_sentences_and_reports_it():
    text = "One two three. Four five six. Seven eight nine ten."
    assert truncate_words(text, 70) == (text, False)
    assert truncate_words(text, 7) == ("One two three. Four five six.", True)
    assert truncate_words("a b c d e f g", 3) == ("a b c", True)  # one long sentence is cut at a word


def test_role_value_that_is_not_a_bare_token_is_refused():
    f = book_400()[0]

    class Broken:
        def redact(self, text):
            return "Deepthi"

    with pytest.raises(StateBuildError):
        entity_roles(f, Broken())


def test_states_are_deterministic():
    for f in book_400()[:60]:
        for stage in STAGES:
            first = build_state(f, stage)
            assert build_state(f, stage) == first
            assert build_state(f, stage, redactor=redactor_for(f)) == first
            assert canonical_json(first) == canonical_json(json.loads(json.dumps(first)))


def test_a_stage_built_first_does_not_change_the_tokens_of_another():
    for f in book_400()[:60]:
        r1, r2 = redactor_for(f), redactor_for(f)
        for stage in ("monitoring", "sanction_docs", "appraisal"):
            build_state(f, stage, redactor=r1)
        for stage in STAGES:
            assert build_state(f, stage, redactor=r1) == build_state(f, stage, redactor=r2)


def test_states_are_json_serialisable_and_all_values_are_plain_types():
    for s in states_400().values():
        json.dumps(s, ensure_ascii=False)
