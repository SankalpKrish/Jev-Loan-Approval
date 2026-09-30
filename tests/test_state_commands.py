"""``jevloan state show | stats | leak-scan`` on a small book written to a temporary file."""

from __future__ import annotations

import json

import pytest
from test_state_common import book_400

from jevloan import cli
from jevloan.data.schema import write_book
from jevloan.state import build_state, commands


@pytest.fixture(scope="module")
def small_book(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("state_book") / "book.jsonl"
    write_book(list(book_400())[:24], path)
    return str(path)


def run(argv: list[str]) -> int:
    return cli.main(argv)


def test_group_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args(["state", "show", "--file-id", "F000001"])
    assert args.func is commands._show and args.stage == "appraisal"


def test_show_prints_the_state_as_json(small_book, capsys):
    assert run(["state", "show", "--file-id", "F000003", "--book", small_book]) == 0
    out = capsys.readouterr()
    file = next(f for f in book_400() if f.file_id == "F000003")
    assert json.loads(out.out) == build_state(file, "appraisal")
    assert "tokens" in out.err
    assert run(["state", "show", "--file-id", "F000003", "--stage", "monitoring", "--book", small_book]) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "monitoring"


def test_show_unknown_file_or_book_or_stage_fails(small_book):
    with pytest.raises(SystemExit):
        run(["state", "show", "--file-id", "F999999", "--book", small_book])
    with pytest.raises(SystemExit):
        run(["state", "show", "--file-id", "F000001", "--book", "/nonexistent/book.jsonl"])
    with pytest.raises(SystemExit):
        run(["state", "show", "--file-id", "F000001", "--stage", "bogus", "--book", small_book])


def test_show_generates_the_file_when_there_is_no_book(monkeypatch, capsys, tmp_path):
    from jevloan.config import RuntimeConfig

    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(book_path=str(tmp_path / "absent.jsonl")))
    assert run(["state", "show", "--file-id", "F000001"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["segment"] == "salaried_personal" and state["stage"] == "appraisal"
    with pytest.raises(SystemExit):
        run(["state", "show", "--file-id", "F900000"])


def test_stats_prints_the_distribution_and_passes(small_book, capsys):
    assert run(["state", "stats", "--book", small_book]) == 0
    out = capsys.readouterr().out
    for seg in ("salaried_personal", "self_employed", "msme_business", "secured_home"):
        assert seg in out
    for stage in ("appraisal", "sanction_docs", "monitoring"):
        assert stage in out
    for col in ("mean", "p50", "p95", "p99", "max"):
        assert col in out
    assert "appraisal, all segments" in out and "PASS" in out


def test_stats_exits_1_over_the_limits(small_book, capsys, monkeypatch):
    monkeypatch.setattr(commands, "MEAN_LIMIT", 100)
    assert run(["state", "stats", "--book", small_book]) == 1
    assert "FAIL" in capsys.readouterr().out
    monkeypatch.setattr(commands, "MEAN_LIMIT", 3000)
    monkeypatch.setattr(commands, "P99_LIMIT", 100)
    assert run(["state", "stats", "--book", small_book]) == 1


def test_leak_scan_passes_on_the_book(small_book, capsys):
    assert run(["state", "leak-scan", "--book", small_book]) == 0
    out = capsys.readouterr().out
    assert "24 files x 3 stages = 72 states" in out and "0 hits" in out and "PIIGate findings: 0" in out and "PASS" in out


def test_leak_scan_exits_1_and_prints_masked_values_only(small_book, capsys, monkeypatch):
    real = commands.build_state
    leaked = book_400()[0]

    def leaky(file, stage, **kw):
        state = real(file, stage, **kw)
        if file.file_id == leaked.file_id and stage == "appraisal":
            state["documents"][0]["text"] += f" {leaked.applicant.pan} {leaked.applicant.name} {leaked.applicant.phone}"
        return state

    monkeypatch.setattr(commands, "build_state", leaky)
    assert run(["state", "leak-scan", "--book", small_book]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "(a)" in out and "(b)" in out and leaked.file_id in out
    for raw in (leaked.applicant.pan, leaked.applicant.name, leaked.applicant.phone):
        assert raw not in out
    assert "pans" in out and "person_names" in out


def test_leak_scan_lists_at_most_twenty_examples(monkeypatch, small_book, capsys):
    real = commands.build_state

    def leaky(file, stage, **kw):
        state = real(file, stage, **kw)
        if stage == "appraisal":
            state["documents"][0]["text"] += f" {file.applicant.pan} {file.applicant.phone}"
        return state

    monkeypatch.setattr(commands, "build_state", leaky)
    assert run(["state", "leak-scan", "--book", small_book]) == 1
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("  (a)") or ln.startswith("  (b)")]
    assert 0 < len(lines) <= 2 * commands.MAX_EXAMPLES


def test_without_a_book_stats_and_leak_scan_generate_one(monkeypatch, capsys):
    calls = []

    def fake_generate(n, seed):
        calls.append((n, seed))
        return list(book_400())[:6]

    monkeypatch.setattr(commands, "generate_book", fake_generate)
    assert run(["state", "stats"]) == 0
    assert run(["state", "leak-scan"]) == 0
    assert calls == [(2000, 7), (2000, 7)]
    capsys.readouterr()
