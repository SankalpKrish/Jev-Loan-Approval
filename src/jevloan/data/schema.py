"""The loan-file schema (PLAN section 3.6), book I/O, and the question ids that apply to each file (section 3.8).

Every model forbids unknown fields, so a typo in a field name fails loudly. Where the contract is silent we say
what we chose:

* ``foir`` and ``ltv`` style numbers are percentages; ``volatility_cv`` and ``cash_deposit_share`` are fractions.
* ``Covenant.required`` is the *threshold* (1.25 for DSCR, 6 months, 0 unapproved facilities, True for insurance)
  and ``reported_value`` is what the borrower reported, so a breach is a comparison of the two; the direction is
  in the covenant's ``evidence_text``.
* ``Meta.true_bureau_score`` exists only so that files in the ``pincode_bureau_thin`` disparity subset (whose
  reported bureau score is hidden) keep labels computed from their real score. Nothing downstream of the state
  builder may read it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jevloan.config import load_yaml_versioned, resolve_path

GRID_PATH = "config/policy/sanction_grid_v1.yaml"
KFS_PATH = "config/policy/kfs_disclosures_v1.yaml"

Segment = Literal["salaried_personal", "self_employed", "msme_business", "secured_home"]
SEGMENTS: tuple[str, ...] = ("salaried_personal", "self_employed", "msme_business", "secured_home")
Product = Literal["personal_loan_unsecured", "business_loan_self_employed", "msme_term_loan", "home_loan"]
PRODUCT_BY_SEGMENT = {
    "salaried_personal": "personal_loan_unsecured",
    "self_employed": "business_loan_self_employed",
    "msme_business": "msme_term_loan",
    "secured_home": "home_loan",
}
Language = Literal["en", "hi", "ta", "bn", "mr", "te"]
PincodeCluster = Literal["PC1", "PC2", "PC3", "PC4", "PC5", "PC6", "PC7", "PC8"]
DocType = Literal[
    "salary_slip", "form16", "itr", "bank_statement_header", "address_proof_utility_bill",
    "address_proof_rent_agreement", "address_proof_passport", "pan_card_text", "employer_letter",
    "gst_return_summary", "business_registration", "property_title", "valuation_report",
]
Script = Literal["latin", "devanagari", "tamil", "bengali"]
FraudType = Literal["identity_mismatch", "salary_pattern_mismatch", "gst_bank_mismatch", "synthetic_identity"]
MissingItem = Literal["income_proof", "address_proof", "statements", "fields_coherence"]
Outcome12m = Literal["repays", "slips", "defaults"]
Weakness = Literal[
    "income_documentation", "repayment_history", "debt_burden", "employment_or_business_stability", "collateral",
    "bureau_thin_file",
]
DisparitySubset = Literal["lang_doc_script", "gender_income_proxy", "pincode_bureau_thin"]
MSME_COVENANT_IDS: tuple[str, ...] = (
    "dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current",
)
EWS_IDS: tuple[str, ...] = ("F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_partial_payments", "F_ews_balance_stress")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(_M):
    line1: str
    city: str
    pincode: str
    state: str


class Applicant(_M):
    name: str
    name_native: str | None = None
    gender: Literal["F", "M", "X"]
    dob_year: int
    pan: str
    aadhaar: str
    phone: str
    email: str
    address: Address
    preferred_language: Language


class CoApplicant(_M):
    name: str
    pan: str
    relation: str


class Demographics(_M):
    gender: Literal["F", "M", "X"]
    pincode_cluster: PincodeCluster
    language: Language


class Application(_M):
    product: Product
    loan_amount_inr: int
    tenure_months: int
    purpose: str
    employment_type: Literal["salaried", "self_employed", "business_owner"]
    employer_name: str | None = None
    business_name: str | None = None
    years_in_job_or_business: float
    declared_monthly_income_inr: int
    city_tier: int


class Bureau(_M):
    score: int | None = None  # None = new to credit (NTC)
    active_loans: int
    max_dpd_12m: int
    enquiries_6m: int
    writeoffs_or_settlements: int
    history_months: int


class Income(_M):
    verified_monthly_income_inr: int  # MSME: monthly cash accruals (DSCR = accruals / debt service)
    volatility_cv: float
    months_history: int
    documentation_type: Literal["salary_slip", "itr", "gst_and_bank", "informal_declared"]


class Obligations(_M):
    existing_emi_inr: int
    proposed_emi_inr: int
    credit_card_utilization: float


class Bank(_M):
    account_number: str
    months_covered: int
    most_recent_month_age: int
    salary_credits_months: int | None = None
    salary_narration_employer: str | None = None
    avg_monthly_credits_inr: int
    emi_bounces_6m: int
    cash_deposit_share: float
    min_balance_breaches_6m: int


class Gst(_M):
    gstin: str
    filings_on_time_12m: int
    turnover_12m_inr: int
    bank_credits_12m_inr: int
    months_filed: int


class Property(_M):
    market_value_inr: int
    valuation_2_inr: int
    property_type: str
    title_status: Literal["clear", "disputed", "pending_mutation"]
    legal_opinion: Literal["positive", "adverse", "pending"]
    ltv: float  # percent, loan / min(market_value, valuation_2)


class Identity(_M):
    pan_aadhaar_linked: bool
    phone_vintage_months: int
    email_domain_type: Literal["corporate", "free", "disposable"]
    address_shared_with_other_apps: int
    bureau_history_vs_age_consistent: bool


class Document(_M):
    doc_id: str
    doc_type: DocType
    month_age: int
    language: Language
    script: Script
    text: str


class SanctionMemo(_M):
    text: str
    product: Product
    ticket_band: str
    tenure_months: int
    conditions: list[str]
    rate_pct: float


class Kfs(_M):
    text: str
    principal_inr: int
    fees_inr: int
    rate_pct: float
    emi_inr: int
    tenure_months: int
    apr_stated_pct: float
    disclosures_present: list[str]


class RepaymentMonth(_M):
    m: int
    dpd: int
    emi_bounced: bool
    partial_payment: bool
    avg_balance_inr: int


class Covenant(_M):
    covenant_id: str
    required: float | int | bool
    reported_value: float | int | bool
    evidence_text: str


class PostDisbursal(_M):
    months: list[RepaymentMonth]
    covenants: list[Covenant] = Field(default_factory=list)  # msme_business only


class Labels(_M):
    sanctionable: bool = False
    fraud: bool = False
    fraud_type: FraudType | None = None
    missing_items: list[MissingItem] = Field(default_factory=list)
    outcome_12m: Outcome12m = "repays"
    risk_pd: float = 0.0
    primary_weakness: Weakness = "debt_burden"
    closeness_level: int = 0
    memo_defects: list[str] = Field(default_factory=list)  # memo_condition_mismatch | apr_math_wrong | disclosure_missing:<name>
    ews_truth: dict[str, bool] = Field(default_factory=dict)
    covenant_breaches: list[str] = Field(default_factory=list)
    question_truth: dict[str, bool | int] = Field(default_factory=dict)


class Meta(_M):
    generator_version: str
    seed: int
    disparity_subset: DisparitySubset | None = None
    true_bureau_score: int | None = None  # see module docstring; only set for pincode_bureau_thin files


class PIIInventory(_M):
    """Every raw identifier embedded anywhere in the file (ground truth for the leak scan and for the redactor's
    known entities). Person names are canonical (title case) with the applicant first; phones, Aadhaar and account
    numbers are bare digits (phones the 10-digit number), PANs upper-case, emails lower-case, address lines as
    written, pincodes ASCII digits.

    ``aliases`` maps another written form of the *same* real-world value to its canonical string: a one-line
    address or an upper-case line1 to the address's line1, a native-script address to the Latin line1 of the address
    it renders, a native-script name or an initial form to the romanised full name. Both the key and the value are
    also in the category list (so a leak scan still searches for both), and the value is never itself a key. A value
    that is deliberately different, such as a fraud file's second PAN, different name or different employer, is
    never an alias: those differences are the evidence. Other formats of one value (a phone printed as
    ``+91 98765 43210``, a spaced Aadhaar, a lower-case PAN, an upper-case name) are covered by the canonical form
    through normalisation and are not listed separately."""

    person_names: list[str] = Field(default_factory=list)
    org_names: list[str] = Field(default_factory=list)
    pans: list[str] = Field(default_factory=list)
    aadhaars: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    account_numbers: list[str] = Field(default_factory=list)
    address_lines: list[str] = Field(default_factory=list)
    pincodes: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)


class LoanFile(_M):
    file_id: str
    segment: Segment
    applicant: Applicant
    co_applicant: CoApplicant | None = None
    demographics: Demographics
    application: Application
    bureau: Bureau
    income: Income
    obligations: Obligations
    bank: Bank
    gst: Gst | None = None
    property: Property | None = None
    identity: Identity
    documents: list[Document]
    sanction_memo: SanctionMemo
    kfs: Kfs
    post_disbursal: PostDisbursal
    labels: Labels
    meta: Meta
    pii_inventory: PIIInventory


# ------------------------------------------------------------------------------------------------ book I/O


def write_book(files: Iterable[LoanFile], path: str | Path) -> int:
    """Write one JSON object per line; returns the number of files written. Parent directories are created."""
    out = resolve_path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for f in files:
            fh.write(f.model_dump_json())
            fh.write("\n")
            n += 1
    return n


def load_book(path: str | Path) -> Iterator[LoanFile]:
    """Stream the files of a book written by :func:`write_book`."""
    with resolve_path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield LoanFile.model_validate_json(line)


# ------------------------------------------------------------------------------------------------ grid and disclosures


@lru_cache(maxsize=1)
def load_grid() -> dict:
    """The sanction grid (config/policy/sanction_grid_v1.yaml) as a dict: ``{version, sha256, rows: [...]}``."""
    data, version, sha = load_yaml_versioned(GRID_PATH)
    return {"version": version, "sha256": sha, "rows": data["rows"], "bureau_band_vocabulary": data["bureau_band_vocabulary"]}


def grid_row_for(product: str, amount_inr: int | float) -> dict:
    """The grid row for a product and loan amount. A band contains its upper bound (5,00,000 is in ``<=5L``)."""
    for row in load_grid()["rows"]:
        if row["product"] != product:
            continue
        lo, hi = row["min_amount_inr"], row["max_amount_inr"]
        if amount_inr > lo and (hi is None or amount_inr <= hi):
            return row
    raise KeyError(f"no grid row for {product} at {amount_inr}")


@lru_cache(maxsize=1)
def load_disclosures() -> list[dict]:
    """The KFS disclosures (config/policy/kfs_disclosures_v1.yaml): a list of ``{id, heading, text}``."""
    data, _, _ = load_yaml_versioned(KFS_PATH)
    return data["disclosures"]


def disclosure_ids() -> list[str]:
    return [d["id"] for d in load_disclosures()]


# ------------------------------------------------------------------------------------------------ question ids

A_QIDS = ("A_income_proof_current", "A_address_proof_valid", "A_statements_cover_months", "A_fields_cohere")
_D_DOUBT = (
    "D_doubt_income_documentation", "D_doubt_repayment_history", "D_doubt_debt_burden", "D_doubt_stability",
    "D_doubt_thin_file",
)
_C_ALL = ("C_willingness", "C_recent_delinquency", "C_capacity", "C_foir_within_limit", "C_income_stable")


def is_salaried_applicant(file: LoanFile) -> bool:
    return file.application.employment_type == "salaried"


def qids_for(segment: str, *, salaried: bool, has_gst: bool) -> list[str]:
    """The section 3.8 question ids that apply to a file with these attributes (order: module A..F)."""
    q = list(A_QIDS)
    q += ["B_identity_coheres"]
    if (segment == "salaried_personal") or (segment == "secured_home" and salaried):
        q += ["B_salary_matches_employer"]
    if segment == "msme_business" or (segment == "self_employed" and has_gst):
        q += ["B_gst_bank_consistent"]
    q += ["B_synthetic_identity_signals"]
    q += list(_C_ALL)
    if segment == "secured_home":
        q += ["C_collateral_adequacy", "C_collateral_title_clear"]
    q += ["D_closeness"]
    q += list(_D_DOUBT)
    if segment == "secured_home":
        q += ["D_doubt_collateral"]
    q += ["E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete"]
    q += [f"E_disclosure_{d}" for d in disclosure_ids()]
    q += list(EWS_IDS)
    if segment == "msme_business":
        q += [f"F_covenant_{c}" for c in MSME_COVENANT_IDS]
    return q


def applicable_qids(file: LoanFile) -> list[str]:
    """The question ids whose truth the generator writes into ``labels.question_truth`` for this file."""
    return qids_for(file.segment, salaried=is_salaried_applicant(file), has_gst=file.gst is not None)


QUESTION_TYPES: dict[str, Literal["noul", "score"]] = {
    "C_willingness": "score", "C_capacity": "score", "C_collateral_adequacy": "score", "D_closeness": "score",
}


def question_type(qid: str) -> Literal["noul", "score"]:
    return QUESTION_TYPES.get(qid, "noul")
