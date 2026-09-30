"""Renders the document snippets, sanction memo and Key Fact Statement of a synthetic loan file.

Limits (set by the state-size budget): a document snippet is at most ``MAX_DOC_WORDS`` (70) words, a file has at most
``MAX_DOCS`` (6) documents, and the memo and the KFS are at most ``MAX_MEMO_WORDS`` / ``MAX_KFS_WORDS`` (150) words
each. The evidence for a label is always in the first sentences of the snippet.

Documents deliberately contain raw synthetic PII in varied formats (spaced or dashed Aadhaar, ``+91 98765 43210`` or
``98765-43210`` phones, upper-case names, ``Rs. 1,45,000`` amounts). Every identifier written is recorded in the
``PIIRecorder``, which becomes the file's ``pii_inventory``. Masked forms (``XXXX XXXX 0123``, ``XXXXXXX3456``) are
partial and are not recorded.

``month_age`` is the age in months of the date that decides freshness: the bill date of a utility bill, the pay month
of a salary slip, the filing date of an ITR or self-declaration, the last statement month, the period of a GST
summary, the start of a rent agreement. It is 0 for documents whose validity is stated in the text instead (passport,
PAN card, registration certificate, title report, valuation report, employer letter). Validity (a passport's expiry,
a rent term's end) is written next to a ``Received:`` stamp carrying the application date, so the reader never needs
to know today's date.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from jevloan import finance
from jevloan.data import names as nm
from jevloan.data.risk import AS_OF, RETIREMENT_AGE
from jevloan.data.schema import (
    Address,
    Document,
    Kfs,
    LoanFile,
    PIIInventory,
    SanctionMemo,
    grid_row_for,
    load_disclosures,
)

MAX_DOC_WORDS = 70
MAX_DOCS = 6
MAX_MEMO_WORDS = 150
MAX_KFS_WORDS = 150

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BANKS = ["Uttam Bank", "Samriddhi Bank", "Nirmal Bank", "Kaveri Commercial Bank", "Bharat Urban Bank", "Meru Bank Ltd"]
UTILITIES = ["State Power Distribution Co", "Metro Electricity Supply Ltd", "City Power Board"]
TELCOS = ["Airwave Mobile", "Telenet Prepaid", "Jio-Star Recharge"]
PRODUCT_LABEL = {
    "personal_loan_unsecured": "Personal loan (unsecured)",
    "business_loan_self_employed": "Business loan (self-employed)",
    "msme_term_loan": "MSME term loan",
    "home_loan": "Home loan",
}
EXTRA_CONDITIONS = [
    "Post-dated cheque or ECS backup for the first EMI",
    "Borrower to keep contact details updated",
    "Pre-disbursal site or office visit report on file",
]
TEMPLATE_FOOTER = "Auto-generated document; no signature required."


# ------------------------------------------------------------------------------------------------ recorder / writer


class PIIRecorder:
    """Collects every raw identifier written into the file, in first-seen order without duplicates.

    ``alias(category, key, canonical)`` records another written form of the *same* real-world value (a one-line
    address of a line1, a native-script rendering, an initial form of a name). Both strings go in the category list
    (the canonical one first) and ``aliases[key] = canonical``, so a redactor can give them one token. A value that
    is deliberately different (a fraud file's second PAN or name) is never aliased.
    """

    def __init__(self) -> None:
        self.data: dict[str, list[str]] = {
            "person_names": [], "org_names": [], "pans": [], "aadhaars": [], "phones": [], "emails": [],
            "account_numbers": [], "address_lines": [], "pincodes": [],
        }
        self.aliases: dict[str, str] = {}

    def alias(self, category: str, key: str, canonical: str) -> str:
        if key == canonical:
            return key
        if self.aliases.get(key, canonical) != canonical:
            raise ValueError(f"{key!r} is already an alias of {self.aliases[key]!r}, not {canonical!r}")
        self._add(category, canonical)
        self._add(category, key)
        self.aliases[key] = canonical
        return key

    def _add(self, key: str, value: str) -> str:
        if value not in self.data[key]:
            self.data[key].append(value)
        return value

    def person(self, v: str) -> str:
        return self._add("person_names", v)

    def org(self, v: str) -> str:
        return self._add("org_names", v)

    def pan(self, v: str) -> str:
        return self._add("pans", v.upper())

    def aadhaar(self, v: str) -> str:
        return self._add("aadhaars", v)

    def phone(self, v: str) -> str:
        return self._add("phones", v[-10:])

    def email(self, v: str) -> str:
        return self._add("emails", v)

    def account(self, v: str) -> str:
        return self._add("account_numbers", v)

    def address(self, v: str) -> str:
        return self._add("address_lines", v)

    def pincode(self, v: str) -> str:
        return self._add("pincodes", v)

    def inventory(self) -> PIIInventory:
        return PIIInventory(**self.data, aliases=dict(self.aliases))


def indian_group(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s if n >= 0 else "-" + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    out = ",".join(parts + [tail])
    return out if n >= 0 else "-" + out


def month_first(m_ago: int) -> date:
    """First day of the month ``m_ago`` months before the application month."""
    idx = AS_OF.year * 12 + (AS_OF.month - 1) - m_ago
    return date(idx // 12, idx % 12 + 1, 1)


def add_months(d: date, k: int) -> date:
    idx = d.year * 12 + (d.month - 1) + k
    return date(idx // 12, idx % 12 + 1, min(d.day, 28))


def month_end(m_ago: int) -> date:
    nxt = add_months(month_first(m_ago), 1)
    return date.fromordinal(nxt.toordinal() - 1)


class Writer:
    """Formatting helpers that record what they write. All randomness comes from the file's rng."""

    def __init__(self, rng: random.Random, rec: PIIRecorder) -> None:
        self.rng = rng
        self.rec = rec

    # money and dates
    def inr(self, amount: float, style: str | None = None) -> str:
        n = int(round(amount))
        g = indian_group(n)
        style = style or self.rng.choices(["rs.", "rs", "sym", "inr", "rs.n"], [50, 15, 15, 10, 10], k=1)[0]
        return {
            "rs.": f"Rs. {g}", "rs": f"Rs {g}/-", "sym": f"₹{g}", "inr": f"INR {g}.00", "rs.n": f"Rs.{g}",
        }[style]

    def lakh(self, amount: float) -> str:
        if amount >= 1e7:
            return f"Rs. {amount / 1e7:.2f} Cr".replace(".00 ", " ")
        if amount >= 1e5:
            return f"Rs. {amount / 1e5:.2f} lakh".replace(".00 ", " ")
        return self.inr(amount, "rs.")

    def dmy(self, d: date, style: int | None = None) -> str:
        style = self.rng.choices([0, 1, 2], [60, 25, 15], k=1)[0] if style is None else style
        if style == 0:
            return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"
        if style == 1:
            return f"{d.day:02d}/{d.month:02d}/{d.year}"
        return f"{d.day:02d}-{d.month:02d}-{d.year}"

    @staticmethod
    def my(d: date) -> str:
        return f"{MONTHS[d.month - 1]} {d.year}"

    def stamp(self) -> str:
        return f"Received: {self.dmy(AS_OF, 0)}."

    def doc_date(self, month_age: int) -> date:
        cap = AS_OF.day if month_age == 0 else 28
        return month_first(month_age).replace(day=self.rng.randint(1, cap))

    # people
    def name(self, name: str, gender: str = "X", style: str | None = None) -> str:
        self.rec.person(name)
        style = style or self.rng.choices(["plain", "upper", "honorific", "initial"], [55, 20, 20, 5], k=1)[0]
        if style == "upper":
            return name.upper()
        if style == "honorific" and gender in ("M", "F"):
            hon = self.rng.choice(["Mr.", "Shri"] if gender == "M" else ["Mrs.", "Ms.", "Smt."])
            return f"{hon} {name}"
        if style == "initial" and " " in name:
            first, _, last = name.partition(" ")
            return self.rec.alias("person_names", f"{first[0]}. {last}", name)
        return name

    def plain_name(self, name: str) -> str:
        self.rec.person(name)
        return name

    def org(self, org: str, upper: bool = False) -> str:
        self.rec.org(org)
        return org.upper() if upper else org

    # identifiers
    def pan(self, pan: str) -> str:
        self.rec.pan(pan)
        return pan.lower() if self.rng.random() < 0.1 else pan

    def aadhaar(self, a: str) -> str:
        style = self.rng.choices(["spaced", "plain", "dashed", "masked"], [55, 15, 10, 20], k=1)[0]
        if style == "masked":
            return f"XXXX XXXX {a[-4:]}"
        self.rec.aadhaar(a)
        if style == "spaced":
            return f"{a[:4]} {a[4:8]} {a[8:]}"
        if style == "dashed":
            return f"{a[:4]}-{a[4:8]}-{a[8:]}"
        return a

    def phone(self, p: str) -> str:
        self.rec.phone(p)
        style = self.rng.choice(["+91 {a} {b}", "{a}-{b}", "{p}", "+91-{p}", "0{p}", "(+91) {a} {b}"])
        return style.format(a=p[:5], b=p[5:], p=p)

    def account(self, acct: str, allow_mask: bool = True) -> str:
        style = self.rng.choices(["plain", "grouped", "masked"], [50, 30, 20 if allow_mask else 0], k=1)[0]
        if style == "masked":
            return "X" * (len(acct) - 4) + acct[-4:]
        self.rec.account(acct)
        if style == "grouped":
            return " ".join(acct[i : i + 4] for i in range(0, len(acct), 4))
        return acct

    def email(self, e: str) -> str:
        self.rec.email(e)
        return e

    def pin(self, pin: str) -> str:
        self.rec.pincode(pin)
        return pin

    def addr(self, a: Address, upper: bool = False, style: int | None = None) -> str:
        style = self.rng.choice([0, 0, 1, 2]) if style is None else style
        pin = self.pin(a.pincode)
        line1 = a.line1.upper() if upper else a.line1
        full = [
            f"{a.line1}, {a.city}, {a.state} - {pin}",
            f"{a.line1}, {a.city} {pin}",
            f"{a.line1}, {a.city}, PIN {pin}",
        ][style]
        full = full.upper() if upper else full
        self.rec.address(a.line1)  # the canonical form: line1 as it appears in the address record
        self.rec.alias("address_lines", line1, a.line1)  # the upper-case line1, when a document prints it that way
        self.rec.alias("address_lines", full, a.line1)  # the one-line address with city, state and PIN
        return full

    def person_extra(self, region: str, gender: str) -> nm.Person:
        p = nm.draw_person(self.rng, region, gender)
        self.rec.person(p.name)
        return p


# ------------------------------------------------------------------------------------------------ plan


@dataclass
class DocPlan:
    """Decisions the generator has made that the documents must show (never persisted)."""

    income_defect: str | None = None  # stale | absent
    address_defect: str | None = None  # expired | wrong_type | mismatched
    coherence: str | None = None  # income_off_type | tenure_past_retirement | experience_impossible
    fraud_type: str | None = None
    id_kinds: tuple[str, ...] = ()  # pan | name | dob (identity_mismatch)
    alt_employer: str | None = None
    alt_person: nm.Person | None = None
    alt_pan: str | None = None
    alt_dob_year: int | None = None
    alt_address: Address | None = None
    native_lang: str | None = None
    native_line1: str | None = None
    alt_native_line1: str | None = None
    templated: bool = False
    informal: bool = False
    biz_pan: str | None = None  # PAN embedded in the GSTIN (entity PAN for MSME)
    business_address: Address | None = None
    dob_month: int = 6
    dob_day: int = 15
    name_region: str = "north"
    # sanction memo / KFS
    memo_tenure: int = 0
    memo_missing_condition: str | None = None
    extra_conditions: tuple[str, ...] = ()
    rate_pct: float = 12.0
    fee_items: dict[str, int] = field(default_factory=dict)
    apr_variant: str | None = None  # plain_rate | off
    omit_disclosures: tuple[str, ...] = ()
    officer: nm.Person | None = None
    officer_phone: str = ""
    officer_email: str = ""
    seed_extras: dict = field(default_factory=dict)


def _t(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _foot(plan: DocPlan) -> str:
    return TEMPLATE_FOOTER if plan.templated else ""


def _round_if_templated(plan: DocPlan, amount: int) -> int:
    return int(round(amount / 5000.0) * 5000) if plan.templated and amount >= 10000 else int(amount)


# ------------------------------------------------------------------------------------------------ English documents


def _designation(file: LoanFile, plan: DocPlan, rng: random.Random) -> str:
    if plan.coherence == "income_off_type":
        return rng.choice(nm.LOW_DESIGNATIONS)
    return rng.choice(nm.DESIGNATIONS)


def doc_salary_slip(f: LoanFile, plan: DocPlan, w: Writer, age: int, desig: str) -> str:
    net = _round_if_templated(plan, f.income.verified_monthly_income_inr)
    gross = int(net * 1.14)
    org = w.org(f.application.employer_name or "", upper=w.rng.random() < 0.2)
    pay_month = w.my(month_first(age))
    who = w.name(f.applicant.name, f.applicant.gender)
    pan = w.pan(f.applicant.pan)
    acct = w.account(f.bank.account_number)
    if w.rng.random() < 0.5:
        return _t(f"{org} salary slip for {pay_month}.", f"Employee: {who}, {desig}.", f"PAN {pan}.",
                  f"Gross {w.inr(gross)}, deductions {w.inr(gross - net)}, net pay {w.inr(net)}.",
                  f"Credited to A/c {acct}.", w.stamp(), _foot(plan))
    return _t(f"Salary slip {pay_month}, {org}.", f"Net pay {w.inr(net)} (gross {w.inr(gross)}).",
              f"{who}, {desig}, PAN {pan}, salary A/c {acct}.", w.stamp(), _foot(plan))


def doc_form16(f: LoanFile, plan: DocPlan, w: Writer) -> str:
    annual = f.income.verified_monthly_income_inr * 12
    org = w.org(f.application.employer_name or "")
    return _t(f"Form 16 for FY 2025-26 issued Jul 2026 by {org}.",
              f"Employee {w.name(f.applicant.name, f.applicant.gender)}, PAN {w.pan(f.applicant.pan)}.",
              f"Gross salary {w.inr(int(annual * 1.14))}, tax deducted {w.inr(int(annual * 0.06))}.", w.stamp(), _foot(plan))


def doc_employer_letter(f: LoanFile, plan: DocPlan, w: Writer, desig: str) -> str:
    rng = w.rng
    org = w.org(f.application.employer_name or "")
    since = add_months(month_first(0), -int(round(f.application.years_in_job_or_business * 12)))
    gross = int(_round_if_templated(plan, f.income.verified_monthly_income_inr) * 1.14)
    hr = w.person_extra(plan.name_region, rng.choice(["M", "F"]))
    hr_phone = nm.gen_phone(rng)
    hr_mail = w.email(nm.hr_email(hr.name, f.application.employer_name or "x y"))
    remaining = max(0, (f.applicant.dob_year + RETIREMENT_AGE["salaried"] - AS_OF.year) * 12 + (plan.dob_month - AS_OF.month))
    return _t(f"{org} confirms {w.name(f.applicant.name, f.applicant.gender)} is a permanent {desig} since {w.my(since)},",
              f"gross salary {w.inr(gross)} per month. Service remaining until superannuation: {remaining} months.",
              f"HR contact {hr.name}, {w.phone(hr_phone)}, {hr_mail}.", w.stamp(), _foot(plan))


def doc_itr(f: LoanFile, plan: DocPlan, w: Writer, age: int) -> str:
    annual = f.income.verified_monthly_income_inr * 12
    who = w.name(f.applicant.name, f.applicant.gender)
    pan = w.pan(f.applicant.pan)
    biz = w.org(f.application.business_name or "")
    d = w.doc_date(age)
    if plan.informal:
        return _t(f"Self-declaration of income dated {w.dmy(d)}: I, {who}, declare average monthly income of",
                  f"{w.inr(f.income.verified_monthly_income_inr)} from {biz}. No ITR filed. PAN {pan}.", w.stamp(), _foot(plan))
    ay = "2026-27" if age < 6 else "2025-26"
    return _t(f"ITR-3 acknowledgement for AY {ay}, filed {w.dmy(d)}.", f"Assessee {who}, PAN {pan}.",
              f"Total income {w.inr(annual)} from {biz}.", w.stamp(), _foot(plan))


def doc_bank_header(f: LoanFile, plan: DocPlan, w: Writer, bank_name: str, upper_name: bool) -> str:
    rng = w.rng
    b = f.bank
    end = month_end(b.most_recent_month_age) if b.most_recent_month_age > 0 else AS_OF
    start = add_months(end.replace(day=1), -(b.months_covered - 1))
    period = f"Statement period {w.dmy(start)} to {w.dmy(end)} ({b.months_covered} months)"
    who = w.name(f.applicant.name, f.applicant.gender, "upper" if upper_name else None)
    acct = w.account(b.account_number)
    head = f"{period}, {bank_name}, A/c {acct}, holder {who}."
    if f.application.employment_type == "salaried" and b.salary_narration_employer:
        emp = w.org(b.salary_narration_employer, upper=True)
        sal = _round_if_templated(plan, f.income.verified_monthly_income_inr)
        m1, m2 = MONTHS[month_first(1).month - 1], MONTHS[month_first(2).month - 1]
        body = (f"Salary credits NEFT-{emp}-SAL: {m1} {w.inr(sal)}, {m2} {w.inr(sal)}; "
                f"{b.salary_credits_months} salary months in period. EMI bounces {b.emi_bounces_6m}.")
    else:
        if b.months_covered >= 12 and f.gst is not None:
            credits = f"Total credits, last 12 months: {w.inr(f.gst.bank_credits_12m_inr)}."
        else:
            credits = f"Total credits, {b.months_covered} months: {w.inr(b.avg_monthly_credits_inr * b.months_covered)}."
        body = f"{credits} Cash deposits {int(round(b.cash_deposit_share * 100))}% of credits. EMI bounces {b.emi_bounces_6m}."
    tail = f"Registered mobile {w.phone(f.applicant.phone)}." if rng.random() < 0.6 else f"KYC Aadhaar {w.aadhaar(f.applicant.aadhaar)}."
    return _t(head, body, tail, w.stamp(), _foot(plan))


def doc_pan_card(f: LoanFile, plan: DocPlan, w: Writer) -> str:
    rng = w.rng
    kinds = plan.id_kinds
    name = plan.alt_person.name if "name" in kinds and plan.alt_person else f.applicant.name
    gender = plan.alt_person.gender if "name" in kinds and plan.alt_person else f.applicant.gender
    pan = plan.alt_pan if "pan" in kinds and plan.alt_pan else f.applicant.pan
    year = plan.alt_dob_year if "dob" in kinds and plan.alt_dob_year else f.applicant.dob_year
    dob = date(year, plan.dob_month, plan.dob_day)
    father = nm.draw_person(rng, plan.name_region, "M")
    father_name = f"{father.first} {name.split(' ', 1)[-1]}"
    w.rec.person(father_name)
    return _t(f"PAN card. Name: {w.name(name, gender, 'upper' if rng.random() < 0.6 else 'plain')}.",
              f"PAN: {w.pan(pan)}. Date of birth: {w.dmy(dob)}. Father's name: {father_name.upper()}.", w.stamp(), _foot(plan))


def _utility_text(f: LoanFile, plan: DocPlan, w: Writer, age: int, addr: Address) -> str:
    rng = w.rng
    who = w.name(f.applicant.name, f.applicant.gender, "upper" if rng.random() < 0.5 else None)
    d = w.doc_date(age)
    if plan.address_defect == "wrong_type":
        return _t(f"Prepaid mobile recharge receipt, {rng.choice(TELCOS)}, {w.dmy(d)}: not a billing statement.",
                  f"Subscriber {who}, number {w.phone(f.applicant.phone)}.", f"Address given: {w.addr(addr)}.", w.stamp(), _foot(plan))
    return _t(f"Electricity bill from {rng.choice(UTILITIES)}, bill date {w.dmy(d)}.", f"Consumer {who}.",
              f"Address: {w.addr(addr, upper=rng.random() < 0.3)}.", f"Amount due {w.inr(rng.randint(6, 48) * 100 + 45)}.", w.stamp(), _foot(plan))


def _rent_text(f: LoanFile, plan: DocPlan, w: Writer, age: int, addr: Address) -> str:
    rng = w.rng
    start = w.doc_date(age)
    end = add_months(start, 11)
    ll = w.person_extra(plan.name_region, rng.choice(["M", "F"]))
    tenant = w.name(f.applicant.name, f.applicant.gender)
    legal = "Unregistered agreement on plain paper, not stamped or notarised." if plan.address_defect == "wrong_type" else (
        "Registered agreement, stamp duty paid.")
    rent = rng.randint(6, 60) * 500
    aad = f" Tenant Aadhaar {w.aadhaar(f.applicant.aadhaar)}." if rng.random() < 0.6 else ""
    return _t(f"Rent agreement, term {w.dmy(start)} to {w.dmy(end)}. {legal}", f"Landlord {ll.name}, tenant {tenant}.{aad}",
              f"Property: {w.addr(addr)}.", f"Monthly rent {w.inr(rent)}.", w.stamp(), _foot(plan))


def _passport_text(f: LoanFile, plan: DocPlan, w: Writer, addr: Address) -> tuple[str, int]:
    rng = w.rng
    issue = add_months(month_first(0), -rng.randint(20, 90))
    expiry = add_months(issue, 120)
    if plan.address_defect == "expired":
        issue = add_months(month_first(0), -rng.randint(125, 170))
        expiry = add_months(issue, 120)
    dob = date(f.applicant.dob_year, plan.dob_month, plan.dob_day)
    holder = w.name(f.applicant.name, f.applicant.gender, "upper")
    age = 0  # validity is in the text (issue and expiry dates), not in month_age
    if plan.address_defect == "wrong_type":
        text = _t(f"Indian passport photo page only, issued {w.dmy(issue)}, expires {w.dmy(expiry)}; the address page is missing.",
                  f"Holder {holder}, born {w.dmy(dob)}.", w.stamp(), _foot(plan))
    else:
        text = _t(f"Indian passport issued {w.dmy(issue)}, expires {w.dmy(expiry)}.", f"Holder {holder}, born {w.dmy(dob)}.",
                  f"Address on last page: {w.addr(addr)}.", w.stamp(), _foot(plan))
    return text, age


def _native_addr(w: Writer, a: Address, line1: str, lang: str, digits: bool) -> str:
    city = nm.CITY_BY_NAME[a.city].native.get(lang, a.city)
    state = nm.state_native(a.state, lang) or a.state
    script = nm.LANG_SCRIPT[lang]
    pin_txt = nm.to_native_digits(a.pincode, script) if digits else a.pincode
    w.pin(a.pincode)  # canonical ASCII form
    full = f"{line1}, {city}, {state} - {pin_txt}"
    w.rec.address(a.line1)  # both native forms render this address, whose Latin line1 is the canonical form
    w.rec.alias("address_lines", line1, a.line1)
    w.rec.alias("address_lines", full, a.line1)
    return full


def native_address_doc(f: LoanFile, plan: DocPlan, w: Writer, addr: Address, line1: str) -> tuple[str, int, str]:
    """Address proof in the applicant's own script. Returns (doc_type, month_age, text). A wrong-type defect is
    never planned for native documents; an expired one is a stale bill or an ended rent term."""
    rng = w.rng
    lang = plan.native_lang or "ta"
    words = nm.NATIVE_WORDS[lang]
    native_name = f.applicant.name_native or f.applicant.name
    w.rec.person(f.applicant.name)
    w.rec.alias("person_names", native_name, f.applicant.name)
    a = _native_addr(w, addr, line1, lang, rng.random() < 0.3)
    expired = plan.address_defect == "expired"
    if rng.random() < 0.5:
        age = rng.randint(3, 6) if expired else rng.choice([0, 1, 1, 2])
        d = w.doc_date(age)
        text = _t(f"{words['elec_bill']} - {words['utility_co']}.", f"{words['name']}: {native_name}.", f"{words['address']}: {a}.",
                  f"{words['bill_date']}: {w.dmy(d)}.", f"{words['amount_due']}: {w.inr(rng.randint(6, 48) * 100 + 45)}.",
                  w.stamp(), _foot(plan))
        return "address_proof_utility_bill", age, text
    age = rng.randint(14, 24) if expired else rng.randint(1, 9)
    start = w.doc_date(age)
    end = add_months(start, 11)
    ll = nm.draw_person(rng, nm.NATIVE_LANG_REGION[lang], rng.choice(["M", "F"]), lang)
    w.rec.alias("person_names", ll.native, ll.name)  # the landlord's native-script name renders his or her romanised name
    text = _t(f"{words['rent']}: {words['term']} {w.dmy(start)} {words['from']} {w.dmy(end)} {words['to']}.",
              f"{words['landlord']}: {ll.native}. {words['tenant']}: {native_name}.", f"{words['property']}: {a}.",
              f"{words['monthly_rent']}: {w.inr(rng.randint(6, 60) * 500)}.", w.stamp(), _foot(plan))
    return "address_proof_rent_agreement", age, text


def doc_gst_summary(f: LoanFile, plan: DocPlan, w: Writer) -> str:
    g = f.gst
    assert g is not None
    w.rec.pan(plan.biz_pan or f.applicant.pan)
    biz = w.org(f.application.business_name or "")
    period = w.my(month_first(1))
    return _t(f"GSTR-3B summary for {period}, {biz}, GSTIN {g.gstin}.", f"Turnover, last 12 months: {w.inr(g.turnover_12m_inr)}.",
              f"Returns filed on time: {g.filings_on_time_12m} of {g.months_filed}.", w.stamp(), _foot(plan))


def doc_business_registration(f: LoanFile, plan: DocPlan, w: Writer) -> tuple[str, int]:
    biz = w.org(f.application.business_name or "")
    reg = add_months(month_first(0), -int(round(f.application.years_in_job_or_business * 12)))
    kind = "Udyam registration certificate" if f.segment == "msme_business" else "Shop and establishment certificate"
    baddr = plan.business_address
    assert baddr is not None
    owner = w.name(f.applicant.name, f.applicant.gender)
    pan = w.pan(plan.biz_pan or f.applicant.pan)
    age = 0  # the registration date in the text is the evidence for business vintage
    return _t(f"{kind}: {biz}, registered {w.dmy(reg.replace(day=w.rng.randint(1, 28)))}.", f"Owner {owner}, PAN {pan}.",
              f"Business address: {w.addr(baddr)}.", w.stamp(), _foot(plan)), age


def doc_property_title(f: LoanFile, plan: DocPlan, w: Writer, prop_addr: Address, ptype: str, co: str | None) -> str:
    rng = w.rng
    p = f.property
    assert p is not None
    seller = w.person_extra(plan.name_region, rng.choice(["M", "F"]))
    adv = w.person_extra(plan.name_region, rng.choice(["M", "F"]))
    status = {"clear": "clear", "disputed": "disputed, litigation pending", "pending_mutation": "mutation pending, khata transfer awaited"}[p.title_status]
    buyers = w.name(f.applicant.name, f.applicant.gender) + (f" and {w.plain_name(co)}" if co else "")
    return _t(f"Title report: title is {status}; legal opinion by {adv.name} is {p.legal_opinion}.",
              f"{ptype.capitalize()} at {w.addr(prop_addr)}, sold by {seller.name} to {buyers}.", w.stamp(), _foot(plan))


def doc_valuation(f: LoanFile, plan: DocPlan, w: Writer, ptype: str) -> str:
    p = f.property
    assert p is not None
    valuer = w.person_extra(plan.name_region, w.rng.choice(["M", "F"]))
    return _t(f"Valuation report for {ptype}: valuer {valuer.name} values the property at {w.inr(p.market_value_inr)};",
              f"second valuation {w.inr(p.valuation_2_inr)}. Loan {w.inr(f.application.loan_amount_inr)}, LTV {p.ltv:.1f}%.", w.stamp(), _foot(plan))


# ------------------------------------------------------------------------------------------------ document set


def _income_age(plan: DocPlan, rng: random.Random, kind: str) -> int:
    if plan.income_defect == "stale":
        return rng.randint(9, 15) if kind == "itr" else rng.randint(3, 8)
    return rng.randint(1, 2) if kind == "itr" else rng.choice([1, 1, 1, 2])


def build_documents(f: LoanFile, plan: DocPlan, w: Writer) -> list[Document]:
    """The document snippets of a file, at most ``MAX_DOCS``, each at most ``MAX_DOC_WORDS`` words."""
    rng = w.rng
    seg = f.segment
    salaried = f.application.employment_type == "salaried"
    desig = _designation(f, plan, rng)
    bank_name = rng.choice(BANKS)
    docs: list[tuple[str, int, str, str, str]] = []  # (doc_type, month_age, language, script, text)

    def add(doc_type: str, age: int, text: str, language: str = "en", script: str = "latin") -> None:
        docs.append((doc_type, age, language, script, text))

    # income proof
    if plan.income_defect != "absent":
        if salaried:
            age = _income_age(plan, rng, "slip")
            add("salary_slip", age, doc_salary_slip(f, plan, w, age, desig))
        else:
            age = _income_age(plan, rng, "itr")
            add("itr", age, doc_itr(f, plan, w, age))
    # bank statement
    add("bank_statement_header", f.bank.most_recent_month_age,
        doc_bank_header(f, plan, w, bank_name, upper_name=rng.random() < 0.4))
    # address proof
    home = f.applicant.address
    if plan.address_defect == "mismatched" and plan.alt_address is not None:
        shown, shown_native = plan.alt_address, plan.alt_native_line1
    else:
        shown, shown_native = home, plan.native_line1
    if plan.native_lang and shown_native:
        dtype, age0, text = native_address_doc(f, plan, w, shown, shown_native)
        add(dtype, age0, text, f.demographics.language, nm.LANG_SCRIPT[plan.native_lang])
    else:
        kind = rng.choices(["utility", "rent", "passport"], [50, 30, 20], k=1)[0]
        if kind == "utility":
            age = rng.randint(3, 6) if plan.address_defect == "expired" else rng.choice([0, 1, 1, 2])
            add("address_proof_utility_bill", age, _utility_text(f, plan, w, age, shown))
        elif kind == "rent":
            age = rng.randint(14, 24) if plan.address_defect == "expired" else rng.randint(1, 9)
            add("address_proof_rent_agreement", age, _rent_text(f, plan, w, age, shown))
        else:
            text, age = _passport_text(f, plan, w, shown)
            add("address_proof_passport", age, text)
    # PAN card
    add("pan_card_text", 0, doc_pan_card(f, plan, w))
    optional: list[tuple[str, int, str]] = []
    if seg == "salaried_personal":
        optional.append(("employer_letter", 0, doc_employer_letter(f, plan, w, desig)))
        if plan.income_defect is None and rng.random() < 0.08:
            optional.insert(0, ("form16", 2, doc_form16(f, plan, w)))
    elif seg == "self_employed":
        if f.gst is not None:
            optional.append(("gst_return_summary", 1, doc_gst_summary(f, plan, w)))
        if rng.random() < 0.6:
            text, age = doc_business_registration(f, plan, w)
            optional.append(("business_registration", age, text))
    elif seg == "msme_business":
        optional.append(("gst_return_summary", 1, doc_gst_summary(f, plan, w)))
        text, age = doc_business_registration(f, plan, w)
        optional.append(("business_registration", age, text))
    elif seg == "secured_home":
        ptype = plan.seed_extras["ptype"]
        optional.append(("property_title", 0, doc_property_title(f, plan, w, plan.seed_extras["prop_addr"], ptype, plan.seed_extras.get("co_name"))))
        optional.append(("valuation_report", 0, doc_valuation(f, plan, w, ptype)))
    for doc_type, age, text in optional:
        add(doc_type, age, text)
    docs = docs[:MAX_DOCS]
    out = []
    for n, (doc_type, age, language, script, text) in enumerate(docs, start=1):
        words = len(text.split())
        if words > MAX_DOC_WORDS:
            raise ValueError(f"{f.file_id} {doc_type} has {words} words (max {MAX_DOC_WORDS}): {text}")
        out.append(Document(doc_id=f"{f.file_id}-D{n}", doc_type=doc_type, month_age=age, language=language, script=script, text=text))
    return out


# ------------------------------------------------------------------------------------------------ memo and KFS


def build_memo_and_kfs(f: LoanFile, plan: DocPlan, w: Writer) -> tuple[SanctionMemo, Kfs]:
    rng = w.rng
    app = f.application
    row = grid_row_for(app.product, app.loan_amount_inr)
    tenure = plan.memo_tenure or app.tenure_months
    conditions = [c["text"] for c in row["required_conditions"] if c["id"] != plan.memo_missing_condition]
    conditions += list(plan.extra_conditions)
    who = w.name(f.applicant.name, f.applicant.gender, "plain")
    amount_txt = w.lakh(app.loan_amount_inr) if rng.random() < 0.3 else w.inr(app.loan_amount_inr, "rs.")
    numbered = " ".join(f"{i}. {c}." for i, c in enumerate(conditions, start=1))
    memo_text = _t(f"Sanction memo (proposed) for {f.file_id}. Applicant {who}. {PRODUCT_LABEL[app.product]}, ticket band {row['band']}.",
                   f"Amount {amount_txt}, tenure {tenure} months, rate {plan.rate_pct:.2f}% p.a. reducing.",
                   f"Conditions: {numbered}", "Subject to sanctioning authority approval.")
    while len(memo_text.split()) > MAX_MEMO_WORDS and plan.extra_conditions:
        conditions = conditions[:-1]
        plan.extra_conditions = plan.extra_conditions[:-1]
        numbered = " ".join(f"{i}. {c}." for i, c in enumerate(conditions, start=1))
        memo_text = _t(f"Sanction memo (proposed) for {f.file_id}. Applicant {who}. {PRODUCT_LABEL[app.product]}, ticket band {row['band']}.",
                       f"Amount {amount_txt}, tenure {tenure} months, rate {plan.rate_pct:.2f}% p.a. reducing.",
                       f"Conditions: {numbered}", "Subject to sanctioning authority approval.")
    if len(memo_text.split()) > MAX_MEMO_WORDS:
        raise ValueError(f"{f.file_id} memo has {len(memo_text.split())} words")
    memo = SanctionMemo(text=memo_text, product=app.product, ticket_band=row["band"], tenure_months=tenure,
                        conditions=conditions, rate_pct=plan.rate_pct)

    principal = app.loan_amount_inr
    fees = sum(plan.fee_items.values())
    emi_inr = int(round(finance.emi(principal, plan.rate_pct, tenure)))
    apr_true = finance.apr_from_components(principal, fees, emi_inr, tenure)
    if plan.apr_variant == "plain_rate":
        apr_stated = round(plan.rate_pct, 2)
    elif plan.apr_variant == "off":
        apr_stated = round(apr_true + rng.choice([-1, 1]) * rng.uniform(0.55, 1.4), 2)
    else:
        apr_stated = round(apr_true + rng.uniform(-0.02, 0.02), 2)
    total = emi_inr * tenure
    officer = plan.officer
    assert officer is not None
    # each section is rendered only if it is present, so nothing is recorded in the inventory for an omitted one
    content = {
        "apr": lambda: f"{apr_stated:.2f}% p.a. including all fees.",
        "total_cost_of_credit": lambda: f"{w.inr(total, 'rs.')} payable; interest {w.inr(total - principal, 'rs.')}, fees {w.inr(fees, 'rs.')}.",
        "fees_and_charges_breakup": lambda: ", ".join(f"{k} {w.inr(v, 'rs.')}" for k, v in plan.fee_items.items()) + ".",
        "cooling_off_period": lambda: "3 days to exit by repaying principal and pro-rata interest.",
        "grievance_redressal_officer": lambda: f"{w.plain_name(officer.name)}, {w.phone(plan.officer_phone)}, {w.email(plan.officer_email)}.",
        "recovery_agent_policy": lambda: "Only bank-appointed agents, bound by the code of conduct.",
        "repayment_schedule": lambda: f"{tenure} EMIs of {w.inr(emi_inr, 'rs.')} due on the 5th; schedule attached.",
        "penal_charges": lambda: "2% p.a. on overdue amount, not compounded.",
    }
    present, sections = [], []
    for d in load_disclosures():
        if d["id"] in plan.omit_disclosures:
            continue
        present.append(d["id"])
        sections.append(f"{len(sections) + 1}. {d['heading']}: {content[d['id']]()}")
    kfs_text = _t(f"Key Fact Statement. Borrower {who}. {PRODUCT_LABEL[app.product]}: amount {w.inr(principal, 'rs.')},",
                  f"tenure {tenure} months, rate {plan.rate_pct:.2f}%, EMI {w.inr(emi_inr, 'rs.')}.", " ".join(sections))
    if len(kfs_text.split()) > MAX_KFS_WORDS:
        raise ValueError(f"{f.file_id} KFS has {len(kfs_text.split())} words: {kfs_text}")
    kfs = Kfs(text=kfs_text, principal_inr=principal, fees_inr=fees, rate_pct=plan.rate_pct, emi_inr=emi_inr,
              tenure_months=tenure, apr_stated_pct=apr_stated, disclosures_present=present)
    return memo, kfs
