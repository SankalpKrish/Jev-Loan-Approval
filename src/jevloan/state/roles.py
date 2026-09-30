"""Tokenising a loan file: the per-file Redactor, the text clean-up, ``entity_roles`` and the documents block.

One ``Redactor`` per file gives every document, every stage and ``entity_roles`` the same token assignment
(``redactor_for``). The Redactor swaps identifiers for tokens; on top of that this module

* rewrites every birth-date mention to ``[DOB_n]`` (``[DOB_1]`` is the applicant's declared birth year), so the
  model compares tokens instead of doing date arithmetic;
* drops honorifics (``Mrs. [APPLICANT]`` would leak gender) and the file id the memo prints;
* folds a country-code prefix into the phone token that follows it (``(+91) [PHONE_1]`` -> ``[PHONE_1]``);
* turns masked identifiers (``XXXX XXXX 0123``, ``XXXXXXX3456``) into the token of the value they mask, and a
  GSTIN into its embedded PAN token;
* trims a document to 70 words at a sentence boundary (it never does for the current generator).

``entity_roles`` is what the *application form* declares, tokenised with the same Redactor. A document token that
matches none of these roles is the mismatch signal.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator

from jevloan.canonical import canonical_json
from jevloan.data.schema import LoanFile
from jevloan.pii.redact import KnownEntities, Redactor

MAX_DOCUMENTS = 6
MAX_DOC_WORDS = 70

BARE_TOKEN_RE = re.compile(r"\[[A-Z]+(?:_[A-Z0-9]+)?\]")


class StateBuildError(ValueError):
    """The state builder refuses to emit a state it cannot vouch for (for example a role value that is not a bare
    token). The pipeline treats any exception from ``build_state`` as a model-free failure."""


# ------------------------------------------------------------------------------------------------ the Redactor


def _raw_texts(file: LoanFile) -> Iterator[str]:
    """Every raw string the state builders redact, in one fixed order: role values, documents, memo, KFS,
    covenant evidence. Redacting them in this order gives any name only the detectors discover a stable token."""
    a, app = file.applicant, file.application
    yield from (a.name, a.pan, a.aadhaar, a.phone, a.address.line1)
    if app.employer_name:
        yield app.employer_name
    if file.co_applicant:
        yield from (file.co_applicant.name, file.co_applicant.pan)
    if app.business_name:
        yield app.business_name
    if file.gst:
        yield _gstin_pan(file.gst.gstin)
    if file.bank.salary_narration_employer:
        yield file.bank.salary_narration_employer
    for d in file.documents:
        yield d.text
    yield file.sanction_memo.text
    yield from file.sanction_memo.conditions
    yield file.kfs.text
    for c in file.post_disbursal.covenants:
        yield c.evidence_text


def redactor_for(file: LoanFile) -> Redactor:
    """A new Redactor for this file's ``pii_inventory`` (aliases included), primed on every text of the file so
    that the token numbering does not depend on which stage is built first."""
    redactor = Redactor(KnownEntities.from_inventory(file.pii_inventory))
    for text in _raw_texts(file):
        redactor.redact(text)
    return redactor


_local = threading.local()
_CACHE_SIZE = 8


def shared_redactor(file: LoanFile) -> Redactor:
    """A per-thread cache of primed Redactors, so building the three stages of one file costs one Redactor. A
    Redactor is not thread-safe, so the cache is thread-local; the key includes a hash of the inventory."""
    cache: OrderedDict[tuple[str, str], Redactor] | None = getattr(_local, "cache", None)
    if cache is None:
        cache = _local.cache = OrderedDict()
    inv_hash = hashlib.sha256(canonical_json(file.pii_inventory).encode()).hexdigest()[:16]
    key = (file.file_id, inv_hash)
    redactor = cache.get(key)
    if redactor is None:
        redactor = redactor_for(file)
        cache[key] = redactor
        while len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    return redactor


# ------------------------------------------------------------------------------------------------ text clean-up

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
_BIRTH_RE = re.compile(
    r"(?<![A-Za-z])(?P<kw>date[ \-]of[ \-]birth|birth[ \-]date|d\.[ ]?o\.[ ]?b\.?|dob|born(?:[ ]on)?)(?![A-Za-z])"
    r"(?P<sep>[\s:.\-]*)"
    rf"(?P<date>(?:\d{{1,2}}(?:st|nd|rd|th)?[\s/.\-]+)?(?:(?:{_MONTHS})\.?[\s/.\-,]*|\d{{1,2}}[\s/.\-]+)?(?P<year>(?:19|20)\d{{2}}))(?!\d)",
    re.IGNORECASE,
)
_HONORIFIC_RE = re.compile(
    r"(?<![A-Za-z])(?:mr|mrs|ms|miss|shri|shree|smt|sri|kumari|dr|s/o|d/o|w/o|c/o)\b\.?[ ]+(?=\[(?:APPLICANT|PERSON_[A-Z0-9]+)\])",
    re.IGNORECASE,
)
_FILE_ID_RE = re.compile(r"(?:[ ]+for)?[ ]+F\d{6}(?![0-9])")
_MASKED_AADHAAR_RE = re.compile(r"(?<![A-Za-z0-9])[Xx*]{4}[ \-][Xx*]{4}[ \-](\d{4})(?![0-9])")
_MASKED_ACCOUNT_RE = re.compile(r"(?<![A-Za-z0-9])[Xx*]{4,}(\d{4})(?![0-9])")
_COUNTRY_CODE_RE = re.compile(r"(?:\([ ]?\+?[ ]?91[ ]?\)|\+[ ]?91|(?<![0-9])0091)[ \-.]?(?=\[PHONE_[A-Z0-9]+\])")
_GSTIN_RE = re.compile(r"\b\d{2}(\[PAN_[A-Z0-9]+\])\d[A-Z][A-Z0-9]\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def rewrite_birth_dates(text: str, dob_year: int, year_map: dict[int, int]) -> str:
    """Rewrite each birth-date mention (``born Mar 1988``, ``Date of birth: Mar 1988``, ``DOB Mar 1988``,
    ``D.O.B. Mar 1988``) to ``[DOB_n]``: n = 1 for ``dob_year``, 2, 3, ... for other years in order of first
    appearance. ``year_map`` (year -> n) is shared by every text of the file and is updated in place."""
    year_map.setdefault(dob_year, 1)

    def repl(m: re.Match[str]) -> str:
        year = int(m.group("year"))
        n = year_map.setdefault(year, max(year_map.values()) + 1)
        return f"{m.group('kw')}{m.group('sep')}[DOB_{n}]"

    return _BIRTH_RE.sub(repl, text)


def truncate_words(text: str, max_words: int = MAX_DOC_WORDS) -> tuple[str, bool]:
    """(text, truncated). Keeps whole sentences up to ``max_words``; a first sentence that is itself too long is
    cut at a word."""
    words = text.split()
    if len(words) <= max_words:
        return text, False
    kept: list[str] = []
    used = 0
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        n = len(sentence.split())
        if used + n > max_words:
            break
        kept.append(sentence)
        used += n
    if not kept:
        return " ".join(words[:max_words]), True
    return " ".join(kept), True


def clean_text(
    file: LoanFile, redactor: Redactor, text: str, *, year_map: dict[int, int] | None = None, protect: tuple[str, ...] = ()
) -> str:
    """Redact one raw text of ``file`` and apply the clean-up above. ``protect`` lists bank-policy phrases that
    must survive verbatim (the redactor would turn "loans above 50 lakh" into an amount band)."""
    markers: dict[str, str] = {}
    for i, phrase in enumerate(sorted(set(protect), key=len, reverse=True)):
        marker = chr(0xE000 + i)  # private-use characters pass through the redactor untouched
        if phrase and phrase in text:
            text = text.replace(phrase, marker)
            markers[marker] = phrase
    out = redactor.redact(text)
    for marker, phrase in markers.items():
        out = out.replace(marker, phrase)
    out = _FILE_ID_RE.sub("", out)
    out = _HONORIFIC_RE.sub("", out)
    out = _mask_to_tokens(file, redactor, out)
    out = _COUNTRY_CODE_RE.sub("", out)  # "(+91) [PHONE_1]" -> "[PHONE_1]": the country code is part of the number
    out = _GSTIN_RE.sub(r"(PAN \1)", out)
    out = rewrite_birth_dates(out, file.applicant.dob_year, year_map if year_map is not None else {})
    return " ".join(out.split())


def _mask_to_tokens(file: LoanFile, redactor: Redactor, text: str) -> str:
    """A masked identifier keeps its last four digits, which are still a partial identifier. Replace it with the
    token of the applicant value it masks (or ``[REDACTED]`` if it masks something else)."""

    def token_if_last4(raw: str, last4: str) -> str:
        return redactor.redact(raw) if raw.endswith(last4) else "[REDACTED]"

    text = _MASKED_AADHAAR_RE.sub(lambda m: token_if_last4(file.applicant.aadhaar, m.group(1)), text)
    return _MASKED_ACCOUNT_RE.sub(lambda m: token_if_last4(file.bank.account_number, m.group(1)), text)


# ------------------------------------------------------------------------------------------------ entity_roles


def _gstin_pan(gstin: str) -> str:
    """The PAN inside a GSTIN: characters 3 to 12."""
    return gstin[2:12]


def _canonical_address_lines(file: LoanFile) -> list[str]:
    alias_keys = set(file.pii_inventory.aliases)
    return [ln for ln in file.pii_inventory.address_lines if ln not in alias_keys]


def _address_in_document(file: LoanFile, doc_type: str) -> str | None:
    """The canonical line1 of the address a document of ``doc_type`` prints (the raw schema keeps the business
    and property addresses only in the documents). The applicant's own residence is excluded."""
    own = file.applicant.address.line1.casefold()
    for doc in file.documents:
        if doc.doc_type != doc_type:
            continue
        low = doc.text.casefold()
        hits = [
            ln for ln in _canonical_address_lines(file)
            if ln.casefold() != own and re.search(rf"(?<![\w]){re.escape(ln.casefold())}(?![\w])", low)
        ]
        if hits:
            return max(hits, key=len)
    return None


def _bare(value: str, what: str) -> str:
    if not BARE_TOKEN_RE.fullmatch(value):
        raise StateBuildError(f"entity role {what} is not a bare token: {value!r}")
    return value


def entity_roles(file: LoanFile, redactor: Redactor) -> dict:
    """What the application form declares, as tokens. Every value is a bare token like ``[PAN_1]``, except the
    co-applicant's ``relation`` (an enum-like word) and an address that no document shows (None)."""
    a, app = file.applicant, file.application

    def tok(raw: str, what: str) -> str:
        return _bare(redactor.redact(raw), what)

    applicant: dict = {
        "name": tok(a.name, "applicant.name"),
        "pan": tok(a.pan, "applicant.pan"),
        "uid": tok(a.aadhaar, "applicant.uid"),
        "phone": tok(a.phone, "applicant.phone"),
        "residence_address": tok(a.address.line1, "applicant.residence_address"),
        "birth": "[DOB_1]",
    }
    if app.employer_name:
        applicant["employer"] = tok(app.employer_name, "applicant.employer")

    co = None
    if file.co_applicant is not None:
        co = {
            "name": tok(file.co_applicant.name, "co_applicant.name"),
            "pan": tok(file.co_applicant.pan, "co_applicant.pan"),
            "relation": file.co_applicant.relation,
        }

    business = None
    if app.business_name:
        biz_pan = _gstin_pan(file.gst.gstin) if file.gst else a.pan  # a proprietorship files under the owner's PAN
        biz_addr = _address_in_document(file, "business_registration")
        business = {
            "name": tok(app.business_name, "business.name"),
            "pan": tok(biz_pan, "business.pan"),
            "address": tok(biz_addr, "business.address") if biz_addr else None,
        }

    prop = None
    if file.property is not None:
        prop_addr = _address_in_document(file, "property_title")
        prop = {"address": tok(prop_addr, "property.address") if prop_addr else None}

    return {"applicant": applicant, "co_applicant": co, "business": business, "property": prop}


# ------------------------------------------------------------------------------------------------ documents


def documents_block(file: LoanFile, redactor: Redactor) -> list[dict]:
    """Every raw document, redacted: ``{doc_type, month_age, script, text}``, at most 6, at most 70 words each."""
    year_map: dict[int, int] = {}
    out = []
    for doc in file.documents[:MAX_DOCUMENTS]:
        text = clean_text(file, redactor, doc.text, year_map=year_map)
        text, _ = truncate_words(text, MAX_DOC_WORDS)
        out.append({"doc_type": doc.doc_type, "month_age": doc.month_age, "script": doc.script, "text": text})
    return out


def narration_org_token(file: LoanFile, redactor: Redactor) -> str | None:
    """``bank.salary_narration_org_token``: the redacted employer named in the salary credits of the bank
    statement (None when the applicant is not salaried). It differs from ``entity_roles.applicant.employer`` on a
    salary-pattern-mismatch file."""
    raw = file.bank.salary_narration_employer
    return _bare(redactor.redact(raw), "bank.salary_narration_org_token") if raw else None
