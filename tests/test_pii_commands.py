"""`jevloan pii fixtures-check` and `jevloan pii scan`."""

import json

from jevloan.cli import build_parser, main


def test_pii_group_is_registered():
    parser = build_parser()
    args = parser.parse_args(["pii", "fixtures-check"])
    assert args.group == "pii" and callable(args.func)
    args = parser.parse_args(["pii", "scan", "x.json"])
    assert args.path == "x.json" and callable(args.func)


def test_fixtures_check_reports_100_percent_catch_and_zero_false_positives(capsys):
    code = main(["pii", "fixtures-check"])
    out = capsys.readouterr().out
    assert code == 0
    assert "100.0%" in out and "0.0%" in out
    assert "catch rate" in out and "false-positive rate" in out
    assert "known limitations" in out
    assert "PASS" in out
    assert "pan_plain" in out and "name_honorific" in out


def test_fixtures_check_fails_when_the_gate_misses_something(capsys, monkeypatch):
    from jevloan.pii import commands, fixtures

    bad = fixtures.Fixture("ADV-X", "made_up", {"state": {"remarks": "letters OYSBA then digits 0712 and T"}}, {"pan"})
    monkeypatch.setattr(commands, "adversarial_fixtures", lambda: [bad])
    assert main(["pii", "fixtures-check"]) == 1
    out = capsys.readouterr().out
    assert "MISS" in out and "FAIL" in out


def test_fixtures_check_fails_on_a_false_positive(capsys, monkeypatch):
    from jevloan.pii import commands

    monkeypatch.setattr(commands, "clean_samples", lambda: [{"state": {"remarks": "PAN ABCPK1234F"}}])
    assert main(["pii", "fixtures-check"]) == 1
    assert "FALSE+" in capsys.readouterr().out


def test_scan_json_file(tmp_path, capsys):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"state": {"documents": [{"text": "Net pay ₹[50-75k]. PAN [PAN_1]"}]}}))
    assert main(["pii", "scan", str(clean)]) == 0
    assert "0 finding" in capsys.readouterr().out

    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps({"state": {"documents": [{"text": "PAN ABCPK1234F"}]}}))
    assert main(["pii", "scan", str(dirty)]) == 1
    out = capsys.readouterr().out
    assert "pan at state.documents[0].text" in out and "ABCPK1234F" not in out


def test_scan_jsonl_file_reports_the_line(tmp_path, capsys):
    path = tmp_path / "book.jsonl"
    path.write_text(
        json.dumps({"a": "clean"}) + "\n\n" + json.dumps({"a": "call 9876543210"}) + "\n" + json.dumps({"a": "Mr Rajesh Kumar"}) + "\n"
    )
    assert main(["pii", "scan", str(path)]) == 1
    out = capsys.readouterr().out
    assert "line 3: phone_in at a" in out
    assert "line 4: person_name at a" in out
    assert "line 1" not in out
    assert "scanned 3 record(s)" in out


def test_scan_missing_file_is_an_error(tmp_path, capsys):
    assert main(["pii", "scan", str(tmp_path / "nope.json")]) == 2
