"""Adversarial fixtures and the clean set: every fixture is caught, nothing clean is blocked."""

import copy
import json

import pytest

from jevloan.pii.detectors import DETECTOR_NAMES
from jevloan.pii.fixtures import CATEGORIES, adversarial_fixtures, clean_samples, known_gap_fixtures
from jevloan.pii.gate import PIIGate

gate = PIIGate()
ADV = adversarial_fixtures()
CLEAN = clean_samples()


def test_fixture_counts():
    assert len(ADV) >= 150
    assert len(CLEAN) >= 100


def test_ids_are_unique_and_payloads_are_json_like():
    assert len({f.id for f in ADV}) == len(ADV)
    for f in ADV:
        json.dumps(f.payload, default=str)  # serialisable apart from a tuple or two
        assert f.expected_detectors and f.expected_detectors <= set(DETECTOR_NAMES) | {"unscannable_body"}


@pytest.mark.parametrize("fx", ADV, ids=lambda f: f"{f.id}-{f.category}")
def test_every_adversarial_fixture_is_caught_by_an_expected_detector(fx):
    findings = gate.scan(fx.payload)
    assert findings, f"{fx.id} [{fx.category}] slipped through: {fx.payload!r}"
    fired = {f.detector for f in findings}
    assert fired & fx.expected_detectors, (fx.id, fx.category, fired, fx.expected_detectors)


def test_catch_rate_is_100_percent():
    caught = sum(1 for f in ADV if gate.scan(f.payload))
    assert caught == len(ADV)


@pytest.mark.parametrize("i", range(len(CLEAN)))
def test_clean_samples_produce_zero_findings(i):
    assert gate.scan(CLEAN[i]) == [], json.dumps(CLEAN[i], ensure_ascii=False)[:400]


def test_clean_false_positive_rate_is_zero():
    assert sum(1 for c in CLEAN if gate.scan(c)) == 0


def test_every_detector_is_exercised_by_a_fixture():
    covered = set().union(*(f.expected_detectors for f in ADV))
    assert set(DETECTOR_NAMES) <= covered


def test_the_spec_categories_are_all_present():
    joined = " ".join(CATEGORIES)
    for needle in [
        "pan_plain", "pan_spaced", "pan_dashed", "pan_dotted", "pan_lowercase", "pan_zerowidth", "pan_gstin", "pan_spelled",
        "aadhaar_plain", "aadhaar_grouped", "aadhaar_vid", "account_number", "card_number", "phone_plain", "phone_plus91", "phone_0091",
        "phone_trunk0", "phone_spelled", "phone_landline", "phone_devanagari", "phone_fullwidth", "phone_zerowidth", "email", "upi_id",
        "ifsc", "passport", "voter_id", "driving_licence", "pincode", "name_honorific", "name_relation", "name_label", "name_allcaps",
        "name_initials", "name_single_first", "name_devanagari", "name_native_label", "key_", "deep_nesting", "list_of_values",
        "question_instructions", "question_criteria", "aadhaar_devanagari", "aadhaar_fullwidth", "aadhaar_spelled", "aadhaar_zerowidth",
    ]:
        assert needle in joined, needle


def test_fixtures_are_deterministic_and_independent_copies():
    a, b = adversarial_fixtures(), adversarial_fixtures()
    assert [(f.id, f.category, f.payload) for f in a] == [(f.id, f.category, f.payload) for f in b]
    a[0].payload["mutated"] = True
    assert "mutated" not in adversarial_fixtures()[0].payload
    assert clean_samples() == clean_samples()
    c = clean_samples()
    c[0]["mutated"] = True
    assert "mutated" not in clean_samples()[0]


def test_fixture_payloads_are_gateway_shaped():
    shaped = sum(1 for f in ADV if isinstance(f.payload, dict) and ("state" in f.payload or "questions" in f.payload))
    assert shaped == len(ADV)
    assert any("questions" in f.payload and "state" in f.payload for f in ADV)


def test_clean_set_covers_the_required_kinds():
    text = json.dumps(CLEAN, ensure_ascii=False)
    for needle in [
        "[APPLICANT]", "[ORG_A]", "[PAN_1]", "[PHONE_1]", "[ADDR_1]", "₹[25-50k]", "50L-1Cr", "750-799", "salaried_personal",
        "Income proof dated within the last 2 months", "examples", "वेतन", "சம்பள", "বেতন",
        "Salary slip for Mar 2026. Employee: [APPLICANT]. Employer: [ORG_A]. Net pay ₹[50-75k]. PAN: [PAN_1].",
    ]:
        assert needle in text, needle


def test_known_gaps_are_documented_with_a_reason_and_are_not_counted():
    gaps = known_gap_fixtures()
    assert len(gaps) >= 5
    for g in gaps:
        assert g.note and g.category
        assert g.id not in {f.id for f in ADV}
    # they are honest: at least the structural ones really are not caught
    uncaught = [g.category for g in gaps if not gate.scan(g.payload)]
    for expected in ["digits_split_across_list_items", "unknown_name_without_marker", "devanagari_digit_words"]:
        assert expected in uncaught


def test_mutating_a_clean_sample_with_pii_makes_it_dirty():
    """A clean sample plus one leaked identifier must be blocked: the clean set is not just trivially clean."""
    for sample in CLEAN[:30]:
        dirty = copy.deepcopy(sample)
        dirty["leak"] = "PAN ABCPK1234F"
        assert gate.scan(dirty)
