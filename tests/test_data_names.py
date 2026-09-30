import random
import re

from jevloan.data import names as nm
from test_data_common import book_2000


def test_verhoeff_against_reference_values():
    # published example: the check digit of 236 is 3
    assert nm.verhoeff_check_digit("236") == "3"
    assert nm.verhoeff_valid("2363")
    assert not nm.verhoeff_valid("2364")


def test_generated_aadhaar_is_valid_verhoeff_and_first_digit_2_to_9():
    rng = random.Random(1)
    for _ in range(500):
        a = nm.gen_aadhaar(rng)
        assert re.fullmatch(r"[2-9]\d{11}", a)
        assert nm.verhoeff_valid(a)


def test_pan_phone_account_formats():
    rng = random.Random(2)
    for holder in "PFC":
        pan = nm.gen_pan(rng, holder, "Sharma")
        assert re.fullmatch(r"[A-Z]{3}" + holder + r"S\d{4}[A-Z]", pan)
    for _ in range(300):
        assert re.fullmatch(r"[6-9]\d{9}", nm.gen_phone(rng))
        assert re.fullmatch(r"[1-9]\d{10,15}", nm.gen_account_number(rng))


def test_gstin_check_character_matches_a_known_gstin():
    # 27AAPFU0939F1ZV is the widely published sample GSTIN
    assert nm.gstin_check_char("27AAPFU0939F1Z") == "V"


def test_gstin_embeds_the_pan_and_has_a_valid_check_character():
    rng = random.Random(3)
    pan = nm.gen_pan(rng, "C", "Vardhan")
    g = nm.gen_gstin(rng, "27", pan)
    assert re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g)
    assert g[2:12] == pan
    assert g[-1] == nm.gstin_check_char(g[:-1])


def test_emails_look_like_emails_and_disposable_domains_are_disposable():
    rng = random.Random(4)
    for kind in ("free", "corporate", "disposable"):
        e = nm.gen_email(rng, "Rahul Sharma", kind, "Vardhan Infotech Pvt Ltd")
        assert re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e)
    assert nm.gen_email(rng, "Rahul Sharma", "disposable").split("@")[1] in nm.DISPOSABLE_EMAIL_DOMAINS
    assert nm.gen_email(rng, "Rahul Sharma", "corporate", "Vardhan Infotech Pvt Ltd").endswith("@vardhaninfotech.co.in")


def test_org_words_never_collide_with_person_name_tokens():
    assert not (set(nm.ORG_PREFIXES) & nm.ALL_PERSON_TOKENS)
    assert not (set(nm.EMPLOYER_SECTORS) & nm.ALL_PERSON_TOKENS)


def test_native_tables_cover_the_pools_they_promise():
    for lang in ("ta", "bn", "hi", "mr"):
        assert set(nm.NATIVE_FIRST[lang]) == {"M", "F"}
        rng = random.Random(5)
        for g in "MF":
            p = nm.draw_person(rng, nm.NATIVE_LANG_REGION[lang], g, lang)
            assert p.native and p.native != p.name
            assert nm.native_form(p.name, lang) == p.native
        script_range = {"ta": (0x0B80, 0x0BFF), "bn": (0x0980, 0x09FF), "hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F)}[lang]
        for src in list(nm.NATIVE_SURNAMES[lang].values()) + [n for g in "MF" for n in nm.NATIVE_FIRST[lang][g].values()]:
            assert all(script_range[0] <= ord(ch) <= script_range[1] or ch == " " for ch in src), src


def test_native_address_words_and_streets_exist_in_every_script():
    for key in ("ta", "bn", "dev"):
        assert all(key in native for _, native in nm.STREETS)
    for lang in ("ta", "bn", "hi", "mr"):
        assert {"elec_bill", "name", "address", "bill_date", "rent", "landlord", "tenant"} <= set(nm.NATIVE_WORDS[lang])
    assert nm.to_native_digits("600034", "tamil") == "௬௦௦௦௩௪"


def test_cities_have_valid_pin_prefixes_and_native_names_where_needed():
    rng = random.Random(6)
    for city in nm.CITIES:
        pin = nm.draw_pin(rng, city)
        assert re.fullmatch(r"[1-9]\d{5}", pin) and pin.startswith(city.pin_prefixes[0][:3])
        for lang in city.langs:
            if lang in nm.NATIVE_LANG_REGION:
                assert lang in city.native, (city.name, lang)


def test_every_identifier_in_the_book_is_format_valid():
    book = book_2000()
    pan_re = re.compile(r"[A-Z]{3}[PCF][A-Z]\d{4}[A-Z]")
    for f in book:
        a = f.applicant
        assert re.fullmatch(r"[A-Z]{3}P[A-Z]\d{4}[A-Z]", a.pan), f.file_id
        assert a.pan[4] == a.name.split(" ")[-1][0].upper()
        assert re.fullmatch(r"[2-9]\d{11}", a.aadhaar) and nm.verhoeff_valid(a.aadhaar)
        assert re.fullmatch(r"[6-9]\d{9}", a.phone)
        assert re.fullmatch(r"[1-9]\d{10,15}", f.bank.account_number)
        assert re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", a.email)
        assert re.fullmatch(r"[1-9]\d{5}", a.address.pincode)
        if f.co_applicant:
            assert re.fullmatch(r"[A-Z]{3}P[A-Z]\d{4}[A-Z]", f.co_applicant.pan)
        if f.gst:
            g = f.gst.gstin
            assert re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g), g
            assert g[-1] == nm.gstin_check_char(g[:-1])
            assert pan_re.fullmatch(g[2:12])
            assert g[2:12] in f.pii_inventory.pans  # the embedded PAN is in the inventory
            if f.segment == "self_employed":
                assert g[2:12] == a.pan  # a proprietor's GSTIN carries the proprietor's PAN
