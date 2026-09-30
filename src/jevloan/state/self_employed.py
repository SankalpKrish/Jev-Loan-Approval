"""Appraisal state for a self-employed business loan (PLAN 3.7): a ``business`` block always, and a ``gst`` block
when the business is GST-registered (60 percent of the book's self-employed files)."""

from __future__ import annotations

from jevloan.data.schema import LoanFile
from jevloan.pii.redact import Redactor
from jevloan.state import base, roles


def build(file: LoanFile, redactor: Redactor) -> dict:
    state = {
        "schema": base.STATE_SCHEMA,
        "stage": "appraisal",
        "segment": file.segment,
        "application": base.application_block(file),
        "bureau": base.bureau_block(file),
        "income": base.income_block(file),
        "obligations": base.obligations_block(file),
        "bank": base.bank_block(file, roles.narration_org_token(file, redactor)),
    }
    if file.gst is not None:
        state["gst"] = base.gst_block(file)
    state["business"] = base.business_block(file)
    state["identity_signals"] = base.identity_block(file)
    state["entity_roles"] = roles.entity_roles(file, redactor)
    state["documents"] = roles.documents_block(file, redactor)
    return state
