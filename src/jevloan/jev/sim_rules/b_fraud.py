"""Simulator rules for module B (fraud screen). State only; tokens and bands, as in the rubric."""

import random

from jevloan.jev.sim_rules import get, noisy_p, noul, sim_rule
from jevloan.jev.sim_rules.a_readiness import first_doc, has_auto_generated_ending, name_token, tokens


def identity_coheres(state: dict) -> bool:
    """Check 1: the pan_card_text PAN, name and DOB tokens equal entity_roles.applicant. Check 2: no document
    ends with the auto-generated sentence. (A firm PAN in entity_roles.business is not compared.)"""
    if has_auto_generated_ending(state):
        return False
    doc = first_doc(state, doc_type="pan_card_text")
    if doc is None:
        return True
    text = str(doc.get("text", ""))
    roles = get(state, "entity_roles.applicant", {}) or {}
    pan, birth, name = roles.get("pan"), roles.get("birth"), roles.get("name")
    if pan and any(token != pan for token in tokens(text, "PAN")):
        return False
    if birth and any(token != birth for token in tokens(text, "DOB")):
        return False
    shown_name = name_token(text)
    return not (name and shown_name and shown_name != name)


@sim_rule("B_identity_coheres")
def b_identity_coheres(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(identity_coheres(state), rng, accuracy=0.93))


def salary_matches_employer(state: dict) -> bool:
    slip = first_doc(state, doc_type="salary_slip")
    slip_orgs = tokens(str(slip.get("text", "")), "ORG") if slip else []
    seen = [
        value
        for value in (
            slip_orgs[0] if slip_orgs else None,
            get(state, "entity_roles.applicant.employer"),
            get(state, "bank.salary_narration_org_token"),
        )
        if value
    ]
    return len(set(seen)) <= 1


@sim_rule("B_salary_matches_employer")
def b_salary_matches_employer(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(salary_matches_employer(state), rng, accuracy=0.94))


@sim_rule("B_gst_bank_consistent")
def b_gst_bank_consistent(state: dict, question: dict, rng: random.Random) -> dict:
    consistent = get(state, "gst.gst_to_bank_ratio_band") not in ("<0.5", ">2")
    return noul(noisy_p(consistent, rng, accuracy=0.93))


def synthetic_signal_count(state: dict) -> int:
    """(a) phone under 3 months, (b) disposable email, (c) bureau history inconsistent with age, (d) address
    shared by 3 or more applications. NTC on its own is deliberately not counted."""
    signals = get(state, "identity_signals", {}) or {}
    return sum(
        (
            signals.get("phone_vintage_band") == "<3m",
            signals.get("email_domain_type") == "disposable",
            signals.get("bureau_history_consistent_with_age") is False,
            signals.get("address_shared_with_other_apps_band") == "3+",
        )
    )


def synthetic_identity(state: dict) -> bool:
    return has_auto_generated_ending(state) or synthetic_signal_count(state) >= 2


@sim_rule("B_synthetic_identity_signals")
def b_synthetic_identity_signals(state: dict, question: dict, rng: random.Random) -> dict:
    return noul(noisy_p(synthetic_identity(state), rng, accuracy=0.92))
