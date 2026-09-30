"""``pii_inventory.aliases``: other written forms of the SAME value map to one canonical string; deliberately
different values (the evidence of a fraud file) never do."""

import re

from jevloan.data import names as nm
from test_data_common import DATE_PAT, ascii_digits, book_2000, parse_date  # noqa: F401

LISTS = ("person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines", "pincodes")


def _lists(inv):
    return {name: getattr(inv, name) for name in LISTS}


def _category(inv, value):
    return [name for name, vals in _lists(inv).items() if value in vals]


def test_every_alias_key_and_value_is_in_the_same_inventory_list():
    total = 0
    for f in book_2000():
        inv = f.pii_inventory
        for key, canon in inv.aliases.items():
            total += 1
            assert key != canon
            cats = _category(inv, key)
            assert len(cats) == 1, (f.file_id, key, cats)
            assert canon in getattr(inv, cats[0]), (f.file_id, key, canon)
            assert cats[0] in ("person_names", "address_lines"), (f.file_id, key)
            assert canon not in inv.aliases, "no alias chains: a canonical string is never itself an alias"
    assert total > 2000  # aliases are the norm, not the exception


def test_a_canonical_address_is_never_nested_inside_another_unaliased_address():
    """No two entries the redactor would treat as separate addresses where one contains the other (that is what
    made line1 and the one-line address get different tokens)."""
    for f in book_2000():
        inv = f.pii_inventory
        canon = [a for a in inv.address_lines if a not in inv.aliases]
        for a in canon:
            for b in canon:
                assert a == b or a.lower() not in b.lower(), (f.file_id, a, b)
        for key in inv.aliases:
            if key in inv.address_lines:
                assert inv.aliases[key] in canon


def test_latin_address_aliases_are_forms_of_their_own_line1():
    for f in book_2000():
        inv = f.pii_inventory
        for key, canon in inv.aliases.items():
            if key not in inv.address_lines or not key.isascii():
                continue
            assert canon.lower() in key.lower(), (f.file_id, key, canon)  # one-line form or upper-case form
            pin = re.search(r"\b([1-9]\d{5})\b", key)
            if key.lower() != canon.lower():
                assert pin and pin[1] in inv.pincodes, (f.file_id, key)


def test_the_applicants_address_forms_alias_to_the_home_line1_and_other_addresses_never_do():
    for f in book_2000():
        inv, home = f.pii_inventory, f.applicant.address
        for key, canon in inv.aliases.items():
            if key not in inv.address_lines:
                continue
            m = re.search(r"(?<!\d)([1-9]\d{5})(?!\d)", ascii_digits(key))
            if home.line1.lower() in key.lower():  # a form of the home address
                assert canon.lower() == home.line1.lower(), (f.file_id, key, canon)
            if m and m[1] != home.pincode:
                # a proof with a different PIN is a different address (a mismatched proof, a property, a business):
                # it must not be folded into the applicant's home address
                assert canon.lower() != home.line1.lower(), (f.file_id, key, canon)


def test_native_script_files_alias_their_name_and_their_address_renderings():
    native = [f for f in book_2000() if f.meta.disparity_subset == "lang_doc_script"]
    assert len(native) >= 60
    script_of = {"ta": "tamil", "bn": "bengali", "hi": "devanagari", "mr": "devanagari"}
    for f in native:
        inv, a = f.pii_inventory, f.applicant
        assert inv.aliases[a.name_native] == a.name  # native name renders the romanised name
        assert inv.person_names[:2] == [a.name, a.name_native]
        lang = f.demographics.language
        doc = next(d for d in f.documents if d.doc_type.startswith("address_proof"))
        assert doc.script == script_of[lang]
        native_keys = [k for k in inv.aliases if k in inv.address_lines and not k.isascii()]
        assert len(native_keys) == 2  # the native line1 and the native one-line address
        # the rendering is of the canonical address: same house number, native form of the same street
        for k in native_keys:
            canon = inv.aliases[k]
            assert canon in inv.address_lines and canon.isascii()
            house = re.sub(r"[^\d/]", "", canon.split(",")[0])
            assert k.startswith(house + ", ")
            street_latin = canon.split(", ")[-1]
            streets = dict(nm.STREETS)
            native_street = streets[street_latin]["ta" if lang == "ta" else "bn" if lang == "bn" else "dev"]
            assert native_street in k
            assert k in doc.text
    for f in book_2000():
        if f.meta.disparity_subset != "lang_doc_script":
            assert all(k.isascii() for k in f.pii_inventory.aliases)


def test_native_landlord_names_alias_their_romanised_name():
    seen = 0
    for f in book_2000():
        for key, canon in f.pii_inventory.aliases.items():
            if key in f.pii_inventory.person_names and not key.isascii() and canon != f.applicant.name:
                lang = f.demographics.language
                assert nm.native_form(canon, lang) == key, (f.file_id, key, canon)
                seen += 1
    assert seen >= 5


def test_initial_forms_alias_the_full_name():
    seen = 0
    for f in book_2000():
        for key, canon in f.pii_inventory.aliases.items():
            if key in f.pii_inventory.person_names and key.isascii():
                first, _, last = canon.partition(" ")
                assert key == f"{first[0]}. {last}"
                assert key in "\n".join(d.text for d in f.documents) + f.sanction_memo.text + f.kfs.text
                seen += 1
    assert seen >= 20


def _pan_card(f):
    d = next(d for d in f.documents if d.doc_type == "pan_card_text")
    m = re.search(r"Name: (.+?)\. PAN: (\w{10})\. Date of birth: " + DATE_PAT, d.text)
    return m[1], m[2].upper()


def test_fraud_evidence_is_never_folded_into_the_genuine_value():
    identity = [f for f in book_2000() if f.labels.fraud_type == "identity_mismatch"]
    salary = [f for f in book_2000() if f.labels.fraud_type == "salary_pattern_mismatch"]
    assert len(identity) >= 20 and len(salary) >= 15
    for f in identity:
        inv = f.pii_inventory
        name, pan = _pan_card(f)
        mentioned = {k.lower() for k in inv.aliases} | {v.lower() for v in inv.aliases.values()}
        if pan != f.applicant.pan:  # a different PAN stays a separate PAN in the inventory
            assert pan in inv.pans and f.applicant.pan in inv.pans and pan != f.applicant.pan
            assert pan.lower() not in mentioned
        if name.lower() != f.applicant.name.lower():  # a different name stays a different person
            canon = [n for n in inv.person_names if n.lower() == name.lower()]
            assert canon and canon[0] != f.applicant.name
            assert name.lower() not in mentioned
            assert inv.aliases.get(canon[0]) is None and canon[0] not in inv.aliases.values()
    for f in salary:  # the two employers are two different organisations, and organisations have no aliases
        inv = f.pii_inventory
        emp, narration = f.application.employer_name, f.bank.salary_narration_employer
        assert emp != narration and emp in inv.org_names and narration in inv.org_names
        assert emp not in inv.aliases and narration not in inv.aliases
        assert emp not in inv.aliases.values() and narration not in inv.aliases.values()
    for f in book_2000():  # only names and addresses are ever aliased
        inv = f.pii_inventory
        for k, v in inv.aliases.items():
            assert not ({k, v} & (set(inv.org_names) | set(inv.pans) | set(inv.emails) | set(inv.phones) | set(inv.aadhaars)))


def test_a_mismatched_address_proof_is_not_folded_into_the_home_address():
    mism = 0
    for f in book_2000():
        doc = next(d for d in f.documents if d.doc_type.startswith("address_proof"))
        text = ascii_digits(doc.text)
        if f.applicant.address.pincode in text or "address_proof" not in f.labels.missing_items or "address page is missing" in text:
            continue
        mism += 1
        inv = f.pii_inventory
        pin = re.search(r"(?<!\d)([1-9]\d{5})(?!\d)", text)[1]
        assert pin != f.applicant.address.pincode
        keys = [k for k in inv.aliases if k in inv.address_lines and pin in ascii_digits(k)]
        assert keys, f.file_id
        assert all(inv.aliases[k].lower() != f.applicant.address.line1.lower() for k in keys)
        assert f.applicant.address.line1 in inv.address_lines  # the home address is still listed, separately
    assert mism >= 10
