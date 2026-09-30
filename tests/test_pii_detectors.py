"""Unit tests for each PII detector, including the tricky obfuscations (PLAN 3.5)."""

import pytest

from jevloan.pii import detectors as D
from jevloan.pii import lexicon as lx
from jevloan.pii.detectors import Finding, mask, mask_name, run_all, run_all_masked


def names(text: str) -> set[str]:
    return {f.detector for f in run_all(text)}


def hit(text: str, detector: str) -> bool:
    return detector in names(text)


# ------------------------------------------------------------------------------------------------ masking


def test_mask_keeps_first_two_and_last_two():
    assert mask("ABCDE1234F") == "AB******4F"
    assert mask("9876543210") == "98******10"
    assert mask("2345 6789 0123") == "23********23"  # separators are dropped before masking


def test_mask_never_returns_the_whole_value():
    for value in ["a", "ab", "abc", "abcd", "abcde", "123456", "9876543210"]:
        assert mask(value) != value
    assert mask("1234") == "1***"
    assert mask("") == ""


def test_mask_name_keeps_first_letter_only():
    assert mask_name("Rajesh Kumar") == "R***"
    assert mask_name("") == ""


def test_findings_never_carry_the_raw_value():
    for text in ["PAN ABCDE1234F", "call 9876543210", "Mr Rajesh Kumar", "a.b@gmail.com", "Aadhaar 2345 6789 0123"]:
        for f in run_all(text):
            assert isinstance(f, Finding)
            assert not hasattr(f, "raw")
            assert f.masked != text[f.start : f.end]


def test_finding_span_property():
    f = run_all("PAN ABCDE1234F")[0]
    assert f.span == (f.start, f.end) == (4, 14)


# ------------------------------------------------------------------------------------------------ PAN


@pytest.mark.parametrize(
    "text",
    [
        "ABCDE1234F",
        "abcde1234f",
        "AbCdE1234f",
        "PAN: ABCDE1234F.",
        "pan no.ABCDE1234F,",
        "ABCDE 1234 F",
        "abcde 1234 f",
        "ABCDE-1234-F",
        "ABCDE.1234.F",
        "ABCDE 1234F",
        "ABCDE1234 F",
        "A B C D E 1 2 3 4 F",
        "A-B-C-D-E-1-2-3-4-F",
        "ABCDE one two three four F",
        "ＡＢＣＤＥ１２３４Ｆ",
        "ABCDE१२३४F",
        "A​BCDE12​34F",
        "29ABCDE1234F1Z5",  # inside a GSTIN
        "gstin 27abcde1234f1z3",
        "PANABCPK1234F",  # glued to a label, real PAN shape
        "ID:ABCDE1234F;",
    ],
)
def test_pan_caught(text):
    assert hit(text, "pan"), text


@pytest.mark.parametrize(
    "text",
    [
        "March 2026 A copy",
        "for April 2026 a bank statement",
        "Form 16 Part A 2026",
        "ABCD1234E",  # 4 letters
        "ABCDEF1234G",  # 6 letters, plain (not glued-PAN shaped)
        "ABCDE12345F",  # 5 digits
        "Salary 2026",
        "GSTR-3B filed for Mar 2026",
        "[PAN_1]",
        "29[PAN_1]1Z5",
        "an art 1234 F",
    ],
)
def test_pan_not_caught(text):
    assert not hit(text, "pan"), text


def test_pan_span_inside_gstin_is_the_pan_only():
    (f,) = [x for x in run_all("GSTIN 29ABCDE1234F1Z5") if x.detector == "pan"]
    assert "GSTIN 29ABCDE1234F1Z5"[f.start : f.end] == "ABCDE1234F"


# ------------------------------------------------------------------------------------------------ Aadhaar, VID, account, card


@pytest.mark.parametrize(
    "text",
    [
        "234567890123",
        "2345 6789 0123",
        "2345-6789-0123",
        "2345.6789.0123",
        "2345/6789/0123",
        "234 567 890 123",
        "2 3 4 5 6 7 8 9 0 1 2 3",
        "२३४५ ६७८९ ०१२३",
        "２３４５６７８９０１２３",
        "23​45 67​89 0123",
        "two three four five six seven eight nine zero one two three",
        "UID: 2345 6789 0123.",
    ],
)
def test_aadhaar_caught(text):
    assert hit(text, "aadhaar"), text


def test_aadhaar_first_digit_rule():
    assert not hit("134567890123", "aadhaar")  # starts with 1
    assert not hit("2345678901234", "aadhaar")  # 13 digits
    assert not hit("23456789012", "aadhaar")  # 11 digits


def test_aadhaar_vid_16_digits():
    for text in ["1234567890123456", "1234 5678 9012 3456", "1234-5678-9012-3456", "१२३४५६७८९०१२३४५६"]:
        assert hit(text, "aadhaar_vid"), text
    assert not hit("123456789012345", "aadhaar_vid")
    assert not hit("12345678901234567", "aadhaar_vid")


@pytest.mark.parametrize("digits", ["1" * 9, "12345678901", "1234567890123456", "123456789012345678", "9" * 20])
def test_account_number_lengths(digits):
    assert hit(f"A/c {digits}", "account_number")


def test_account_number_grouped_and_glued():
    assert hit("Account 1234 5678 9012 34", "account_number")
    assert hit("acct 12-34-56-78-90-12", "account_number")
    assert hit("ACC12345678901", "account_number")  # stuck to letters
    assert hit("a/c 123.456.789.012", "account_number")


@pytest.mark.parametrize(
    "text",
    [
        "12345678",  # 8 digits
        "Mar 2026",
        "Rs 12,50,000",  # Indian grouping never joins (comma)
        "12,345,678,901",
        "3fa9c1e2b4d5a6f73fa9c1e2b4d5a6f73fa9c1e2b4d5a6f7",  # hex digest
        "a" * 12 + "1234567890" + "b" * 12,
    ],
)
def test_account_number_not_caught(text):
    assert not hit(text, "account_number"), text


def test_card_number_needs_luhn():
    assert hit("4111 1111 1111 1111", "card_number")
    assert hit("4111-1111-1111-1111", "card_number")
    assert hit("378282246310005", "card_number")  # 15-digit Amex
    assert hit("4222222222222", "card_number")  # 13-digit Visa
    assert not hit("4111 1111 1111 1112", "card_number")  # fails Luhn
    assert hit("4111 1111 1111 1112", "account_number")  # still an account-sized number


# ------------------------------------------------------------------------------------------------ phone


@pytest.mark.parametrize(
    "text",
    [
        "9876543210",
        "+919876543210",
        "+91 98765 43210",
        "+91-98765-43210",
        "+91 9876543210",
        "0091 9876543210",
        "0091-98765-43210",
        "09876543210",
        "0 98765 43210",
        "98765 43210",
        "98765-43210",
        "98765.43210",
        "987 654 3210",
        "9 8 7 6 5 4 3 2 1 0",
        "9-8-7-6-5-4-3-2-1-0",
        "९८७६५४३२१०",
        "９８７６５４３２１०".replace("०", "０"),
        "98​76​54​32​10",
        "nine eight seven six five four three two one zero",
        "nine-eight-seven-six-five-four-three-two-one-zero",
        "double nine eight seven triple six five four three",
        "nine eight seven six oh five four three two oh",
        "9876O43210",
        "6" + "0" * 9,
        "(022) 2345 6789",
        "(0755) 2345678",
        "022-23456789",
        "+91 22 2345 6789",
        "011 2612 3456",
        "080 4123 4567",
        "Phone: 98765-43210.",
    ],
)
def test_phone_caught(text):
    assert hit(text, "phone_in"), text


@pytest.mark.parametrize(
    "text",
    [
        "5876543210",  # mobile numbers start 6-9
        "98765 4321",  # 9 digits
        "Mar 2026",
        "FY 2025-26",
        "1234567",
        "score 750-799",
    ],
)
def test_phone_not_caught(text):
    assert not hit(text, "phone_in"), text


# ------------------------------------------------------------------------------------------------ email, UPI


@pytest.mark.parametrize(
    "text",
    [
        "priya.sharma@gmail.com",
        "PRIYA.SHARMA@GMAIL.COM",
        "p+loan@yahoo.co.in",
        "a_b-c@sub.domain.example.org",
        "mail me at rajesh99@outlook.com.",
        "priya [at] gmail [dot] com",
        "priya(at)gmail(dot)com",
        "priya {at} gmail {dot} com",
        "write to priya at gmail dot com",
        "priya @ gmail.com",
        "priya＠gmail．com",
    ],
)
def test_email_caught(text):
    assert hit(text, "email"), text


@pytest.mark.parametrize(
    "text",
    ["meet at 5 pm at the bank", "look at the dot", "the rate @ 9.5%", "user@localhost", "@handle", "[EMAIL_1]", "email_domain_type"],
)
def test_email_not_caught(text):
    assert not hit(text, "email"), text


@pytest.mark.parametrize(
    "text",
    ["pay rahul@okaxis", "rahul.k@oksbi", "9876543210@ybl", "UPI: anita_77@paytm.", "x1@okhdfcbank now", "name-1@ibl"],
)
def test_upi_caught(text):
    assert hit(text, "upi_id"), text


def test_upi_is_not_an_email():
    assert not hit("priya@gmail.com", "upi_id")
    assert not hit("priya@okaxis", "email")


# ------------------------------------------------------------------------------------------------ IFSC, passport, voter, DL


@pytest.mark.parametrize("text", ["SBIN0001234", "sbin0001234", "HDFC0ABC123", "HDFC 0001234", "IFSC: ICIC0000456."])
def test_ifsc_caught(text):
    assert hit(text, "ifsc"), text


@pytest.mark.parametrize("text", ["Bank0Branch", "SBI0001234", "SBIN1001234", "IFSC not provided"])
def test_ifsc_not_caught(text):
    assert not hit(text, "ifsc"), text


@pytest.mark.parametrize("text", ["J8369854", "j8369854", "Passport J 8369854", "J-8369854", "P1234567."])
def test_passport_caught(text):
    assert hit(text, "passport"), text


@pytest.mark.parametrize("text", ["J83698", "J83698541", "a 1234567", "F000001", "Rs1234567"])
def test_passport_not_caught(text):
    assert not hit(text, "passport"), text


@pytest.mark.parametrize("text", ["ABC1234567", "abc1234567", "EPIC ABC 1234567", "ABC-1234567"])
def test_voter_id_caught(text):
    assert hit(text, "voter_id"), text


@pytest.mark.parametrize("text", ["MH1220110012345", "MH12 20110012345", "MH-12-20110012345", "DL0420110149646", "ka 05 20110012345"])
def test_driving_licence_caught(text):
    assert hit(text, "driving_licence"), text


def test_driving_licence_not_caught():
    assert not hit("MH12 2011001234", "driving_licence")  # too few digits


# ------------------------------------------------------------------------------------------------ PIN code


@pytest.mark.parametrize(
    "text",
    [
        "PIN: 560034",
        "Pincode 560034",
        "PIN Code - 560034",
        "pin code: 560 034",
        "Postal code 400001",
        "ZIP 110001",
        "पिन कोड: ५६००३४",
        "Bengaluru - 560034",
        "Mumbai, 400001",
        "Chennai 600001",
        "12 MG Road, Whitefield, Bengaluru - 560066",
        "Flat 4, Some Society, Vasant, Nowhereville - 411001",
    ],
)
def test_pin_caught_in_context(text):
    assert hit(text, "pincode_ctx"), text


@pytest.mark.parametrize(
    "text",
    ["560034", "amount 560034 paid", "PIN: 060034", "PIN: 56003", "ref 400001 closed", "[PIN]", "Rs 100000"],
)
def test_pin_not_caught_without_context(text):
    assert not hit(text, "pincode_ctx"), text


# ------------------------------------------------------------------------------------------------ person names


@pytest.mark.parametrize(
    "text",
    [
        "Mr Rajesh Kumar",
        "Mrs. Priya Sharma",
        "Ms Anita",
        "Miss Kavita Nair",
        "Shri Ramesh Chandra",
        "Shree Ganesh Patil",
        "Sri Venkatesh",
        "Smt. Sunita Devi",
        "Kumari Pooja Singh",
        "Km Neha",
        "Dr Anil Kapoor",
        "Late Shri Mohan Lal",
        "MR RAJESH KUMAR",
        "mr rajesh kumar",
        "Mr Zoravar Bhatnagar",  # unknown surname, honorific is enough
        "Dr. Ambedkar Kulasekaran",
    ],
)
def test_person_name_honorifics(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "Rajesh S/o Mohan Lal",
        "Priya D/o Ramesh",
        "Anita W/o Sanjay Gupta",
        "C/o Thiruvengadam Reddiar",
        "s/o zorba ravichandran",
        "son of Ramesh Kumar",
        "daughter of Mr Vijay Shah",
        "wife of Sanjay Verma",
        "husband of Kavita",
        "care of Anil Sharma",
    ],
)
def test_person_name_relations(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "Name: Rajesh Kumar",
        "Name: Zxqwv Plmnb",
        "Applicant: Priya Sharma",
        "Applicant name: Neha Dhawan",
        "Applicant name Thangavelu Palanisamy",
        "Account holder: Akram Menezes",
        "Employee name: Shahid Luthra",
        "Borrower: Chintan Trivedi",
        "borrower: velmurugan chinnasamy",
        "Guarantor: Piyush Nambiar",
        "Proprietor: Deepa Sethi",
        "Name = SHAZIA JACOB",
        "Father's name: Ramesh Prasad",
        "Employee name: A. Krishnamoorthy",
        "Bank statement. Name: Sunil Grover, account type savings",
    ],
)
def test_person_name_labels(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "Priya Sharma",
        "RAJESH KUMAR SHARMA",
        "PRIYA NAIR",
        "Cheque issued in favour of Sudheer Mann dated last month",
        "Witness: Rakesh Yadav and Anita Singh",
        "Reference given by Zoravar Sharma",  # unknown first name + surname anchor
        "Kumar Iyer confirmed",
        "K. Venkatesh",
        "R Sharma",
        "S.K. Gupta was present",
        "M. S. Subramanian attended",
        "K Venkata Ramana",
    ],
)
def test_person_name_lexicon_sequences_and_initials(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "Cheque signed by Priya",
        "Amount paid to Sunil on the due date",
        "As told by Kavita, the shop is closed",
        "Spoke with Deepak regarding the salary",
        "Kiran approved it",
        "Priya's salary slip",
    ],
)
def test_person_name_single_first_name_in_sentence(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "आवेदक: राहुल शर्मा",
        "खाताधारक का नाम: रमेश गुप्ता",
        "प्रिया वर्मा ने हस्ताक्षर किए",
        "सुनीता",
        "श्रीमती कमला बाई",
        "श्री रघुनाथ प्रसाद",
        "नाम: अनुराधा तिवारी",
        "पिता का नाम: हरिशंकर मिश्रा",
        "பெயர்: குமரன் முத்து",
        "নাম: সুমন ঘোষ",
        "పేరు: శ్రీనివాస్ రెడ్డి",
        "திருமதி லட்சுமி அம்மாள்",
        "শ্রী রাহুল ঘোষ",
        "பிரியா மீனா",
        "সুমন দাস",
    ],
)
def test_person_name_native_scripts(text):
    assert hit(text, "person_name"), text


@pytest.mark.parametrize(
    "text",
    [
        "Grace period of 5 days",
        "Gold loan margin as per grid",
        "Late payment charges apply",
        "Rose Garden Apartments",
        "Will be reviewed on Sunday",
        "Mark the box; Hope this helps",
        "Bill of exchange, Pat on the back, Sunny weather, Rich text",
        "Bank name: State Bank of India",
        "Product name: Personal Loan",
        "Gandhi Road, Patel Nagar, Anand Vihar",
        "Sharma Traders and Reddy Enterprises",
        "Sri Lanka office",
        "State Bank of India, Bank of Baroda, Punjab National Bank",
        "Salary Slip Bank Statement Utility Bill Form 16 Property Title Deed",
        "Employee: [APPLICANT]. Employer: [ORG_A].",
        "Applicant: salaried",
        "Name: [APPLICANT]",
        "Mr [APPLICANT]",
        "son of [PERSON_2]",
        "Employer letter Mar 2026 January February March April May June",
        "Km 5 stone; 3 km from the branch",
        "यह दस्तावेज़ वेतन पर्ची का सारांश है",
        "இது சம்பள சீட்டின் சுருக்கம்",
        "প্রয়োজনীয় সমস্ত নথি যাচাই করা হয়েছে",
    ],
)
def test_person_name_false_positive_guards(text):
    assert not hit(text, "person_name"), (text, [f for f in run_all(text) if f.detector == "person_name"])


def test_ambiguous_english_names_only_extend_never_anchor():
    assert not hit("Rose", "person_name")
    assert not hit("Gold Rich Hope", "person_name")
    assert hit("Mark Sharma", "person_name")  # a real anchor (Sharma) is present


# ------------------------------------------------------------------------------------------------ allowlist and vocabulary


@pytest.mark.parametrize(
    "text",
    [
        "[PAN_1]", "[APPLICANT]", "[ORG_A]", "[ADDR_2]", "[PIN]", "[UID_1]", "[PHONE_1]", "[EMAIL_1]", "[ACCT_1]", "[PERSON_12]",
        "₹[25-50k]", "₹[50L-1Cr]", "₹[<10k]", "₹[>5Cr]", "₹[1-2Cr]", "₹[75k-1L]",
        "PAN [PAN_1] Aadhaar [UID_1] phone [PHONE_1] a/c [ACCT_1] email [EMAIL_1] at [ADDR_1], [PIN]",
    ],
)
def test_redaction_tokens_pass(text):
    assert run_all(text) == []


@pytest.mark.parametrize(
    "text",
    ["50L-1Cr", "30-40", "<600", "750-799", "1-3y", "800+", "NTC", "10-25k", "75k-1L", ">5Cr", "0.5-0.8", "<5%", "5-10%", "3+", "1-29", "90+",
     "PROCEED_TO_SANCTIONING_AUTHORITY", "salaried_personal", "msme_business", "secured_home", "pending_mutation", "informal_declared",
     "jevloan.state.v1", "policy-2026.09-v1", "sim-jev-0.1", "F000123"],
)
def test_state_vocabulary_and_bands_pass(text):
    assert run_all(text) == []


def test_smuggling_a_pan_inside_brackets_does_not_work():
    assert hit("[ABCDE1234F]", "pan")
    assert hit("[ABCDE_1234F]", "pan")
    assert hit("[PAN_1]ABCDE1234F", "pan")
    assert hit("[PHONE_9876543210]", "phone_in")
    assert hit("[PRIYA]", "person_name")  # a bracketed name is not one of our tokens


def test_hex_digests_and_uuids_are_not_identifiers():
    digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert run_all(f"hash {digest}") == []
    assert run_all("id 123e4567-e89b-12d3-a456-426614174000") == []


def test_ordinary_prose_with_numbers_is_clean():
    for text in [
        "Salary slip for Mar 2026. Net pay in the 50-75k band; 6 of 6 months present; FOIR 40-50; score 750-799.",
        "Bank statement covers Oct 2025 to Mar 2026, average balance 75k-1L, 0 EMI bounces in 6 months.",
        "Tenure 60 months, ticket 5-10L, LTV 70-75, DSCR 1.25-1.5, vintage 3-7y.",
        "Filed on 12 Mar; page 3 of 7; clause 4.2.1; annexure B; Q3 FY26.",
        "weights 0.30 0.30 0.15 0.10 0.15 tolerance 0.10",
    ]:
        assert run_all(text) == [], text


# ------------------------------------------------------------------------------------------------ views and structure


def test_run_all_finds_in_both_views_and_maps_spans_back():
    text = "call +91 98765 43210 or 9876543210 now"
    phones = [f for f in run_all(text) if f.detector == "phone_in"]
    covered = sorted(text[f.start : f.end] for f in phones)
    assert "+91 98765 43210" in covered and "9876543210" in covered


def test_run_all_masked_dedupes_on_detector_and_mask():
    findings = run_all_masked("9876543210")
    assert len({(f.detector, f.masked) for f in findings}) == len(findings)


def test_detector_registry_is_complete():
    expected = {
        "pan", "aadhaar", "aadhaar_vid", "account_number", "card_number", "phone_in", "email", "upi_id", "ifsc",
        "passport", "voter_id", "driving_licence", "pincode_ctx", "person_name",
    }
    assert set(D.DETECTORS) == expected
    for name, fn in D.DETECTORS.items():
        assert fn.__name__ == f"detect_{name}"


# ------------------------------------------------------------------------------------------------ lexicon


def test_lexicon_sizes():
    assert len(lx.FIRST_NAMES) >= 300
    assert len(lx.SURNAMES) >= 150
    assert len(lx.DEVANAGARI_NAMES) >= 50


def test_lexicon_entries_are_lowercase_and_clean():
    for word in lx.FIRST_NAMES | lx.SURNAMES:
        assert word == word.lower() and word.strip() == word and " " not in word


def test_english_word_names_are_excluded_or_special_cased():
    for word in ["joy", "hope", "rose", "mark", "will", "bill", "sunny", "gold", "rich", "pat", "sunday"]:
        assert word not in lx.FIRST_NAMES and word not in lx.SURNAMES
        assert word in lx.AMBIGUOUS_NAMES or word in lx.NON_NAME_CAPS


def test_lexicon_words_are_not_stop_or_terminator_words():
    assert not (lx.NAME_ANCHORS & lx.NON_NAME_CAPS)
    assert not (lx.NAME_ANCHORS & lx.NAME_TERMINATORS)


def test_devanagari_map_covers_lexicon():
    assert set(lx.LATIN_TO_DEVANAGARI.values()) <= lx.DEVANAGARI_NAMES
