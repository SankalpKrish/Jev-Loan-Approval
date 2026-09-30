"""Adversarial PII fixtures and a clean (already redacted) sample set for the gate (PLAN 3.5, spec section 4).

`adversarial_fixtures()` returns at least 150 payloads shaped like gateway requests (`{"state": ..., "questions":
...}`) with one identifier hidden somewhere in them, disguised in the ways real data gets disguised. Each carries
the detectors any of which should fire. `clean_samples()` returns at least 100 realistic, already-redacted states
and question payloads that must produce zero findings; it measures the false-positive rate.

`known_gap_fixtures()` lists obfuscation classes the gate cannot reasonably catch. They are *not* part of the
catch-rate denominator but are printed by `jevloan pii fixtures-check`, so the limitation stays visible.

Everything is synthetic and generated from a fixed seed. Identifiers are format-valid inventions, not real ones.
"""

from __future__ import annotations

import base64
import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from jevloan.pii import lexicon as lx

__all__ = ["Fixture", "adversarial_fixtures", "clean_samples", "known_gap_fixtures", "CATEGORIES"]

SEED = 20260929


@dataclass
class Fixture:
    id: str
    category: str
    payload: Any
    expected_detectors: set[str] = field(default_factory=set)
    note: str = ""


# --------------------------------------------------------------------------------------------------
# Synthetic value generators
# --------------------------------------------------------------------------------------------------

_UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_HOLDER = "PCHFATBLJG"


def _pan(rng: random.Random) -> str:
    return (
        "".join(rng.choice(_UP) for _ in range(3)) + rng.choice(_HOLDER) + rng.choice(_UP)
        + "".join(str(rng.randrange(10)) for _ in range(4)) + rng.choice(_UP)
    )


def _digits(rng: random.Random, n: int, first: str = "1-9") -> str:
    lo, hi = (int(first[0]), int(first[-1]))
    return str(rng.randint(lo, hi)) + "".join(str(rng.randrange(10)) for _ in range(n - 1))


def _luhn_complete(prefix: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)


def _phone(rng: random.Random) -> str:
    return str(rng.randint(6, 9)) + "".join(str(rng.randrange(10)) for _ in range(9))


def _group(s: str, size: int, sep: str) -> str:
    return sep.join(s[i : i + size] for i in range(0, len(s), size))


_DEV = str.maketrans("0123456789", "०१२३४५६७८९")
_TAM = str.maketrans("0123456789", "௦௧௨௩௪௫௬௭௮௯")
_BEN = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
_FULL = str.maketrans("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ@.", "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ＠．")
_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _spell(digits: str, *, collapse: bool = False, sep: str = " ") -> str:
    out: list[str] = []
    i = 0
    while i < len(digits):
        d = digits[i]
        run = 1
        while collapse and i + run < len(digits) and digits[i + run] == d and run < 3:
            run += 1
        word = _WORDS[int(d)]
        if run == 2:
            out.append("double " + word)
        elif run == 3:
            out.append("triple " + word)
        else:
            out.append(word)
        i += run
    return sep.join(out)


def _zw(s: str, rng: random.Random, chars: str = "​‌⁠﻿") -> str:
    out = []
    for ch in s:
        out.append(ch)
        if rng.random() < 0.5:
            out.append(rng.choice(chars))
    return "".join(out)


def _names(rng: random.Random) -> tuple[str, str]:
    firsts = sorted(lx.FIRST_NAMES)
    lasts = sorted(lx.SURNAMES)
    return rng.choice(firsts).capitalize(), rng.choice(lasts).capitalize()


# --------------------------------------------------------------------------------------------------
# Payload hosts: where the disguised value sits
# --------------------------------------------------------------------------------------------------

_DOC_TYPES = ["salary_slip", "bank_statement_header", "address_proof_utility_bill", "employer_letter", "pan_card_text",
              "address_proof_rent_agreement", "itr", "gst_return_summary", "property_title", "valuation_report"]
_QIDS = ["A_income_proof_current", "A_address_proof_valid", "B_identity_coheres", "B_salary_matches_employer",
         "C_income_stable", "C_foir_within_limit", "E_memo_matches_grid", "F_ews_emi_bounces"]


def _state(rng: random.Random, docs: list[dict[str, Any]] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": "jevloan.state.v1", "stage": "appraisal", "segment": rng.choice(["salaried_personal", "self_employed", "msme_business", "secured_home"]),
        "application": {"product": "personal_loan_unsecured", "loan_amount_band": rng.choice(["3-5L", "5-10L", "10-25L"]),
                        "tenure_months": rng.choice([24, 36, 48, 60]), "purpose": "debt_consolidation", "employment_type": "salaried",
                        "applicant_age_band": rng.choice(["25-34", "35-44", "45-54"]), "city_tier": rng.choice([1, 2, 3])},
        "bureau": {"score_band": rng.choice(["650-699", "700-749", "750-799"]), "active_loans": rng.randint(0, 4), "max_dpd_12m_band": "0"},
        "income": {"verified_monthly_income_band": rng.choice(["25-50k", "50-75k", "75k-1L"]), "volatility": "low", "documentation_type": "salary_slip"},
        "obligations": {"foir_pct_band": rng.choice(["30-40", "40-50"]), "segment_foir_limit_pct": 55},
        "identity_signals": {"pan_aadhaar_linked": True, "phone_vintage_band": "1-3y", "email_domain_type": "free"},
    }
    if extra:
        state.update(extra)
    if docs is not None:
        state["documents"] = docs
    return state


def _questions(rng: random.Random, n: int = 2) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid in rng.sample(_QIDS, n):
        out[qid] = {
            "type": "noul",
            "instructions": {"question": "Does the evidence in `documents` support the claim?", "focus": "the most recent document", "refer_to": ["`documents`"]},
            "criteria": {"true": {"what": "Evidence is present and consistent", "examples": ["salary credited every month", "employer matches"]},
                         "false": {"what": "Evidence is missing or conflicting", "not_for": "minor formatting differences", "examples": ["no salary credit", "employer differs"]}},
        }
    return out


def _doc(rng: random.Random, text: str) -> dict[str, Any]:
    return {"doc_type": rng.choice(_DOC_TYPES), "month_age": rng.randint(0, 5), "script": "latin", "text": text}


def _host(rng: random.Random, kind: int, text: str) -> Any:
    """Put `text` somewhere realistic. `kind` cycles through the layouts."""
    pre = "Salary slip for Mar 2026. Employer: [ORG_A]. Net pay ₹[50-75k]. "
    k = kind % 9
    if k == 0:  # third document of a state
        docs = [_doc(rng, "Address proof, latest utility bill. Address: [ADDR_1]."), _doc(rng, "Bank statement header for Mar 2026. Account: [ACCT_1]."),
                _doc(rng, pre + text)]
        return {"state": _state(rng, docs), "questions": _questions(rng)}
    if k == 1:  # first document
        docs = [_doc(rng, text)]
        return {"state": _state(rng, docs), "questions": _questions(rng)}
    if k == 2:  # question instruction
        q = _questions(rng)
        qid = next(iter(q))
        q[qid]["instructions"]["focus"] = text
        return {"state": _state(rng, [_doc(rng, "Salary slip for Mar 2026. Net pay ₹[50-75k].")]), "questions": q}
    if k == 3:  # criteria example
        q = _questions(rng)
        qid = next(iter(q))
        q[qid]["criteria"]["false"]["examples"].append(text)
        return {"state": _state(rng), "questions": q}
    if k == 4:  # bank narration inside the state
        return {"state": _state(rng, None, {"bank": {"months_covered": 6, "salary_narration_org_token": "[ORG_A]", "narration_sample": text}}), "questions": _questions(rng, 1)}
    if k == 5:  # deep nesting
        node: Any = {"note": text}
        for depth in range(10):
            node = {f"level_{depth}": node, "flag": depth % 2 == 0}
        return {"state": _state(rng, None, {"extra": node}), "questions": _questions(rng, 1)}
    if k == 6:  # inside a list of strings
        return {"state": _state(rng, None, {"remarks": ["checked", "in order", text, "closed"]}), "questions": _questions(rng, 1)}
    if k == 7:  # a question payload on its own
        q = _questions(rng, 1)
        qid = next(iter(q))
        q[qid]["instructions"] = f"Is the applicant's statement consistent? Context: {text}"
        return {"questions": q}
    docs = [_doc(rng, "Employer letter confirming service since 2021."), _doc(rng, text), _doc(rng, "Form 16 summary, tax deducted at source in order.")]
    return {"state": _state(rng, docs)}


# --------------------------------------------------------------------------------------------------
# Cases: (category, text, expected detectors)
# --------------------------------------------------------------------------------------------------

_Case = tuple[str, str, set[str]]


def _cases(rng: random.Random) -> list[_Case]:
    c: list[_Case] = []

    def add(cat: str, text: str, *expected: str) -> None:
        c.append((cat, text, set(expected)))

    # ---- PAN ------------------------------------------------------------------------------------
    pans = [_pan(rng) for _ in range(20)]
    add("pan_plain", f"PAN card copy submitted. PAN: {pans[0]}. Name matches application.", "pan")
    add("pan_plain", f"Permanent Account Number {pans[1]} appears on the salary slip.", "pan")
    add("pan_plain", pans[2], "pan")
    add("pan_plain", f"Please verify pan no.{pans[3]}, dob and photo.", "pan")
    add("pan_lowercase", f"pan: {pans[4].lower()} (as typed by applicant)", "pan")
    add("pan_lowercase", f"tds certificate lists {pans[5].lower()} as deductee", "pan")
    add("pan_mixedcase", f"PAN {pans[6][:3].lower()}{pans[6][3:6].upper()}{pans[6][6:].lower()} on file", "pan")
    add("pan_spaced", f"PAN {pans[7][:5]} {pans[7][5:9]} {pans[7][9]}", "pan")
    add("pan_spaced", f"PAN: {pans[8][:5].lower()} {pans[8][5:9]} {pans[8][9].lower()}", "pan")
    add("pan_dashed", f"PAN {pans[9][:5]}-{pans[9][5:9]}-{pans[9][9]}", "pan")
    add("pan_dotted", f"PAN {pans[10][:5]}.{pans[10][5:9]}.{pans[10][9]}", "pan")
    add("pan_letters_spaced", "PAN " + " ".join(pans[11]), "pan")
    add("pan_letters_spaced", "PAN " + "-".join(pans[12]), "pan")
    add("pan_gstin", f"GSTIN 29{pans[13]}1Z5 registered under the business", "pan")
    add("pan_gstin", f"gst reg no: 27{pans[14].lower()}1z3", "pan")
    add("pan_zerowidth", "PAN " + _zw(pans[15], rng), "pan")
    add("pan_fullwidth", "PAN " + pans[16].translate(_FULL), "pan")
    add("pan_devanagari_digits", f"PAN {pans[17][:5]}{pans[17][5:9].translate(_DEV)}{pans[17][9]}", "pan")
    add("pan_spelled_digits", f"PAN {pans[18][:5]} {_spell(pans[18][5:9])} {pans[18][9]}", "pan")
    add("pan_glued", f"PAN{pans[19]} attached", "pan")
    add("pan_glued", f"PANNO-{pans[3]}", "pan")

    # ---- Aadhaar / VID --------------------------------------------------------------------------
    aad = [_digits(rng, 12, "2-9") for _ in range(14)]
    add("aadhaar_plain", f"Aadhaar number {aad[0]} verified via OTP", "aadhaar", "account_number")
    add("aadhaar_plain", f"UID: {aad[1]}", "aadhaar", "account_number")
    add("aadhaar_grouped", f"Aadhaar {_group(aad[2], 4, ' ')}", "aadhaar", "account_number")
    add("aadhaar_grouped", f"aadhar no {_group(aad[3], 4, '-')} on the address proof", "aadhaar", "account_number")
    add("aadhaar_grouped", f"UID {_group(aad[4], 4, '.')}", "aadhaar", "account_number")
    add("aadhaar_grouped", f"Aadhaar {_group(aad[5], 4, '/')}", "aadhaar", "account_number")
    add("aadhaar_grouped", f"Aadhaar {_group(aad[6], 3, ' ')}", "aadhaar", "account_number")
    add("aadhaar_devanagari", f"आधार संख्या {_group(aad[7], 4, ' ').translate(_DEV)}", "aadhaar", "account_number")
    add("aadhaar_bengali_digits", f"Aadhaar {aad[8].translate(_BEN)}", "aadhaar", "account_number")
    add("aadhaar_tamil_digits", f"Aadhaar {_group(aad[9], 4, ' ').translate(_TAM)}", "aadhaar", "account_number")
    add("aadhaar_fullwidth", f"Aadhaar {aad[10].translate(_FULL)}", "aadhaar", "account_number")
    add("aadhaar_zerowidth", "Aadhaar " + _zw(aad[11], rng), "aadhaar", "account_number")
    add("aadhaar_spelled", f"Aadhaar {_spell(aad[12])}", "aadhaar", "account_number")
    add("aadhaar_single_digits", "Aadhaar " + " ".join(aad[13]), "aadhaar", "account_number")
    vids = [_digits(rng, 16) for _ in range(4)]
    add("aadhaar_vid", f"Virtual ID {vids[0]} used for e-KYC", "aadhaar_vid", "account_number", "card_number")
    add("aadhaar_vid", f"VID {_group(vids[1], 4, ' ')}", "aadhaar_vid", "account_number", "card_number")
    add("aadhaar_vid", f"VID {_group(vids[2], 4, '-')}", "aadhaar_vid", "account_number", "card_number")
    add("aadhaar_vid", f"VID {vids[3].translate(_DEV)}", "aadhaar_vid", "account_number", "card_number")

    # ---- account numbers ------------------------------------------------------------------------
    for n in (9, 10, 11, 12, 14, 15, 16, 18):
        add("account_number", f"Salary credited to A/c No {_digits(rng, n)} for six months", "account_number", "aadhaar", "aadhaar_vid", "phone_in", "card_number")
    add("account_number_grouped", f"Account {_group(_digits(rng, 12), 4, ' ')} at branch", "account_number", "aadhaar")
    add("account_number_grouped", f"acct {_group(_digits(rng, 14), 2, '-')}", "account_number")
    add("account_number_grouped", f"Bank account: {_group(_digits(rng, 15), 5, '.')}", "account_number")
    add("account_number_grouped", f"a/c {_group(_digits(rng, 11), 3, '/')}", "account_number", "aadhaar", "phone_in")
    add("account_number_glued", f"ACC{_digits(rng, 13)}", "account_number")
    add("account_number_glued", f"SB{_digits(rng, 11)} savings", "account_number", "aadhaar")
    add("account_number_devanagari", f"खाता संख्या {_digits(rng, 13).translate(_DEV)}", "account_number")
    add("account_number_zerowidth", "Account " + _zw(_digits(rng, 14), rng), "account_number")
    add("account_number_spelled", "Account " + _spell(_digits(rng, 10), collapse=True), "account_number", "phone_in")
    add("account_number_fullwidth", "Account " + _digits(rng, 12).translate(_FULL), "account_number", "aadhaar")
    add("account_number_ifsc_pair", f"Beneficiary account {_digits(rng, 15)} IFSC SBIN0{_digits(rng, 6)}", "account_number", "ifsc")

    # ---- cards ----------------------------------------------------------------------------------
    for prefix in ("453212", "512345", "601100", "37282", "4111111111"):
        base = prefix + "".join(str(rng.randrange(10)) for _ in range((15 if prefix.startswith("37") else 16) - len(prefix) - 1))
        card = _luhn_complete(base)
        if len(card) == 16:
            add("card_number", f"Card ending on file: {_group(card, 4, ' ')}", "card_number", "aadhaar_vid", "account_number")
        else:
            add("card_number", f"Amex {card}", "card_number", "account_number")
    add("card_number", f"Card {_group(_luhn_complete('4' + _digits(rng, 14)), 4, '-')}", "card_number", "aadhaar_vid", "account_number")

    # ---- phones ---------------------------------------------------------------------------------
    ph = [_phone(rng) for _ in range(24)]
    add("phone_plain", f"Contact number {ph[0]} (mobile)", "phone_in", "account_number")
    add("phone_plain", f"Mob: {ph[1]}", "phone_in", "account_number")
    add("phone_plus91", f"Phone +91 {ph[2][:5]} {ph[2][5:]}", "phone_in", "account_number", "aadhaar")
    add("phone_plus91", f"Phone +91-{ph[3][:5]}-{ph[3][5:]}", "phone_in", "account_number", "aadhaar")
    add("phone_plus91", f"call +91{ph[4]}", "phone_in", "account_number", "aadhaar")
    add("phone_plus91", f"call +91 {ph[5]} today", "phone_in", "account_number", "aadhaar")
    add("phone_0091", f"WhatsApp 0091 {ph[6]}", "phone_in", "account_number")
    add("phone_0091", f"0091-{ph[7][:5]}-{ph[7][5:]}", "phone_in", "account_number")
    add("phone_trunk0", f"Phone 0{ph[8]}", "phone_in", "account_number")
    add("phone_trunk0", f"Phone 0 {ph[9][:5]} {ph[9][5:]}", "phone_in", "account_number")
    add("phone_spaced", f"Mobile {ph[10][:5]} {ph[10][5:]}", "phone_in", "account_number")
    add("phone_spaced", f"Mobile {ph[11][:3]} {ph[11][3:6]} {ph[11][6:]}", "phone_in", "account_number")
    add("phone_dashed", f"Mobile {ph[12][:5]}-{ph[12][5:]}", "phone_in", "account_number")
    add("phone_dotted", f"Mobile {ph[13][:5]}.{ph[13][5:]}", "phone_in", "account_number")
    add("phone_single_digits", "Mobile " + " ".join(ph[14]), "phone_in", "account_number")
    add("phone_single_digits", "Mobile " + "-".join(ph[15]), "phone_in", "account_number")
    add("phone_spelled", f"call {_spell(ph[16])}", "phone_in", "account_number")
    add("phone_spelled", f"call {_spell(ph[17], sep='-')}", "phone_in", "account_number")
    add("phone_spelled_double", f"call {_spell(ph[18], collapse=True)}", "phone_in", "account_number")
    add("phone_spelled_double", "reach me on nine eight double seven six five triple four two", "phone_in", "account_number")
    add("phone_spelled_oh", "phone nine eight seven six oh five four three two oh", "phone_in", "account_number")
    add("phone_devanagari_digits", f"फ़ोन {ph[19].translate(_DEV)}", "phone_in", "account_number")
    add("phone_fullwidth", f"Phone {ph[20].translate(_FULL)}", "phone_in", "account_number")
    add("phone_zerowidth", "Phone " + _zw(ph[21], rng), "phone_in", "account_number")
    add("phone_letter_o", f"Phone {ph[22][:5]}O{ph[22][6:]}", "phone_in", "account_number")
    add("phone_landline", "Office (022) 2345 6789", "phone_in")
    add("phone_landline", "Office 022-23456789", "phone_in", "account_number")
    add("phone_landline", "Office +91 44 2345 6789", "phone_in", "account_number")
    add("phone_landline", "Office 080 4123 4567", "phone_in", "account_number")
    add("phone_landline", "Tel: 011 2612 3456", "phone_in", "account_number")
    add("phone_in_upi", f"UPI {ph[23]}@ybl", "upi_id", "phone_in", "account_number")

    # ---- email / UPI ----------------------------------------------------------------------------
    f1, l1 = _names(rng)
    add("email_plain", f"Email {f1.lower()}.{l1.lower()}@gmail.com", "email")
    add("email_plain", f"reach {l1.lower()}{rng.randint(10, 99)}@yahoo.co.in for documents", "email")
    add("email_plain", f"corporate mail {f1.lower()}@infosys-demo.com", "email")
    add("email_uppercase", f"EMAIL: {f1.upper()}.{l1.upper()}@OUTLOOK.COM", "email")
    add("email_plus_tag", f"{f1.lower()}+loan@gmail.com", "email")
    add("email_obfuscated", f"email {f1.lower()} [at] gmail [dot] com", "email")
    add("email_obfuscated", f"email {f1.lower()}(at)rediffmail(dot)com", "email")
    add("email_obfuscated", f"write to {l1.lower()} at hotmail dot com", "email")
    add("email_fullwidth", f"Email {(f1.lower() + '@gmail.com').translate(_FULL).lower()}", "email")
    add("email_spaced_at", f"Email {f1.lower()} @ gmail.com", "email")
    for handle in ("okaxis", "oksbi", "ybl", "paytm", "okhdfcbank", "ibl", "upi", "axl"):
        add("upi_id", f"pay via UPI {f1.lower()}{rng.randint(1, 99)}@{handle}", "upi_id")
    add("upi_id", f"UPI ID: {_phone(rng)}@paytm", "upi_id", "phone_in", "account_number")

    # ---- IFSC / passport / voter / DL -----------------------------------------------------------
    for bank in ("SBIN", "HDFC", "ICIC", "UTIB", "KKBK"):
        add("ifsc", f"IFSC {bank}0{_digits(rng, 6, '0-9')} branch", "ifsc")
    add("ifsc", "ifsc code: sbin0001234", "ifsc")
    add("ifsc_spaced", "IFSC HDFC 0001234", "ifsc")
    for _ in range(3):
        letter = rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
        add("passport", f"Passport No. {letter}{_digits(rng, 7)} valid till 2031", "passport")
    add("passport", f"passport {rng.choice(_UP)}{_digits(rng, 7)}".lower(), "passport")
    add("passport_spaced", f"Passport {rng.choice(_UP)} {_digits(rng, 7)}", "passport")
    for _ in range(3):
        add("voter_id", f"Voter ID {''.join(rng.choice(_UP) for _ in range(3))}{_digits(rng, 7)}", "voter_id")
    add("voter_id_spaced", f"EPIC {''.join(rng.choice(_UP) for _ in range(3))} {_digits(rng, 7)}", "voter_id")
    for st in ("MH", "KA", "DL", "TN", "GJ"):
        add("driving_licence", f"DL {st}{_digits(rng, 2)}{_digits(rng, 11)}", "driving_licence", "account_number")
    add("driving_licence_spaced", f"Driving licence MH12 {_digits(rng, 11)}", "driving_licence", "account_number")
    add("driving_licence_spaced", f"DL: KA-05-{_digits(rng, 11)}", "driving_licence", "account_number")

    # ---- PIN codes (contextual) -----------------------------------------------------------------
    pins = [_digits(rng, 6) for _ in range(9)]
    add("pincode_ctx", f"PIN: {pins[0]}", "pincode_ctx")
    add("pincode_ctx", f"Pincode {pins[1]}", "pincode_ctx")
    add("pincode_ctx", f"PIN Code - {pins[2]}", "pincode_ctx")
    add("pincode_ctx", f"Postal code: {pins[3]}", "pincode_ctx")
    add("pincode_ctx", f"पिन कोड: {pins[4].translate(_DEV)}", "pincode_ctx")
    add("pincode_spaced", f"PIN {pins[5][:3]} {pins[5][3:]}", "pincode_ctx")
    add("pincode_city", f"Flat 4, Some Road, Bengaluru - {pins[6]}", "pincode_ctx")
    add("pincode_city", f"House 12, Sector 9, Gurugram, {pins[7]}", "pincode_ctx")
    add("pincode_city", f"registered office, Chennai - {pins[8]}", "pincode_ctx")

    # ---- person names ---------------------------------------------------------------------------
    def nm() -> str:
        a, b = _names(rng)
        return f"{a} {b}"

    for hon in ("Mr", "Mrs", "Ms", "Miss", "Shri", "Smt", "Kumari", "Dr", "Sri", "Shree"):
        add("name_honorific", f"Applicant is {hon}. {nm()}, resident of the city", "person_name")
    add("name_honorific", "Mr Zoravar Bhatnagar attended the visit", "person_name")
    add("name_honorific", "Smt. Ishwari Thirumalai signed the declaration", "person_name")
    add("name_honorific", f"Late Shri {nm()} was the original account holder", "person_name")
    add("name_honorific", f"MR {nm().upper()} - signatory", "person_name")
    add("name_honorific", "Dr. Ambedkar Kulasekaran confirmed employment", "person_name")
    add("name_relation", f"{nm()} S/o {nm()}", "person_name")
    add("name_relation", f"Guarantor Jayanthi D/o Shri Bhaskaran Pillai", "person_name")
    add("name_relation", f"applicant W/o {nm()}, aged 34", "person_name")
    add("name_relation", "C/o Thiruvengadam Reddiar, Salem", "person_name")
    add("name_relation", f"born to son of {nm()}", "person_name")
    add("name_relation", f"daughter of Mr {nm()}", "person_name")
    add("name_relation", f"wife of {nm()} works at the firm", "person_name")
    for label in ("Name", "Applicant", "Applicant name", "Account holder", "Employee name", "Borrower", "Guarantor", "Proprietor"):
        add("name_label", f"{label}: {nm()}", "person_name")
    add("name_label", "Name: Zxqwv Plmnb", "person_name")
    add("name_label", "Applicant name Thangavelu Palanisamy", "person_name")
    add("name_label", "borrower: velmurugan chinnasamy", "person_name")
    add("name_label", f"Name = {nm().upper()}", "person_name")
    add("name_label", "Employee name: A. Krishnamoorthy", "person_name")
    add("name_lexicon_pair", f"Cheque issued in favour of {nm()} dated last month", "person_name")
    add("name_lexicon_pair", f"Witness: {nm()} and {nm()}", "person_name")
    add("name_lexicon_pair", "The account is jointly held with Rakesh Yadav", "person_name")
    add("name_lexicon_pair", "Reference given by Zoravar Sharma", "person_name")
    add("name_lexicon_pair", "Kumar Iyer confirmed the address", "person_name")
    add("name_allcaps", "SALARY CREDIT FROM RAJESH KUMAR SHARMA", "person_name")
    add("name_allcaps", "PRIYA NAIR", "person_name")
    add("name_allcaps", f"NEFT FROM {nm().upper()} REF 2026", "person_name")
    add("name_initials", "K. Venkatesh", "person_name")
    add("name_initials", "Signature: R Sharma", "person_name")
    add("name_initials", "S.K. Gupta was present", "person_name")
    add("name_initials", "M. S. Subramanian attended", "person_name")
    add("name_initials", "K Venkata Ramana", "person_name")
    for sentence in ("Cheque signed by Priya", "Amount paid to Sunil on the due date", "As told by Kavita, the shop is closed",
                     "Reference: Anjali will verify the details", "Spoke with Deepak regarding the salary", "Transferred to Meena last month",
                     "The loan was recommended by Suresh"):
        add("name_single_first", sentence, "person_name")
    for text in ("आवेदक: राहुल शर्मा", "खाताधारक का नाम: रमेश गुप्ता", "प्रिया वर्मा ने हस्ताक्षर किए", "सुनीता", "यह दस्तावेज़ राजेश कुमार का है"):
        add("name_devanagari", text, "person_name")
    add("name_devanagari_honorific", "श्रीमती कमला बाई का वेतन खाता", "person_name")
    add("name_devanagari_honorific", "श्री रघुनाथ प्रसाद", "person_name")
    add("name_native_label", "नाम: अनुराधा तिवारी", "person_name")
    add("name_native_label", "पिता का नाम: हरिशंकर मिश्रा", "person_name")
    add("name_native_label", "பெயர்: குமரன் முத்து", "person_name")
    add("name_native_label", "নাম: সুমন ঘোষ", "person_name")
    add("name_native_label", "పేరు: శ్రీనివాస్ రెడ్డి", "person_name")
    add("name_native_honorific", "திருமதி லட்சுமி அம்மாள்", "person_name")
    add("name_native_honorific", "শ্রী রাহুল ঘোষ", "person_name")
    add("name_tamil", "பிரியா மீனா", "person_name")
    add("name_bengali", "সুমন দাস", "person_name")

    # ---- combined realistic documents -----------------------------------------------------------
    n1, p1, ph1, ac1 = nm(), _pan(rng), _phone(rng), _digits(rng, 12)
    add("doc_salary_slip", f"Salary slip for Mar 2026. Employee: {n1}. PAN: {p1}. Net pay ₹[50-75k]. Bank A/c {ac1}.", "person_name", "pan", "account_number", "aadhaar")
    add("doc_bank_header", f"Bank statement. Account holder: {nm()}. Account No: {_digits(rng, 14)}. IFSC HDFC0{_digits(rng, 6)}. Mobile {_phone(rng)}.", "person_name", "account_number", "ifsc", "phone_in")
    add("doc_utility_bill", f"Electricity bill. Consumer: Mrs. {nm()}. Address: Flat 12, MG Road, Pune - {_digits(rng, 6)}. Contact {_phone(rng)}.", "person_name", "pincode_ctx", "phone_in", "account_number")
    add("doc_pan_card", f"Income Tax Department. Name: {nm()}. Father's Name: {nm()}. PAN {_pan(rng)}.", "person_name", "pan")
    add("doc_employer_letter", f"This is to certify that Shri {nm()} is employed. Aadhaar {_group(_digits(rng, 12, '2-9'), 4, ' ')}.", "person_name", "aadhaar", "account_number")
    add("doc_rent_agreement", f"Rent agreement between {nm()} and {nm()}. Email {n1.split()[0].lower()}@gmail.com", "person_name", "email")
    add("doc_gst_summary", f"GSTIN 27{_pan(rng)}1Z5. Proprietor: {nm()}. Registered mobile {_phone(rng)}", "pan", "person_name", "phone_in", "account_number")
    add("doc_property_title", f"Sale deed. Owner: {nm()} S/o {nm()}. Survey no 44. PIN: {_digits(rng, 6)}", "person_name", "pincode_ctx")
    add("doc_valuation", f"Valuation report prepared for {nm()}. Contact +91 {_phone(rng)}", "person_name", "phone_in", "account_number")
    add("doc_passport_page", f"Passport {rng.choice(_UP)}{_digits(rng, 7)}. Surname: {_names(rng)[1]}. Given names: {_names(rng)[0]}", "passport", "person_name")
    return c


# --------------------------------------------------------------------------------------------------
# Structural fixtures: keys, nesting, JSON strings, encodings
# --------------------------------------------------------------------------------------------------


def _structural(rng: random.Random) -> list[tuple[str, Any, set[str]]]:
    out: list[tuple[str, Any, set[str]]] = []
    pan, phone, aad = _pan(rng), _phone(rng), _digits(rng, 12, "2-9")
    name = " ".join(_names(rng))
    acct = _digits(rng, 13)
    out.append(("key_pan", {"state": {"identity_signals": {pan: "mismatch"}}}, {"pan"}))
    out.append(("key_phone", {"state": {"contacts": {phone: {"verified": True}}}}, {"phone_in", "account_number"}))
    out.append(("key_aadhaar", {"state": {aad: "linked"}}, {"aadhaar", "account_number"}))
    out.append(("key_name", {"state": {"references": {name: "friend"}}}, {"person_name"}))
    out.append(("key_email", {"state": {"notes": {"jayanthi.nair@gmail.com": "primary"}}}, {"email"}))
    out.append(("key_account", {"questions": {acct: {"type": "noul", "instructions": "ok?"}}}, {"account_number"}))
    out.append(("key_lowercase_pan", {"state": {pan.lower(): 1}}, {"pan"}))
    out.append(("key_underscored_pan", {"state": {f"{pan[:5]}_{pan[5:9]}_{pan[9]}": 1}}, {"pan"}))
    out.append(("key_context_name", {"state": {"applicant_name": "Thirumalai Vasan"}}, {"person_name"}))
    out.append(("key_context_name", {"state": {"borrower": {"full_name": "zorba ravichandran"}}}, {"person_name"}))
    out.append(("key_context_name", {"state": {"guarantor_name": "Bhuvaneswari"}}, {"person_name"}))
    out.append(("key_context_pin", {"state": {"address": {"pincode": 560034}}}, {"pincode_ctx"}))
    out.append(("key_context_pin", {"state": {"address": {"pin_code": "400001"}}}, {"pincode_ctx"}))
    out.append(("number_value_phone", {"state": {"contact": {"mobile": int(phone)}}}, {"phone_in", "account_number"}))
    out.append(("number_value_account", {"state": {"bank": {"account": int(acct)}}}, {"account_number"}))
    out.append(("number_value_float_account", {"state": {"bank": {"account": float(int(acct))}}}, {"account_number"}))
    out.append(("list_of_values", {"state": {"identifiers": ["stable", pan, "checked"]}}, {"pan"}))
    out.append(("list_of_values", {"state": {"phones": [phone]}}, {"phone_in", "account_number"}))
    out.append(("list_of_values", {"state": {"matrix": [["a", "b"], ["c", ["d", f"Aadhaar {aad}"]]]}}, {"aadhaar", "account_number"}))
    out.append(("tuple_value", {"state": {"names": ("ok", f"Mr {name}")}}, {"person_name"}))
    deep: Any = {"reference": f"PAN {pan}"}
    for i in range(14):
        deep = {f"n{i}": deep}
    out.append(("deep_nesting", {"state": deep}, {"pan"}))
    deep2: Any = [f"call {phone}"]
    for _ in range(12):
        deep2 = [deep2, "x"]
    out.append(("deep_nesting", {"state": {"blocks": deep2}}, {"phone_in", "account_number"}))
    out.append(("question_instructions", {"questions": {"B_identity_coheres": {"type": "noul", "instructions": f"Does the PAN {pan} on the card match the salary slip?",
                                                                            "criteria": {"true": "match", "false": "mismatch"}}}}, {"pan"}))
    out.append(("question_instructions", {"questions": {"B_identity_coheres": {"type": "noul", "instructions": {"question": "Is the person the same?", "potential_duplicate": {"name": f"Mr {name}", "location": "Pune"}}}}}, {"person_name"}))
    out.append(("question_criteria", {"questions": {"C_income_stable": {"type": "score", "instructions": "Rate stability", "criteria": [{"level": "low", "what": f"salary paid by cheque to {name}"}, {"level": "high", "what": "regular"}]}}}, {"person_name"}))
    out.append(("question_criteria", {"questions": {"A_address_proof_valid": {"type": "choice", "instructions": "Which?", "criteria": {"utility": {"what": "bill", "examples": [f"bill for +91 {phone}"]}, "rent": None}}}}, {"phone_in", "account_number"}))
    out.append(("json_string", {"state": '{"documents": [{"text": "PAN ' + pan + '"}]}'}, {"pan"}))
    out.append(("json_string", {"state": '{"bank": {"holder": "Applicant name: ' + name + '"}}'}, {"person_name"}))
    out.append(("percent_encoded", {"state": {"remarks": "phone=" + "".join(f"%{ord(ch):02X}" for ch in phone)}}, {"phone_in", "account_number"}))
    out.append(("html_entities", {"state": {"remarks": "acct " + "".join(f"&#{ord(ch)};" for ch in acct)}}, {"account_number"}))
    out.append(("base64_value", {"state": {"remarks": base64.b64encode(f"PAN {pan} verified".encode()).decode()}}, {"pan"}))
    out.append(("base64_value", {"state": {"remarks": base64.b64encode(f"call {phone} now".encode()).decode()}}, {"phone_in", "account_number"}))
    out.append(("hex_value", {"state": {"remarks": phone.encode().hex()}}, {"phone_in", "account_number"}))
    out.append(("cyrillic_lookalike", {"state": {"remarks": "PAN " + pan.replace("A", "А").replace("E", "Е").replace("P", "Р", 1)}}, {"pan"}))
    out.append(("mixed_multi_pii", {"state": _state(rng, [_doc(rng, f"Name: {name}. PAN {pan}. Mobile {phone}. A/c {acct}.")])}, {"person_name", "pan", "phone_in", "account_number"}))
    return out


def _url_like(rng: random.Random) -> list[tuple[str, Any, set[str]]]:
    phone, pan = _phone(rng), _pan(rng)
    return [
        ("in_url_string", {"state": {"link": f"https://example.org/verify?pan={pan}&src=app"}}, {"pan"}),
        ("in_url_string", {"state": {"link": f"https://example.org/u/{phone}/profile"}}, {"phone_in", "account_number"}),
    ]


# --------------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_adversarial() -> tuple[Fixture, ...]:
    rng = random.Random(SEED)
    fixtures: list[Fixture] = []
    for i, (cat, text, expected) in enumerate(_cases(rng)):
        payload = _host(rng, i, text)
        fixtures.append(Fixture(f"ADV-{len(fixtures) + 1:03d}", cat, payload, set(expected)))
    for cat, payload, expected in _structural(rng) + _url_like(rng):
        fixtures.append(Fixture(f"ADV-{len(fixtures) + 1:03d}", cat, payload, set(expected)))
    return tuple(fixtures)


def adversarial_fixtures() -> list[Fixture]:
    """At least 150 payloads, each hiding one identifier. Every one must be caught by `PIIGate`."""
    import copy

    return [Fixture(f.id, f.category, copy.deepcopy(f.payload), set(f.expected_detectors), f.note) for f in _build_adversarial()]


CATEGORIES = tuple(dict.fromkeys(f.category for f in _build_adversarial()))


def known_gap_fixtures() -> list[Fixture]:
    """Obfuscations the gate does not catch and cannot reasonably catch without unacceptable false positives.
    They are documented here and reported by `jevloan pii fixtures-check`; they are not in the catch rate."""
    rng = random.Random(SEED + 1)
    phone, pan, aad = _phone(rng), _pan(rng), _digits(rng, 12, "2-9")
    gaps = [
        ("digits_split_across_list_items", {"state": {"parts": [phone[:5], phone[5:]]}},
         "digits split across separate JSON values: joining neighbouring scalars would flag ordinary lists of bands and counts"),
        ("digits_split_across_fields", {"state": {"a": {"prefix": phone[:5]}, "b": {"suffix": phone[5:]}}},
         "an identifier split across two fields is only reconstructible by the receiver"),
        ("single_digits_as_list", {"state": {"digits": list(phone)}},
         "a list of single-digit values; twelve monthly flags or counts look identical"),
        ("reversed_pan", {"state": {"remarks": f"reference {pan[::-1]}"}},
         "a reversed or otherwise permuted identifier needs the receiver to know the trick"),
        ("transliterated_hindi_digit_words", {"state": {"remarks": "phone nau aath saat chhah paanch chaar teen do ek shunya"}},
         "spelled digits in Hindi words: only English digit words are converted"),
        ("devanagari_digit_words", {"state": {"remarks": "फोन नौ आठ सात छह पांच चार तीन दो एक शून्य"}},
         "spelled digits in Devanagari words: only English digit words are converted"),
        ("unknown_name_without_marker", {"state": {"documents": [{"doc_type": "employer_letter", "text": "Verified with Zoravar Bhatnagar in accounts."}]}},
         "a name that is in neither lexicon, with no honorific, label or relation marker, is indistinguishable from a product or place name; known names are handled by the Redactor"),
        ("unknown_native_name_without_marker", {"state": {"documents": [{"doc_type": "employer_letter", "text": "கணக்காளர் சிவகுமார் சரிபார்த்தார்"}]}},
         "a native-script name outside the (small) Tamil/Bengali/Telugu lexicons without a marker"),
        ("leetspeak_pan", {"state": {"remarks": "reference " + pan.replace("B", "8").replace("O", "0").replace("I", "1").replace("S", "5").replace("A", "4", 1)}},
         "letters swapped for look-alike digits break the letter/digit shape; only the letter O and l/I between digits are undone"),
        ("aadhaar_as_words_in_sentence", {"state": {"remarks": f"the number begins {aad[:6]} and ends {aad[6:]} exactly"}},
         "an identifier interrupted by prose words is not a digit run"),
        ("pan_letters_and_digits_far_apart", {"state": {"remarks": f"letters {pan[:5]} then, much later, digits {pan[5:9]} and {pan[9]}"}},
         "the parts of a PAN separated by prose"),
    ]
    return [Fixture(f"GAP-{i + 1:02d}", cat, payload, set(), note) for i, (cat, payload, note) in enumerate(gaps)]


# --------------------------------------------------------------------------------------------------
# Clean samples
# --------------------------------------------------------------------------------------------------

_MONTHLY = ["<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L"]
_AMOUNT = ["<1L", "1-3L", "3-5L", "5-10L", "10-25L", "25-50L", "50L-1Cr", "1-2Cr", "2-5Cr", ">5Cr"]
_SCORE = ["NTC", "<600", "600-649", "650-699", "700-749", "750-799", "800+"]
_FOIR = ["<30", "30-40", "40-50", "50-55", "55-60", "60-70", ">70"]
_DPD = ["0", "1-29", "30-59", "60-89", "90+"]
_TOKENS = ["[APPLICANT]", "[PERSON_2]", "[ORG_A]", "[ORG_B]", "[PAN_1]", "[PAN_2]", "[UID_1]", "[PHONE_1]", "[EMAIL_1]", "[ACCT_1]", "[ADDR_1]", "[ADDR_2]", "[PIN]"]

_CLEAN_DOC_TEMPLATES = [
    "Salary slip for {mon} 2026. Employee: [APPLICANT]. Employer: [ORG_A]. Net pay ₹[{m}]. PAN: [PAN_1].",
    "Bank statement header. Account holder: [APPLICANT]. Account: [ACCT_1]. Statement period {mon} 2025 to {mon2} 2026. Average balance ₹[{m}].",
    "Electricity bill for {mon} 2026. Consumer: [APPLICANT]. Address: [ADDR_1]. Amount due ₹[{m}]. Contact [PHONE_1].",
    "Rent agreement executed {mon} 2025 between [PERSON_2] (landlord) and [APPLICANT] (tenant) for the premises at [ADDR_1]. Monthly rent ₹[{m}].",
    "Employer letter. This is to certify that [APPLICANT] has been employed with [ORG_A] since {mon} 2021 as a permanent employee. Gross salary ₹[{m}] per month.",
    "PAN card text: Name [APPLICANT]. Father's name [PERSON_2]. Permanent Account Number [PAN_1]. Date of birth {mon} 1988.",
    "GST return summary for {mon} 2026. Legal name [ORG_A]. GSTIN 29[PAN_1]1Z5. Taxable turnover ₹[{m}]. Filed on time.",
    "Business registration certificate issued to [ORG_A], proprietor [APPLICANT], registered {mon} 2019. Registered address [ADDR_1].",
    "Property title deed: owner [APPLICANT], survey extract verified, encumbrance nil since {mon} 2015. Location [ADDR_1]. Market value ₹[{a}].",
    "Valuation report dated {mon} 2026 for the property owned by [APPLICANT]. Market value ₹[{a}]. Distress value ₹[{a2}]. Valuer: [ORG_B].",
    "Form 16 for FY 2025-26. Employer [ORG_A]. Employee [APPLICANT], PAN [PAN_1]. Gross salary ₹[{m}]. Tax deducted ₹[{m2}].",
    "Address proof, passport copy. Holder [APPLICANT]. Address [ADDR_1]. Passport number [ID_1]. Issued {mon} 2020.",
    "ITR acknowledgement. Assessee [APPLICANT], PAN [PAN_1]. Total income ₹[{a}] for AY 2025-26. Filed {mon} 2025.",
    "Bank narration sample: SALARY CREDIT [ORG_A] {mon} 2026 ₹[{m}]; EMI DEBIT ₹[{m2}]; UPI [EMAIL_1].",
]
_HINDI = [
    "यह दस्तावेज़ वेतन पर्ची का सारांश है। कर्मचारी: [APPLICANT]। नियोक्ता: [ORG_A]। शुद्ध वेतन ₹[50-75k]।",
    "बैंक विवरण का शीर्षक। खाताधारक: [APPLICANT]। खाता संख्या: [ACCT_1]। अवधि मार्च 2026।",
    "बिजली का बिल। उपभोक्ता: [APPLICANT]। पता: [ADDR_1]। देय राशि ₹[10-25k]।",
    "किराया अनुबंध। मकान मालिक [PERSON_2] और किरायेदार [APPLICANT] के बीच। मासिक किराया ₹[10-25k]।",
    "यह प्रमाणित किया जाता है कि कर्मचारी स्थायी है और उसका वेतन नियमित रूप से खाते में जमा होता है।",
    "आवेदन के सभी दस्तावेज़ जांच लिए गए हैं और वेतन पर्ची तीन महीने से पुरानी नहीं है।",
]
_TAMIL = [
    "இது சம்பள சீட்டின் சுருக்கம். ஊழியர்: [APPLICANT]. நிறுவனம்: [ORG_A]. நிகர சம்பளம் ₹[50-75k].",
    "வங்கி அறிக்கை தலைப்பு. கணக்கு வைத்திருப்பவர்: [APPLICANT]. கணக்கு எண்: [ACCT_1]. காலம் மார்ச் 2026.",
    "மின் கட்டணம். நுகர்வோர்: [APPLICANT]. முகவரி: [ADDR_1]. செலுத்த வேண்டிய தொகை ₹[10-25k].",
    "இந்த ஆவணம் சரிபார்க்கப்பட்டது மற்றும் சம்பளம் மாதந்தோறும் வங்கிக் கணக்கில் வரவு வைக்கப்படுகிறது.",
]
_BENGALI = [
    "এটি বেতন স্লিপের সারাংশ। কর্মচারী: [APPLICANT]। নিয়োগকর্তা: [ORG_A]। নিট বেতন ₹[50-75k]।",
    "ব্যাংক বিবরণীর শিরোনাম। অ্যাকাউন্ট ধারক: [APPLICANT]। অ্যাকাউন্ট নম্বর: [ACCT_1]।",
]
_RUBRIC_PHRASES = [
    ("Income proof dated within the last 2 months", ["salary slip for the latest month", "bank statement showing salary credit this month"],
     ["salary slip dated 5 months ago", "an unsigned offer letter"]),
    ("Address proof is valid and in the applicant's own name", ["utility bill of the last 3 months", "registered rent agreement"],
     ["bill in a relative's name", "expired passport"]),
    ("Statements cover the required number of months", ["6 of 6 months present", "months contiguous with no gap"],
     ["only 3 of 6 months present", "a gap in the middle of the period"]),
    ("Application fields are coherent with each other", ["declared income close to verified income", "employer in form matches slip"],
     ["declared income far above verified income", "city tier does not match address"]),
    ("Salary credits match the employer pattern in the documents", ["same employer token on slip and narration", "credit on a fixed day each month"],
     ["different employer token in narration", "credits from many small senders"]),
    ("Late payment charges, grace period and processing fee are disclosed", ["fee schedule lists each charge", "grace period stated in days"],
     ["fees mentioned without amounts", "no cooling-off period stated"]),
    ("Gold loan and secured loan margins follow the grid", ["LTV within the grid row", "margin equals the product limit"],
     ["LTV above the limit", "tenure above the maximum"]),
    ("Bank of India statement and State Bank of India statement are both accepted", ["Bank of Baroda statement", "Punjab National Bank passbook"],
     ["screenshot of a mobile app", "handwritten ledger"]),
]


def _rubric(rng: random.Random) -> dict[str, Any]:
    q, yes, no = rng.choice(_RUBRIC_PHRASES)
    return {
        rng.choice(_QIDS): {
            "type": "noul",
            "instructions": {"question": q + "?", "focus": rng.choice(["the most recent document", "all documents", "the bank block"]),
                             "refer_to": rng.sample(["`documents`", "`bank.months_covered`", "`income.documentation_type`", "`identity_signals`"], 2)},
            "criteria": {"true": {"what": q, "examples": yes}, "false": {"what": "The opposite of the above", "not_for": "minor formatting issues", "examples": no}},
        }
    }


def _clean_state(rng: random.Random, segment: str, docs: list[str]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": "jevloan.state.v1", "stage": "appraisal", "segment": segment,
        "application": {"product": {"salaried_personal": "personal_loan_unsecured", "self_employed": "business_loan_self_employed",
                                    "msme_business": "msme_term_loan", "secured_home": "home_loan"}[segment],
                        "loan_amount_band": rng.choice(_AMOUNT), "tenure_months": rng.choice([12, 24, 36, 60, 120, 180]),
                        "purpose": rng.choice(["debt_consolidation", "working_capital", "home_purchase", "equipment"]),
                        "employment_type": "salaried" if segment == "salaried_personal" else "self_employed",
                        "years_in_job_or_business_band": rng.choice(["<1y", "1-3y", "3-7y", ">7y"]),
                        "declared_monthly_income_band": rng.choice(_MONTHLY), "applicant_age_band": rng.choice(["21-30", "30-40", "40-50", "50-60"]),
                        "city_tier": rng.choice([1, 2, 3])},
        "bureau": {"score_band": rng.choice(_SCORE), "active_loans": rng.randint(0, 5), "max_dpd_12m_band": rng.choice(_DPD),
                   "enquiries_6m": rng.randint(0, 8), "writeoffs_or_settlements": rng.choice([0, 0, 0, 1]), "history_length_band": rng.choice(["<1y", "1-3y", "3-7y", ">7y"])},
        "income": {"verified_monthly_income_band": rng.choice(_MONTHLY), "volatility": rng.choice(["low", "moderate", "high"]),
                   "months_history": rng.randint(3, 36), "documentation_type": rng.choice(["salary_slip", "itr", "gst_and_bank", "informal_declared"])},
        "obligations": {"existing_emi_band": rng.choice(_MONTHLY), "proposed_emi_band": rng.choice(_MONTHLY), "foir_pct_band": rng.choice(_FOIR),
                        "segment_foir_limit_pct": rng.choice([50, 55, 60]), "credit_card_utilization_band": rng.choice(["<30", "30-50", "50-75", ">75"])},
        "bank": {"months_covered": rng.randint(3, 12), "months_required": 6, "most_recent_month_age": rng.randint(0, 3),
                 "salary_credits_months": rng.randint(0, 12), "salary_narration_org_token": rng.choice(["[ORG_A]", "[ORG_B]"]),
                 "avg_monthly_credits_band": rng.choice(_MONTHLY), "emi_bounces_6m": rng.randint(0, 3), "cash_deposit_share_band": rng.choice(["<10", "10-30", ">30"]),
                 "min_balance_breaches_6m": rng.randint(0, 3)},
        "identity_signals": {"pan_aadhaar_linked": rng.choice([True, False]), "phone_vintage_band": rng.choice(["<3m", "3-12m", "1-3y", ">3y"]),
                             "email_domain_type": rng.choice(["corporate", "free", "disposable"]), "address_shared_with_other_apps_band": rng.choice(["0", "1-2", "3+"]),
                             "bureau_history_consistent_with_age": rng.choice([True, False])},
        "documents": [{"doc_type": rng.choice(_DOC_TYPES), "month_age": rng.randint(0, 6), "script": "latin", "text": t} for t in docs],
    }
    if segment in ("self_employed", "msme_business"):
        state["gst"] = {"filings_on_time_12m": rng.randint(6, 12), "months_filed": rng.randint(6, 12), "gst_turnover_band_12m": rng.choice(_AMOUNT),
                        "bank_credits_band_12m": rng.choice(_AMOUNT), "gst_to_bank_ratio_band": rng.choice(["<0.5", "0.5-0.8", "0.8-1.2", "1.2-2", ">2"])}
        state["business"] = {"dscr_band": rng.choice(["<1", "1-1.25", "1.25-1.5", "1.5-2", ">2"]), "vintage_years_band": rng.choice(["<2", "2-5", "5-10", ">10"])}
    if segment == "secured_home":
        state["property"] = {"property_type": rng.choice(["apartment", "independent_house", "plot"]), "market_value_band": rng.choice(_AMOUNT),
                             "ltv_pct_band": rng.choice(["<60", "60-70", "70-75", "75-80", "80-85", ">85"]), "ltv_limit_pct": 80,
                             "title_status": rng.choice(["clear", "disputed", "pending_mutation"]), "legal_opinion": rng.choice(["positive", "adverse", "pending"]),
                             "valuation_spread_band": rng.choice(["<5%", "5-10%", "10-20%", ">20%"])}
    return state


def _clean_doc(rng: random.Random) -> str:
    mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return rng.choice(_CLEAN_DOC_TEMPLATES).format(
        mon=rng.choice(mons), mon2=rng.choice(mons), m=rng.choice(_MONTHLY), m2=rng.choice(_MONTHLY), a=rng.choice(_AMOUNT), a2=rng.choice(_AMOUNT))


_HANDWRITTEN_CLEAN: list[dict[str, Any]] = [
    {"state": {"documents": [{"doc_type": "salary_slip", "month_age": 1, "script": "latin",
                              "text": "Salary slip for Mar 2026. Employee: [APPLICANT]. Employer: [ORG_A]. Net pay ₹[50-75k]. PAN: [PAN_1]."}]}},
    {"questions": {"A_income_proof_current": {"type": "noul", "instructions": {"question": "Is the income proof dated within the last 2 months?", "refer_to": ["`documents`"]},
                                                "criteria": {"true": {"what": "Income proof dated within the last 2 months", "examples": ["salary slip for the latest month", "bank statement with a salary credit this month"]},
                                                             "false": {"what": "Income proof older than 2 months or missing", "not_for": "a slip that only lacks a stamp", "examples": ["slip from 5 months ago", "no income document"]}}}}},
    {"what": "Income proof dated within the last 2 months", "examples": ["salary slip for Mar 2026", "bank statement showing this month's salary credit"]},
    {"state": {"segment": "secured_home", "property": {"title_status": "clear", "legal_opinion": "positive", "ltv_pct_band": "70-75", "valuation_spread_band": "5-10%"}}},
    {"state": {"bureau": {"score_band": "750-799", "max_dpd_12m_band": "0", "history_length_band": "3-7y"}, "obligations": {"foir_pct_band": "30-40"}}},
    {"state": {"loan": {"product": "msme_term_loan", "loan_amount_band": "50L-1Cr", "tenure_months": 60, "months_since_disbursal": 9},
               "repayment": [{"m": 1, "dpd_band": "0", "emi_bounced": False, "partial_payment": False, "avg_balance_band": "1-2L"},
                             {"m": 2, "dpd_band": "1-29", "emi_bounced": True, "partial_payment": False, "avg_balance_band": "<10k"}],
               "covenants": [{"covenant_id": "dscr_min_1_25", "required": True, "reported_value_band": "1-1.25", "evidence_text": "DSCR for the quarter is below the covenant. Reported by [ORG_A]."}]}},
    {"state": {"sanction_memo": {"text": "Sanction advised for [APPLICANT], ticket 5-10L, tenure 60 months, guarantor required above 10L.", "product": "personal_loan_unsecured", "ticket_band": "5-10L", "tenure_months": 60, "conditions": ["guarantor", "salary account with the bank"]},
               "kfs": {"text": "Key Fact Statement. APR 14.20 percent. Fees and charges as per schedule. Cooling-off period 3 days. Grievance redressal officer: [PERSON_2].", "apr_stated_pct": 14.2, "apr_recomputed_pct": 14.35, "rate_pct": 12.75, "tenure_months": 60}}},
    {"state": {"remarks": "Grace period of 5 days applies. Gold loan margin as per grid. Late payment charges are disclosed."}},
    {"state": {"remarks": "Bank statement covers Oct 2025 to Mar 2026. Form 26AS reviewed. GSTR-3B filed for Mar 2026. Schedule III not applicable."}},
    {"state": {"remarks": "State Bank of India, Bank of Baroda and Punjab National Bank statements are accepted. Bengaluru and Chennai branches are in tier 1."}},
    {"state": {"remarks": "Applicant has 6-12 months of history, 3 of 5 documents verified, FOIR 40-50, score 750-799, LTV 60-70, DSCR 1.25-1.5."}},
    {"state": {"remarks": "Property near Gandhi Road, Patel Nagar, Anand Vihar and Nehru Place area; ward office at Shivaji Chowk."}},
    {"state": {"outcome": "PROCEED_TO_SANCTIONING_AUTHORITY", "queue": None, "reason_codes": ["ACCEPT_AS_ADVISED"], "policy": "policy-2026.09-v1", "model": "sim-jev-0.1"}},
    {"state": {"remarks": "Score 720 and a limit of 500000 in the bureau extract; 12 EMIs paid on time; 3 enquiries in 6 months."}},
    {"state": {"documents": [{"doc_type": "employer_letter", "month_age": 0, "script": "latin",
                              "text": "Employer letter on the letterhead of [ORG_A] confirms that [APPLICANT] is a confirmed employee since Jun 2021, designation Senior Executive."}]}},
    {"state": {"remarks": "Salary slip Salary Slip Bank Statement Utility Bill Rent Agreement Property Title Deed Valuation Report Employer Letter Form 16 Business Registration."}},
    {"state": {"documents": [{"doc_type": "address_proof_utility_bill", "month_age": 2, "script": "devanagari", "text": "बिजली का बिल। उपभोक्ता: [APPLICANT]। पता: [ADDR_1]। देय राशि ₹[10-25k]। भुगतान की अंतिम तिथि मार्च 2026।"}]}},
    {"state": {"documents": [{"doc_type": "salary_slip", "month_age": 1, "script": "tamil", "text": "இது சம்பள சீட்டின் சுருக்கம். ஊழியர்: [APPLICANT]. நிறுவனம்: [ORG_A]. நிகர சம்பளம் ₹[50-75k]."}]}},
    {"state": {"documents": [{"doc_type": "address_proof_utility_bill", "month_age": 1, "script": "bengali", "text": "এটি বিদ্যুৎ বিলের সারাংশ। গ্রাহক: [APPLICANT]। ঠিকানা: [ADDR_1]।"}]}},
    {"questions": {"E_memo_matches_grid": {"type": "noul", "instructions": {"question": "Do the memo conditions match the grid row?", "grid_row": {"product": "home_loan", "ticket_band": "50L-1Cr", "max_tenure_months": 240, "max_ltv_pct": 75,
                                                                                                                                                  "required_conditions": ["property insurance", "equitable mortgage"]}},
                                                "criteria": {"true": "conditions match", "false": "conditions differ"}}}},
    {"questions": {"C_willingness": {"type": "score", "instructions": "Rate conduct on existing obligations, from `bureau`.",
                                     "criteria": [{"level": "serious delinquency", "what": "90+ days past due or a write-off", "not_for": "a single 1-29 day slip", "examples": ["max_dpd_12m_band 90+", "writeoffs_or_settlements 1"]},
                                                  {"level": "spotless", "what": "no delinquency in 12 months", "not_for": "one bounce", "examples": ["max_dpd_12m_band 0", "emi_bounces_6m 0"]}]}}},
    {"questions": {"D_closeness": {"type": "score", "instructions": "How close is the file to the sanction boundary?", "criteria": ["clearly not sanctionable", "far", "borderline", "close", "clearly sanctionable"]}}},
    {"questions": {"B_identity_coheres": {"type": "noul", "instructions": {"question": "Do `identity_signals` cohere with `application`?", "refer_to": ["`identity_signals`", "`application`"]},
                                          "criteria": {"true": {"what": "PAN linked to Aadhaar, phone older than 3 months, corporate or free email", "examples": ["pan_aadhaar_linked true", "phone_vintage_band 1-3y"]},
                                                       "false": {"what": "unlinked PAN, new phone, disposable email", "not_for": "a free email domain alone", "examples": ["pan_aadhaar_linked false", "email_domain_type disposable"]}}}}},
    {"state": {"remarks": "Threshold 0.50 and 0.70, weights 0.30 0.30 0.15 0.10 0.15, tolerance 0.10 pp, version 2026.09"}},
    {"state": {"remarks": "Filed on 12 Mar; next review in Q3 FY26; page 3 of 7; clause 4.2.1; annexure B."}},
]


@lru_cache(maxsize=1)
def _build_clean() -> tuple[Any, ...]:
    rng = random.Random(SEED + 2)
    out: list[Any] = [x for x in _HANDWRITTEN_CLEAN]
    segments = ["salaried_personal", "self_employed", "msme_business", "secured_home"]
    for i in range(40):  # generated appraisal states with redacted documents
        n_docs = rng.randint(1, 5)
        docs = [_clean_doc(rng) for _ in range(n_docs)]
        if i % 5 == 0:
            docs.append(rng.choice(_HINDI))
        if i % 7 == 0:
            docs.append(rng.choice(_TAMIL))
        if i % 11 == 0:
            docs.append(rng.choice(_BENGALI))
        out.append({"state": _clean_state(rng, segments[i % 4], docs), "questions": _rubric(rng)})
    for _ in range(20):  # question payloads
        out.append({"questions": {**_rubric(rng), **_rubric(rng)}})
    for _ in range(12):  # single documents
        out.append({"documents": [{"doc_type": rng.choice(_DOC_TYPES), "month_age": rng.randint(0, 6), "script": "latin", "text": _clean_doc(rng)}]})
    for text in _HINDI + _TAMIL + _BENGALI:
        out.append({"documents": [{"doc_type": "salary_slip", "month_age": 1, "script": "devanagari", "text": text}]})
    for _ in range(12):  # token-heavy fragments
        toks = rng.sample(_TOKENS, 4)
        out.append({"state": {"remarks": f"{toks[0]} linked to {toks[1]}; {toks[2]} appears with {toks[3]}; balance ₹[{rng.choice(_MONTHLY)}]; ratio {rng.choice(_FOIR)}."}})
    return tuple(out)


def clean_samples() -> list[dict[str, Any]]:
    """At least 100 realistic, already-redacted states and question payloads that must not be blocked."""
    import copy

    return [copy.deepcopy(x) for x in _build_clean()]
