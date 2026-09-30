"""No identifier, label or demographic in any state; the state stays compact (PLAN 3.7, M1)."""

from __future__ import annotations

import re

import pytest
from test_state_common import book_400, states_400

from jevloan.canonical import canonical_json
from jevloan.data.schema import SEGMENTS
from jevloan.pii import PIIGate
from jevloan.state import STAGES, build_state, redactor_for
from jevloan.state.commands import (
    MEAN_LIMIT,
    P99_LIMIT,
    compute_token_stats,
    format_token_stats,
    inventory_hits,
    leak_scan,
)
from jevloan.state.tokens import estimate_tokens

ALLOWED_TOP_LEVEL = {
    "schema", "stage", "segment", "application", "bureau", "income", "obligations", "bank", "gst", "business", "property",
    "identity_signals", "entity_roles", "documents", "sanction_memo", "kfs", "policy_grid_row", "required_disclosures",
    "loan", "repayment", "covenants",
}  # fmt: skip
FORBIDDEN_KEYS = {
    "file_id", "demographics", "meta", "labels", "pii_inventory", "applicant_block", "true_bureau_score", "gender", "dob_year",
    "name_native", "pincode_cluster", "language", "disparity_subset", "aadhaar", "email", "account_number", "gstin",
    "preferred_language", "address", "line1", "pincode", "verified_monthly_income_inr", "loan_amount_inr", "market_value_inr",
    "turnover_12m_inr", "bank_credits_12m_inr", "avg_balance_inr", "principal_inr", "fees_inr", "emi_inr",
}  # fmt: skip
# "address" is a legal key only inside entity_roles (a token); see the walk below.
LABEL_WORDS = (
    "sanctionable", "fraud_type", "missing_items", "disparity", "closeness_level", "primary_weakness", "outcome_12m", "risk_pd",
    "memo_defects", "ews_truth", "covenant_breaches", "question_truth", "identity_mismatch", "salary_pattern_mismatch",
    "gst_bank_mismatch", "synthetic_identity", "lang_doc_script", "gender_income_proxy", "pincode_bureau_thin",
)  # fmt: skip


def walk(obj, path=()):
    """Yield (path, key, value) for every dict entry, and (path, None, value) for list items."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from walk(v, (*path, k))
    elif isinstance(obj, list):
        for v in obj:
            yield path, None, v
            yield from walk(v, path)


def test_top_level_keys_are_from_the_plan():
    for key, s in states_400().items():
        assert set(s) <= ALLOWED_TOP_LEVEL, key


def test_no_forbidden_keys_anywhere():
    for (file_id, stage), s in states_400().items():
        for path, key, _ in walk(s):
            if key is None:
                continue
            if key == "address" and path and path[-1] in ("business", "property"):
                continue  # entity_roles.business.address / entity_roles.property.address hold tokens
            assert key not in FORBIDDEN_KEYS, (file_id, stage, path, key)


def test_no_label_words_in_any_state():
    for (file_id, stage), s in states_400().items():
        text = canonical_json(s)
        for word in LABEL_WORDS:
            assert word not in text, (file_id, stage, word)


def test_file_id_never_appears():
    for (file_id, stage), s in states_400().items():
        assert file_id not in canonical_json(s), (file_id, stage)
        assert not re.search(r"\bF\d{6}\b", canonical_json(s)), (file_id, stage)


def test_no_raw_rupee_amount_and_no_precise_date():
    month = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    for (file_id, stage), s in states_400().items():
        text = canonical_json(s)
        assert not re.search(r"(?:Rs\.?|INR|₹)\s*\d", text), (file_id, stage)
        assert not re.search(r"\d{5,}", text), (file_id, stage, re.findall(r"\d{5,}", text))  # no long digit run at all
        assert not re.search(rf"\b\d{{1,2}}(?:st|nd|rd|th)? (?:{month})[a-z]* \d{{4}}", text), (file_id, stage)
        assert not re.search(r"\b\d{1,2}([/\-.])\d{1,2}\1\d{2,4}\b", text), (file_id, stage)
        for _, key, value in walk(s):
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                assert abs(value) < 1000, (file_id, stage, key, value)  # counts, months, percentages and ratios only


def test_no_gender_marker_and_no_country_code_left_in_text():
    for (file_id, stage), s in states_400().items():
        text = canonical_json(s)
        assert not re.search(r"\b(?:Mr|Mrs|Ms|Miss|Shri|Smt|Sri|Kumari)\b", text), (file_id, stage)
        assert not re.search(r"\+\s?91|\(\s*91\s*\)|\b0091\b", text), (file_id, stage)
        assert "X" * 4 not in text and "xxxx" not in text.lower(), (file_id, stage)  # no masked identifier with its last 4 digits


def test_leak_scan_over_400_files_has_no_hits_and_no_gate_findings():
    result = leak_scan(book_400())
    assert result["states"] == 400 * len(STAGES)
    assert result["a_hits"] == [], result["a_hits"][:5]
    assert result["b_findings"] == [], result["b_findings"][:5]


def test_the_scan_really_detects_a_leak():
    """Guard against a scan that passes because it is blind: inject each kind of raw value and expect a hit."""
    f = book_400()[0]
    a = f.applicant
    for kind, needle in (
        ("pans", a.pan), ("aadhaars", a.aadhaar), ("phones", a.phone), ("emails", a.email),
        ("account_numbers", f.bank.account_number), ("address_lines", a.address.line1), ("person_names", a.name),
        ("pincodes", a.address.pincode), ("org_names", f.application.employer_name),
    ):
        state = build_state(f, "appraisal")
        state["documents"][0]["text"] += f" note {needle}"
        kinds = {h.kind for h in inventory_hits(f, state)}
        assert kind in kinds or (kind == "person_names" and "person_name_part" in kinds), (kind, kinds)
    spaced = build_state(f, "appraisal")
    spaced["documents"][0]["text"] += f" {a.aadhaar[:4]} {a.aadhaar[4:8]}-{a.aadhaar[8:]} {a.phone[:5]} {a.phone[5:]}"
    assert {"aadhaars", "phones"} <= {h.kind for h in inventory_hits(f, spaced)}
    upper = build_state(f, "appraisal")
    upper["documents"][0]["text"] += f" {a.name.upper()} {a.pan.lower()}"
    assert {"pans", "person_names"} <= {h.kind for h in inventory_hits(f, upper)}
    gate_leak = build_state(f, "appraisal")
    gate_leak["documents"][0]["text"] += f" PAN {a.pan}"
    assert PIIGate().scan(gate_leak)
    for (_, stage) in [(f.file_id, s) for s in STAGES]:
        assert inventory_hits(f, build_state(f, stage)) == []


def test_raw_documents_would_leak_everywhere():
    """The un-redacted documents trip both checks on every file, so a clean pass on the states means something."""
    gate = PIIGate()
    for f in book_400()[:40]:
        state = build_state(f, "appraisal")
        state["documents"] = [{"doc_type": d.doc_type, "month_age": d.month_age, "script": d.script, "text": d.text} for d in f.documents]
        assert inventory_hits(f, state), f.file_id
        assert gate.scan(state), f.file_id


def test_token_estimate_meets_the_m1_requirement_on_400_files():
    stats = compute_token_stats(book_400())
    appraisal = stats["appraisal"]
    assert len(appraisal) == 400
    mean = sum(appraisal) / len(appraisal)
    p99 = sorted(appraisal)[int(0.99 * len(appraisal)) - 1]
    assert mean < MEAN_LIMIT == 3000 and p99 < P99_LIMIT == 3500
    text, ok = format_token_stats(stats)
    assert ok and "PASS" in text
    assert {seg for seg, _ in stats["by"]} == set(SEGMENTS)
    for s in states_400().values():
        assert len(canonical_json(s)) <= 6500  # about 3k tokens of real Jev


def test_estimate_matches_the_helper_on_real_states():
    f = book_400()[3]
    s = build_state(f, "appraisal")
    assert estimate_tokens(s) == -(-len(canonical_json(s)) * 10 // 22)
    assert 800 < estimate_tokens(s) < 3000


def test_stats_fail_when_the_appraisal_mean_or_p99_is_too_high():
    fake = {"by": {("salaried_personal", "appraisal"): [3100] * 100}, "appraisal": [3100] * 100}
    assert format_token_stats(fake)[1] is False
    fake = {"by": {("salaried_personal", "appraisal"): [1000] * 98 + [3600, 3700]}, "appraisal": [1000] * 98 + [3600, 3700]}
    assert format_token_stats(fake)[1] is False
    fake = {"by": {("salaried_personal", "appraisal"): [1500] * 100}, "appraisal": [1500] * 100}
    assert format_token_stats(fake)[1] is True


@pytest.mark.parametrize("stage", STAGES)
def test_a_state_built_without_a_redactor_matches_one_built_with_it(stage):
    f = book_400()[10]
    assert build_state(f, stage) == build_state(f, stage, redactor=redactor_for(f)) == states_400()[(f.file_id, stage)]
