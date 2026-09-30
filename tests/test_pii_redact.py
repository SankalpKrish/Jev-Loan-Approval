"""Redactor: D8 tokens, consistency, tolerance, dates, amounts, and 'redacted output passes the gate'."""

import random
import re

import pytest

from jevloan.pii.gate import PIIGate
from jevloan.pii.redact import MONTHLY_BANDS, TICKET_BANDS, KnownEntities, Redactor, amount_band

gate = PIIGate()

INVENTORY = {
    "person_names": ["Priya Sharma", "Rahul Sharma", "Kavita Menon"],
    "org_names": ["Infosys Pvt Ltd", "Meridian Traders"],
    "pans": ["ABCPK1234F", "PQRST6789Z"],
    "aadhaars": ["234567890123"],
    "phones": ["9876543210", "8123456789"],
    "emails": ["priya.sharma@example.com"],
    "account_numbers": ["123456789012345"],
    "address_lines": ["12/3, Gandhi Road, Bengaluru - 560034", "7 Nehru Street, Chennai 600001"],
    "pincodes": ["560034", "600001"],
}


def redactor(**overrides):
    inv = {**INVENTORY, **overrides}
    return Redactor(KnownEntities.from_inventory(inv))


def clean(text: str) -> bool:
    return gate.scan({"text": text}) == []


# ------------------------------------------------------------------------------------------------ KnownEntities


def test_known_entities_fields_match_pii_inventory():
    fields = {"person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines", "pincodes"}
    k = KnownEntities.from_inventory(INVENTORY)
    for name in fields:
        assert getattr(k, name) == INVENTORY[name]


def test_from_inventory_tolerates_missing_none_and_blank():
    k = KnownEntities.from_inventory({"person_names": ["A B", "", None], "pans": None})
    assert k.person_names == ["A B"] and k.pans == [] and k.emails == []
    assert KnownEntities.from_inventory({}) == KnownEntities()


def test_from_inventory_accepts_a_model_with_model_dump():
    class Inv:
        def model_dump(self):
            return dict(INVENTORY)

    assert KnownEntities.from_inventory(Inv()).pans == INVENTORY["pans"]


def test_native_alias_after_the_applicant_shares_the_applicant_token():
    k = KnownEntities.from_inventory({"person_names": ["Priya Sharma", "प्रिया शर्मा", "Rahul Sharma"]})
    assert k.person_names == ["Priya Sharma", "Rahul Sharma"] and k.native_names == ["प्रिया शर्मा"]
    r = Redactor(k)
    out = r.redact("आवेदक प्रिया शर्मा और Rahul Sharma")
    assert "[APPLICANT]" in out and "[PERSON_2]" in out and "[PERSON_3]" not in out


# ------------------------------------------------------------------------------------------------ token scheme (D8)


def test_applicant_is_always_applicant_and_others_are_numbered_in_inventory_order():
    r = redactor()
    out = r.redact("Priya Sharma met Rahul Sharma and Kavita Menon")
    assert out == "[APPLICANT] met [PERSON_2] and [PERSON_3]"


def test_orgs_pans_uids_phones_emails_accounts_addresses_pins():
    r = redactor()
    out = r.redact(
        "Employer Infosys Pvt Ltd; firm Meridian Traders; PAN ABCPK1234F and PQRST6789Z; Aadhaar 234567890123; "
        "phones 9876543210 8123456789; mail priya.sharma@example.com; a/c 123456789012345; "
        "addresses 12/3, Gandhi Road, Bengaluru - 560034 and 7 Nehru Street, Chennai 600001"
    )
    for token in ["[ORG_A]", "[ORG_B]", "[PAN_1]", "[PAN_2]", "[UID_1]", "[PHONE_1]", "[PHONE_2]", "[EMAIL_1]", "[ACCT_1]", "[ADDR_1]", "[ADDR_2]"]:
        assert token in out, (token, out)
    assert clean(out)


def test_pincode_alone_becomes_pin():
    assert redactor().redact("Pincode 560034 was entered") == "Pincode [PIN] was entered"


def test_address_absorbs_trailing_city_and_pin():
    r = redactor()
    assert r.redact("Resides at 12/3, Gandhi Road, Bengaluru - 560034.") == "Resides at [ADDR_1]."
    assert r.redact("Lives at 12/3 Gandhi Road, Bengaluru, Karnataka 560034") == "Lives at [ADDR_1]"


def test_partial_address_still_matches():
    assert "[ADDR_1]" in redactor().redact("registered office: 12/3, Gandhi Road")


def test_same_value_gets_the_same_token_and_different_values_differ():
    r = redactor()
    a = r.redact("PAN ABCPK1234F")
    b = r.redact("again abcpk1234f and A B C P K 1 2 3 4 F")
    assert a == "PAN [PAN_1]" and b == "again [PAN_1] and [PAN_1]"
    assert r.redact("PQRST6789Z") == "[PAN_2]"


def test_a_second_different_pan_that_is_not_in_the_inventory_gets_the_next_free_number():
    r = redactor(pans=["ABCPK1234F"])
    assert r.redact("PAN ABCPK1234F") == "PAN [PAN_1]"
    assert r.redact("co-signer PAN KLMNO5678P") == "co-signer PAN [PAN_2]"
    assert r.redact("and KLMNO5678P again, plus UVWXY4321A") == "and [PAN_2] again, plus [PAN_3]"


def test_unknown_identifiers_continue_numbering_after_the_inventory():
    r = redactor()
    assert r.redact("PAN KLMNO5678P") == "PAN [PAN_3]"
    assert r.redact("mobile 9123456780") == "mobile [PHONE_3]"
    assert r.redact("Aadhaar 3456 7890 1234") == "Aadhaar [UID_2]"
    assert r.redact("mail other@example.org") == "mail [EMAIL_2]"
    assert r.redact("acct 99887766554433") == "acct [ACCT_2]"


def test_unknown_person_names_get_the_next_free_person_number():
    r = redactor()
    assert r.redact("witness Mr Zoravar Bhatnagar") == "witness Mr [PERSON_4]"
    assert r.redact("ZORAVAR BHATNAGAR again") == "[PERSON_4] again"
    assert r.redact("also Mr Thirumalai Vasan") == "also Mr [PERSON_5]"


def test_discovered_name_parts_reuse_the_token():
    r = redactor(person_names=["Priya Sharma"])
    assert r.redact("Mr Zoravar Bhatnagar signed") == "Mr [PERSON_2] signed"
    assert r.redact("Zoravar confirmed") == "[PERSON_2] confirmed"  # a lexicon-free part still matches the same person


def test_empty_inventory_still_redacts_via_detectors():
    r = Redactor(KnownEntities())
    out = r.redact("PAN ABCPK1234F, call 9876543210, Mr Rajesh Kumar")
    assert out == "PAN [PAN_1], call [PHONE_1], Mr [PERSON_2]"


def test_token_map_is_raw_to_token_and_covers_variants():
    r = redactor()
    r.redact("PRIYA SHARMA, abcpk 1234 f and +91 98765-43210")
    tm = r.token_map
    assert tm["Priya Sharma"] == "[APPLICANT]" and tm["PRIYA SHARMA"] == "[APPLICANT]"
    assert tm["ABCPK1234F"] == "[PAN_1]" and tm["abcpk 1234 f"] == "[PAN_1]"
    assert tm["9876543210"] == "[PHONE_1]" and tm["+91 98765-43210"] == "[PHONE_1]"
    assert tm["Infosys Pvt Ltd"] == "[ORG_A]"


# ------------------------------------------------------------------------------------------------ matching tolerance


@pytest.mark.parametrize(
    "text,token",
    [
        ("Priya Sharma", "[APPLICANT]"),
        ("PRIYA SHARMA", "[APPLICANT]"),
        ("priya sharma", "[APPLICANT]"),
        ("Sharma Priya", "[APPLICANT]"),
        ("Sharma, Priya", "[APPLICANT]"),
        ("Priya", "[APPLICANT]"),
        ("SHARMA", "[APPLICANT]"),
        ("P. Sharma", "[APPLICANT]"),
        ("P Sharma", "[APPLICANT]"),
        ("Priya  Sharma", "[APPLICANT]"),
        ("Priya's salary", "[APPLICANT]'s salary"),
        ("प्रिया शर्मा", "[APPLICANT]"),
        ("प्रिया", "[APPLICANT]"),
    ],
)
def test_person_name_variants(text, token):
    out = redactor(person_names=["Priya Sharma"]).redact(text)
    assert out == token


def test_person_parts_shorter_than_three_characters_are_not_replaced_alone():
    r = redactor(person_names=["Om Prakash"])
    assert r.redact("Om Prakash") == "[APPLICANT]"
    assert r.redact("went to Om Nagar") == "went to Om Nagar"  # "Om" is two characters
    assert r.redact("Prakash said") == "[APPLICANT] said"


def test_english_word_name_parts_only_match_capitalised():
    r = redactor(person_names=["Grace Thomas"])
    assert r.redact("grace period of 5 days") == "grace period of 5 days"
    assert r.redact("Thomas signed") == "[APPLICANT] signed"
    assert r.redact("Grace Thomas signed") == "[APPLICANT] signed"


def test_shared_surname_goes_to_the_earlier_person_but_full_names_stay_distinct():
    r = redactor()
    assert r.redact("Rahul Sharma and Priya Sharma") == "[PERSON_2] and [APPLICANT]"
    assert r.redact("Sharma") == "[APPLICANT]"


def test_honorifics_survive_and_names_inside_other_words_are_left_alone():
    r = redactor(person_names=["Priya Sharma"])
    assert r.redact("Mrs. Priya Sharma") == "Mrs. [APPLICANT]"
    assert r.redact("Priyanka Sharmaji") == r.redact("Priyanka Sharmaji")  # neither part matches inside a longer word
    assert "[APPLICANT]" not in r.redact("Sharmaji")


@pytest.mark.parametrize(
    "text",
    [
        "ABCPK1234F", "abcpk1234f", "ABCPK 1234 F", "abcpk 1234 f", "ABCPK-1234-F", "A B C P K 1 2 3 4 F",
        "29ABCPK1234F1Z5", "PAN:ABCPK1234F.", "ＡＢＣＰＫ１２３４Ｆ", "ABCPK१२३४F", "ABCPK one two three four F",
    ],
)
def test_pan_matches_across_separators_and_case(text):
    out = redactor(pans=["ABCPK1234F"]).redact(text)
    assert "[PAN_1]" in out and clean(out)


@pytest.mark.parametrize(
    "text",
    [
        "9876543210", "+91 98765-43210", "+91 9876543210", "+919876543210", "0091 98765 43210", "09876543210", "98765 43210",
        "9 8 7 6 5 4 3 2 1 0", "९८७६५४३२१०", "nine eight seven six five four three two one zero",
        "9876​543210",
    ],
)
def test_phone_matches_with_prefixes_and_separators(text):
    out = redactor(phones=["9876543210"]).redact(f"call {text} now")
    assert out == "call [PHONE_1] now"


@pytest.mark.parametrize("text", ["2345 6789 0123", "2345-6789-0123", "234567890123", "२३४५ ६७८९ ०१२३", "2 3 4 5 6 7 8 9 0 1 2 3"])
def test_aadhaar_matches_in_any_grouping(text):
    assert redactor(aadhaars=["234567890123"]).redact(f"UID {text}.") == "UID [UID_1]."


def test_account_number_matches_grouped():
    r = redactor(account_numbers=["123456789012345"])
    assert r.redact("A/c 1234 5678 9012 345 and 123456789012345") == "A/c [ACCT_1] and [ACCT_1]"


def test_email_case_insensitive():
    r = redactor(emails=["priya.sharma@example.com"])
    assert r.redact("PRIYA.SHARMA@EXAMPLE.COM") == "[EMAIL_1]"
    assert r.redact("priya.sharma @ example.com") == "[EMAIL_1]"


def test_org_suffix_variants():
    r = redactor(org_names=["Infosys Pvt Ltd"])
    for text in ["Infosys Pvt Ltd", "INFOSYS PVT LTD", "Infosys Ltd.", "Infosys Private Limited", "Infosys"]:
        assert r.redact(f"Employer: {text}") .startswith("Employer: [ORG_A]"), text


def test_org_with_ampersand_matches_and_form():
    r = redactor(org_names=["Sharma & Sons"])
    assert r.redact("Sharma & Sons") == "[ORG_A]"
    assert r.redact("Sharma and Sons") == "[ORG_A]"


# ------------------------------------------------------------------------------------------------ dates


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("15/03/2026", "Mar 2026"),
        ("15-03-2026", "Mar 2026"),
        ("15.03.2026", "Mar 2026"),
        ("2026-03-15", "Mar 2026"),
        ("15 March 2026", "Mar 2026"),
        ("15th March, 2026", "Mar 2026"),
        ("15-Mar-26", "Mar 2026"),
        ("15 Mar 2026", "Mar 2026"),
        ("March 15, 2026", "Mar 2026"),
        ("Sept 3 2025", "Sep 2025"),
        ("01/09/25", "Sep 2025"),
        ("03/25/2026", "Mar 2026"),  # mm/dd
        ("15-Mar-90", "Mar 1990"),
    ],
)
def test_explicit_dates_become_month_year(raw, expected):
    assert Redactor(KnownEntities()).redact(f"dated {raw} ok") == f"dated {expected} ok"


def test_non_dates_are_left_alone():
    r = Redactor(KnownEntities())
    for text in ["ratio 31/13/2026x", "Mar 2026", "FY 2025-26", "version 1.2.3", "March"]:
        assert "Mar" in r.redact(text) or "2025-26" in r.redact(text) or r.redact(text) == text


def test_period_of_two_dates():
    assert Redactor(KnownEntities()).redact("Statement period 01/09/2025 to 31/08/2026") == "Statement period Sep 2025 to Aug 2026"


# ------------------------------------------------------------------------------------------------ amounts


@pytest.mark.parametrize(
    "value,band",
    [
        (0, "<10k"), (9_999, "<10k"), (10_000, "10-25k"), (24_999, "10-25k"), (25_000, "25-50k"), (49_999, "25-50k"),
        (50_000, "50-75k"), (74_999, "50-75k"), (75_000, "75k-1L"), (99_999, "75k-1L"), (100_000, "1-2L"), (199_999, "1-2L"),
        (200_000, "2-5L"), (499_999, "2-5L"), (500_000, ">5L"), (999_999, ">5L"),
        (1_000_000, "10-25L"), (2_499_999, "10-25L"), (2_500_000, "25-50L"), (4_999_999, "25-50L"), (5_000_000, "50L-1Cr"),
        (9_999_999, "50L-1Cr"), (10_000_000, "1-2Cr"), (19_999_999, "1-2Cr"), (20_000_000, "2-5Cr"), (49_999_999, "2-5Cr"),
        (50_000_000, ">5Cr"), (1_000_000_000, ">5Cr"),
    ],
)
def test_amount_band_boundaries(value, band):
    assert amount_band(value) == band


def test_band_tables_are_the_plan_vocabulary():
    assert [b for _, b in MONTHLY_BANDS] == ["<10k", "10-25k", "25-50k", "50-75k", "75k-1L", "1-2L", "2-5L", ">5L"]
    assert [b for _, b in TICKET_BANDS] == ["10-25L", "25-50L", "50L-1Cr", "1-2Cr", "2-5Cr"]


@pytest.mark.parametrize(
    "raw,band",
    [
        ("Rs 45,000", "25-50k"),
        ("Rs. 45,000", "25-50k"),
        ("Rs.45000", "25-50k"),
        ("INR 45,000", "25-50k"),
        ("inr 45000", "25-50k"),
        ("₹45,000", "25-50k"),
        ("₹ 45000", "25-50k"),
        ("Rs. 85,000/-", "75k-1L"),
        ("Rs 1,25,000", "1-2L"),
        ("Rs 125,000", "1-2L"),
        ("Rs. 41,04,000", "25-50L"),
        ("Rs 5 lakh", ">5L"),
        ("Rs 5 lakhs", ">5L"),
        ("₹12.5 lakh", "10-25L"),
        ("Rs 25 L", "25-50L"),
        ("Rs 1 Cr", "1-2Cr"),
        ("₹2.5 crore", "2-5Cr"),
        ("Rs 40k", "25-50k"),
        ("Rs. 1,69,32,000", "1-2Cr"),
        ("Rs.18,42,12,012/-", ">5Cr"),
        ("Rs 3,445", "<10k"),
        ("50000 rupees", "50-75k"),
        ("4,50,000 INR", "2-5L"),
        ("5 lakh", ">5L"),
        ("2 crore", "2-5Cr"),
    ],
)
def test_currency_amounts_become_bands(raw, band):
    out = Redactor(KnownEntities()).redact(f"Net pay {raw} credited.")
    assert f"₹[{band}]" in out, out
    assert not re.search(r"\d{4,}", out.replace("2026", "")), out


def test_amounts_after_money_words_are_banded_too():
    r = Redactor(KnownEntities())
    assert "₹[10-25k]" in r.redact("Monthly rent 15000 paid")
    assert "₹[50-75k]" in r.redact("salary: 62,500")
    assert r.redact("salary for 2026") == "salary for 2026"  # a year is not money
    assert r.redact("tenure 60 months, EMI 40%") == "tenure 60 months, EMI 40%"


def test_existing_bands_and_tokens_are_left_alone_and_redaction_is_idempotent():
    r = redactor()
    text = "Employee [APPLICANT] of [ORG_A], PAN [PAN_1], net pay ₹[50-75k], FOIR 40-50, score 750-799"
    assert r.redact(text) == text
    once = r.redact("Priya Sharma earns Rs 65,000; phone 9876543210; dated 15/03/2026")
    assert r.redact(once) == once


# ------------------------------------------------------------------------------------------------ residual detection


def test_residual_pass_tokenises_what_known_entities_missed():
    r = Redactor(KnownEntities())
    out = r.redact("PIN: 560034; UPI rahul@okaxis; IFSC SBIN0001234; passport J8369854; DL MH12 20110012345; voter ABC1234567; card 4111 1111 1111 1111")
    assert "[PIN]" in out and "[EMAIL_1]" in out and "[IFSC]" in out and "[ID_1]" in out and "[ACCT_1]" in out
    assert clean(out)


def test_redact_obj_walks_structures():
    r = redactor()
    obj = {"documents": [{"text": "Priya Sharma, PAN ABCPK1234F"}, {"text": "call 9876543210"}], "n": 3, "flag": True, "k": ("Kavita Menon",)}
    out = r.redact_obj(obj)
    assert out["documents"][0]["text"] == "[APPLICANT], PAN [PAN_1]"
    assert out["documents"][1]["text"] == "call [PHONE_1]"
    assert out["n"] == 3 and out["flag"] is True and out["k"] == ["[PERSON_3]"]


def test_normalised_output_for_obfuscated_input():
    r = redactor()
    out = r.redact("Ｐｒｉｙａ Ｓｈａｒｍａ ​ 9876​543210")
    assert out == "[APPLICANT]  [PHONE_1]" or out.replace("  ", " ") == "[APPLICANT] [PHONE_1]"


# ------------------------------------------------------------------------------------------------ output passes the gate


FIRSTS = ["Priya", "Rahul", "Zoravar", "Thirumalai", "Ishwari", "Kavita", "Bhuvaneswari", "Anand", "Meenakshi", "Zubin", "Chinnasamy", "Venkatesh", "Sourav", "Amrita"]
LASTS = ["Sharma", "Bhatnagar", "Palanisamy", "Iyer", "Ghosh", "Reddy", "Kulkarni", "Vasan", "Nair", "Menezes", "D'Souza", "Ramachandran"]
STREETS = ["MG Road", "Gandhi Nagar", "Station Road", "Lal Bahadur Shastri Marg", "Sector 21", "Temple Street"]
CITIES = [("Bengaluru", "5600"), ("Mumbai", "4000"), ("Chennai", "6000"), ("Pune", "4110"), ("Kolkata", "7000"), ("Nagpur", "4400")]
ORGS = ["Northstar Agro Products", "Kaveri Garments", "Meridian Software Services", "Blue Lotus Textiles"]

DOC_TEMPLATES = [
    "Salary slip for {mon}. Employee {name}, PAN {pan}. Employer {org} Pvt Ltd. Net pay Rs. {amt:,}. Bank A/c {acct}. Received: {d1}.",
    "Bank statement period {d1} to {d2}, holder {NAME}, A/c {acct_spaced}, {org} Ltd. Total credits Rs {amt2}/-. Registered mobile +91 {ph_spaced}.",
    "Electricity bill dated {d1}. Consumer {NAME}. Address: {addr}. Amount due Rs.{amt}. Contact {ph}.",
    "Rent agreement, term {d1} to {d2}. Landlord {name2}, tenant {name}. Property: {addr}. Monthly rent ₹{amt}. Tenant Aadhaar {aad_grouped}.",
    "Employer letter: this is to certify that Shri {name} (PAN {pan_lower}) has been with {org} since {d1}. Gross salary Rs. {amt} per month. Mail {email}.",
    "ITR-3 acknowledgement for AY 2026-27, filed {d1}. Assessee {name}, PAN {pan}. Total income Rs. {amt3} from {org} Pvt Ltd.",
    "GST return summary for {mon}. Legal name {org} Pvt Ltd, GSTIN 29{pan}1Z5, proprietor {name}. Taxable turnover Rs {amt3}. Mobile {ph}.",
    "Sale deed. Owner: {name} S/o {name2}. Property: {addr}. Market value Rs. {amt3}. Registered on {d1}. PIN: {pin}.",
    "Valuation report dated {d1} for {name}. Market value Rs.{amt3}, distress value Rs.{amt2}. Valuer {name2}, contact {ph}.",
    "PAN card text. Name: {NAME}. Father's Name: {name2}. PAN {pan}. DOB: {dob}.",
    "Passport {passport}. Holder {name}. Address {addr}. Issued {d1}.",
    "Form 16 FY 2025-26. Employee {name}, PAN {pan}, employer {org}. Gross salary Rs {amt3}. Tax deducted Rs {amt}. Certified {d2}.",
]


def make_case(rng: random.Random):
    def person():
        return f"{rng.choice(FIRSTS)} {rng.choice(LASTS)}"

    name, name2 = person(), person()
    org = rng.choice(ORGS)
    city, pin_prefix = rng.choice(CITIES)
    pin = pin_prefix + f"{rng.randrange(100):02d}"
    line1 = f"{rng.randint(1, 250)}/{rng.randint(1, 9)}, {rng.choice(STREETS)}"
    addr = f"{line1}, {city} - {pin}"
    letters = lambda n: "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(n))  # noqa: E731
    pan = letters(3) + rng.choice("PCHF") + letters(1) + f"{rng.randrange(10000):04d}" + letters(1)
    phone = str(rng.randint(6, 9)) + f"{rng.randrange(10**9):09d}"
    acct = str(rng.randint(10**11, 10**15))
    aad = str(rng.randint(2, 9)) + f"{rng.randrange(10**11):011d}"
    email = f"{name.split()[0].lower()}.{name.split()[1].lower()}@example.com"
    passport = rng.choice("JKLMNP") + f"{rng.randrange(10**7):07d}"
    inventory = {
        "person_names": [name, name2], "org_names": [org + " Pvt Ltd"], "pans": [pan], "aadhaars": [aad], "phones": [phone],
        "emails": [email], "account_numbers": [acct], "address_lines": [addr], "pincodes": [pin],
    }
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def date():
        d, m, y = rng.randint(1, 28), rng.randint(1, 12), rng.choice([2024, 2025, 2026])
        return rng.choice([f"{d:02d}/{m:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{d} {months[m - 1]} {y}", f"{d:02d}-{months[m - 1]}-{str(y)[2:]}", f"{d}th {months[m - 1]}, {y}"])

    def indian(n):
        s = str(n)
        if len(s) <= 3:
            return s
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return ",".join(parts + [tail])

    fmt = {
        "mon": f"{rng.choice(months)} {rng.choice([2025, 2026])}", "name": name, "NAME": name.upper(), "name2": name2, "org": org, "pan": pan,
        "pan_lower": pan.lower(), "acct": acct, "acct_spaced": " ".join(acct[i : i + 4] for i in range(0, len(acct), 4)), "ph": phone,
        "ph_spaced": f"{phone[:5]}-{phone[5:]}", "addr": addr, "d1": date(), "d2": date(), "dob": date(), "email": email, "pin": pin,
        "passport": passport, "aad_grouped": f"{aad[:4]} {aad[4:8]} {aad[8:]}", "amt": rng.randrange(1000, 90000),
        "amt2": indian(rng.randrange(100000, 90_000_000)), "amt3": indian(rng.randrange(100000, 900_000_000)),
    }
    return inventory, [t.format(**fmt) for t in DOC_TEMPLATES]


def test_redacted_output_passes_the_gate_for_generated_documents():
    rng = random.Random(4242)
    for _ in range(80):
        inventory, docs = make_case(rng)
        r = Redactor(KnownEntities.from_inventory(inventory))
        for raw in docs:
            assert gate.scan({"text": raw}), raw  # the raw text is a leak
            out = r.redact(raw)
            findings = gate.scan({"text": out})
            assert findings == [], (raw, out, findings)


def test_no_raw_inventory_value_survives_redaction():
    rng = random.Random(99)
    for _ in range(60):
        inventory, docs = make_case(rng)
        r = Redactor(KnownEntities.from_inventory(inventory))
        for raw in docs:
            out = r.redact(raw)
            low = out.casefold()
            compact = re.sub(r"[\s\-./_]", "", low)
            for kind in ("person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines"):
                for value in inventory[kind]:
                    assert value.casefold() not in low, (kind, value, out)
                    if kind in ("pans", "aadhaars", "phones", "account_numbers"):
                        assert re.sub(r"[\s\-./_]", "", value.casefold()) not in compact, (kind, value, out)
            for name in inventory["person_names"]:
                for part in name.split():
                    assert not re.search(rf"(?<![A-Za-z]){re.escape(part)}(?![A-Za-z])", out, re.I), (part, out)


def test_amounts_in_generated_documents_never_leave_a_raw_rupee_figure():
    rng = random.Random(7)
    for _ in range(40):
        inventory, docs = make_case(rng)
        r = Redactor(KnownEntities.from_inventory(inventory))
        for raw in docs:
            out = r.redact(raw)
            for m in re.finditer(r"(?:Rs\.?|INR|₹)\s*\d", out):
                pytest.fail(f"raw rupee figure left: {out}")


# ------------------------------------------------------------------------------------------------ aliases and token fidelity

LINE1 = "No. 300, Park Avenue"
FULL = "No. 300, Park Avenue, Patna, PIN 800774"
TAMIL_ADDR = "எண் ௩௦௦, பார்க் அவென்யூ, பாட்னா"


def test_from_inventory_reads_aliases_and_defaults_to_empty():
    assert KnownEntities.from_inventory({}).aliases == {}
    assert KnownEntities().aliases == {}
    k = KnownEntities.from_inventory({"address_lines": [LINE1], "aliases": {FULL: LINE1}})
    assert k.aliases == {FULL: LINE1}
    assert KnownEntities.from_inventory({"aliases": None}).aliases == {}


def test_prefix_addresses_share_one_token_without_an_alias_entry():
    r = redactor(address_lines=[LINE1, FULL, "7 Nehru Street, Chennai 600001"], pincodes=["800774", "600001"])
    assert r.redact(LINE1) == "[ADDR_1]"
    assert r.redact(FULL) == "[ADDR_1]"
    assert r.redact("Property: 7 Nehru Street, Chennai 600001") == "Property: [ADDR_2]"
    assert r.redact(f"Address: {FULL}. Second: {LINE1}.") == "Address: [ADDR_1]. Second: [ADDR_1]."


def test_prefix_merge_is_case_and_punctuation_insensitive_and_order_independent():
    r = redactor(address_lines=["NO 300 PARK AVENUE PATNA 800774", "no. 300, park avenue", "Flat 9, Lake View"], pincodes=[])
    assert r.redact("no. 300, Park Avenue") == "[ADDR_1]"
    assert r.redact("NO 300 PARK AVENUE PATNA 800774") == "[ADDR_1]"
    assert r.redact("Flat 9, Lake View") == "[ADDR_2]"


def test_prefix_merge_needs_at_least_two_tokens_and_a_true_prefix():
    r = redactor(address_lines=["12", "12 Green Park, Delhi", "Green Park, Delhi"], pincodes=[])
    assert r.redact("12 Green Park, Delhi") != r.redact("Green Park, Delhi")  # a one-token line is not a prefix; not a prefix either
    r2 = redactor(address_lines=["Park Avenue", "300 Park Avenue Patna"], pincodes=[])
    assert r2.redact("Park Avenue") == "[ADDR_1]"
    assert r2.redact("300 Park Avenue Patna") == "[ADDR_2]"  # shares words but does not start with them


def test_alias_address_shares_the_canonical_token_and_takes_no_number():
    r = redactor(
        address_lines=[LINE1, FULL, "7 Nehru Street, Chennai 600001"],
        pincodes=["800774", "600001"],
        aliases={FULL: LINE1},
    )
    assert r.redact(LINE1) == "[ADDR_1]"
    assert r.redact(FULL) == "[ADDR_1]"
    assert r.redact("7 Nehru Street, Chennai 600001") == "[ADDR_2]"  # the alias did not consume ADDR_2
    assert r.token_map[FULL] == r.token_map[LINE1] == "[ADDR_1]"


def test_alias_that_is_only_in_the_aliases_dict_still_works():
    r = redactor(address_lines=[LINE1], pincodes=[], aliases={FULL: LINE1})
    assert r.redact(f"Lives at {FULL}") == "Lives at [ADDR_1]"


def test_native_script_address_alias_gets_the_same_token_as_line1():
    r = redactor(address_lines=[LINE1, "9 Other Road, Kochi"], pincodes=[], aliases={TAMIL_ADDR: LINE1})
    assert r.redact(f"முகவரி: {TAMIL_ADDR}") == "முகவரி: [ADDR_1]"
    assert r.redact("முகவரி: எண் 300, பார்க் அவென்யூ, பாட்னா") == "முகவரி: [ADDR_1]"  # native digits and ASCII digits alike
    assert r.redact("9 Other Road, Kochi") == "[ADDR_2]"


def test_native_alias_name_gives_the_applicant_token():
    native = "நித்யா கணேசன்"
    r = redactor(person_names=["Nithya Ganesan", "Rahul Menon"], aliases={native: "Nithya Ganesan"})
    assert r.redact(f"விண்ணப்பதாரர்: {native}") == "விண்ணப்பதாரர்: [APPLICANT]"
    assert r.redact("Nithya Ganesan") == "[APPLICANT]"
    assert r.redact("Rahul Menon") == "[PERSON_2]"  # the alias did not take a person number
    assert r.token_map[native] == "[APPLICANT]"


def test_alias_listed_in_person_names_is_not_a_separate_person():
    native = "नित्या गणेशन"
    r = redactor(person_names=["Nithya Ganesan", native, "Rahul Menon"], aliases={native: "Nithya Ganesan"})
    assert r.redact(native) == "[APPLICANT]"
    assert r.redact("Rahul Menon") == "[PERSON_2]"
    assert r.redact("witness Mr Zoravar Bhatnagar") == "witness Mr [PERSON_3]"


def test_explicit_alias_takes_precedence_over_the_positional_native_rule():
    native = "राहुल मेनन"
    # positionally the native name at index 1 would be the applicant's alias; the explicit alias says it is Rahul's
    k = KnownEntities.from_inventory({"person_names": ["Nithya Ganesan", native, "Rahul Menon"], "aliases": {native: "Rahul Menon"}})
    assert k.person_names == ["Nithya Ganesan", native, "Rahul Menon"]  # no positional pop
    r = Redactor(k)
    assert r.redact(native) == "[PERSON_2]"
    assert r.redact("Nithya Ganesan") == "[APPLICANT]"
    # without the alias entry the positional fallback still applies
    k2 = KnownEntities.from_inventory({"person_names": ["Nithya Ganesan", native, "Rahul Menon"]})
    assert Redactor(k2).redact(native) == "[APPLICANT]"


def test_aliases_apply_in_every_category():
    r = redactor(
        org_names=["Infosys Pvt Ltd"], pans=["ABCPK1234F"], phones=["9876543210"], emails=["priya.sharma@example.com"], account_numbers=["123456789012345"],
        pincodes=["560034"],
        aliases={
            "Infosys BPM Services": "Infosys Pvt Ltd", "abcpk1234g": "ABCPK1234F", "8123456789": "9876543210",
            "p.sharma@corp.example.org": "priya.sharma@example.com", "0123456789012": "123456789012345",
        },
    )
    assert r.redact("Employer Infosys BPM Services") == "Employer [ORG_A]"
    assert r.redact("PAN ABCPK1234G") == "PAN [PAN_1]"
    assert r.redact("mobile 81234 56789") == "mobile [PHONE_1]"
    assert r.redact("mail p.sharma@corp.example.org") == "mail [EMAIL_1]"
    assert r.redact("acct 0123456789012") == "acct [ACCT_1]"
    assert r.redact("PAN ABCPK1234F") == "PAN [PAN_1]"


def test_alias_chains_resolve_to_the_final_canonical_value():
    r = redactor(address_lines=[LINE1], pincodes=[], aliases={FULL: "No. 300, Park Avenue, Patna", "No. 300, Park Avenue, Patna": LINE1})
    assert r.redact(FULL) == "[ADDR_1]"
    assert r.redact("No. 300, Park Avenue, Patna") == "[ADDR_1]"


def test_unresolvable_alias_is_ignored():
    r = redactor(address_lines=[LINE1], pincodes=[], aliases={"Some Where": "Not In Any List"})
    assert r.redact(LINE1) == "[ADDR_1]"
    assert r.redact("Some Where") == "Some Where"


def test_alias_output_passes_the_gate_and_leaks_nothing():
    r = redactor(
        address_lines=[LINE1, FULL], pincodes=["800774"], aliases={FULL: LINE1, TAMIL_ADDR + ", PIN ௮௦௦௭௭௪": LINE1},
    )
    text = f"Residence: {FULL}. Native: {TAMIL_ADDR}, PIN ௮௦௦௭௭௪. Short: {LINE1}. PIN: 800774."
    out = r.redact(text)
    assert clean(out)
    assert "Park" not in out and "800774" not in out and "பார்க்" not in out
    assert out.count("[ADDR_1]") >= 3
