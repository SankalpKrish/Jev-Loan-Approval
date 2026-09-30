"""Shared helpers for the A/B/C question-pack tests.

* `make_state(segment, updates)`: a clean hand-built appraisal state that matches PLAN 3.7 (bands, tokens,
  redacted document text in the shape the state builder produces). Tests change one thing and look at the answer.
* `book_rows()`: a cached slice of the synthetic book with the real appraisal states and the generator's truth,
  for tests that need realistic files.
"""

import copy
from functools import lru_cache

BOOK_N = 160
BOOK_SEED = 7

SLIP = (
    "[ORG_A] salary slip for Aug 2026. Employee: [APPLICANT], Senior Associate. PAN [PAN_1]. Gross ₹[50-75k], "
    "deductions ₹[<10k], net pay ₹[50-75k]. Credited to A/c [ACCT_1]. Received: Sep 2026."
)
ITR = (
    "ITR-3 acknowledgement for AY 2026-27, filed Aug 2026. Assessee [APPLICANT], PAN [PAN_1]. "
    "Total income ₹[10-25L] from [ORG_A]. Received: Sep 2026."
)
BANK = (
    "Statement period Mar 2026 to Aug 2026 (6 months), Nirmal Bank, A/c [ACCT_1], holder [APPLICANT]. Salary credits "
    "NEFT-[ORG_A]-SAL: Aug ₹[50-75k], Jul ₹[50-75k]; 6 salary months in period. EMI bounces 0. Received: Sep 2026."
)
UTILITY = (
    "Electricity bill from State Power Distribution Co, bill date Jul 2026. Consumer [APPLICANT]. Address: [ADDR_1]. "
    "Amount due ₹[<10k]. Received: Sep 2026."
)
RENT = (
    "Rent agreement, term Apr 2026 to Mar 2027. Registered agreement, stamp duty paid. Landlord [PERSON_2], tenant "
    "[APPLICANT]. Property: [ADDR_1]. Monthly rent ₹[10-25k]. Received: Sep 2026."
)
PASSPORT = (
    "Indian passport issued Aug 2022, expires Aug 2032. Holder [APPLICANT], born [DOB_1]. Address on last page: "
    "[ADDR_1]. Received: Sep 2026."
)
PAN_CARD = (
    "PAN card. Name: [APPLICANT]. PAN: [PAN_1]. Date of birth: [DOB_1]. Father's name: [PERSON_3]. Received: Sep 2026."
)
LETTER = (
    "[ORG_A] confirms [APPLICANT] is a permanent Senior Associate since Mar 2022, gross salary ₹[50-75k] per month. "
    "Service remaining until superannuation: 272 months. HR contact [PERSON_4], [PHONE_2], [EMAIL_2]. Received: Sep 2026."
)
AUTO_GENERATED = "Auto-generated document; no signature required."


def doc(doc_type: str, text: str, month_age: int = 0, script: str = "latin") -> dict:
    return {"doc_type": doc_type, "month_age": month_age, "script": script, "text": text}


def set_path(state: dict, path: str, value) -> None:
    node = state
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def make_state(segment: str = "salaried_personal", updates: dict | None = None, documents: list[dict] | None = None) -> dict:
    """A clean, coherent appraisal state for `segment`. `updates` maps dotted paths to new values; `documents`
    replaces the document list. Home loans are salaried unless `application.employment_type` is updated."""
    salaried = segment in ("salaried_personal", "secured_home")
    state: dict = {
        "schema": "jevloan.state.v1",
        "stage": "appraisal",
        "segment": segment,
        "application": {
            "product": {
                "salaried_personal": "personal_loan_unsecured",
                "self_employed": "business_loan_self_employed",
                "msme_business": "msme_term_loan",
                "secured_home": "home_loan",
            }[segment],
            "loan_amount_band": "3-5L",
            "tenure_months": 36,
            "purpose": "medical",
            "employment_type": "salaried" if salaried else ("business_owner" if segment == "msme_business" else "self_employed"),
            "years_in_job_or_business_band": "5-10y",
            "declared_monthly_income_band": "50-75k",
            "applicant_age_band": "35-39",
            "city_tier": 1,
        },
        "bureau": {
            "score_band": "750-799",
            "active_loans": 1,
            "max_dpd_12m_band": "0",
            "enquiries_6m": 1,
            "writeoffs_or_settlements": 0,
            "history_length_band": ">7y",
        },
        "income": {
            "verified_monthly_income_band": "50-75k",
            "volatility": "low",
            "months_history": 60,
            "documentation_type": "salary_slip" if salaried else ("gst_and_bank" if segment == "msme_business" else "itr"),
        },
        "obligations": {
            "existing_emi_band": "<10k",
            "proposed_emi_band": "10-25k",
            "foir_pct_band": "30-40",
            "segment_foir_limit_pct": {"salaried_personal": 55, "self_employed": 50, "msme_business": 80, "secured_home": 60}[segment],
            "credit_card_utilization_band": "10-30%",
        },
        "bank": {
            "months_covered": 12 if segment in ("self_employed", "msme_business") else 6,
            "months_required": 12 if segment in ("self_employed", "msme_business") else 6,
            "most_recent_month_age": 1,
            "salary_credits_months": 6 if salaried else None,
            "salary_narration_org_token": "[ORG_A]" if salaried else None,
            "avg_monthly_credits_band": "50-75k",
            "emi_bounces_6m": 0,
            "cash_deposit_share_band": "5-15%",
            "min_balance_breaches_6m": 0,
        },
        "identity_signals": {
            "pan_aadhaar_linked": True,
            "phone_vintage_band": ">3y",
            "email_domain_type": "free",
            "address_shared_with_other_apps_band": "0",
            "bureau_history_consistent_with_age": True,
        },
        "entity_roles": {
            "applicant": {
                "name": "[APPLICANT]",
                "pan": "[PAN_1]",
                "uid": "[UID_1]",
                "phone": "[PHONE_1]",
                "residence_address": "[ADDR_1]",
                "birth": "[DOB_1]",
            },
            "co_applicant": None,
            "business": None,
            "property": None,
        },
    }
    if salaried:
        state["entity_roles"]["applicant"]["employer"] = "[ORG_A]"
    if segment in ("self_employed", "msme_business"):
        state["gst"] = {
            "filings_on_time_12m": 12,
            "months_filed": 12,
            "gst_turnover_band_12m": "1-2Cr",
            "bank_credits_band_12m": "1-2Cr",
            "gst_to_bank_ratio_band": "0.8-1.2",
        }
        state["business"] = {"dscr_band": "1.5-2", "vintage_years_band": "5-10y"}
        state["entity_roles"]["business"] = {"name": "[ORG_A]", "pan": "[PAN_2]", "address": "[ADDR_2]"}
    if segment == "secured_home":
        state["property"] = {
            "property_type": "apartment",
            "market_value_band": "25-50L",
            "ltv_pct_band": "60-70",
            "ltv_limit_pct": 80,
            "title_status": "clear",
            "legal_opinion": "positive",
            "valuation_spread_band": "<5%",
        }
        state["entity_roles"]["property"] = {"address": "[ADDR_2]"}
    if documents is None:
        income = doc("salary_slip", SLIP, 1) if salaried else doc("itr", ITR, 1)
        documents = [income, doc("bank_statement_header", BANK, 1), doc("address_proof_utility_bill", UTILITY, 1), doc("pan_card_text", PAN_CARD)]
        if segment == "salaried_personal":
            documents.append(doc("employer_letter", LETTER))
    state["documents"] = copy.deepcopy(documents)
    for path, value in (updates or {}).items():
        set_path(state, path, value)
    return state


def add_headroom(state: dict, foir: str | None = None, ltv: str | None = None) -> dict:
    """The same state with the headroom bands the state builder adds (a copy). `make_state` leaves them out on purpose,
    which is the fallback path of the rules; use this for the primary path."""
    out = copy.deepcopy(state)
    if foir is not None:
        out["obligations"]["foir_headroom_pts_band"] = foir
    if ltv is not None:
        out["property"]["ltv_headroom_pts_band"] = ltv
    return out


def replace_doc(state: dict, doc_type_prefix: str, **changes) -> dict:
    """The same state with the first document whose doc_type starts with the prefix changed."""
    out = copy.deepcopy(state)
    for d in out["documents"]:
        if d["doc_type"].startswith(doc_type_prefix):
            d.update(changes)
            return out
    raise KeyError(doc_type_prefix)


def drop_doc(state: dict, doc_type: str) -> dict:
    out = copy.deepcopy(state)
    out["documents"] = [d for d in out["documents"] if d["doc_type"] != doc_type]
    return out


@lru_cache(maxsize=1)
def book_rows() -> tuple[dict, ...]:
    """The first BOOK_N files of the synthetic book with their real appraisal states and the generator's truth."""
    from jevloan.data.generator import generate_book
    from jevloan.state import build_state

    rows = []
    for loan_file in generate_book(BOOK_N, BOOK_SEED):
        rows.append(
            {
                "file_id": loan_file.file_id,
                "segment": loan_file.segment,
                "employment_type": loan_file.application.employment_type,
                "has_gst": loan_file.gst is not None,
                "subset": loan_file.meta.disparity_subset,
                "state": build_state(loan_file, "appraisal"),
                "truth": dict(loan_file.labels.question_truth),
            }
        )
    return tuple(rows)
