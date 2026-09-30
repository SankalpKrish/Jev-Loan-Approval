import dataclasses
import enum
import hashlib
from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel

from jevloan.canonical import canonical_json, sha256_hex, to_jsonable


class Color(enum.Enum):
    RED = "red"


class Outcome(enum.StrEnum):
    PROCEED = "PROCEED"


class Level(enum.IntEnum):
    HIGH = 3


@dataclasses.dataclass
class Point:
    x: int
    y: Color


class Model(BaseModel):
    name: str
    when: datetime
    inner: Point | None = None


def test_exact_format():
    assert canonical_json({"b": [1, 2], "a": {"z": None, "y": True}}) == '{"a":{"y":true,"z":null},"b":[1,2]}'


def test_key_order_does_not_matter():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_non_ascii_kept_unescaped():
    assert canonical_json({"नाम": "é"}) == '{"नाम":"é"}'


def test_enums_dataclasses_and_datetimes_become_plain_json():
    when = datetime(2026, 9, 29, 10, 15, 0, 123000, tzinfo=UTC)
    obj = {"o": Outcome.PROCEED, "c": Color.RED, "l": Level.HIGH, "p": Point(1, Color.RED), "d": when, "day": date(2026, 9, 29)}
    assert to_jsonable(obj) == {
        "o": "PROCEED",
        "c": "red",
        "l": 3,
        "p": {"x": 1, "y": "red"},
        "d": "2026-09-29T10:15:00.123000+00:00",
        "day": "2026-09-29",
    }
    assert canonical_json(obj).startswith('{"c":"red","d":"2026-09-29T10:15:00.123000+00:00"')


def test_pydantic_model_with_nested_dataclass():
    when = datetime(2026, 1, 2, 3, 4, 5)
    model = Model(name="n", when=when, inner=Point(2, Color.RED))
    assert canonical_json(model) == '{"inner":{"x":2,"y":"red"},"name":"n","when":"2026-01-02T03:04:05"}'


def test_tuples_and_sets():
    assert canonical_json({"t": (1, 2), "s": {3, 1, 2}}) == '{"s":[1,2,3],"t":[1,2]}'
    assert canonical_json(frozenset({"b", "a"})) == '["a","b"]'


def test_non_string_keys():
    assert canonical_json({2: "a", 1: "b", Color.RED: "c", None: "d"}) == '{"1":"b","2":"a","null":"d","red":"c"}'


def test_unserialisable_raises():
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_sha256_hex_str_and_bytes():
    expected = hashlib.sha256(b"abc").hexdigest()
    assert sha256_hex("abc") == sha256_hex(b"abc") == expected
    assert sha256_hex("é") == hashlib.sha256("é".encode()).hexdigest()
    assert len(sha256_hex("")) == 64
