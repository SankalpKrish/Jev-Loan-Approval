from argparse import Namespace

from jevloan.api.commands import _serve
from jevloan.config import RuntimeConfig


def test_production_serve_refuses_simulator(monkeypatch, capsys):
    monkeypatch.setattr("jevloan.api.commands.load_runtime", lambda: RuntimeConfig(backend="sim"))
    assert _serve(Namespace(mode="production", host="127.0.0.1", port=8000)) == 2
    assert "backend=sim" in capsys.readouterr().err


def test_production_serve_refuses_unassigned_owners(monkeypatch, capsys):
    monkeypatch.setattr("jevloan.api.commands.load_runtime", lambda: RuntimeConfig(backend="real"))
    monkeypatch.setattr("jevloan.api.commands.owners_unassigned", lambda policy: ["credit_review"])
    assert _serve(Namespace(mode="production", host="127.0.0.1", port=8000)) == 2
    assert "credit_review" in capsys.readouterr().err
