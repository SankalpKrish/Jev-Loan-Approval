"""The redactor (PLAN 3.5, D8): swap every identifier for a token that is consistent within one file.

`Redactor(known).redact(text)` works in five stages:
  1. normalise the text (NFKC, invisible characters, native digits, spelled digits; see `normalize`);
  2. replace *known* entities from the file's `pii_inventory`: emails, PANs, Aadhaar numbers, phones, account
     numbers, address lines (absorbing a trailing city and PIN), PIN codes, organisations, then person names
     (full name, reversed, initials + surname, each part of 3+ characters, and native-script forms);
  3. turn explicit dates into `Mon YYYY` and rupee amounts into bands such as `₹[25-50k]`;
  4. run the detectors and tokenise anything unknown they still find (the next free token number);
  5. verify with the gate's detectors and fall back to `[REDACTED]` if a hit survived.

Tokens: `[APPLICANT]` (person_names[0]) / `[PERSON_2]`..., `[ORG_A]`..., `[PAN_1]`, `[UID_1]`, `[PHONE_1]`, `[EMAIL_1]`,
`[ACCT_1]`, `[ADDR_1]`, `[PIN]`, plus `[ID_n]` (passport, voter id, licence), `[IFSC]`, `[REDACTED]`. The same
raw value always gets the same token within one Redactor. The output is the *normalised* text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from jevloan.pii import lexicon as lx
from jevloan.pii.detectors import _TOKEN_RE, Finding, run_all
from jevloan.pii.normalize import normalize

__all__ = ["KnownEntities", "Redactor", "amount_band", "MONTHLY_BANDS", "TICKET_BANDS"]

# --------------------------------------------------------------------------------------------------
# Bands (PLAN 3.7)
# --------------------------------------------------------------------------------------------------

# Monthly-style bands, used for amounts under Rs 10 lakh: (upper bound exclusive in rupees, label).
MONTHLY_BANDS: tuple[tuple[float, str], ...] = (
    (10_000, "<10k"), (25_000, "10-25k"), (50_000, "25-50k"), (75_000, "50-75k"),
    (100_000, "75k-1L"), (200_000, "1-2L"), (500_000, "2-5L"), (1_000_000, ">5L"),
)  # fmt: skip
# Ticket-size bands for Rs 10 lakh and above.
TICKET_BANDS: tuple[tuple[float, str], ...] = (
    (2_500_000, "10-25L"), (5_000_000, "25-50L"), (10_000_000, "50L-1Cr"), (20_000_000, "1-2Cr"),
    (50_000_000, "2-5Cr"),
)  # fmt: skip


def amount_band(value: float) -> str:
    """Band label for a rupee amount: the monthly table below Rs 10L, the amount table from Rs 10L up."""
    value = abs(value)
    for upper, label in MONTHLY_BANDS:
        if value < upper:
            return label
    for upper, label in TICKET_BANDS:
        if value < upper:
            return label
    return ">5Cr"


# --------------------------------------------------------------------------------------------------
# Known entities
# --------------------------------------------------------------------------------------------------


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


@dataclass
class KnownEntities:
    """Raw identifiers of one loan file, exactly as written. Field names match `pii_inventory`; index 0 of each
    list is the applicant's own value. `native_names[i]` optionally holds the native-script rendering of
    `person_names[i]` (use "" for none).

    `aliases` maps an alias raw string to its canonical raw string (the canonical value is one of the entries
    in the other lists): the one-line address -> line1, a native-script address -> line1, a native-script name ->
    the applicant's name. Every alias is replaced with the canonical value's token, in every category, and an
    alias never takes a token number of its own. Explicit aliases take precedence over the positional
    `native_names` handling."""

    person_names: list[str] = field(default_factory=list)
    org_names: list[str] = field(default_factory=list)
    pans: list[str] = field(default_factory=list)
    aadhaars: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    account_numbers: list[str] = field(default_factory=list)
    address_lines: list[str] = field(default_factory=list)
    pincodes: list[str] = field(default_factory=list)
    native_names: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_inventory(cls, d: Mapping[str, Any] | Any) -> KnownEntities:
        if not isinstance(d, Mapping):
            dump = getattr(d, "model_dump", None)
            d = dump() if callable(dump) else dict(vars(d))
        kwargs = {name: _clean_list(d.get(name)) for name in _LIST_FIELDS if name != "native_names"}
        native = d.get("native_names", d.get("person_names_native"))
        kwargs["native_names"] = [str(v).strip() if v else "" for v in (native or [])]
        raw_aliases = d.get("aliases") or {}
        kwargs["aliases"] = {str(a).strip(): str(c).strip() for a, c in dict(raw_aliases).items() if a and c}
        names = kwargs["person_names"]
        explicit = {_norm_text(a) for a in kwargs["aliases"]}
        # Fallback when there is no `aliases` entry: the data generator lists an applicant's native-script name
        # right after the applicant. That is an alias of the same person and must share the [APPLICANT] token,
        # not become [PERSON_2].
        if len(names) >= 2 and _norm_text(names[1]) not in explicit and _is_native_only(names[1]) and not _is_native_only(names[0]):
            alias = names.pop(1)
            if not kwargs["native_names"]:
                kwargs["native_names"] = [alias]
            elif not kwargs["native_names"][0]:
                kwargs["native_names"][0] = alias
        return cls(**kwargs)


def _norm_text(s: str) -> str:
    return " ".join(normalize(s).casefold().split())


def _is_native_only(name: str) -> bool:
    """A name with no ASCII letters at all: written purely in a native script."""
    return bool(name.strip()) and not any(ch.isascii() and ch.isalpha() for ch in name)


_LIST_FIELDS = (
    "person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines",
    "pincodes", "native_names",
)  # fmt: skip
_ENTITY_FIELDS = _LIST_FIELDS[:-1]

# --------------------------------------------------------------------------------------------------
# Regex helpers
# --------------------------------------------------------------------------------------------------

_NB = "A-Za-z0-9ऀ-ॣ०-෿"  # characters that make up a "word" for boundaries, any script
_B_L = f"(?<![{_NB}])"
_B_R = f"(?![{_NB}])"
_TOKEN_RE = re.compile(rf"[{_NB}]+(?:['’][{_NB}]+)*")
_NAME_SEP = r"[\s.,\-]{1,3}"
_ID_SEP = r"[ \t\n\-./_]?"
_HON_PREFIX_RE = re.compile(r"^(?:mr|mrs|ms|miss|shri|shree|sri|smt|dr|prof|late)\.?\s+", re.IGNORECASE)
_ORG_TAIL = r"(?:[\s,.]+(?:pvt|private|ltd|limited|llp|inc|corp|corporation|company|co)(?![A-Za-z]))*"
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:[\s,.]+(?:pvt|private|ltd|limited|llp|inc|corp|corporation|company|co))+\.?\s*$", re.IGNORECASE
)

_PH_BASE = 0xE100
_PH_MAX = 0xF8FF - _PH_BASE


def _letters(i: int) -> str:
    """A, B, ... Z, AA, AB, ..."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _tokens(s: str) -> list[str]:
    return _TOKEN_RE.findall(s)


def _seq_pattern(tokens: list[str], sep: str = _NAME_SEP) -> str:
    return _B_L + sep.join(re.escape(t) for t in tokens) + _B_R


def _spaced(chars: str, sep: str = _ID_SEP) -> str:
    return sep.join(re.escape(c) for c in chars)


@dataclass
class _Group:
    kind: str
    regex: re.Pattern[str]
    names: dict[str, tuple[str, str]]  # group name -> (registry kind, registry key)


# --------------------------------------------------------------------------------------------------
# Dates and amounts
# --------------------------------------------------------------------------------------------------

_MONTH_ALT = (
    "january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sept|sep|"
    "october|oct|november|nov|december|dec"
)
_MON_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MON_INDEX = {m.lower(): i for i, m in enumerate(_MON_ABBR)}
_DATE_ISO_RE = re.compile(r"(?<![0-9])(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?![0-9])")
_DATE_DMY4_RE = re.compile(r"(?<![0-9])(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?![0-9])")
_DATE_DMY2_RE = re.compile(r"(?<![0-9])(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})(?![0-9])")
_DATE_DMON_RE = re.compile(
    rf"(?<![0-9])(\d{{1,2}})(?:st|nd|rd|th)?[ \-./,]*({_MONTH_ALT})(?![A-Za-z])\.?[ \-./,]*(\d{{4}}|\d{{2}})(?![0-9])",
    re.IGNORECASE,
)
_DATE_MOND_RE = re.compile(
    rf"(?<![A-Za-z])({_MONTH_ALT})\.?[ ]+(\d{{1,2}})(?:st|nd|rd|th)?,?[ ]+(\d{{4}})(?![0-9])", re.IGNORECASE
)


def _year(s: str) -> int:
    y = int(s)
    if len(s) == 2:
        y += 1900 if y >= 50 else 2000
    return y


def _mon_year(month: int, year: int) -> str:
    return f"{_MON_ABBR[month - 1]} {year}"


def _dates(text: str) -> str:
    def iso(m: re.Match[str]) -> str:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return _mon_year(mo, y)
        return m.group(0)

    def dmy(m: re.Match[str]) -> str:
        a, b, ys = int(m.group(1)), int(m.group(2)), m.group(3)
        y = _year(ys)
        if not 1900 <= y <= 2100:
            return m.group(0)
        if 1 <= b <= 12 and 1 <= a <= 31:
            return _mon_year(b, y)  # dd/mm/yyyy (Indian order)
        if 1 <= a <= 12 and 1 <= b <= 31:
            return _mon_year(a, y)  # mm/dd/yyyy
        return m.group(0)

    def dmon(m: re.Match[str]) -> str:
        d = int(m.group(1))
        if not 1 <= d <= 31:
            return m.group(0)
        return _mon_year(_MON_INDEX[m.group(2).lower()[:3]] + 1, _year(m.group(3)))

    def mond(m: re.Match[str]) -> str:
        d = int(m.group(2))
        if not 1 <= d <= 31:
            return m.group(0)
        return _mon_year(_MON_INDEX[m.group(1).lower()[:3]] + 1, _year(m.group(3)))

    text = _DATE_ISO_RE.sub(iso, text)
    text = _DATE_DMON_RE.sub(dmon, text)
    text = _DATE_MOND_RE.sub(mond, text)
    text = _DATE_DMY4_RE.sub(dmy, text)
    return _DATE_DMY2_RE.sub(dmy, text)


_NUM = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"
_NUM_BIG = r"(?:\d{1,3}(?:,\d{2,3})+|\d{4,})(?:\.\d+)?"
_MULT = r"lakhs?|lacs?|crores?|cr|l|k|thousand|mn|million"
_AMT_PRE_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:rs\.?|inr|₹|rupees?)[ \t]*-?[ \t]*(?P<num>{_NUM})"
    rf"(?:[ \t]*(?P<mult>{_MULT})(?![A-Za-z0-9]))?(?:[ \t]*/-)?",
    re.IGNORECASE,
)
_AMT_POST_RE = re.compile(
    rf"(?<![0-9.,])(?P<num>{_NUM})[ \t]*(?P<mult>lakhs?|lacs?|crores?)?[ \t]*(?:rupees|rs\.?|inr|/-)(?![A-Za-z])",
    re.IGNORECASE,
)
_AMT_LAKH_RE = re.compile(rf"(?<![0-9.,])(?P<num>{_NUM})[ \t]*(?P<mult>lakhs?|lacs?|crores?)(?![A-Za-z])", re.IGNORECASE)
_AMT_CTX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<kw>salary|pay|amount|balance|emi|credited|debited|rent|premium|turnover|income|value|"
    r"valuation|loan|principal|fees?|charges|deposit|limit|outstanding|cost|price|payable|credit|debit)"
    rf"(?P<mid>[ \t]*(?::|=|-|of|is|was|as|at)?[ \t]*)(?P<num>{_NUM_BIG})"
    r"(?![0-9])(?![ \t]*(?:%|months?|years?|yrs?|days?|-\d))",
    re.IGNORECASE,
)
_MULT_VALUE = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "k": 1e3, "thousand": 1e3, "mn": 1e6, "million": 1e6,
}  # fmt: skip


def _to_rupees(num: str, mult: str | None) -> float:
    value = float(num.replace(",", ""))
    return value * _MULT_VALUE.get((mult or "").lower(), 1.0)


def _amounts(text: str) -> str:
    def band(m: re.Match[str]) -> str:
        return f"₹[{amount_band(_to_rupees(m.group('num'), m.group('mult')))}]"

    def ctx(m: re.Match[str]) -> str:
        num = m.group("num")
        plain = num.replace(",", "")
        if "," not in num and "." not in num and 1900 <= int(plain) <= 2100:
            return m.group(0)  # a year, not money
        return f"{m.group('kw')}{m.group('mid')}₹[{amount_band(float(plain))}]"

    text = _AMT_PRE_RE.sub(band, text)
    text = _AMT_POST_RE.sub(band, text)
    text = _AMT_LAKH_RE.sub(band, text)
    return _AMT_CTX_RE.sub(ctx, text)


_ADDR_PIN_RE = re.compile(
    r"(\[ADDR_\d+\])(?:[,\s]+[A-Z][A-Za-z]+(?:[ ][A-Z][A-Za-z]+){0,2}){0,2}[\s,.\-–]*(?:PIN(?:[ ]?code)?[ ]?:?[ ]?)?\[PIN\]",
)

# --------------------------------------------------------------------------------------------------
# The Redactor
# --------------------------------------------------------------------------------------------------

_DETECTOR_KIND: dict[str, str] = {
    "pan": "pan", "aadhaar": "aadhaar", "aadhaar_vid": "aadhaar", "phone_in": "phone", "email": "email",
    "upi_id": "email", "account_number": "account", "card_number": "account", "passport": "id",
    "voter_id": "id", "driving_licence": "id", "ifsc": "ifsc", "pincode_ctx": "pin", "person_name": "person",
}  # fmt: skip
_PRIORITY = (
    "aadhaar_vid", "aadhaar", "card_number", "phone_in", "pan", "email", "upi_id", "account_number", "ifsc",
    "passport", "voter_id", "driving_licence", "pincode_ctx", "person_name",
)  # fmt: skip
_TOKEN_FORMAT = {
    "pan": "[PAN_{n}]", "aadhaar": "[UID_{n}]", "phone": "[PHONE_{n}]", "email": "[EMAIL_{n}]",
    "account": "[ACCT_{n}]", "address": "[ADDR_{n}]", "id": "[ID_{n}]",
}  # fmt: skip


def _canon_pan(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _canon_phone(s: str) -> str:
    d = _digits(s)
    return d[-10:] if len(d) >= 10 else d


def _canon_email(s: str) -> str:
    local, _, domain = re.sub(r"\s+", "", s).lower().partition("@")
    return f"{local}@{domain}" if domain else local


def _person_key(tokens: list[str]) -> str:
    return " ".join(sorted(t.casefold() for t in tokens))


class Redactor:
    """Per-file redactor. Not thread-safe; create one per loan file."""

    def __init__(self, known: KnownEntities) -> None:
        self.known = known
        self.token_map: dict[str, str] = {}
        self._reg: dict[str, dict[str, str]] = {}
        self._count: dict[str, int] = {}
        self._person_parts: dict[str, str] = {}
        self._dyn_people: list[tuple[str, str]] = []  # (regex source, token) for names found by the detectors
        self._dyn_group: _Group | None = None
        self._dyn_dirty = False
        self._slots: list[str] = []
        self._groups: list[_Group] = []
        self._register_known()

    # ------------------------------------------------------------------------------------------
    # Token registry
    # ------------------------------------------------------------------------------------------

    def _new_token(self, kind: str, index: int) -> str:
        """The token for the `index`-th (0-based) entity of `kind`."""
        if kind == "person":
            return "[APPLICANT]" if index == 0 else f"[PERSON_{index + 1}]"
        if kind == "org":
            return f"[ORG_{_letters(index)}]"
        if kind == "ifsc":
            return "[IFSC]"
        if kind == "pin":
            return "[PIN]"
        return _TOKEN_FORMAT[kind].format(n=index + 1)

    def _register(self, kind: str, key: str, raw: str | None = None) -> str:
        table = self._reg.setdefault(kind, {})
        token = table.get(key)
        if token is None:
            index = self._count.get(kind, 0)
            self._count[kind] = index + 1
            token = table[key] = self._new_token(kind, index)
        if raw is not None:
            self.token_map.setdefault(raw, token)
        return token

    def _prepare(self) -> tuple[dict[str, list[str]], dict[str, list[tuple[str, str]]]]:
        """Effective inventory lists (alias entries removed) and, per list, the (alias raw, canonical raw) pairs."""
        k = self.known
        lists = {f: list(getattr(k, f)) for f in _ENTITY_FIELDS}
        pairs: dict[str, list[tuple[str, str]]] = {f: [] for f in _ENTITY_FIELDS}
        drop: dict[str, set[int]] = {f: set() for f in _ENTITY_FIELDS}
        alias_of = {_norm_text(a): _norm_text(c) for a, c in k.aliases.items() if a and c}

        def resolve(x: str) -> str:
            seen: set[str] = set()
            while x in alias_of and x not in seen:
                seen.add(x)
                x = alias_of[x]
            return x

        where: dict[str, tuple[str, int]] = {}
        for f in _ENTITY_FIELDS:
            for i, v in enumerate(lists[f]):
                where.setdefault(_norm_text(v), (f, i))
        for alias_raw in k.aliases:
            a = _norm_text(alias_raw)
            c = resolve(a)
            if c == a or c not in where:
                continue  # unresolvable: treat the alias as an ordinary entry, if it is one
            f, ci = where[c]
            pairs[f].append((alias_raw, lists[f][ci]))
            if a in where:
                drop[where[a][0]].add(where[a][1])
        effective = {f: [v for i, v in enumerate(vals) if i not in drop[f]] for f, vals in lists.items()}
        # positional native-script names: native_names[i] belongs to person_names[i] (unless aliased explicitly)
        explicit = {_norm_text(a) for a in k.aliases}
        kept = {_norm_text(v) for v in effective["person_names"]}
        for i, native in enumerate(k.native_names):
            if native and i < len(k.person_names) and _norm_text(native) not in explicit and _norm_text(k.person_names[i]) in kept:
                pairs["person_names"].append((native, k.person_names[i]))
        return effective, pairs

    def _register_known(self) -> None:
        eff, pairs = self._prepare()
        groups: list[_Group | None] = []
        groups.append(self._build_emails(eff["emails"], pairs["emails"]))
        groups.append(self._build_pans(eff["pans"], pairs["pans"]))
        groups.append(self._build_digits("aadhaar", eff["aadhaars"], pairs["aadhaars"], _digits, 12))
        groups.append(self._build_phones(eff["phones"], pairs["phones"]))
        groups.append(self._build_digits("account", eff["account_numbers"], pairs["account_numbers"], _digits, 6))
        groups.append(self._build_addresses(eff["address_lines"], pairs["address_lines"]))
        groups.append(self._build_pins(eff["pincodes"], pairs["pincodes"]))
        groups.append(self._build_orgs(eff["org_names"], pairs["org_names"]))
        groups.append(self._build_persons(eff["person_names"], pairs["person_names"]))
        self._groups = [g for g in groups if g is not None]

    def _alternation(self, kind: str, variants: list[tuple[str, str, str]], flags: int) -> _Group | None:
        """variants: (registry kind, registry key, regex source), earlier and longer first."""
        if not variants:
            return None
        names: dict[str, tuple[str, str]] = {}
        parts: list[str] = []
        for i, (reg_kind, key, source) in enumerate(variants):
            name = f"v{i}"
            names[name] = (reg_kind, key)
            parts.append(f"(?P<{name}>{source})")
        return _Group(kind, re.compile("|".join(parts), flags), names)

    @staticmethod
    def _entries(values: list[str], pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """(raw, canonical raw) for every value; regular values first so aliases never take a number."""
        return [(v, v) for v in values] + list(pairs)

    # ---- emails, identifiers -------------------------------------------------------------------

    def _build_emails(self, emails: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants = []
        for raw, canon_raw in self._entries(emails, pairs):
            key = _canon_email(canon_raw)
            self._register("email", key, raw)
            local, _, domain = _canon_email(raw).partition("@")
            if not domain:
                continue
            src = (
                r"(?<![A-Za-z0-9._%+\-])" + re.escape(local) + r"[ ]?@[ ]?" + re.escape(domain)
                + r"(?![A-Za-z0-9\-]|\.[A-Za-z0-9])"
            )
            variants.append(("email", key, src))
        variants.sort(key=lambda v: -len(v[2]))
        return self._alternation("email", variants, re.IGNORECASE)

    def _build_pans(self, pans: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants = []
        for raw, canon_raw in self._entries(pans, pairs):
            key, own = _canon_pan(canon_raw), _canon_pan(raw)
            if not key or not own:
                continue
            self._register("pan", key, raw)
            variants.append(("pan", key, r"(?<![A-Za-z])" + _spaced(own) + r"(?![A-Za-z])"))
        return self._alternation("pan", variants, re.IGNORECASE)

    def _build_digits(
        self, kind: str, values: list[str], pairs: list[tuple[str, str]], canon: Callable[[str], str], min_len: int
    ) -> _Group | None:
        variants = []
        for raw, canon_raw in self._entries(values, pairs):
            key, own = canon(canon_raw), canon(raw)
            if not key:
                continue
            self._register(kind, key, raw)
            if len(own) >= min_len:
                variants.append((kind, key, r"(?<![0-9])" + _spaced(own) + r"(?![0-9])"))
        variants.sort(key=lambda v: -len(v[2]))
        return self._alternation(kind, variants, 0)

    def _build_phones(self, phones: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants = []
        for raw, canon_raw in self._entries(phones, pairs):
            key, own = _canon_phone(canon_raw), _canon_phone(raw)
            if not key or not own:
                continue
            self._register("phone", key, raw)
            variants.append(
                ("phone", key, r"(?<![0-9])(?:(?:(?:\+|00)?91[ \-.]?0?)|0)?" + _spaced(own) + r"(?![0-9])")
            )
        return self._alternation("phone", variants, 0)

    def _build_pins(self, pins: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants = []
        for raw, canon_raw in self._entries(pins, pairs):
            key, own = _digits(canon_raw), _digits(raw)
            if len(key) != 6 or len(own) != 6:
                continue
            self._register("pin", key, raw)
            variants.append(("pin", key, r"(?<![0-9])" + re.escape(own[:3]) + r"[ ]?" + re.escape(own[3:]) + r"(?![0-9])"))
        return self._alternation("pin", variants, 0)

    # ---- addresses, organisations --------------------------------------------------------------

    def _build_addresses(self, lines: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        """One token per real address. Without an alias entry, a line whose (case-folded) tokens start with
        another line's tokens is the same address written longer, and shares the shorter line's token."""
        toks_of = {raw: tuple(t.casefold() for t in _tokens(normalize(raw))) for raw in lines}
        key_of: dict[str, str] = {}
        for raw, toks in toks_of.items():
            best = toks
            for other in toks_of.values():
                if 2 <= len(other) < len(best) and toks[: len(other)] == other:
                    best = other
            key_of[raw] = " ".join(best)
        variants: list[tuple[str, str, str, int]] = []
        for raw, canon_raw in self._entries(lines, pairs):
            key = key_of.get(canon_raw) or " ".join(t.casefold() for t in _tokens(normalize(canon_raw)))
            if not key:
                continue
            self._register("address", key, raw)
            norm = normalize(raw)
            segments = [s for s in re.split(r"[,;\n]+", norm) if _tokens(s)]
            seen: set[str] = set()
            for cut in range(len(segments), 0, -1):
                toks = _tokens(", ".join(segments[:cut]))
                if not toks or (cut < len(segments) and sum(len(t) for t in toks) < 8):
                    continue
                src = _B_L + rf"[^{_NB}]+".join(re.escape(t) for t in toks) + _B_R
                if src not in seen:
                    seen.add(src)
                    variants.append(("address", key, src, len(toks)))
        variants.sort(key=lambda v: -v[3])
        return self._alternation("address", [v[:3] for v in variants], re.IGNORECASE)

    def _build_orgs(self, orgs: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants: list[tuple[str, str, str, int]] = []
        for raw, canon_raw in self._entries(orgs, pairs):
            key = " ".join(t.casefold() for t in _tokens(normalize(canon_raw)) if t.casefold() != "and")
            norm = normalize(raw)
            core = _LEGAL_SUFFIX_RE.sub("", norm).strip(" ,.&")
            full_toks = [t for t in _tokens(norm) if t.casefold() != "and"]
            if not key or not full_toks:
                continue
            self._register("org", key, raw)
            forms = [full_toks]
            core_toks = [t for t in _tokens(core) if t.casefold() != "and"]
            if core_toks and core_toks != full_toks and sum(len(t) for t in core_toks) >= 4:
                forms.append(core_toks)
            for toks in forms:
                if len(toks) == 1 and len(toks[0]) < 4:
                    continue
                org_sep = rf"(?:[^{_NB}]+(?:and[^{_NB}]+)?)"
                tail = _ORG_TAIL if toks is not forms[0] else ""
                src = _B_L + org_sep.join(re.escape(t) for t in toks) + tail + _B_R
                variants.append(("org", key, src, len(toks)))
        variants.sort(key=lambda v: -v[3])
        return self._alternation("org", [v[:3] for v in variants], re.IGNORECASE)

    # ---- persons -------------------------------------------------------------------------------

    def _build_persons(self, names: list[str], pairs: list[tuple[str, str]]) -> _Group | None:
        variants: list[tuple[str, str, str, int, int]] = []  # kind, key, src, ntokens, length
        rev = {v: k for k, v in lx.LATIN_TO_DEVANAGARI.items()}
        seen_src: set[str] = set()
        aliases_of: dict[str, list[str]] = {}
        for alias_raw, canon_raw in pairs:
            aliases_of.setdefault(_norm_text(canon_raw), []).append(alias_raw)

        def add(key: str, toks: list[str], *, sep: str = _NAME_SEP) -> None:
            if not toks:
                return
            src = _seq_pattern(toks, sep)
            if len(toks) == 1 and toks[0].casefold() in lx.COMMON_ENGLISH_NAME_PARTS:
                t = re.escape(toks[0])
                src = _B_L + f"(?-i:{t.capitalize()}|{t.upper()})" + _B_R
            if src in seen_src:
                return
            seen_src.add(src)
            variants.append(("person", key, src, len(toks), sum(len(t) for t in toks)))

        table = self._reg.setdefault("person", {})
        for i, raw in enumerate(names):
            forms = [raw] + aliases_of.get(_norm_text(raw), [])
            key = f"#{i}"
            if key not in table:
                table[key] = self._new_token("person", i)
            token = table[key]
            for form_raw in forms:
                self.token_map.setdefault(form_raw, token)
                base = normalize(form_raw)
                while _HON_PREFIX_RE.match(base):
                    base = _HON_PREFIX_RE.sub("", base, count=1)
                toks = _tokens(base)
                if not toks:
                    continue
                latin_variants: list[list[str]] = [toks]
                # cross-script forms of the same name from the lexicon
                mapped = [lx.LATIN_TO_DEVANAGARI.get(t.casefold()) or rev.get(t) for t in toks]
                if all(mapped):
                    latin_variants.append([m for m in mapped if m])
                for seq in latin_variants:
                    add(key, seq)
                    if len(seq) >= 2:
                        add(key, seq[::-1])
                        first, last = seq[0], seq[-1]
                        if len(seq) >= 3:
                            add(key, [last] + seq[:-1])
                            add(key, [first, last])
                        if first.isascii() and first[:1].isalpha():
                            variants.append(
                                ("person", key, _B_L + re.escape(first[0]) + r"(?:\.|[ ]+|\.[ ]*)" + re.escape(last) + _B_R,
                                 2, len(last) + 1)
                            )
                    for part in seq:
                        if len(part) >= 3:
                            add(key, [part])
                            self._person_parts.setdefault(part.casefold(), token)
                            self.token_map.setdefault(part, token)
        self._count["person"] = len(names)  # discovered names take the next free number
        # longest sequences first so "Priya Sharma" wins over "Priya"
        variants.sort(key=lambda v: (-v[3], -v[4]))
        return self._alternation("person", [v[:3] for v in variants], re.IGNORECASE)

    # ------------------------------------------------------------------------------------------
    # Redaction
    # ------------------------------------------------------------------------------------------

    def _placeholder(self, token: str) -> str:
        if len(self._slots) >= _PH_MAX:
            return token
        self._slots.append(token)
        return chr(_PH_BASE + len(self._slots) - 1)

    def _restore(self, text: str) -> str:
        if not self._slots:
            return text
        slots = self._slots
        return re.sub("[-]", lambda m: slots[ord(m.group(0)) - _PH_BASE], text)

    def _protect_existing_tokens(self, text: str) -> str:
        """Existing redaction tokens become placeholders so no name or number pattern can bite into them."""
        if "[" not in text:
            return text
        return _TOKEN_RE.sub(lambda m: self._placeholder(m.group(0)), text)

    def _apply_group(self, group: _Group, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            reg_kind, key = group.names[m.lastgroup or ""]
            token = key if reg_kind == "dyn" else self._reg[reg_kind][key]
            self.token_map.setdefault(m.group(0), token)
            return self._placeholder(token)

        return group.regex.sub(repl, text)

    def redact(self, text: str) -> str:
        if not text:
            return text
        self._slots = []
        t = self._protect_existing_tokens(normalize(text))
        for group in self._groups:
            t = self._apply_group(group, t)
        dyn = self._dynamic_group()
        if dyn is not None:
            t = self._apply_group(dyn, t)
        t = self._restore(t)
        self._slots = []
        t = _ADDR_PIN_RE.sub(r"\1", t)
        t = _dates(t)
        t = _amounts(t)
        return self._residual(t)

    def redact_obj(self, obj: Any) -> Any:
        """Redact every string value in a JSON-like structure (dict keys are left alone)."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, Mapping):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(v) for v in obj]
        return obj

    def _dynamic_group(self) -> _Group | None:
        """Names the detectors discovered earlier become known: later mentions (any case, or a lone part) map to
        the same token even where no marker or lexicon hit would find them again."""
        if self._dyn_dirty:
            variants = sorted(self._dyn_people, key=lambda v: -len(v[0]))
            names: dict[str, tuple[str, str]] = {}
            parts: list[str] = []
            for i, (src, token) in enumerate(variants):
                names[f"d{i}"] = ("dyn", token)
                parts.append(f"(?P<d{i}>{src})")
            self._dyn_group = _Group("dyn", re.compile("|".join(parts), re.IGNORECASE), names) if parts else None
            self._dyn_dirty = False
        return self._dyn_group

    def _remember_person(self, toks: list[str], token: str) -> None:
        seen = {src for src, _ in self._dyn_people}
        forms = [toks] + ([toks[::-1]] if len(toks) > 1 else []) + [[t] for t in toks if len(t) >= 3]
        for form in forms:
            if len(form) == 1 and form[0].casefold() in lx.COMMON_ENGLISH_NAME_PARTS:
                continue
            src = _seq_pattern(form)
            if src not in seen:
                seen.add(src)
                self._dyn_people.append((src, token))
                self._dyn_dirty = True

    # ---- residual detector pass ----------------------------------------------------------------

    def _token_for_finding(self, detector: str, raw: str) -> str:
        kind = _DETECTOR_KIND[detector]
        if kind == "pin":
            return "[PIN]"
        if kind == "ifsc":
            return "[IFSC]"
        if kind == "pan":
            return self._register("pan", _canon_pan(raw), raw)
        if kind == "aadhaar":
            return self._register("aadhaar", _digits(raw), raw)
        if kind == "phone":
            return self._register("phone", _canon_phone(raw), raw)
        if kind == "email":
            return self._register("email", _canon_email(raw), raw)
        if kind == "account":
            return self._register("account", _digits(raw), raw)
        if kind == "id":
            return self._register("id", _canon_pan(raw), raw)
        return self._person_token(raw)

    def _person_token(self, raw: str) -> str:
        toks = _tokens(raw)
        if not toks:
            return "[REDACTED]"
        table = self._reg.setdefault("person", {})
        key = _person_key(toks)
        if key in table:
            self.token_map.setdefault(raw, table[key])
            self._remember_person(toks, table[key])
            return table[key]
        hits = {self._person_parts[t.casefold()] for t in toks if len(t) >= 3 and t.casefold() in self._person_parts}
        if len(hits) == 1:
            token = next(iter(hits))
            table[key] = token
            self.token_map.setdefault(raw, token)
            self._remember_person(toks, token)
            return token
        # next free number; number 1 is the applicant's own slot and is never handed to a discovered name
        index = max(self._count.get("person", 0), 1)
        self._count["person"] = index + 1
        token = self._new_token("person", index)
        table[key] = token
        for t in toks:
            if len(t) >= 3:
                self._person_parts.setdefault(t.casefold(), token)
        self.token_map.setdefault(raw, token)
        self._remember_person(toks, token)
        return token

    def _select(self, findings: list[Finding]) -> list[Finding]:
        order = {name: i for i, name in enumerate(_PRIORITY)}
        chosen: list[Finding] = []
        for f in sorted(findings, key=lambda f: (order.get(f.detector, 99), -(f.end - f.start), f.start)):
            if all(f.end <= c.start or f.start >= c.end for c in chosen):
                chosen.append(f)
        return sorted(chosen, key=lambda f: f.start)

    def _residual(self, text: str) -> str:
        for _ in range(6):
            n = normalize(text)
            findings = run_all(n)
            if not findings:
                return n
            out = n
            for f in reversed(self._select(findings)):
                out = out[: f.start] + self._token_for_finding(f.detector, n[f.start : f.end]) + out[f.end :]
            text = out
        # last resort: nothing that still trips a detector may leave
        n = normalize(text)
        for f in reversed(self._select(run_all(n))):
            n = n[: f.start] + "[REDACTED]" + n[f.end :]
        return n
