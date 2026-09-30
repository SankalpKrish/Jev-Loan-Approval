"""Module B: fraud screen (PLAN section 3.8). Four Nouls.

Every comparison is between tokens or bands that the state already has. The redactor gives one token to one
real value within a file (`[PAN_1]`, `[ORG_A]`, `[ADDR_1]`, `[DOB_1]`), so a document that shows a different
token from `entity_roles` is a mismatch, and Jev never sees an identity. A firm PAN in `entity_roles.business.pan`
that differs from the applicant's PAN is normal, and is not an identity mismatch.

Design notes (see docs/RUBRICS.md):
* `B_identity_coheres` covers two things because its truth (PLAN 3.8) is "fraud_type is neither
  identity_mismatch nor synthetic_identity": the PAN card tokens against `entity_roles.applicant`, and the
  auto-generated document ending that every synthetic-identity file carries.
* A `bureau.score_band` of NTC is not counted as a synthetic-identity signal. About 7% of genuine files are
  new to credit with a short history, so it does not separate the two.
"""

from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register

_AUTO_GENERATED = "Auto-generated document; no signature required."

_IDENTITY = noul_wire(
    "Is the identity of this file consistent, so that both checks in the focus pass?",
    focus=(
        "Check 1: on the `pan_card_text` document in `documents`, the PAN token, the name token after 'Name:' and "
        "every `[DOB_n]` token are the same tokens as `entity_roles.applicant.pan`, `entity_roles.applicant.name` "
        "and `entity_roles.applicant.birth`. Check 2: no document in `documents` ends with "
        f"'{_AUTO_GENERATED}'. A firm PAN in `entity_roles.business.pan` that differs from the applicant's PAN is "
        "normal and is ignored."
    ),
    refer_to=[
        "`documents`",
        "`entity_roles.applicant.pan`",
        "`entity_roles.applicant.name`",
        "`entity_roles.applicant.birth`",
    ],
    true_what=(
        "Both checks pass: the pan_card_text tokens match `entity_roles.applicant`, and no document ends with the "
        "auto-generated sentence."
    ),
    true_examples=[
        "pan_card_text shows [PAN_1], [APPLICANT] and [DOB_1], and entity_roles.applicant has pan [PAN_1], name [APPLICANT], birth [DOB_1]",
        "a business owner whose entity_roles.business.pan is [PAN_2] while the pan_card_text still shows the applicant's [PAN_1]",
        "every document ends with its Received stamp",
    ],
    false_what=(
        "Check 1 or check 2 fails: pan_card_text shows a PAN, name or [DOB_n] token that differs from "
        "`entity_roles.applicant`, or a document ends with the auto-generated sentence."
    ),
    false_not_for=(
        "A firm PAN in `entity_roles.business.pan` that differs from the applicant's PAN, or a pan_card_text whose "
        "PAN, name and birth tokens all match."
    ),
    false_examples=[
        "pan_card_text shows [PAN_2] while entity_roles.applicant.pan is [PAN_1]",
        "pan_card_text shows birth token [DOB_2] while entity_roles.applicant.birth is [DOB_1]",
        f"a document ends with '{_AUTO_GENERATED}'",
    ],
)

_SALARY = noul_wire(
    "Is the employer token on the salary slip the same as `entity_roles.applicant.employer` and the same as "
    "`bank.salary_narration_org_token`?",
    focus=(
        "Read the employer token, such as [ORG_A], from the text of the `salary_slip` document in `documents`. "
        "When there is no salary_slip document, compare only `entity_roles.applicant.employer` with "
        "`bank.salary_narration_org_token`. Ignore pay amounts and dates."
    ),
    refer_to=["`documents`", "`entity_roles.applicant.employer`", "`bank.salary_narration_org_token`"],
    true_what="The employer token on the salary slip, `entity_roles.applicant.employer` and `bank.salary_narration_org_token` are all the same token.",
    true_examples=[
        "salary_slip shows [ORG_A], employer is [ORG_A], salary_narration_org_token is [ORG_A]",
        "no salary_slip in documents, employer is [ORG_A] and salary_narration_org_token is [ORG_A]",
        "salary_slip shows [ORG_B], employer is [ORG_B], salary_narration_org_token is [ORG_B]",
    ],
    false_what="One of the three tokens differs from the others.",
    false_not_for="Three tokens that are the same, whatever the pay month or pay amount on the slip.",
    false_examples=[
        "salary_slip shows [ORG_A] but bank.salary_narration_org_token is [ORG_B]",
        "entity_roles.applicant.employer is [ORG_A] but salary_narration_org_token is [ORG_C], with no salary_slip",
        "salary_slip shows [ORG_B] while employer is [ORG_A] and salary_narration_org_token is [ORG_A]",
    ],
)

_GST = noul_wire(
    "Do the GST filings and the bank credits tell the same story, meaning `gst.gst_to_bank_ratio_band` is 0.5-0.8, "
    "0.8-1.2 or 1.2-2?",
    focus=(
        "Use `gst.gst_to_bank_ratio_band` only. It is a band of GST turnover divided by bank credits. The bands "
        "<0.5 and >2 mean the two sources disagree. `gst.gst_turnover_band_12m` and `gst.bank_credits_band_12m` "
        "are for reference."
    ),
    refer_to=["`gst.gst_to_bank_ratio_band`", "`gst.gst_turnover_band_12m`", "`gst.bank_credits_band_12m`"],
    true_what="`gst.gst_to_bank_ratio_band` is 0.5-0.8, 0.8-1.2 or 1.2-2.",
    true_examples=[
        "gst_to_bank_ratio_band 0.8-1.2",
        "gst_to_bank_ratio_band 0.5-0.8",
        "gst_to_bank_ratio_band 1.2-2",
    ],
    false_what="`gst.gst_to_bank_ratio_band` is <0.5 or >2.",
    false_not_for="A ratio band of 0.5-0.8 or 1.2-2, which are still a yes, and late filings, which are a different question.",
    false_examples=[
        "gst_to_bank_ratio_band >2",
        "gst_to_bank_ratio_band <0.5",
        "gst_to_bank_ratio_band >2 with gst_turnover_band_12m 1-2Cr and bank_credits_band_12m 25-50L",
    ],
)

_SYNTHETIC = noul_wire(
    "Is there a sign of a synthetic identity in this file?",
    focus=(
        "Yes when a document in `documents` ends with 'Auto-generated document; no signature required.', or when two "
        "or more of these four signals are present. Signal (a): `identity_signals.phone_vintage_band` is <3m. Signal "
        "(b): `identity_signals.email_domain_type` is disposable. Signal (c): "
        "`identity_signals.bureau_history_consistent_with_age` is false. Signal (d): "
        "`identity_signals.address_shared_with_other_apps_band` is 3+. A `bureau.score_band` of NTC is not a signal "
        "on its own."
    ),
    refer_to=[
        "`documents`",
        "`identity_signals.phone_vintage_band`",
        "`identity_signals.email_domain_type`",
        "`identity_signals.bureau_history_consistent_with_age`",
        "`identity_signals.address_shared_with_other_apps_band`",
    ],
    true_what=(
        "A document ends with the auto-generated sentence, or two or more of signals (a), (b), (c) and (d) are present."
    ),
    true_examples=[
        f"a document ends with '{_AUTO_GENERATED}'",
        "phone_vintage_band <3m together with email_domain_type disposable",
        "bureau_history_consistent_with_age false together with address_shared_with_other_apps_band 3+",
    ],
    false_what="No document ends with the auto-generated sentence, and at most one of signals (a), (b), (c) and (d) is present.",
    false_not_for=(
        "A single signal on its own, or a `bureau.score_band` of NTC on its own. A new-to-credit applicant with "
        "a settled phone and email is a no."
    ),
    false_examples=[
        "phone_vintage_band >3y, email_domain_type corporate, bureau_history_consistent_with_age true, address_shared_with_other_apps_band 0",
        "email_domain_type disposable and nothing else, with documents ending in their Received stamp",
        "bureau.score_band NTC with phone_vintage_band 1-3y and email_domain_type free",
    ],
)


def _salaried_applicant(state: dict) -> bool:
    return (state.get("application") or {}).get("employment_type") == "salaried"


def _has_gst_block(state: dict) -> bool:
    return bool(state.get("gst"))


def _q(qid, wire, reason, polarity, segments, applies=None) -> QuestionDef:
    return register(
        QuestionDef(
            qid=qid,
            module="B",
            stage="appraisal",
            qtype="noul",
            segments=frozenset(segments),
            risk_polarity=polarity,
            routing_relevant=True,
            reason_text=reason,
            wire=wire,
            applies=applies,
        )
    )


_q(
    "B_identity_coheres",
    _IDENTITY,
    "Identity details do not cohere: PAN card details differ from the application, or the documents are auto-generated",
    "yes_is_good",
    ALL_SEGMENTS,
)
# Salaried applicants only: the salaried personal loan, and a home loan whose applicant is salaried.
_q(
    "B_salary_matches_employer",
    _SALARY,
    "Salary slip employer differs from the employer on the application or in the bank salary credits",
    "yes_is_good",
    {"salaried_personal", "secured_home"},
    applies=_salaried_applicant,
)
# Only when the state has a gst block (MSME always; a self-employed applicant only if GST registered).
_q(
    "B_gst_bank_consistent",
    _GST,
    "GST turnover and bank credits disagree by more than a factor of two",
    "yes_is_good",
    {"self_employed", "msme_business"},
    applies=_has_gst_block,
)
_q(
    "B_synthetic_identity_signals",
    _SYNTHETIC,
    "Signs of a synthetic identity: new phone or disposable email, inconsistent bureau history, shared address, or auto-generated documents",
    "yes_is_bad",
    ALL_SEGMENTS,
)
