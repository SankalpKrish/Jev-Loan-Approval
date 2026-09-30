"""PII detectors (PLAN 3.5).

Every detector has the signature `detect_<name>(text) -> list[Finding]` and works on whatever string it is
given: `run_all` feeds it the normalised text and the digit-compacted view. A `Finding` never carries the raw
value, only its span and a masked form (first 2 and last 2 characters, or `X***` for names), so findings are
safe to log.

Design notes
  * Numeric detectors use lookarounds on digits only, so an identifier stuck to letters ("ACC12345678901")
    is still caught, while a hex digest ("3fa9...") is skipped by `_in_hex_blob`.
  * Our own redaction tokens (`[PAN_1]`, `[APPLICANT]`, `₹[25-50k]`) and state vocabulary / band strings are
    blanked or short-circuited before any detector looks at the text.
  * The name detector is the loosest one; see `lexicon.py` for how the false-positive surface is limited.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from jevloan.pii import lexicon as lx
from jevloan.pii.normalize import JOINABLE_RE, compact_digits, compact_digits_mapped, normalize

__all__ = [
    "Finding",
    "DETECTORS",
    "DETECTOR_NAMES",
    "STATE_VOCAB",
    "ALLOWED_TOKEN_PREFIXES",
    "mask",
    "mask_name",
    "run_all",
    "run_all_masked",
    "is_allowed_token",
    "blank_allowlisted",
    "detect_pan",
    "detect_aadhaar",
    "detect_aadhaar_vid",
    "detect_account_number",
    "detect_card_number",
    "detect_phone_in",
    "detect_email",
    "detect_upi_id",
    "detect_ifsc",
    "detect_passport",
    "detect_voter_id",
    "detect_driving_licence",
    "detect_pincode_ctx",
    "detect_person_name",
]


@dataclass(frozen=True, slots=True)
class Finding:
    detector: str
    start: int
    end: int
    masked: str

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)


_SQUASH_RE = re.compile(r"[ \t\n\-./_]")


def mask(value: str) -> str:
    """Keep the first 2 and last 2 characters, star the rest. Values of 4 or fewer characters keep only the
    first one so a masked form is never the whole value."""
    value = _SQUASH_RE.sub("", value)
    n = len(value)
    if n == 0:
        return ""
    if n == 1:
        return "*"
    if n <= 4:
        return value[0] + "*" * (n - 1)
    return value[:2] + "*" * (n - 4) + value[-2:]


def mask_name(value: str) -> str:
    value = value.strip()
    return (value[:1] + "***") if value else ""


# --------------------------------------------------------------------------------------------------
# Allowlist: redaction tokens, band strings, state vocabulary
# --------------------------------------------------------------------------------------------------

ALLOWED_TOKEN_PREFIXES = (
    "APPLICANT", "PERSON", "ORG", "EMPLOYER", "BUSINESS", "COMPANY", "PAN", "UID", "AADHAAR", "PHONE",
    "EMAIL", "ACCT", "ACCOUNT", "ADDR", "ADDRESS", "PIN", "CITY", "DATE", "AMOUNT", "ID", "IFSC", "REDACTED",
    "NAME", "BANK", "DOB", "GSTIN", "UPI", "CARD", "PASSPORT", "VOTER", "DL", "CO_APPLICANT", "GUARANTOR",
)  # fmt: skip
_PREFIX_ALT = "|".join(sorted(ALLOWED_TOKEN_PREFIXES, key=len, reverse=True))
# [PAN_1], [ORG_A], [PERSON_12], [APPLICANT], [PIN], and amount bands such as [25-50k], [<10k], [50L-1Cr].
_TOKEN_RE = re.compile(
    rf"\[(?:(?:{_PREFIX_ALT})(?:_[A-Z0-9]{{1,3}})?|[<>]?\d{{1,3}}(?:k|L|Cr)?(?:-\d{{1,3}}(?:k|L|Cr)?)?\+?)\]"
)
_FILLER = ""


def is_allowed_token(s: str) -> bool:
    return _TOKEN_RE.fullmatch(s) is not None


_UUID_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])")


def blank_allowlisted(text: str) -> str:
    """Replace our own redaction tokens (and UUIDs, whose digit groups would otherwise fuse across hyphens)
    with same-length filler so they cannot take part in a match."""
    if "[" in text:
        text = _TOKEN_RE.sub(lambda m: _FILLER * len(m.group(0)), text)
    if "-" in text and len(text) >= 36:
        text = _UUID_RE.sub(lambda m: _FILLER * len(m.group(0)), text)
    return text


# A short numeric band ("50L-1Cr", "30-40", "<600", "750-799", "1-3y", "800+", "0.5-0.8", "<5%") or NTC.
_BAND_RE = re.compile(
    r"(?:NTC|[<>]?\d{1,3}(?:\.\d{1,2})?(?:k|L|Cr|y|m|%)?(?:-\d{1,3}(?:\.\d{1,2})?(?:k|L|Cr|y|m|%)?)?\+?)"
)

STATE_VOCAB: frozenset[str] = frozenset(
    """
    salaried_personal self_employed msme_business secured_home appraisal sanction_docs monitoring
    jevloan.state.v1 personal_loan_unsecured business_loan_self_employed msme_term_loan home_loan
    salaried self_employed business low moderate high latin devanagari tamil bengali
    salary_slip form16 itr bank_statement_header address_proof_utility_bill address_proof_rent_agreement
    address_proof_passport pan_card_text employer_letter gst_return_summary business_registration
    property_title valuation_report gst_and_bank informal_declared clear disputed pending_mutation positive
    adverse pending corporate free disposable NTC
    identity_mismatch salary_pattern_mismatch gst_bank_mismatch synthetic_identity
    income_documentation repayment_history debt_burden employment_or_business_stability collateral
    bureau_thin_file yes no true false none
    PROCEED_TO_SANCTIONING_AUTHORITY HUMAN_REVIEW DECLINE_RECOMMENDED DEFICIENCY_NOTICE FRAUD_INVESTIGATION
    DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF DISBURSAL_BLOCKED WATCHLIST_T0 WATCHLIST_T1 WATCHLIST_T2 WATCHLIST_T3
    """.split()
)


def _is_vocab(s: str) -> bool:
    return s in STATE_VOCAB or (len(s) <= 12 and _BAND_RE.fullmatch(s) is not None)


# --------------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------------

_DIGIT_RE = re.compile(r"[0-9]")
_ALNUM_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")


def _in_hex_blob(text: str, start: int, end: int) -> bool:
    """True if [start, end) sits inside a long hex token (a digest or id), where digit runs are noise."""
    lo, hi = start, end
    while lo > 0 and text[lo - 1].isalnum():
        lo -= 1
    while hi < len(text) and text[hi].isalnum():
        hi += 1
    return (hi - lo) >= 24 and _HEX_RE.fullmatch(text, lo, hi) is not None


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _find(
    name: str,
    regex: re.Pattern[str],
    text: str,
    *,
    group: int = 0,
    validate: Callable[[str, re.Match[str]], bool] | None = None,
    hexcheck: bool = False,
) -> list[Finding]:
    out: list[Finding] = []
    for m in regex.finditer(text):
        s, e = m.span(group)
        if s < 0:
            continue
        raw = text[s:e]
        if validate is not None and not validate(raw, m):
            continue
        if hexcheck and _in_hex_blob(text, s, e):
            continue
        out.append(Finding(name, s, e, mask(raw)))
    return out


# --------------------------------------------------------------------------------------------------
# PAN
# --------------------------------------------------------------------------------------------------

# A PAN is five letters, four digits, one letter. The plain form is matched wherever it appears, so the PAN
# inside a GSTIN ("29ABCDE1234F1Z5": state code, PAN, entity digit, Z, checksum) is found as well.
_PAN_PLAIN_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z])")
# Stuck to preceding letters ("PANABCDE1234F"): only accept a real PAN shape (4th letter is the holder type).
_PAN_GLUED_RE = re.compile(r"(?<=[A-Za-z])[A-Za-z]{3}[PCHFATBLJGpchfatbljg][A-Za-z][0-9]{4}[A-Za-z](?![A-Za-z])")
# Separators between the blocks, or between every character ("A B C D E 1 2 3 4 F").
_PAN_SEP_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{5}|(?:[A-Za-z][ .\-_/]){4}[A-Za-z])[ .\-_/]?"
    r"(?:[0-9]{4}|(?:[0-9][ .\-_/]){3}[0-9])[ .\-_/]?[A-Za-z](?![A-Za-z0-9])"
)
_MONTH_WORDS = frozenset({"march", "april"})


def _pan_sep_ok(raw: str, m: re.Match[str]) -> bool:
    # "March 2026 A" style prose is not a PAN; a real separated PAN is not a month name.
    first = _SQUASH_RE.sub("", raw)[:5].lower()
    return first not in _MONTH_WORDS


_SEP_CHARS = " .-_/"


def _ascii_alpha(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _pan_context_ok(text: str, s: int, e: int) -> bool:
    """Cheap test before the PAN regexes run: five letters (contiguous, or alternating with separators) end just
    before the digits, and one letter (not followed by another letter) follows them."""
    n = len(text)
    j = s - 1
    if j >= 0 and text[j] in _SEP_CHARS:
        j -= 1
    if j < 4:
        return False
    if not (text[j - 4 : j + 1].isascii() and text[j - 4 : j + 1].isalpha()):
        # alternating "A B C D E"
        if j < 8 or not all(_ascii_alpha(text[j - 2 * k]) and text[j - 2 * k + 1] in _SEP_CHARS for k in range(4)) or not _ascii_alpha(text[j - 8]):
            return False
    k = e
    if k < n and text[k] in _SEP_CHARS:
        k += 1
    if k >= n or not _ascii_alpha(text[k]):
        return False
    return k + 1 >= n or not _ascii_alpha(text[k + 1])  # a digit may follow: GSTIN 29<PAN>1Z5


_PAN_CAND_RE = re.compile(r"[0-9]{4}|(?:[0-9][ .\-_/]){3}[0-9]")


def detect_pan(text: str) -> list[Finding]:
    """Search only around 4-digit blocks (or spaced digits): a PAN always has one, and prose rarely does."""
    out: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    n = len(text)
    last_hi = -1
    for cm in _PAN_CAND_RE.finditer(text):
        if not _pan_context_ok(text, cm.start(), cm.end()):
            continue
        lo, hi = max(0, cm.start() - 12), min(n, cm.end() + 4)
        if hi <= last_hi:
            continue
        last_hi = hi
        for rx, validate in ((_PAN_PLAIN_RE, None), (_PAN_GLUED_RE, None), (_PAN_SEP_RE, _pan_sep_ok)):
            for m in rx.finditer(text, lo, hi):
                s, e = m.span()
                if (s, e) in seen or (rx is _PAN_SEP_RE and any(s <= a and e >= b for a, b in seen)):
                    continue
                raw = text[s:e]
                if validate is not None and not validate(raw, m):
                    continue
                seen.add((s, e))
                out.append(Finding("pan", s, e, mask(raw)))
    return out


# --------------------------------------------------------------------------------------------------
# Aadhaar, VID, account and card numbers
# --------------------------------------------------------------------------------------------------

_AADHAAR_RE = re.compile(r"(?<![0-9])[2-9][0-9]{11}(?![0-9])")
_VID_RE = re.compile(r"(?<![0-9])[0-9]{16}(?![0-9])")
_ACCOUNT_RE = re.compile(r"(?<![0-9])[0-9]{9,}(?![0-9])")
_CARD_RE = re.compile(r"(?<![0-9])[0-9]{13,19}(?![0-9])")


def detect_aadhaar(text: str) -> list[Finding]:
    return _find("aadhaar", _AADHAAR_RE, text, hexcheck=True)


def detect_aadhaar_vid(text: str) -> list[Finding]:
    return _find("aadhaar_vid", _VID_RE, text, hexcheck=True)


def detect_account_number(text: str) -> list[Finding]:
    return _find("account_number", _ACCOUNT_RE, text, hexcheck=True)


def detect_card_number(text: str) -> list[Finding]:
    return _find("card_number", _CARD_RE, text, validate=lambda raw, m: _luhn_ok(raw), hexcheck=True)


# --------------------------------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------------------------------

# 10-digit mobile starting 6-9, or (country code | trunk 0) + a 10-digit national number (mobile or landline
# with STD code). Separators are handled by the compacted view.
_PHONE_RE = re.compile(r"(?<![0-9])(?:(?:(?:\+|00)?91|0)[1-9]|[6-9])[0-9]{9}(?![0-9])")
# "(022) 2345 6789": parentheses are not joinable separators, so match this shape directly.
_PHONE_PAREN_RE = re.compile(r"\(\s*0[0-9]{2,4}\s*\)[ \-.]?[0-9](?:[ \-.]?[0-9]){5,7}(?![0-9])")


def detect_phone_in(text: str) -> list[Finding]:
    out = _find("phone_in", _PHONE_RE, text, hexcheck=True)
    out += _find("phone_in", _PHONE_PAREN_RE, text)
    return out


# --------------------------------------------------------------------------------------------------
# Email and UPI
# --------------------------------------------------------------------------------------------------

_LOCAL = r"[A-Za-z0-9._%+\-]"
_EMAIL_RE = re.compile(
    rf"(?<!{_LOCAL}){_LOCAL}+\s?@\s?[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{{2,}}"
)
_EMAIL_BRACKET_RE = re.compile(
    rf"(?<!{_LOCAL})[A-Za-z0-9]{_LOCAL}*\s*[\[({{<]\s*at\s*[\])}}>]\s*[A-Za-z0-9\-]+"
    r"(?:\s*(?:\.|[\[({<]\s*dot\s*[\])}>])\s*[A-Za-z0-9\-]+)+",
    re.IGNORECASE,
)
_EMAIL_WORDS_RE = re.compile(
    rf"(?<!{_LOCAL})[A-Za-z0-9]{_LOCAL}*\s+at\s+[A-Za-z0-9\-]+(?:\s+dot\s+[A-Za-z0-9\-]+)+(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_UPI_RE = re.compile(
    rf"(?<!{_LOCAL})(?<!@)[A-Za-z0-9][A-Za-z0-9._\-]+@[A-Za-z]{{2,}}(?![A-Za-z0-9@])(?!\.[A-Za-z0-9])"
)
_OBF_AT_RE = re.compile(r"[\[({<]\s*at\s*[\])}>]|\sat\s.{1,60}?\sdot\s", re.IGNORECASE)


def detect_email(text: str) -> list[Finding]:
    out = _find("email", _EMAIL_RE, text)
    seen = {(f.start, f.end) for f in out}
    for rx in (_EMAIL_BRACKET_RE, _EMAIL_WORDS_RE):
        for f in _find("email", rx, text):
            if (f.start, f.end) not in seen:
                out.append(f)
                seen.add((f.start, f.end))
    return out


def detect_upi_id(text: str) -> list[Finding]:
    if "@" not in text:
        return []
    return _find("upi_id", _UPI_RE, text)


# --------------------------------------------------------------------------------------------------
# IFSC, passport, voter id, driving licence
# --------------------------------------------------------------------------------------------------

_IFSC_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{4}[ \-]?0[A-Za-z0-9]{6}(?![A-Za-z0-9])")
_PASSPORT_UPPER_SEP_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][ \-][0-9]{7}(?![A-Za-z0-9])")
_PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][0-9]{7}(?![A-Za-z0-9])")
_VOTER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{3}[0-9]{7}(?![A-Za-z0-9])")
_VOTER_UPPER_SEP_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}[ \-][0-9]{7}(?![A-Za-z0-9])")
_DL_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}[ \-]?[0-9]{2}[ \-]?[0-9]{11}(?![A-Za-z0-9])")


def _ifsc_ok(raw: str, m: re.Match[str]) -> bool:
    tail = _SQUASH_RE.sub("", raw)[5:]
    return any(ch.isdigit() for ch in tail)


_IFSC_TAIL_RE = re.compile(r"0[A-Za-z0-9]{6}(?![A-Za-z0-9])")


def detect_ifsc(text: str) -> list[Finding]:
    """Four letters, "0", six alphanumerics. Anchored on the "0" (a literal, so the scan is fast); the four
    letters before it are checked by hand."""
    out: list[Finding] = []
    for m in _IFSC_TAIL_RE.finditer(text):
        z = m.start()
        lo = z - 5 if z >= 5 and text[z - 1] in " -" else z - 4
        if lo < 0:
            continue
        head = text[lo : lo + 4]
        if not (head.isascii() and head.isalpha()) or (lo > 0 and text[lo - 1].isalnum() and text[lo - 1].isascii()):
            continue
        raw = text[lo : m.end()]
        if not _ifsc_ok(raw, m):
            continue
        out.append(Finding("ifsc", lo, m.end(), mask(raw)))
    return out


def detect_passport(text: str) -> list[Finding]:
    return _find("passport", _PASSPORT_RE, text) + _find("passport", _PASSPORT_UPPER_SEP_RE, text)


def detect_voter_id(text: str) -> list[Finding]:
    return _find("voter_id", _VOTER_RE, text) + _find("voter_id", _VOTER_UPPER_SEP_RE, text)


def detect_driving_licence(text: str) -> list[Finding]:
    return _find("driving_licence", _DL_RE, text)


# --------------------------------------------------------------------------------------------------
# PIN codes (context only: a bare 6-digit number is far too common)
# --------------------------------------------------------------------------------------------------

_PLACE_ALT = "|".join(sorted((re.escape(p) for p in lx.PLACE_NAMES), key=len, reverse=True))
_PIN_CAND_RE = re.compile(r"(?<![0-9])[1-9][0-9]{2}[ ]?[0-9]{3}(?![0-9])")
# What may sit just before a PIN, tested on a short window that ends at the candidate.
_PIN_TAILS = (
    re.compile(
        r"(?<![A-Za-z])(?:pin[\s\-]?code|pincode|pin|postal[\s\-]?code|post[\s\-]?code|zip(?:[\s\-]?code)?"
        r"|पिन[\s\-]?कोड|पिनकोड|पिन)[ \t]*(?:no\.?|number|#)?[ \t]*[:=\-]?[ \t]*$",
        re.IGNORECASE,
    ),
    re.compile(rf"(?<![A-Za-z])(?:{_PLACE_ALT})[ \t]*[,\-–]?[ \t]*(?:pin(?:code)?[ \t]*:?[ \t]*)?$", re.IGNORECASE),
    # "..., Bengaluru - 560034": a capitalised place after a comma, then a dash
    re.compile(r",[ \t]*[A-Z][A-Za-z]+(?:[ \t][A-Z][A-Za-z]+){0,2}[ \t]*[\-–][ \t]*$"),
)
_SIXDIG_RE = _PIN_CAND_RE


def detect_pincode_ctx(text: str) -> list[Finding]:
    out: list[Finding] = []
    for m in _PIN_CAND_RE.finditer(text):
        window = text[max(0, m.start() - 48) : m.start()]
        if any(rx.search(window) for rx in _PIN_TAILS):
            out.append(Finding("pincode_ctx", m.start(), m.end(), mask(m.group(0))))
    return out


# --------------------------------------------------------------------------------------------------
# Person names
# --------------------------------------------------------------------------------------------------

_HONORIFICS = (
    "mr", "mrs", "ms", "miss", "mx", "shri", "shree", "sri", "smt", "srimati", "shrimati", "sushri", "kumari",
    "km", "dr", "prof", "late",
)  # fmt: skip
_HON_SET = frozenset(_HONORIFICS)
_REL_SLASH = r"[sdwhc][ ]?[/\\][ ]?o"
_REL_WORDS = ("son of", "daughter of", "wife of", "husband of", "care of", "widow of", "father of", "mother of")


def _label_regex_piece(label: str) -> str:
    parts = []
    for word in label.split():
        parts.append(re.escape(word).replace("\\'", "['’]?").replace("'", "['’]?"))
    return r"\s+".join(parts)


_LABELS_ALL = sorted(set(lx.LABELS_A) | set(lx.LABELS_B), key=len, reverse=True)
_LABEL_A_SET = frozenset(lx.LABELS_A)
_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?P<rel>" + _REL_SLASH + "|" + "|".join(w.replace(" ", r"\s+") for w in _REL_WORDS) + r")"
    r"|(?P<hon>" + "|".join(_HONORIFICS) + r")\.?"
    r"|(?P<label>" + "|".join(_label_regex_piece(x) for x in _LABELS_ALL) + r")"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_TOKEN_SCAN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*\.?")
_CAPTOK_RE = re.compile(r"(?<![A-Za-z0-9_'’\-])[A-Z][A-Za-z'’\-]*\.?")
_INDIC = "ऀ-ॣ०-෿"
_INDIC_TOK_RE = re.compile(rf"[{_INDIC}]+")
_INDIC_ANY_RE = re.compile("[\u0900-\u0dff]")
_NATIVE_LABEL_RE = re.compile(
    "(?:" + "|".join(re.escape(x) for x in sorted(lx.NATIVE_LABELS, key=len, reverse=True)) + r")[ \t]*:[ \t]*"
)
_NATIVE_HON_RE = re.compile(
    "(?<![" + _INDIC + "])(?:" + "|".join(re.escape(x) for x in sorted(lx.NATIVE_HONORIFICS, key=len, reverse=True))
    + r")\.?(?![" + _INDIC + "])"
)
_NATIVE_STOP = frozenset(
    "का की के है हैं और में से पर को ने यह वह एक नाम पता खाता बैंक ऋण आवेदक आय वेतन दस्तावेज़ दस्तावेज प्रमाण पत्र "
    "नंबर संख्या राशि दिनांक तारीख कर्मचारी नियोक्ता उपभोक्ता ग्राहक मालिक कृपया धन्यवाद".split()
)

_VOCAB_WORDS = frozenset(w for entry in STATE_VOCAB for w in re.split(r"[_.\s]+", entry.lower()) if w)
_STOP_LOWER = lx.NON_NAME_CAPS | lx.NAME_TERMINATORS | _VOCAB_WORDS | frozenset(
    {"self", "joint", "individual", "sole", "same", "above", "below", "per", "who", "whose", "person", "has", "have",
     "that", "this", "these", "those", "which", "what", "when", "where", "there", "their", "they", "was", "were",
     "will", "would", "can", "may", "must", "should", "been", "being", "also", "only", "any", "all", "each",
     "verified", "confirmed", "present", "absent", "missing", "matches", "match", "mismatch", "declared",
     "employed", "unemployed", "retired", "student", "housewife", "professional", "salaried", "married",
     "single", "resident", "citizen", "male", "female", "other", "details", "detail", "information", "info"}
)


def _core(tok: str) -> str:
    tok = tok.rstrip(".").replace("’", "'")
    if tok.endswith("'s"):
        tok = tok[:-2]
    return tok


def _is_anchor(core_lower: str) -> bool:
    if core_lower in lx.NAME_ANCHORS:
        return True
    if "-" in core_lower:
        return any(p in lx.NAME_ANCHORS for p in core_lower.split("-") if p)
    return False


def _read_name_tokens(
    text: str,
    pos: int,
    *,
    max_tokens: int,
    lenient: bool,
    allow_hon: bool = True,
) -> tuple[int, int, int] | None:
    """Read up to `max_tokens` name-like Latin tokens starting at `pos` (single spaces between them).
    Returns (start, end, anchor_count) of the accepted run, or None.

    Strict (default): a token counts if it is capitalised and not a stop word, or is a lexicon name in any case.
    Lenient (after "Name:" style labels or S/o): any alphabetic token that is not a stop word counts."""
    n = len(text)
    while pos < n and text[pos] == " ":
        pos += 1
    start = pos
    end = pos
    count = 0
    anchors = 0
    while count < max_tokens and pos < n:
        m = _TOKEN_SCAN_RE.match(text, pos)
        if not m:
            break
        tok = m.group(0)
        core = _core(tok)
        low = core.lower()
        if not core:
            break
        if allow_hon and low in _HON_SET and count == 0:
            # "Late Shri Ramesh", "S/o Mr Ramesh": step over a second honorific.
            nxt = m.end()
            while nxt < n and text[nxt] == " ":
                nxt += 1
            pos = nxt
            start = pos
            end = pos
            continue
        is_initial = len(core) == 1 and core.isupper()
        anchor = _is_anchor(low)
        ok: bool
        if is_initial:
            ok = True
        elif low in _STOP_LOWER:
            ok = False
        elif anchor:
            ok = True
        elif lenient:
            ok = len(core) >= 2
        else:
            ok = core[0].isupper()
        if not ok:
            break
        if anchor:
            anchors += 1
        count += 1
        end = pos + len(core)
        # a period after a full word ends the name ("... Mr Sharma."); after an initial it continues
        pos = m.end()
        if tok.endswith(".") and not is_initial:
            break
        if pos < n and text[pos] == " ":
            while pos < n and text[pos] == " ":
                pos += 1
        else:
            break
    if count == 0:
        return None
    return start, end, anchors


_ALPHA_RE = re.compile(r"[A-Za-z]+")
_MARKER_FIRST = frozenset(
    {w for w in _HONORIFICS}
    | {"son", "daughter", "wife", "husband", "care", "widow", "father", "mother", "s", "d", "w", "h", "c"}
    | {re.match(r"[a-z]+", lab.lower()).group() for lab in _LABELS_ALL if re.match(r"[a-z]+", lab.lower())}  # type: ignore[union-attr]
)


def _marker_matches(text: str):
    """Yield marker matches. A cheap word scan finds candidate positions; the full (slow) marker regex is only
    tried, anchored, where a marker word could start."""
    low = text.lower() if text.isascii() else None
    for wm in _ALPHA_RE.finditer(low if low is not None else text):
        word = wm.group(0) if low is not None else wm.group(0).lower()
        if word in _MARKER_FIRST:
            m = _MARKER_RE.match(text, wm.start())
            if m:
                yield m


def _marker_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _marker_matches(text):
        kind = m.lastgroup
        end = m.end()
        if kind == "hon":
            word = m.group("hon").lower()
            got = _read_name_tokens(text, end, max_tokens=3, lenient=False)
            if got is None:
                continue
            s, e, anchors = got
            first = text[s : s + 5].lower()
            if word in ("late", "km") and anchors == 0 and not _next_is_honorific(text, end):
                continue
            if word == "sri" and first.startswith("lanka"):
                continue
            spans.append((s, e))
        elif kind == "rel":
            word = m.group("rel").lower()
            slash = "/" in word or "\\" in word
            got = _read_name_tokens(text, end, max_tokens=3, lenient=slash)
            if got is not None:
                spans.append(got[:2])
        elif kind == "label":
            got = _label_value(text, m)
            if got is not None:
                spans.append(got)
    return spans


def _next_is_honorific(text: str, pos: int) -> bool:
    m = _TOKEN_SCAN_RE.match(text, pos + 1 if pos < len(text) and text[pos] == " " else pos)
    return bool(m and _core(m.group(0)).lower() in _HON_SET)


_LABEL_PUNCT_RE = re.compile(r"[ \t]*(?P<p>[:=\-–])?[ \t]*")


def _label_value(text: str, m: re.Match[str]) -> tuple[int, int] | None:
    label = re.sub(r"\s+", " ", m.group("label").lower()).replace("’", "'")
    pm = _LABEL_PUNCT_RE.match(text, m.end())
    punct = pm.group("p") if pm else None
    pos = pm.end() if pm else m.end()
    has_name_word = "name" in label or "holder" in label or label in ("surname", "lessor", "lessee", "drawer", "depositor")
    if label in ("name", "full name", "first name", "last name", "middle name", "given name", "family name", "surname"):
        # bare "Name:" must not follow another word ("Bank name: ...", "Product name: ...")
        before = text[: m.start()].rstrip(" \t")
        if before and (before[-1].isalnum() or before[-1] in "'’"):
            return None
        if punct not in (":", "="):
            return None
    tier_a = label in _LABEL_A_SET
    if punct is None and not (tier_a and "name" in label):
        return None  # "Applicant Rajesh" needs a colon; "Applicant name Rajesh" does not
    lenient = tier_a and punct in (":", "=")
    got = _read_name_tokens(text, pos, max_tokens=3 if lenient else 4, lenient=lenient)
    if got is None:
        return None
    return got[0], got[1]


def _sequence_spans(text: str) -> list[tuple[int, int]]:
    """Runs of capitalised tokens where at least one is a lexicon name; plus a lone capitalised first name."""
    toks = list(_CAPTOK_RE.finditer(text))
    if not toks:
        return []
    info: list[tuple[re.Match[str], str, str]] = []
    any_anchor = False
    for m in toks:
        low = _core(m.group(0)).lower()
        if low in lx.NAME_ANCHORS or low in lx.AMBIGUOUS_NAMES or "-" in low or "'" in low:
            any_anchor = any_anchor or _is_anchor(low)
        info.append((m, low, ""))
    if not any_anchor:
        return []
    spans: list[tuple[int, int]] = []
    seg: list[int] = []  # indexes into info for the current run

    def flush(next_is_terminator: bool) -> None:
        if not seg:
            return
        idx = list(seg)
        seg.clear()
        anchors = [i for i in idx if _is_anchor(info[i][1])]
        if not anchors:
            return
        lo, hi = idx.index(anchors[0]), idx.index(anchors[-1])
        # extend by one non-anchor token on each side (e.g. "Zoravar Sharma", "Priya Sharma X")
        lo = max(0, lo - 1)
        hi = min(len(idx) - 1, hi + 1)
        chosen = idx[lo : hi + 1]
        if len(chosen) == 1:
            low = info[chosen[0]][1]
            if low not in lx.FIRST_NAMES or next_is_terminator:
                return
            tok = _core(info[chosen[0]][0].group(0))
            if len(tok) < 3:
                return
        spans.append((info[chosen[0]][0].start(), info[chosen[-1]][0].start() + len(_core(info[chosen[-1]][0].group(0)))))

    prev_end = -1
    prev_tok = ""
    for i, (m, low, _) in enumerate(info):
        raw = m.group(0)
        contiguous = prev_end >= 0 and _only_spaces(text, prev_end, m.start()) and (
            not prev_tok.endswith(".") or len(prev_tok) == 2
        )
        is_split = low in lx.NAME_TERMINATORS or low in lx.NON_NAME_CAPS or low in _HON_SET
        if not contiguous:
            flush(False)
        if is_split:
            flush(low in lx.NAME_TERMINATORS)
            prev_end, prev_tok = m.end(), raw
            continue
        seg.append(i)
        prev_end, prev_tok = m.end(), raw
        if raw.endswith(".") and len(raw) > 2:
            flush(False)  # sentence end after a full word
    flush(False)
    return spans


def _only_spaces(text: str, a: int, b: int) -> bool:
    return b > a and text[a:b].strip(" ") == ""


def _native_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    if not _INDIC_ANY_RE.search(text):
        return spans
    for m in _INDIC_TOK_RE.finditer(text):
        tok = m.group(0)
        if tok in lx.DEVANAGARI_NAMES or tok in lx.OTHER_NATIVE_NAMES:
            spans.append(m.span())
    for m in _NATIVE_HON_RE.finditer(text):
        pos = m.end()
        while pos < len(text) and text[pos] == " ":
            pos += 1
        start = pos
        end = pos
        count = 0
        while count < 3:
            t = _INDIC_TOK_RE.match(text, pos)
            if not t or t.group(0) in _NATIVE_STOP or _NATIVE_HON_RE.fullmatch(t.group(0)):
                break
            count += 1
            end = t.end()
            pos = end
            if pos < len(text) and text[pos] == " ":
                pos += 1
            else:
                break
        if count:
            spans.append((start, end))
    for m in _NATIVE_LABEL_RE.finditer(text):
        pos = m.end()
        start = pos
        end = pos
        count = 0
        while count < 4:
            t = re.compile(rf"[{_INDIC}A-Za-z][{_INDIC}A-Za-z'’.\-]*").match(text, pos)
            if not t:
                break
            tok = t.group(0).rstrip(".")
            if tok in _NATIVE_STOP or tok.lower() in _STOP_LOWER:
                break
            count += 1
            end = pos + len(tok)
            pos = t.end()
            if pos < len(text) and text[pos] == " ":
                pos += 1
            else:
                break
        if count:
            spans.append((start, end))
    return spans


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans = sorted(s for s in spans if s[1] > s[0])
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def detect_person_name(text: str) -> list[Finding]:
    spans = _marker_spans(text) + _sequence_spans(text) + _native_spans(text)
    return [Finding("person_name", s, e, mask_name(text[s:e])) for s, e in _merge(spans)]


# --------------------------------------------------------------------------------------------------
# Registry and the combined scan
# --------------------------------------------------------------------------------------------------

DETECTORS: dict[str, Callable[[str], list[Finding]]] = {
    "pan": detect_pan,
    "aadhaar": detect_aadhaar,
    "aadhaar_vid": detect_aadhaar_vid,
    "account_number": detect_account_number,
    "card_number": detect_card_number,
    "phone_in": detect_phone_in,
    "email": detect_email,
    "upi_id": detect_upi_id,
    "ifsc": detect_ifsc,
    "passport": detect_passport,
    "voter_id": detect_voter_id,
    "driving_licence": detect_driving_licence,
    "pincode_ctx": detect_pincode_ctx,
    "person_name": detect_person_name,
}
DETECTOR_NAMES = tuple(DETECTORS)

# Detectors that need a run of at least N digits somewhere in the view they scan.
_MIN_RUN = {
    "pan": 4, "aadhaar": 12, "aadhaar_vid": 16, "account_number": 9, "card_number": 13, "phone_in": 10,
    "ifsc": 1, "passport": 7, "voter_id": 7, "driving_licence": 9, "pincode_ctx": 6,
}  # fmt: skip
_JOINABLE_RE = JOINABLE_RE
_DIGIT_RUN_RE = re.compile(r"[0-9]+")


def _max_run(view: str) -> int:
    runs = _DIGIT_RUN_RE.findall(view)
    return max(map(len, runs)) if runs else 0


def _run(n: str, need_spans: bool) -> list[Finding]:
    """Run every detector on the (already normalised) text and on its digit-compacted view."""
    if not n or _is_vocab(n):
        return []
    n = blank_allowlisted(n)
    found: dict[tuple, Finding] = {}

    def add(f: Finding, key_span: tuple[int, int] | None = None) -> None:
        key = (f.detector, *(key_span or f.span)) if need_spans else (f.detector, f.masked)
        found.setdefault(key, f)

    if _DIGIT_RE.search(n):
        compact = compact_digits(n) if _JOINABLE_RE.search(n) else None
        cmap: list[int] | None = None
        views: list[tuple[str, int, bool]] = [(n, _max_run(n), False)]
        if compact is not None:
            views.append((compact, _max_run(compact), True))
        for view, longest, is_compact in views:
            for name, need in _MIN_RUN.items():
                if longest < need:
                    continue
                for f in DETECTORS[name](view):
                    if is_compact and need_spans:
                        if cmap is None:
                            _, cmap = compact_digits_mapped(n)
                        add(Finding(f.detector, cmap[f.start], cmap[f.end - 1] + 1, f.masked))
                    else:
                        add(f)
        if "(" in n:  # "(022) 2345 6789": parentheses are not joinable separators
            for f in _find("phone_in", _PHONE_PAREN_RE, n):
                add(f)
    if "@" in n or _OBF_AT_RE.search(n):
        for f in detect_email(n):
            add(f)
        for f in detect_upi_id(n):
            add(f)
    for f in detect_person_name(n):
        add(f)
    return sorted(found.values(), key=lambda f: (f.start, f.end, f.detector))


def run_all(text: str) -> list[Finding]:
    """Normalise `text`, run every detector on it and on its digit-compacted view, and return findings with
    spans in the normalised text (see `jevloan.pii.normalize.normalize`)."""
    return _run(normalize(text), need_spans=True)


def run_all_masked(text: str) -> list[Finding]:
    """Like `run_all` but de-duplicated on (detector, masked) and without span remapping. Used by the gate."""
    return _run(normalize(text), need_spans=False)
