"""Module A: file readiness (PLAN section 3.8). Four Nouls, all segments, all "yes is good".

A "no" on any of these is a missing or unusable item, and the policy engine turns it into a deficiency notice
that names the item. So each question is one item, worded literally, and points at the state fields by name.
Evidence comes from the redacted state (PLAN 3.7): the documents' `doc_type` and `month_age`, the
`Received:` stamp each document ends with, and the tokens (`[ADDR_1]`, `[DOB_1]`...) that the redactor makes
consistent within a file so that a mismatch shows without showing the identity.

`reason_text` names the adverse finding (what a "no" means), because that is what the engine quotes.
"""

from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register

MONTHLY_INCOME_BAND_ORDER = ["<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L"]

_INCOME_PROOF = noul_wire(
    "Is the applicant's income proof document in `documents` present, with a `month_age` of 0, 1 or 2?",
    focus=(
        "The income proof is one document. It is the `salary_slip` document when `application.employment_type` "
        "is salaried, and the `itr` document when `application.employment_type` is self_employed or "
        "business_owner. Other document types are not income proof."
    ),
    refer_to=["`application.employment_type`", "`documents`"],
    true_what=(
        "The required income proof document (salary_slip for a salaried applicant, itr for any other applicant) "
        "is in `documents` and its `month_age` is 0, 1 or 2."
    ),
    true_examples=[
        "employment_type salaried, and a salary_slip document with month_age 1",
        "employment_type self_employed, and an itr document with month_age 2",
        "employment_type business_owner, and an itr document with month_age 0",
    ],
    false_what="The required income proof document is missing from `documents`, or its `month_age` is 3 or more.",
    false_not_for="A salary_slip or itr document with month_age 0, 1 or 2. Other document types do not change the answer.",
    false_examples=[
        "employment_type salaried, and documents holds a bank_statement_header, a pan_card_text and an employer_letter but no salary_slip",
        "employment_type self_employed, and an itr document with month_age 9",
        "employment_type salaried, and a salary_slip document with month_age 3",
    ],
)

_ADDRESS_PROOF = noul_wire(
    "Is the address proof document in `documents` a valid proof of the applicant's residence address?",
    focus=(
        "The address proof is the document whose `doc_type` starts with address_proof. It is valid only when all "
        "four conditions hold. (1) It is a utility bill, a registered and stamped rent agreement, or a passport "
        "whose text includes the address page. (2) A utility bill has `month_age` 0, 1 or 2. (3) A rent "
        "agreement lasts 11 months, so its `month_age` is 11 or less and its end month is the same as or later "
        "than the `Received:` month; a passport has an expiry month that is the same as or later than the "
        "`Received:` month. (4) The address token in its text, such as [ADDR_1], is the same token as "
        "`entity_roles.applicant.residence_address`. A document in a `script` other than latin is judged by the "
        "same four conditions."
    ),
    refer_to=["`documents`", "`entity_roles.applicant.residence_address`"],
    true_what=(
        "The address proof is a utility bill, a registered rent agreement or a passport with its address page; it "
        "is not out of date; and its address token is the same as `entity_roles.applicant.residence_address`."
    ),
    true_examples=[
        "a utility bill with month_age 1 and address token [ADDR_1], when residence_address is [ADDR_1]",
        "a registered rent agreement with month_age 5 and address token [ADDR_1], when residence_address is [ADDR_1]",
        "a passport with the address page, an expiry month later than the Received month, and address token [ADDR_1], when residence_address is [ADDR_1]",
    ],
    false_what="At least one of the four conditions fails: the document is the wrong kind, it is out of date, or its address token differs.",
    false_not_for=(
        "A utility bill with month_age 2, or a document in a `script` other than latin that meets all four conditions."
    ),
    false_examples=[
        "a utility bill with month_age 4",
        "a prepaid mobile recharge receipt, or an unregistered rent agreement on plain paper, or a passport photo page whose address page is missing",
        "a utility bill with address token [ADDR_2], when residence_address is [ADDR_1]",
    ],
)

_STATEMENTS = noul_wire(
    "Is `bank.months_covered` greater than or equal to `bank.months_required`?",
    focus="Compare only these two numbers. Ignore `bank.most_recent_month_age` and every other field.",
    refer_to=["`bank.months_covered`", "`bank.months_required`"],
    true_what="`bank.months_covered` is the same as `bank.months_required`, or larger.",
    true_examples=[
        "months_covered 6 with months_required 6",
        "months_covered 12 with months_required 6",
        "months_covered 12 with months_required 12",
    ],
    false_what="`bank.months_covered` is smaller than `bank.months_required`.",
    false_not_for="Two equal numbers. Equal numbers are a yes.",
    false_examples=[
        "months_covered 5 with months_required 6",
        "months_covered 9 with months_required 12",
        "months_covered 3 with months_required 6",
    ],
)

_FIELDS_COHERE = noul_wire(
    "Do the application fields agree with one another on all three checks in the focus?",
    focus=(
        "Check 1: `application.declared_monthly_income_band` is the same band as `income.verified_monthly_income_band`, "
        "or at most one band higher (band order is in `data`). Check 2: when a document with `doc_type` "
        "employer_letter says 'Service remaining until superannuation' followed by a number of months, "
        "`application.tenure_months` is at most that number of months; with no such document, check 2 passes. "
        "Check 3: the years in `application.years_in_job_or_business_band` fit inside the applicant's working "
        "life, which starts at age 16, given `application.applicant_age_band`. These pairs are too long: a years "
        "band of >10y with an age band of <20, 20-24 or 25-29, and a years band of 5-10y with an age band of <20 "
        "or 20-24. Every other pair passes."
    ),
    data={"monthly_income_band_order": MONTHLY_INCOME_BAND_ORDER},
    refer_to=[
        "`application.declared_monthly_income_band`",
        "`income.verified_monthly_income_band`",
        "`application.tenure_months`",
        "`application.years_in_job_or_business_band`",
        "`application.applicant_age_band`",
        "`documents`",
    ],
    true_what="All three checks pass.",
    true_examples=[
        "declared band 25-50k and verified band 25-50k, no employer_letter, years band 2-5y with age band 35-39",
        "declared band 50-75k and verified band 25-50k (one band higher), and an employer_letter saying 'Service remaining until superannuation: 120 months' with tenure_months 60",
        "declared band 1-2L and verified band 75k-1L (one band higher), a self_employed applicant with no employer_letter",
    ],
    false_what="At least one of the three checks fails.",
    false_not_for=(
        "A declared band that is exactly one band above the verified band (check 1 passes), or a file with no "
        "employer_letter (check 2 passes)."
    ),
    false_examples=[
        "declared band 1-2L with verified band 25-50k (three bands higher)",
        "an employer_letter saying 'Service remaining until superannuation: 20 months' with tenure_months 60",
        "years band >10y with age band 25-29",
    ],
)


def _q(qid, wire, reason, *, segments=ALL_SEGMENTS, applies=None) -> QuestionDef:
    return register(
        QuestionDef(
            qid=qid,
            module="A",
            stage="appraisal",
            qtype="noul",
            segments=frozenset(segments),
            risk_polarity="yes_is_good",
            routing_relevant=True,
            reason_text=reason,
            wire=wire,
            applies=applies,
        )
    )


_q(
    "A_income_proof_current",
    _INCOME_PROOF,
    "Income proof (salary slip, or ITR for self-employed and business applicants) is missing or 3 or more months old",
)
_q(
    "A_address_proof_valid",
    _ADDRESS_PROOF,
    "Address proof is invalid: out of date, the wrong kind of document, or its address does not match the application",
)
_q(
    "A_statements_cover_months",
    _STATEMENTS,
    "Bank statements cover fewer months than the product requires",
)
_q(
    "A_fields_cohere",
    _FIELDS_COHERE,
    "Application fields do not agree: declared income, tenure against retirement, or years of experience against age",
)
