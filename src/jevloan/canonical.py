"""Canonical JSON and hashing, shared by the audit chain, state hashes and config hashes."""

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel


def to_jsonable(obj: Any) -> Any:
    """Convert dataclasses, pydantic models, enums, datetimes, sets and tuples to plain JSON types."""
    if isinstance(obj, Enum):  # before str/int: StrEnum members are also str
        return to_jsonable(obj.value)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump())
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {_key(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted((to_jsonable(v) for v in obj), key=canonical_json)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    raise TypeError(f"{type(obj).__name__} is not JSON-serialisable")


def _key(key: Any) -> str:
    key = to_jsonable(key)
    return key if isinstance(key, str) else json.dumps(key)  # same spelling json.dumps gives int/bool keys


def canonical_json(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, non-ASCII kept as-is."""
    return json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    return sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()
