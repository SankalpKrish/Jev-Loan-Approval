"""State builders (PLAN 3.7): one loan file in, the compact PII-free JSON state out.

    build_state(file, stage) -> dict      stage: "appraisal" | "sanction_docs" | "monitoring"
    redactor_for(file) -> Redactor        the per-file Redactor (same tokens for every document and stage)
    STATE_SCHEMA                          "jevloan.state.v1"

The state is the only thing sent to the model. It carries bands, enums, small integers and redacted text, and never
a file id, demographics, the raw applicant block, meta, labels, the PII inventory, a raw rupee figure or a date more
precise than month and year.
"""

from __future__ import annotations

from jevloan.data.schema import LoanFile
from jevloan.pii.redact import Redactor
from jevloan.state import msme, salaried, secured_home, self_employed, stages
from jevloan.state.base import STAGES, STATE_SCHEMA
from jevloan.state.roles import StateBuildError, redactor_for, shared_redactor
from jevloan.state.tokens import estimate_tokens

__all__ = ["STAGES", "STATE_SCHEMA", "StateBuildError", "build_state", "estimate_tokens", "redactor_for"]

_APPRAISAL = {
    "salaried_personal": salaried.build,
    "self_employed": self_employed.build,
    "msme_business": msme.build,
    "secured_home": secured_home.build,
}


def build_state(file: LoanFile, stage: str, *, redactor: Redactor | None = None) -> dict:
    """The state for one stage of one file. Deterministic: the same file gives the same state. ``redactor`` is
    optional; pass the one from ``redactor_for(file)`` to reuse it across stages (a per-thread cache does that
    anyway when it is omitted)."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    r = redactor if redactor is not None else shared_redactor(file)
    if stage == "appraisal":
        try:
            builder = _APPRAISAL[file.segment]
        except KeyError:
            raise ValueError(f"no appraisal state builder for segment {file.segment!r}") from None
        return builder(file, r)
    if stage == "sanction_docs":
        return stages.build_sanction_docs(file, r)
    return stages.build_monitoring(file, r)
