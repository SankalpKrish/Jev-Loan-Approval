"""Simulator rules for module A (file readiness), plus the small helpers modules B and C reuse.

Each rule reads the redacted state only (what real Jev sees), works out the signal the way PLAN 3.8's truth
definition does (as nearly as the bands and tokens in the state allow), and answers through `noisy_p`, so a
stated 0.9 is right about 90% of the time and errors come with real confidence.

Deliberate simulator weakness (it gives the fairness tests something to detect): `A_address_proof_valid`
drops to about 60% accuracy when the address proof document's `script` is not latin, and the answer is
pulled toward "no". The truth of those files is unaffected by the script.
"""

import random
import re

from jevloan.jev.sim_rules import get, noisy_p, noul, sim_rule

MONTH_NUMBER = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
MONTHLY_INCOME_BANDS = ["<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L"]
AUTO_GENERATED = "Auto-generated document; no signature required."

_TOKEN_KINDS = r"APPLICANT|PERSON|ORG|PAN|UID|PHONE|EMAIL|ACCT|ADDR|DOB"
_TOKEN_RE = re.compile(rf"\[(?P<kind>{_TOKEN_KINDS})(?:_[A-Z0-9]+)?\]")
_NAME_AFTER_LABEL_RE = re.compile(rf"\bName:\s*(\[(?:{_TOKEN_KINDS})(?:_[A-Z0-9]+)?\])")
_MON_YEAR_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


# --- helpers (reused by b_fraud and c_appraisal) -----------------------------------------------------------


def documents(state: dict) -> list[dict]:
    docs = get(state, "documents", [])
    return [d for d in docs if isinstance(d, dict)] if isinstance(docs, list) else []


def first_doc(state: dict, doc_type: str | None = None, prefix: str | None = None) -> dict | None:
    for doc in documents(state):
        kind = str(doc.get("doc_type", ""))
        if (doc_type is not None and kind == doc_type) or (prefix is not None and kind.startswith(prefix)):
            return doc
    return None


def tokens(text: str, kind: str) -> list[str]:
    """Every redaction token of one kind in `text`, in order: tokens(text, "PAN") -> ["[PAN_1]", ...]."""
    return [m.group(0) for m in _TOKEN_RE.finditer(text) if m.group("kind") == kind]


def name_token(text: str) -> str | None:
    """The token after 'Name:' in a PAN card text (the holder), or the first person-like token."""
    found = _NAME_AFTER_LABEL_RE.search(text)
    if found:
        return found.group(1)
    for kind in ("APPLICANT", "PERSON"):
        if tokens(text, kind):
            return tokens(text, kind)[0]
    return None


def has_auto_generated_ending(state: dict) -> bool:
    return any(AUTO_GENERATED in str(doc.get("text", "")) for doc in documents(state))


def month_index(text: str, after: str | None = None, nth: int = 0) -> int | None:
    """(year * 12 + month) of the nth 'Mon YYYY' in `text`, counting only what follows `after` if given."""
    if after is not None:
        position = text.find(after)
        if position < 0:
            return None
        text = text[position:]
    found = _MON_YEAR_RE.findall(text)
    if len(found) <= nth:
        return None
    month, year = found[nth]
    return int(year) * 12 + MONTH_NUMBER[month]


def band_range(band: object, open_high_span: float = 10.0) -> tuple[float, float] | None:
    """Numeric (low, high) of a band such as '10-20', '<1', '>20', '20+', '36-40y'; None if it has no number."""
    if not isinstance(band, str):
        return None
    numbers = [float(n) for n in _NUMBER_RE.findall(band)]
    if not numbers:
        return None
    text = band.strip()
    if text.startswith("<"):
        return 0.0, numbers[0]
    if text.startswith(">") or text.endswith("+"):
        return numbers[0], numbers[0] + open_high_span
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    return numbers[0], numbers[0]


# --- A_income_proof_current --------------------------------------------------------------------------------


def income_proof_current(state: dict) -> tuple[bool, int | None]:
    """(is the required income proof present and current, its month_age)."""
    needed = "salary_slip" if get(state, "application.employment_type") == "salaried" else "itr"
    doc = first_doc(state, doc_type=needed)
    if doc is None:
        return False, None
    age = int(doc.get("month_age", 0) or 0)
    return age <= 2, age


@sim_rule("A_income_proof_current")
def a_income_proof_current(state: dict, question: dict, rng: random.Random) -> dict:
    current, age = income_proof_current(state)
    accuracy = 0.85 if age in (2, 3) else 0.95 if age is None else 0.93
    return noul(noisy_p(current, rng, accuracy=accuracy))


# --- A_address_proof_valid ---------------------------------------------------------------------------------

_WRONG_KIND_MARKERS = ("prepaid", "not a billing statement", "plain paper", "unregistered", "address page is missing", "photo page only")


def address_proof_valid(state: dict) -> bool:
    doc = first_doc(state, prefix="address_proof")
    if doc is None:
        return False
    kind = str(doc.get("doc_type"))
    text = str(doc.get("text", ""))
    low = text.lower()
    age = int(doc.get("month_age", 0) or 0)
    if any(marker in low for marker in _WRONG_KIND_MARKERS):
        return False
    received = month_index(text, after="Received:")
    if kind.endswith("utility_bill") and age >= 3:
        return False
    if kind.endswith("rent_agreement"):
        end = month_index(text, after="term", nth=1)
        if age >= 12 or (end is not None and received is not None and end < received):
            return False
    if kind.endswith("passport"):
        expiry = month_index(text, after="expires")
        if expiry is not None and received is not None and expiry < received:
            return False
    mine = get(state, "entity_roles.applicant.residence_address")
    shown = tokens(text, "ADDR")
    return not (mine and shown and mine not in shown)


@sim_rule("A_address_proof_valid")
def a_address_proof_valid(state: dict, question: dict, rng: random.Random) -> dict:
    """Deliberate simulator weakness: an address proof whose `script` is not latin gets about 60% accuracy and is
    pulled toward "no" (p is scaled down), whatever the truth. Native-script proofs of valid addresses therefore
    get wrongly low probabilities, so the fairness tests have something to find."""
    valid = address_proof_valid(state)
    doc = first_doc(state, prefix="address_proof")
    if doc is not None and doc.get("script", "latin") != "latin":
        return noul(noisy_p(valid, rng, accuracy=0.6) * 0.55)
    return noul(noisy_p(valid, rng, accuracy=0.90))


# --- A_statements_cover_months -----------------------------------------------------------------------------


@sim_rule("A_statements_cover_months")
def a_statements_cover_months(state: dict, question: dict, rng: random.Random) -> dict:
    covered = get(state, "bank.months_covered")
    required = get(state, "bank.months_required")
    enough = True if covered is None or required is None else covered >= required
    return noul(noisy_p(enough, rng, accuracy=0.95))


# --- A_fields_cohere ---------------------------------------------------------------------------------------

_SUPERANNUATION_RE = re.compile(r"superannuation:?\s*(\d+)\s*months", re.IGNORECASE)


def income_band_gap(state: dict) -> int | None:
    """How many bands the declared income band is above the verified one (negative if below)."""
    declared = get(state, "application.declared_monthly_income_band")
    verified = get(state, "income.verified_monthly_income_band")
    if declared not in MONTHLY_INCOME_BANDS or verified not in MONTHLY_INCOME_BANDS:
        return None
    return MONTHLY_INCOME_BANDS.index(declared) - MONTHLY_INCOME_BANDS.index(verified)


def tenure_past_superannuation(state: dict) -> bool:
    letter = first_doc(state, doc_type="employer_letter")
    tenure = get(state, "application.tenure_months")
    if letter is None or tenure is None:
        return False
    found = _SUPERANNUATION_RE.search(str(letter.get("text", "")))
    return bool(found) and tenure > int(found.group(1))


_TOO_LONG_YEARS = {">10y": ("<20", "20-24", "25-29"), "5-10y": ("<20", "20-24")}


def experience_too_long(state: dict) -> bool:
    """True for the (years band, age band) pairs the rubric names as too long for a working life that starts at 16.
    Older applicants cannot be checked with these bands, so they pass."""
    years = get(state, "application.years_in_job_or_business_band")
    return get(state, "application.applicant_age_band") in _TOO_LONG_YEARS.get(years, ())


@sim_rule("A_fields_cohere")
def a_fields_cohere(state: dict, question: dict, rng: random.Random) -> dict:
    gap = income_band_gap(state)
    incoherent = (gap is not None and gap >= 2) or tenure_past_superannuation(state) or experience_too_long(state)
    borderline = not incoherent and gap == 1
    if borderline:
        return noul(noisy_p(True, rng, accuracy=0.75))
    return noul(noisy_p(not incoherent, rng, accuracy=0.88))
