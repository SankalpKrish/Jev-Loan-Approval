"""Appraisal state for a secured (home) loan (PLAN 3.7): a ``property`` block. The applicant is salaried 65 percent
of the time (then the bank block carries the salary narration token) and self-employed otherwise."""

from __future__ import annotations

from jevloan.data.schema import LoanFile
from jevloan.pii.redact import Redactor
from jevloan.state import base, roles


def build(file: LoanFile, redactor: Redactor) -> dict:
    return {
        "schema": base.STATE_SCHEMA,
        "stage": "appraisal",
        "segment": file.segment,
        "application": base.application_block(file),
        "bureau": base.bureau_block(file),
        "income": base.income_block(file),
        "obligations": base.obligations_block(file),
        "bank": base.bank_block(file, roles.narration_org_token(file, redactor)),
        "property": base.property_block(file),
        "identity_signals": base.identity_block(file),
        "entity_roles": roles.entity_roles(file, redactor),
        "documents": roles.documents_block(file, redactor),
    }
