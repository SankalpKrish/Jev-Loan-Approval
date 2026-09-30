import hashlib
import os
from pathlib import Path

import pytest

from jevloan import config
from jevloan.config import (
    ConfigError,
    RuntimeConfig,
    load_runtime,
    load_yaml_versioned,
    repo_root,
    resolve_path,
)

ENV_VARS = ("JEVLOAN_BACKEND", "JEVLOAN_DB_PATH", "TYPESAFE_DEFAULT_MODEL", "TYPESAFE_API_KEY")


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake repo root with its own .env, and an os.environ copy that dotenv can write to freely."""
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in ENV_VARS:
        os.environ.pop(name, None)
    monkeypatch.setattr(config, "repo_root", lambda: tmp_path)
    return tmp_path


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_root_has_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_resolve_path(tmp_path: Path):
    assert resolve_path("config/runtime.yaml") == repo_root() / "config" / "runtime.yaml"
    assert resolve_path(tmp_path / "x.db") == tmp_path / "x.db"


def test_load_yaml_versioned(tmp_path: Path):
    text = 'version: "v9"\nvalue: 3\n'
    path = write(tmp_path / "c.yaml", text)
    data, version, digest = load_yaml_versioned(path)
    assert data == {"version": "v9", "value": 3}
    assert version == "v9"
    assert digest == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("text", ["value: 3\n", "- a\n- b\n", "", "a: [unclosed\n"])
def test_load_yaml_versioned_rejects_bad_files(tmp_path: Path, text: str):
    with pytest.raises(ConfigError):
        load_yaml_versioned(write(tmp_path / "c.yaml", text))


def test_load_yaml_versioned_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_yaml_versioned(tmp_path / "nope.yaml")


def test_shipped_runtime_yaml(isolated_env: Path):
    cfg = load_runtime(repo_root() / "config" / "runtime.yaml")
    assert cfg == RuntimeConfig(version="runtime-v1", backend="sim")
    assert (cfg.model, cfg.timeout_budget_s, cfg.max_concurrency, cfg.rate_limit_rpm) == ("jev-latest", 2.0, 16, 1000)
    assert (cfg.breaker_failure_threshold, cfg.breaker_cooldown_s) == (5, 30.0)
    assert (cfg.db_path, cfg.book_path) == ("data/jevloan.db", "data/book.jsonl")
    assert cfg.usd_per_million_input_tokens == 0.042
    assert cfg.force_human_review is False


def test_yaml_values_are_applied(isolated_env: Path):
    path = write(isolated_env / "r.yaml", 'version: "x"\nbackend: real\ntimeout_budget_s: 0.5\nforce_human_review: true\n')
    cfg = load_runtime(path)
    assert (cfg.version, cfg.backend, cfg.timeout_budget_s, cfg.force_human_review) == ("x", "real", 0.5, True)


def test_env_overrides(isolated_env: Path):
    path = write(isolated_env / "r.yaml", 'version: "x"\nbackend: sim\n')
    os.environ.update(JEVLOAN_BACKEND="real", JEVLOAN_DB_PATH="/tmp/other.db", TYPESAFE_DEFAULT_MODEL="jev-1.13.0")
    cfg = load_runtime(path)
    assert (cfg.backend, cfg.db_path, cfg.model) == ("real", "/tmp/other.db", "jev-1.13.0")


def test_empty_env_var_is_ignored(isolated_env: Path):
    path = write(isolated_env / "r.yaml", 'version: "x"\nbackend: sim\n')
    os.environ["JEVLOAN_BACKEND"] = ""
    assert load_runtime(path).backend == "sim"


def test_dotenv_is_loaded_but_does_not_override_real_env(isolated_env: Path):
    write(isolated_env / ".env", "JEVLOAN_BACKEND=real\nTYPESAFE_DEFAULT_MODEL=from-dotenv\nTYPESAFE_API_KEY=k-123\n")
    path = write(isolated_env / "r.yaml", 'version: "x"\nbackend: sim\n')
    os.environ["TYPESAFE_DEFAULT_MODEL"] = "from-env"
    cfg = load_runtime(path)
    assert cfg.backend == "real"  # from .env
    assert cfg.model == "from-env"  # the real environment wins
    assert os.environ["TYPESAFE_API_KEY"] == "k-123"  # exported for the SDK


def test_invalid_values_raise_config_error(isolated_env: Path):
    with pytest.raises(ConfigError):
        load_runtime(write(isolated_env / "a.yaml", 'version: "x"\nbackend: mainframe\n'))
    with pytest.raises(ConfigError):
        load_runtime(write(isolated_env / "b.yaml", 'version: "x"\ntimeout_budget_s: 0\n'))
    with pytest.raises(ConfigError):  # typos must not be silently ignored
        load_runtime(write(isolated_env / "c.yaml", 'version: "x"\ntimeout_budget: 3\n'))
    os.environ["JEVLOAN_BACKEND"] = "nope"
    with pytest.raises(ConfigError):
        load_runtime(write(isolated_env / "d.yaml", 'version: "x"\n'))
