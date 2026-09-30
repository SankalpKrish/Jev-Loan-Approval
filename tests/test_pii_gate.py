"""PIIGate: scan/check, paths, keys, numbers, structure, and speed (PLAN 3.5)."""

import json
import random
import time

import pytest

from jevloan.pii import gate as gate_module
from jevloan.pii.fixtures import _clean_doc, _clean_state, _rubric
from jevloan.pii.gate import PIIBlocked, PIIFinding, PIIGate

gate = PIIGate()


def paths(payload):
    return {(f.detector, f.path) for f in gate.scan(payload)}


def clear_caches():
    for fn in (gate_module._scan_str, gate_module._scan_key, gate_module._key_context, gate_module._scan_ctx, gate_module._key_segment):
        fn.cache_clear()


# ------------------------------------------------------------------------------------------------ paths


def test_scan_reports_json_pointer_like_paths():
    payload = {
        "state": {"documents": [{"text": "clean"}, {"text": "clean"}, {"text": "PAN ABCDE1234F"}]},
        "questions": {"A_x": {"instructions": {"question": "call 9876543210"}}},
    }
    found = paths(payload)
    assert ("pan", "state.documents[2].text") in found
    assert ("phone_in", "questions.A_x.instructions.question") in found


def test_root_level_string_and_list_paths():
    assert paths("PAN ABCDE1234F") == {("pan", "$")}
    assert ("pan", "[1]") in paths(["ok", "ABCDE1234F"])


def test_odd_keys_use_bracket_paths():
    found = paths({"state": {"has space": "ABCDE1234F", "a.b": "ABCDE1234F"}})
    assert ("pan", 'state["has space"]') in found
    assert ("pan", 'state["a.b"]') in found


def test_pii_shaped_keys_are_reported_but_never_put_in_a_path():
    payload = {"state": {"9876543210": {"note": "ok"}, "ABCDE1234F": "x", "Mr Rajesh Kumar": {"deep": "9876543210"}}}
    findings = gate.scan(payload)
    assert findings
    for f in findings:
        assert "9876543210" not in f.path and "ABCDE1234F" not in f.path and "Rajesh" not in f.path
    assert any(f.path == "state.<key>" and f.detector == "phone_in" for f in findings)
    assert any(f.path == "state.<key>.deep" for f in findings)  # values below a PII key are still scanned


# ------------------------------------------------------------------------------------------------ values


def test_scan_keys_and_values_and_containers():
    assert ("pan", "a") in paths({"a": "ABCDE1234F"})
    assert ("pan", "a[1]") in paths({"a": ["x", "ABCDE1234F"]})
    assert ("pan", "a[0]") in paths({"a": ("ABCDE1234F",)})
    assert paths({"a": {"b": {"c": [[["ABCDE1234F"]]]}}})
    assert paths({"a": {"ABCDE1234F"}})  # sets are walked too


def test_numbers_are_converted_to_strings():
    assert ("account_number", "acct") in paths({"acct": 123456789012345})
    assert ("phone_in", "mob") in paths({"mob": 9876543210})
    assert ("account_number", "acct") in paths({"acct": 1234567890123.0})
    assert ("pincode_ctx", "address.pincode") in paths({"address": {"pincode": 560034}})


def test_small_numbers_bools_none_are_ignored():
    assert gate.scan({"a": 3, "b": 36, "c": 0.8333333333, "d": True, "e": None, "f": -7, "g": 99999, "h": 1e-9}) == []


def test_bytes_and_unknown_objects_are_scanned_not_skipped():
    assert paths({"a": b"PAN ABCDE1234F"})

    class Thing:
        def __str__(self):
            return "call 9876543210"

    assert paths({"a": Thing()})


def test_dataclass_payloads_are_walked():
    from dataclasses import dataclass

    @dataclass
    class Doc:
        text: str

    assert paths({"doc": Doc("PAN ABCDE1234F")}) == {("pan", "doc.text")}


def test_json_strings_are_parsed_and_walked_with_precise_paths():
    inner = json.dumps({"documents": [{"text": "PAN ABCDE1234F"}]})
    assert ("pan", "state.documents[0].text") in paths({"state": inner})
    assert gate.scan({"state": json.dumps({"a": [1, 2, 3, 4, 5, 6, 7, 8, 9, 1, 0]})}) == []


def test_base64_and_hex_layers_are_decoded_once():
    import base64

    assert paths({"a": base64.b64encode(b"PAN ABCDE1234F here").decode()})
    assert paths({"a": b"9876543210".hex()})
    assert gate.scan({"a": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"}) == []


def test_key_context_catches_bare_names_and_pins():
    assert ("person_name", "applicant_name") in paths({"applicant_name": "Thirumalai Vasan"})
    assert ("person_name", "b.full_name") in paths({"b": {"full_name": "zorba ravichandran"}})
    assert ("person_name", "guarantor_name") in paths({"guarantor_name": "Bhuvaneswari"})
    assert ("person_name", "names[0]") in paths({"names": ["Bhuvaneswari"]})  # list values keep the key context
    assert ("pincode_ctx", "pin_code") in paths({"pin_code": "400001"})
    # ordinary values under those keys stay clean
    assert gate.scan({"applicant_name": "[APPLICANT]", "name": "salary_slip", "pincode": "[PIN]", "guarantor": None}) == []


def test_depth_limit_refuses_absurd_nesting():
    node = {"x": "ok"}
    for _ in range(100):
        node = {"n": node}
    assert any(f.detector == "depth_limit" for f in gate.scan(node))


# ------------------------------------------------------------------------------------------------ check / PIIBlocked


def test_check_returns_none_when_clean_and_raises_with_findings():
    assert gate.check({"state": {"remarks": "Net pay ₹[50-75k], PAN [PAN_1]"}}) is None
    with pytest.raises(PIIBlocked) as info:
        gate.check({"state": {"remarks": "PAN ABCDE1234F and call 9876543210"}})
    err = info.value
    assert {f.detector for f in err.findings} >= {"pan", "phone_in"}
    assert all(isinstance(f, PIIFinding) for f in err.findings)
    assert err.detectors >= {"pan", "phone_in"}
    text = str(err)
    assert "ABCDE1234F" not in text and "9876543210" not in text  # masked only, even in the message


def test_piiblocked_can_be_rebuilt_from_a_string():
    err = PIIBlocked("blocked somewhere")
    assert str(err) == "blocked somewhere" and err.findings == []
    assert isinstance(err, Exception)


def test_findings_are_masked_and_never_contain_the_raw_value():
    raw = {"pan": "ABCDE1234F", "phone": "9876543210", "aadhaar": "234567890123", "email": "priya.sharma@gmail.com", "name": "Mr Rajesh Kumar"}
    for value in raw.values():
        for f in gate.scan({"x": f"value {value} here"}):
            assert value not in f.masked and value not in f.path
            assert "*" in f.masked


def test_findings_are_dataclass_like():
    f = gate.scan({"a": "ABCDE1234F"})[0]
    assert f.as_dict() == {"detector": "pan", "path": "a", "masked": "AB******4F"}


def test_scan_is_repeatable_and_cache_safe():
    payload = {"a": "PAN ABCDE1234F"}
    assert gate.scan(payload) == gate.scan(payload)
    clear_caches()
    assert gate.scan(payload) == gate.scan(payload)


# ------------------------------------------------------------------------------------------------ speed


def _payload(n_docs: int, seed: int):
    rng = random.Random(seed)
    docs = [" ".join(" ".join(_clean_doc(rng) for _ in range(6)).split()[:120]) for _ in range(n_docs)]
    return {"state": _clean_state(rng, "salaried_personal", docs), "questions": {**_rubric(rng), **_rubric(rng), **_rubric(rng)}}


def best_of(payload_fn, runs=9, cold=True):
    best = 1e9
    for i in range(runs):
        payload = payload_fn(i)
        if cold:
            clear_caches()
        t = time.perf_counter()
        gate.scan(payload)
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def test_payload_of_12kb_scans_in_under_20ms_even_with_cold_caches():
    sample = _payload(11, 1)
    size = len(json.dumps(sample, ensure_ascii=False))
    assert size >= 11_000, size
    assert best_of(lambda i: _payload(11, 100 + i)) < 20.0


def test_state_of_6_to_7kb_scans_in_under_5ms():
    """Real Jev bills about 2.15 characters per token, so a 3k-token state is 6-7 KB."""
    probe = _payload(5, 2)
    size = len(json.dumps(probe, ensure_ascii=False))
    assert 5_500 <= size <= 8_500, size
    # documents are new every request; keys, enum vocabulary and rubric boilerplate repeat, so they are warm
    gate.scan(_payload(5, 3))
    assert best_of(lambda i: _payload(5, 200 + i), cold=False) < 5.0


def test_repeat_scan_of_the_same_payload_is_nearly_free():
    payload = _payload(7, 4)
    gate.scan(payload)
    t = time.perf_counter()
    for _ in range(20):
        gate.scan(payload)
    assert (time.perf_counter() - t) / 20 * 1000 < 2.0
