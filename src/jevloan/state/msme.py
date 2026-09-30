"""Appraisal state for an MSME term loan (PLAN 3.7): ``gst`` and ``business`` blocks. Capacity is judged on DSCR
(``business.dscr_band``); ``obligations.segment_foir_limit_pct`` is 80, which is a DSCR of 1.25."""

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
    state["gst"] = base.gst_block(file)  # every MSME file is GST-registered
    state["business"] = base.business_block(file)
    state["identity_signals"] = base.identity_block(file)
    state["entity_roles"] = roles.entity_roles(file, redactor)
    state["documents"] = roles.documents_block(file, redactor)
    return state
