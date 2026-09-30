"""The ``sanction_docs`` and ``monitoring`` states (PLAN 3.7).

``sanction_docs``: the memo and the Key Fact Statement (text redacted), the APR recomputed in code from the KFS
components, the sanction-grid row for the product and ticket (bank policy, not personal data), and the required
disclosures. ``monitoring``: six months of repayment (raw DPD days plus their band, balance bands), the balance trend, and for
MSME the covenants.
"""

from __future__ import annotations

from jevloan import finance
from jevloan.data.schema import Covenant, LoanFile, grid_row_for, load_disclosures, load_grid
from jevloan.pii.redact import Redactor
from jevloan.state import base, roles


def _policy_condition_texts() -> tuple[str, ...]:
    """Every required-condition sentence in the sanction grid. They are bank policy with no personal data, and
    the model must be able to compare the memo's wording with the grid's word for word."""
    return tuple(sorted({c["text"] for row in load_grid()["rows"] for c in row["required_conditions"]}))


def grid_row_block(product: str, principal_inr: float) -> dict:
    """The grid row as the model sees it: product, band, max tenure, required conditions and (home loans only)
    the LTV ceiling."""
    row = grid_row_for(product, principal_inr)
    block = {
        "product": row["product"],
        "band": row["band"],
        "max_tenure_months": row["max_tenure_months"],
        "required_conditions": [{"id": c["id"], "text": c["text"]} for c in row["required_conditions"]],
    }
    if row.get("max_ltv_pct") is not None:
        block["max_ltv_pct"] = row["max_ltv_pct"]
    return block


def build_sanction_docs(file: LoanFile, redactor: Redactor) -> dict:
    memo, kfs = file.sanction_memo, file.kfs
    policy = _policy_condition_texts()
    conditions = [c if c in policy else roles.clean_text(file, redactor, c) for c in memo.conditions]
    apr_recomputed = round(finance.apr_from_components(kfs.principal_inr, kfs.fees_inr, kfs.emi_inr, kfs.tenure_months), 2)
    return {
        "schema": base.STATE_SCHEMA,
        "stage": "sanction_docs",
        "segment": file.segment,
        "sanction_memo": {
            "text": roles.clean_text(file, redactor, memo.text, protect=policy),
            "product": memo.product,
            "ticket_band": memo.ticket_band,
            "tenure_months": memo.tenure_months,
            "conditions": conditions,
        },
        "kfs": {
            "text": roles.clean_text(file, redactor, kfs.text),
            "apr_stated_pct": kfs.apr_stated_pct,
            "apr_recomputed_pct": apr_recomputed,
            "rate_pct": kfs.rate_pct,
            "tenure_months": kfs.tenure_months,
        },
        "policy_grid_row": grid_row_block(memo.product, kfs.principal_inr),
        "required_disclosures": [{"id": d["id"], "heading": d["heading"]} for d in load_disclosures()],
    }


def _reported_value_band(covenant: Covenant) -> float | int | bool | str:
    """The DSCR covenant's reported ratio becomes a DSCR band (edge at the 1.25 covenant); the others are small
    integers or a flag and are kept."""
    if covenant.covenant_id == "dscr_min_1_25":
        return base.dscr_band(float(covenant.reported_value))
    return covenant.reported_value


def build_monitoring(file: LoanFile, redactor: Redactor) -> dict:
    months = sorted(file.post_disbursal.months, key=lambda m: m.m)
    return {
        "schema": base.STATE_SCHEMA,
        "stage": "monitoring",
        "segment": file.segment,
        "loan": {
            "product": file.application.product,
            "loan_amount_band": base.amount_band(file.application.loan_amount_inr),
            "tenure_months": file.application.tenure_months,
            "months_since_disbursal": len(months),
            "balance_change_band": base.balance_change_band([m.avg_balance_inr for m in months]),
        },
        "repayment": [
            {
                "m": m.m,
                "dpd_days": m.dpd,  # a small integer, not personal data; dpd_band stays alongside it
                "dpd_band": base.dpd_band(m.dpd),
                "emi_bounced": m.emi_bounced,
                "partial_payment": m.partial_payment,
                "avg_balance_band": base.monthly_band(m.avg_balance_inr),
            }
            for m in months
        ],
        "covenants": [  # MSME only; every other segment has none
            {
                "covenant_id": c.covenant_id,
                "required": c.required,  # the threshold: bank policy, not personal data
                "reported_value_band": _reported_value_band(c),
                "evidence_text": roles.clean_text(file, redactor, c.evidence_text),
            }
            for c in file.post_disbursal.covenants
        ],
    }
