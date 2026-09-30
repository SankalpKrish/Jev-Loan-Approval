import json

import pytest

from jevloan import cli
from jevloan.data.commands import compute_stats, format_stats
from jevloan.data.schema import load_book
from test_data_common import book_2000


def test_generate_writes_a_deterministic_book(tmp_path, capsys):
    a, b = tmp_path / "a.jsonl", tmp_path / "b" / "b.jsonl"
    assert cli.main(["data", "generate", "--n", "40", "--seed", "3", "--out", str(a)]) == 0
    assert cli.main(["data", "generate", "--n", "40", "--seed", "3", "--out", str(b)]) == 0
    assert "wrote 40 synthetic files (seed 3)" in capsys.readouterr().out
    assert a.read_bytes() == b.read_bytes()
    lines = a.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40 and json.loads(lines[0])["file_id"] == "F000001"
    assert len(list(load_book(a))) == 40


def test_generate_defaults_to_2000_files_and_the_runtime_book_path(monkeypatch, tmp_path, capsys):
    from jevloan.data import commands
    from jevloan.config import RuntimeConfig

    target = tmp_path / "runtime_book.jsonl"
    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(book_path=str(target)))
    seen = {}
    monkeypatch.setattr(commands, "generate_book", lambda n, seed: seen.update(n=n, seed=seed) or [])
    assert cli.main(["data", "generate"]) == 0
    assert seen == {"n": 2000, "seed": 7} and target.exists()


def test_stats_prints_rates_outcomes_disparities_and_groups(tmp_path, capsys):
    path = tmp_path / "book.jsonl"
    cli.main(["data", "generate", "--n", "200", "--seed", "7", "--out", str(path)])
    capsys.readouterr()
    assert cli.main(["data", "stats", "--book", str(path)]) == 0
    out = capsys.readouterr().out
    for needle in ("Synthetic book: 200 files", "Segment mix", "salaried_personal", "sanctionable", "fraud", "missing item",
                   "memo defect", "12-month outcome", "repays", "defaults", "lang_doc_script", "gender_income_proxy",
                   "pincode_bureau_thin", "By gender", "By pincode_cluster", "By language", "PC7"):
        assert needle in out, needle


def test_stats_on_a_missing_book_fails_cleanly(tmp_path, capsys):
    assert cli.main(["data", "stats", "--book", str(tmp_path / "nope.jsonl")]) == 1
    assert "jevloan data generate" in capsys.readouterr().out


def test_compute_stats_matches_direct_counts():
    book = book_2000()
    s = compute_stats(book)
    assert s["n"] == 2000
    assert s["sanctionable"] == pytest.approx(sum(f.labels.sanctionable for f in book) / 2000)
    assert s["segments"] == {"salaried_personal": 600, "self_employed": 400, "msme_business": 500, "secured_home": 500}
    assert sum(s["outcomes"].values()) == pytest.approx(1.0)
    assert s["groups"]["gender"]["F"]["n"] + s["groups"]["gender"]["M"]["n"] + s["groups"]["gender"]["X"]["n"] == 2000
    assert "Engineered disparity subsets" in format_stats(s)
