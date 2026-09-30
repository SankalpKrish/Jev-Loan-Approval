import json

from jevloan.data.generator import generate_book
from jevloan.data.schema import write_book
from jevloan.eval.core import evaluate_run


def test_sim_run_writes_filled_reports_and_never_recommends_go(tmp_path):
    book_path = tmp_path / "book.jsonl"
    run_dir = tmp_path / "runs" / "sim-small"
    run_dir.mkdir(parents=True)
    files = generate_book(10, 23)
    write_book(files, book_path)
    (run_dir / "metadata.json").write_text(json.dumps({"run_id": "sim-small", "backend": "sim", "book_path": str(book_path),
                                                           "policy_version": "test-policy", "audit_db_path": str(tmp_path / "missing.db")}))
    rows = []
    for f in files:
        for stage, modules in (("appraisal", "ABCD"), ("sanction_docs", "E"), ("monitoring", "F")):
            rows.append({"run_id": "sim-small", "file_id": f.file_id, "segment": f.segment, "stage": stage,
                         "outcome": "HUMAN_REVIEW", "decision": {"outcome": "HUMAN_REVIEW", "modules": {
                             m: {"module": m, "outcome": "uncertain", "score": None, "band": None, "flags": [], "reasons": []} for m in modules}},
                         "answers": {}, "model_version": "sim", "latency_ms": 10.0, "input_tokens": 100, "failure_kind": None})
    (run_dir / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = evaluate_run("sim-small", runs_dir=str(tmp_path / "runs"), reports_dir=str(tmp_path / "reports"), book_path=str(book_path))
    out = tmp_path / "reports" / "sim-small"
    assert result["recommendation"] == "PROVISIONAL: NOT A GO"
    assert result["replay_rows"] == 24  # default holdout excludes numeric ids divisible by five
    assert (out / "metrics.json").exists()
    memo = (out / "GO_NO_GO_MEMO.md").read_text()
    assert "{{" not in memo
    assert "sim-small" in memo
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["agreement"][0]["n"] == 8
    assert metrics["pii"]["fixture_total"] >= 150


def test_split_uses_every_fifth_numeric_file_as_dev(tmp_path):
    book_path = tmp_path / "book.jsonl"
    run_dir = tmp_path / "runs" / "split"
    run_dir.mkdir(parents=True)
    files = generate_book(10, 2)
    write_book(files, book_path)
    (run_dir / "metadata.json").write_text(json.dumps({"backend": "sim", "book_path": str(book_path)}))
    (run_dir / "decisions.jsonl").write_text("")
    result = evaluate_run("split", split="dev", runs_dir=str(tmp_path / "runs"), reports_dir=str(tmp_path / "reports"), book_path=str(book_path))
    assert result["book_n"] == 2
