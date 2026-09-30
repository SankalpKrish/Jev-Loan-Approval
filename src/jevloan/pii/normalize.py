"""Text normalisation for the PII gate (PLAN 3.5).

`normalize(s)` collapses the tricks used to slip an identifier past a regex:
compatibility forms (fullwidth, circled, math digits) via NFKC, invisible format characters, native-script
digits, Cyrillic/Greek look-alike letters, URL and HTML escapes, `9876O43210`-style letter/digit swaps, and
spelled-out digits ("nine eight double seven ..."). `compact_digits(s)` then joins runs of digits that are
separated by a single separator so `98765 43210` and `9-8-7-6-5-4-3-2-1-0` become plain digit runs.

Both functions are pure and idempotent. Detectors run on both views (see `detectors.run_all`).
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote

__all__ = ["normalize", "compact_digits", "compact_digits_mapped", "DIGIT_ZEROS", "SEPARATORS", "JOINABLE_RE"]

# --------------------------------------------------------------------------------------------------
# Character tables
# --------------------------------------------------------------------------------------------------

# Code point of "0" for every Unicode decimal-digit (Nd) block, generated from `unicodedata` (a test re-derives it).
# NFKC already handles fullwidth, math and circled digits; the rest (Devanagari, Bengali, Tamil...) it leaves alone.
DIGIT_ZEROS = (
    0x0660, 0x06F0, 0x07C0, 0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66,
    0x0DE6, 0x0E50, 0x0ED0, 0x0F20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90,
    0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620, 0xA8D0, 0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10,
    0x104A0, 0x10D30, 0x10D40, 0x11066, 0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450, 0x114D0, 0x11650,
    0x116C0, 0x116D0, 0x116DA, 0x11730, 0x118E0, 0x11950, 0x11BF0, 0x11C50, 0x11D50, 0x11DA0, 0x11F50,
    0x16130, 0x16A60, 0x16AC0, 0x16B50, 0x16D70, 0x1CCF0, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6,
    0x1E140, 0x1E2F0, 0x1E4F0, 0x1E5F1, 0x1E950, 0x1FBF0,
)  # fmt: skip

# Invisible format / joiner characters. Category Cf plus a few Mn/Zs oddities.
_STRIP = (
    [0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180E, 0x3164, 0xFFA0]
    + list(range(0x200B, 0x2010))  # zero-width space/joiners, LRM, RLM
    + list(range(0x202A, 0x202F))  # bidi embeddings
    + list(range(0x2060, 0x2070))  # word joiner, invisible operators, bidi isolates
    + list(range(0xFE00, 0xFE10))  # variation selectors
    + [0xFEFF]
    + list(range(0xE0000, 0xE0080))  # tag characters
)  # fmt: skip

# Look-alike Cyrillic and Greek letters -> Latin. Indian scripts are unaffected.
_CONFUSABLES = {
    # Cyrillic upper
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J", "Ү": "Y",
    # Cyrillic lower
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ԛ": "q", "ԝ": "w",
    # Greek upper
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Greek lower
    "ο": "o", "ν": "v", "ι": "i", "α": "a", "ρ": "p",
}  # fmt: skip

_NON_ASCII_TABLE: dict[int, str | None] = {}
for _zero in DIGIT_ZEROS:
    for _i in range(10):
        _NON_ASCII_TABLE[_zero + _i] = str(_i)
for _cp in _STRIP:
    _NON_ASCII_TABLE[_cp] = None
for _src, _dst in _CONFUSABLES.items():
    _NON_ASCII_TABLE[ord(_src)] = _dst

# --------------------------------------------------------------------------------------------------
# Spelled-out digits
# --------------------------------------------------------------------------------------------------

_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}  # fmt: skip
_WORD_ALT = "zero|one|two|three|four|five|six|seven|eight|nine"
_TOKEN = rf"(?:(?:double|triple|dbl)[\s\-_]+)?(?:{_WORD_ALT}|oh|o)"
_SEQ_SEP = r"[\s,\-_]*"
_SPELLED_RE = re.compile(
    rf"(?<![A-Za-z0-9]){_TOKEN}(?:{_SEQ_SEP}{_TOKEN})+(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_PIECE_RE = re.compile(rf"(double|triple|dbl)?[\s\-_]*({_WORD_ALT}|oh|o)", re.IGNORECASE)
_MULT = {"double": 2, "dbl": 2, "triple": 3}


def _spelled_to_digits(match: re.Match[str]) -> str:
    text = match.group(0)
    out: list[str] = []
    explicit = 0
    for piece in _PIECE_RE.finditer(text):
        mult = _MULT.get((piece.group(1) or "").lower(), 1)
        word = piece.group(2).lower()
        if word in ("o", "oh"):
            out.append("0" * mult)
        else:
            out.append(_DIGIT_WORDS[word] * mult)
            explicit += 1
    digits = "".join(out)
    # A real spelled number has at least 4 digits and 2 unambiguous digit words. This keeps
    # "one or two", "oh no" and friends untouched.
    if len(digits) >= 4 and explicit >= 2:
        return digits
    return text


_WORD_LIST = tuple(_DIGIT_WORDS)


def _spell_digits(s: str) -> str:
    low = s.lower()
    if sum(low.count(w) for w in _WORD_LIST) < 2:
        return s
    return _SPELLED_RE.sub(_spelled_to_digits, s)


# --------------------------------------------------------------------------------------------------
# Escapes and lookalikes
# --------------------------------------------------------------------------------------------------

_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_ENTITY_RE = re.compile(r"&(?:#\d{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z]{2,8});")
_LOOKALIKE_ZERO_RE = re.compile(r"(?<=\d)[Oo](?=\d)")
_LOOKALIKE_ONE_RE = re.compile(r"(?<=\d)[lI](?=\d)")
_LOOKALIKE_RE = re.compile(r"(?<=\d)[OolI](?=\d)")


def _undo_escapes(s: str) -> str:
    if "%" in s and len(_PCT_RE.findall(s)) >= 3:
        s = unquote(s)
    if "&" in s and _ENTITY_RE.search(s):
        s = html.unescape(s)
    return s


def normalize(s: str) -> str:
    """Return the canonical form of `s` that detectors scan. Idempotent."""
    if not s:
        return s
    if not s.isascii():
        s = unicodedata.normalize("NFKC", s)
        s = s.translate(_NON_ASCII_TABLE)
    if "%" in s or "&" in s:
        s = _undo_escapes(s)
        if not s.isascii():
            s = s.translate(_NON_ASCII_TABLE)
    if _LOOKALIKE_RE.search(s):
        s = _LOOKALIKE_ZERO_RE.sub("0", s)
        s = _LOOKALIKE_ONE_RE.sub("1", s)
    return _spell_digits(s)


# --------------------------------------------------------------------------------------------------
# Digit-compacted view
# --------------------------------------------------------------------------------------------------

SEPARATORS = " \t\n-./_"
# A "." straight after a lone leading 0 is a decimal point ("0.30 0.15"), not a separator, so lists of fractions
# do not fuse into one long run.
_JOIN_RE = re.compile(r"(?<=\d)(?:[ \t\n\-/_]|(?<!(?<![0-9.])0)\.)(?=\d)")
JOINABLE_RE = _JOIN_RE


def compact_digits(s: str) -> str:
    """Join runs of digits separated by exactly one separator (space, tab, newline, - . / _).

    "98765 43210" -> "9876543210", "9-8-7-6" -> "9876". Two separators in a row do not join, and digits
    are never joined across letters, so band strings such as "50L-1Cr" or "750-799" stay short.
    """
    return _JOIN_RE.sub("", s)


def compact_digits_mapped(s: str) -> tuple[str, list[int]]:
    """Like `compact_digits`, but also returns `index_map` where `index_map[i]` is the offset in `s` of the
    i-th character of the compacted string. `index_map[-1]` is len(s) so a span (a, b) in the compacted view
    covers `s[index_map[a]:index_map[b - 1] + 1]`."""
    removed = [m.start() for m in _JOIN_RE.finditer(s)]
    if not removed:
        return s, list(range(len(s))) + [len(s)]
    drop = set(removed)
    kept = [i for i in range(len(s)) if i not in drop]
    return "".join(s[i] for i in kept), kept + [len(s)]
