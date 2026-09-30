"""Shared helpers for the test_state_* modules: a cached 400-file book and its states. Contains no tests."""

from __future__ import annotations

import re
from functools import lru_cache

from jevloan.data.generator import generate_book
from jevloan.data.schema import LoanFile
from jevloan.state import STAGES, build_state, redactor_for

BARE_TOKEN = re.compile(r"\[[A-Z]+(?:_[A-Z0-9]+)?\]")
TOKEN_ANY = re.compile(r"\[[A-Z]+(?:_[A-Z0-9]+)?\]")


@lru_cache(maxsize=1)
def book_400() -> tuple[LoanFile, ...]:
    return tuple(generate_book(400, 7))


@lru_cache(maxsize=1)
def states_400() -> dict[tuple[str, str], dict]:
    """{(file_id, stage): state} for every file of the 400-file book, built once per test session."""
    out: dict[tuple[str, str], dict] = {}
    for f in book_400():
        r = redactor_for(f)
        for stage in STAGES:
            out[(f.file_id, stage)] = build_state(f, stage, redactor=r)
    return out


def appraisal(f: LoanFile) -> dict:
    return states_400()[(f.file_id, "appraisal")]


def doc_of(state: dict, prefix: str) -> dict | None:
    """The first document of the state whose doc_type starts with ``prefix``."""
    return next((d for d in state["documents"] if d["doc_type"].startswith(prefix)), None)


def files_where(pred) -> list[LoanFile]:
    return [f for f in book_400() if pred(f)]
