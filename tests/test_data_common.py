"""Shared helpers for the test_data_* modules (cached books and a small PII scanner). Contains no tests."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import date
from functools import lru_cache

from jevloan.data.generator import generate_book
from jevloan.data.schema import LoanFile

MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
_book_seconds: dict[str, float] = {}


@lru_cache(maxsize=1)
def book_2000() -> tuple[LoanFile, ...]:
    started = time.perf_counter()
    book = tuple(generate_book(2000, 7))
    _book_seconds["2000"] = time.perf_counter() - started
    return book


def book_2000_seconds() -> float:
    book_2000()
    return _book_seconds["2000"]


@lru_cache(maxsize=1)
def book_300() -> tuple[LoanFile, ...]:
    return tuple(generate_book(300, 11))


def ascii_digits(text: str) -> str:
    """Native-script digits to ASCII."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in text)


PAN_RE = re.compile(r"(?i)(?<![A-Za-z])[A-Z]{5}\d{4}[A-Z](?![A-Za-z])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RUN_RE = re.compile(r"\d+(?:[ \-./_]\d+)*")


def digit_runs(text: str) -> list[str]:
    """Digit runs as a PII gate sees them: runs separated by a single space, dash, dot, slash or underscore are
    joined, then compacted to bare digits."""
    return [re.sub(r"\D", "", m) for m in RUN_RE.findall(ascii_digits(text))]


def unexplained_pii(text: str, inv) -> list[str]:
    """Identifier-looking things in ``text`` that are not in the inventory ``inv``."""
    problems = []
    for m in PAN_RE.findall(text):
        if m.upper() not in inv.pans:
            problems.append(f"PAN {m}")
    for m in EMAIL_RE.findall(text):
        if m not in inv.emails:
            problems.append(f"email {m}")
    for run in digit_runs(text):
        if len(run) < 9:
            continue
        if run in inv.aadhaars or run in inv.account_numbers:
            continue
        if any(run == ph or run in ("0" + ph, "91" + ph, "091" + ph, "0091" + ph) for ph in inv.phones):
            continue
        problems.append(f"digits {run}")
    return problems


def parse_date(s: str) -> date:
    s = s.strip()
    m = re.fullmatch(r"(\d{2}) ([A-Z][a-z]{2}) (\d{4})", s)
    if m:
        return date(int(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.fullmatch(r"(\d{2})[/-](\d{2})[/-](\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    raise ValueError(s)


DATE_PAT = r"(\d{2} [A-Z][a-z]{2} \d{4}|\d{2}[/-]\d{2}[/-]\d{4})"


def amounts(text: str) -> list[int]:
    """Rupee amounts written as Rs. / Rs / ₹ / INR followed by an Indian-grouped number."""
    out = []
    for m in re.finditer(r"(?:Rs\.?\s?|₹|INR\s)(\d[\d,]*)", text):
        out.append(int(m[1].replace(",", "")))
    return out
