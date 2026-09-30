"""Runtime configuration and versioned-YAML loading."""

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jevloan.canonical import sha256_hex


class ConfigError(ValueError):
    """A config file is missing, malformed or invalid."""


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "runtime-v1"
    backend: Literal["real", "sim"] = "sim"
    model: str = "jev-latest"
    timeout_budget_s: float = Field(2.0, gt=0)
    max_concurrency: int = Field(16, ge=1)
    rate_limit_rpm: int = Field(1000, ge=1)
    breaker_failure_threshold: int = Field(5, ge=1)
    breaker_cooldown_s: float = Field(30.0, ge=0)
    db_path: str = "data/jevloan.db"
    book_path: str = "data/book.jsonl"
    usd_per_million_input_tokens: float = Field(0.042, ge=0)
    force_human_review: bool = False


# environment variable -> RuntimeConfig field
_ENV_OVERRIDES = {
    "JEVLOAN_BACKEND": "backend",
    "JEVLOAN_DB_PATH": "db_path",
    "TYPESAFE_DEFAULT_MODEL": "model",
}


def repo_root() -> Path:
    """The directory containing pyproject.toml, found from this file, then from the cwd."""
    for start in (Path(__file__).resolve().parent, Path.cwd()):
        for directory in (start, *start.parents):
            if (directory / "pyproject.toml").is_file():
                return directory
    raise ConfigError("cannot locate the repo root: no pyproject.toml above this file or the cwd")


def resolve_path(p: str | Path) -> Path:
    """Absolute paths pass through; relative ones are taken relative to the repo root."""
    path = Path(p).expanduser()
    return path if path.is_absolute() else repo_root() / path


def load_yaml_versioned(path: str | Path) -> tuple[dict[str, Any], str, str]:
    """Load a YAML mapping that has a top-level `version`; returns (data, version, sha256 of file bytes)."""
    resolved = resolve_path(path)
    try:
        raw = resolved.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {resolved}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{resolved} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{resolved} must contain a YAML mapping at the top level")
    if "version" not in data:
        raise ConfigError(f"{resolved} has no top-level 'version'")
    return data, str(data["version"]), sha256_hex(raw)


def load_runtime(path: str | Path = "config/runtime.yaml") -> RuntimeConfig:
    """Load runtime config. `.env` in the repo root is read without overriding real env vars,
    then JEVLOAN_BACKEND / JEVLOAN_DB_PATH / TYPESAFE_DEFAULT_MODEL override the YAML."""
    load_dotenv(repo_root() / ".env", override=False)
    data, version, _ = load_yaml_versioned(path)
    data["version"] = version
    for env_name, field in _ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            data[field] = os.environ[env_name]
    try:
        return RuntimeConfig(**data)
    except ValidationError as exc:
        raise ConfigError(f"invalid runtime config {path}: {exc}") from exc
