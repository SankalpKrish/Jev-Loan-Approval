import functools
import os

import pytest

from jevloan.audit.log import AuditLog
from jevloan.cli import build_parser, main
from jevloan.config import RuntimeConfig, load_runtime
from jevloan.jev import commands
from jevloan.jev.gateway import JevGateway


class StubGate:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc

    def check(self, payload) -> None:
        if self.exc is not None:
            raise self.exc


@pytest.fixture
def standalone(monkeypatch, tmp_db):
    """Run `jev hello` without W1-pii and without reading config/.env: a stub gate, no egress guard, sim backend."""
    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(backend="sim", db_path=str(tmp_db)))
    monkeypatch.setattr(commands, "_make_gate", lambda: StubGate())
    monkeypatch.setattr(commands, "JevGateway", functools.partial(JevGateway, egress_guard=lambda inner, gate: inner))
    return tmp_db


def test_jev_hello_is_registered():
    args = build_parser().parse_args(["jev", "hello", "--backend", "sim", "--db", "x.db"])
    assert args.func is commands._hello and args.backend == "sim" and args.db == "x.db"
    assert build_parser().parse_args(["jev", "hello"]).backend is None


def test_hello_with_the_sim_backend_exits_0_and_prints_typed_answers(standalone, capsys):
    assert main(["jev", "hello", "--backend", "sim", "--db", str(standalone)]) == 0

    out = capsys.readouterr().out
    assert "model=sim-jev-0.1" in out
    assert "PROVISIONAL" in out
    for qid in ("hello_foir_within_limit", "hello_primary_weakness", "hello_repayment_conduct"):
        assert qid in out
    assert "noul" in out and "choice" in out and "score" in out and "derived_confidence" in out

    audit = AuditLog(standalone)
    (entry,) = audit.trail("HELLO")
    assert entry["event_type"] == "MODEL_CALL" and entry["stage"] == "hello" and entry["model_version"] == "sim-jev-0.1"
    assert audit.verify().ok


def test_hello_uses_the_db_option_over_the_config(standalone, tmp_path):
    other = tmp_path / "other.db"
    assert main(["jev", "hello", "--backend", "sim", "--db", str(other)]) == 0
    assert AuditLog(other).trail("HELLO")
    assert AuditLog(standalone).trail("HELLO") == []


def test_hello_backend_option_overrides_the_config(monkeypatch, standalone):
    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(backend="real", db_path=str(standalone)))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["jev", "hello", "--backend", "sim"]) == 0  # would need a key if it stayed on "real"


def test_hello_exits_2_when_the_gate_blocks(monkeypatch, standalone, capsys):
    monkeypatch.setattr(commands, "_make_gate", lambda: StubGate(RuntimeError("gate down")))
    assert main(["jev", "hello", "--backend", "sim", "--db", str(standalone)]) == 2
    out = capsys.readouterr().out
    assert "FAILED" in out and "pii_blocked" in out
    (entry,) = AuditLog(standalone).trail("HELLO")
    assert entry["event_type"] == "PII_BLOCK"


def test_hello_exits_2_when_the_backend_fails(monkeypatch, standalone, capsys):
    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(backend="sim", db_path=str(standalone), force_human_review=True))
    assert main(["jev", "hello", "--backend", "sim", "--db", str(standalone)]) == 2
    assert "forced_human" in capsys.readouterr().out
    (entry,) = AuditLog(standalone).trail("HELLO")
    assert entry["event_type"] == "MODEL_FAILURE"


def test_hello_on_the_real_backend_without_a_key_exits_2_with_a_clear_message(monkeypatch, standalone, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["jev", "hello", "--backend", "real", "--db", str(standalone)]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_hello_questions_follow_the_structured_rubric_format():
    questions = commands.HELLO_QUESTIONS
    assert sorted(q["type"] for q in questions.values()) == ["choice", "noul", "score"]
    for question in questions.values():
        assert {"question", "focus", "refer_to"} <= set(question["instructions"])
        assert all(ref.startswith("`") and ref.endswith("`") for ref in question["instructions"]["refer_to"])
    noul = questions["hello_foir_within_limit"]["criteria"]
    assert set(noul) == {"true", "false"}
    assert 2 <= len(noul["true"]["examples"]) <= 3 and 2 <= len(noul["false"]["examples"]) <= 3
    assert "not_for" in noul["false"]
    for option in questions["hello_primary_weakness"]["criteria"].values():
        assert {"what", "not_for", "examples"} <= set(option) and 1 <= len(option["examples"]) <= 3
    levels = questions["hello_repayment_conduct"]["criteria"]
    assert [level["level"] for level in levels] == ["poor", "fair", "clean"]  # worst to best
    assert all({"what", "not_for", "examples"} <= set(level) for level in levels)


def test_hello_with_the_real_gate_and_egress_guard(monkeypatch, tmp_db, capsys):
    pytest.importorskip("jevloan.pii.egress")
    pytest.importorskip("jevloan.pii.gate")
    monkeypatch.setattr(commands, "load_runtime", lambda: RuntimeConfig(backend="sim", db_path=str(tmp_db)))

    assert main(["jev", "hello", "--backend", "sim", "--db", str(tmp_db)]) == 0
    assert "model=sim-jev" in capsys.readouterr().out
    audit = AuditLog(tmp_db)
    assert [e["event_type"] for e in audit.trail("HELLO")] == ["MODEL_CALL"]  # the gate found nothing in the hello payload
    assert audit.verify().ok


@pytest.mark.real_jev
def test_hello_against_real_jev(tmp_db, capsys):
    pytest.importorskip("jevloan.pii.egress")
    pytest.importorskip("jevloan.pii.gate")
    load_runtime()  # reads .env, so a key kept there counts
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("TYPESAFE_API_KEY is not set")

    assert main(["jev", "hello", "--backend", "real", "--db", str(tmp_db)]) == 0

    out = capsys.readouterr().out
    assert "PROVISIONAL" not in out
    audit = AuditLog(tmp_db)
    (entry,) = audit.trail("HELLO")
    assert entry["event_type"] == "MODEL_CALL"
    assert entry["model_version"] and not entry["model_version"].startswith("sim-")
    assert entry["input_tokens"] > 0
    assert audit.verify().ok
