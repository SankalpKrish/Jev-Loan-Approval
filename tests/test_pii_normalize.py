"""normalize() and compact_digits() (PLAN 3.5)."""

import unicodedata

import pytest

from jevloan.pii.normalize import DIGIT_ZEROS, compact_digits, compact_digits_mapped, normalize


def test_every_unicode_decimal_digit_maps_to_ascii():
    missed = []
    for cp in range(0x20000):
        ch = chr(cp)
        if unicodedata.category(ch) == "Nd" and not ch.isascii():
            if normalize(ch) != str(unicodedata.digit(ch)):
                missed.append(hex(cp))
    assert missed == []


def test_digit_zero_table_matches_unicode():
    derived = {cp for cp in range(0x20000) if unicodedata.category(chr(cp)) == "Nd" and unicodedata.digit(chr(cp)) == 0}
    assert set(DIGIT_ZEROS) | {0x30} == derived
    for zero in DIGIT_ZEROS:
        for i in range(10):
            assert unicodedata.digit(chr(zero + i)) == i


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("९८७६५४३२१०", "9876543210"),  # Devanagari
        ("৯৮৭৬৫৪৩২১০", "9876543210"),  # Bengali
        ("௯௮௭௬௫௪௩௨௧௦", "9876543210"),  # Tamil
        ("౯౮౭౬౫౪౩౨౧౦", "9876543210"),  # Telugu
        ("૯૮૭૬૫૪૩૨૧૦", "9876543210"),  # Gujarati
        ("９８７６５４３２１０", "9876543210"),  # fullwidth
        ("ＡＢＣＤＥ１２３４Ｆ", "ABCDE1234F"),  # fullwidth letters and digits
        ("①②③", "123"),  # circled
        ("𝟗𝟖𝟕", "987"),  # mathematical bold
    ],
)
def test_native_and_compatibility_digits(raw, expected):
    assert normalize(raw) == expected


def test_invisible_characters_are_stripped():
    assert normalize("9​8‌7‍6⁠5﻿4­3") == "9876543"
    assert normalize("A​B​C") == "ABC"
    assert normalize("a‮b") == "ab"


def test_cyrillic_and_greek_lookalikes_become_latin():
    assert normalize("АВСDE1234F") == "ABCDE1234F"  # Cyrillic А, В, С
    assert normalize("ΑΒΕ") == "ABE"  # Greek


def test_percent_and_html_escapes_are_undone():
    assert normalize("%39%38%37%36%35") == "98765"
    assert normalize("&#57;&#56;&#55;&#54;") == "9876"
    assert normalize("100% sure, 50%") == "100% sure, 50%"  # ordinary percent signs are untouched


def test_letter_o_and_l_between_digits():
    assert normalize("9876O43210") == "9876043210"
    assert normalize("98l6543210") == "9816543210"
    assert normalize("Rs 5000 only") == "Rs 5000 only"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("nine eight seven six five four three two one zero", "9876543210"),
        ("NINE EIGHT SEVEN SIX", "9876"),
        ("Nine-Eight-Seven-Six-Five", "98765"),
        ("nine, eight, seven, six", "9876"),
        ("double nine eight seven triple six", "9987666"),
        ("double-nine double-eight", "9988"),
        ("triple zero five five", "000" + "55"),
        ("nine oh oh five", "9005"),
        ("nine eight oh two", "9802"),
        ("call nine eight seven six five four three two one zero now", "call 9876543210 now"),
        ("PAN ABCDE one two three four F", "PAN ABCDE 1234 F"),
    ],
)
def test_spelled_digits(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "one or two documents",
        "oh no, one more",
        "there is one of the two",
        "zero balance in one account",
        "the phone rang three times",
        "Rate is one two",
        "someone stoned the bone",
    ],
)
def test_ordinary_number_words_are_left_alone(text):
    assert normalize(text) == text


def test_normalize_is_idempotent():
    samples = [
        "９८७६ nine eight double seven ​6 ABCDE١٢٣٤F",
        "Salary slip for Mar 2026. Net pay ₹[50-75k]. PAN: [PAN_1].",
        "%39%38%37%36 &#57;",
        "प्रिया शर्मा 9876543210",
        "",
    ]
    for s in samples:
        once = normalize(s)
        assert normalize(once) == once


def test_ascii_text_is_returned_unchanged_when_nothing_applies():
    s = "Bank statement for Mar 2026, average balance in the 50-75k band."
    assert normalize(s) is s or normalize(s) == s


# ------------------------------------------------------------------------------------------------ compact_digits


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("98765 43210", "9876543210"),
        ("98765-43210", "9876543210"),
        ("98765.43210", "9876543210"),
        ("98765/43210", "9876543210"),
        ("98765_43210", "9876543210"),
        ("9 8 7 6 5 4 3 2 1 0", "9876543210"),
        ("9-8-7-6", "9876"),
        ("+91 98765 43210", "+919876543210"),
        ("2345 6789 0123", "234567890123"),
        ("98765\n43210", "9876543210"),
    ],
)
def test_compact_joins_single_separators(raw, expected):
    assert compact_digits(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "98765  43210",  # two spaces
        "98765 - 43210",  # separator run of three
        "98765, 43210",  # a comma is not a separator
        "50L-1Cr",  # letters between the digits
        "Rs 12 lakh",
        "abc 123 def",
    ],
)
def test_compact_leaves_everything_else_alone(raw):
    assert compact_digits(raw) == raw


def test_letters_break_a_chain():
    assert compact_digits("10-25k 25-50k") == "1025k 2550k"


def test_band_strings_never_form_long_runs():
    for band in ["50L-1Cr", "30-40", "<600", "750-799", "1-3y", "800+", "10-25k", "75k-1L", ">5Cr", "0.5-0.8", "1.25-1.5", "<5%", "60-89"]:
        digits = compact_digits(band)
        longest = max((len(r) for r in "".join(c if c.isdigit() else " " for c in digits).split()), default=0)
        assert longest <= 6, band


def test_decimal_fractions_do_not_fuse():
    text = "weights 0.30 0.30 0.15 0.10 0.15 sum to one"
    longest = max(len(r) for r in "".join(c if c.isdigit() else " " for c in compact_digits(text)).split())
    assert longest < 9


def test_compact_mapped_matches_compact_and_maps_offsets():
    text = "call +91 98765-43210 or 9 8 7 6 5 4 3 2 1 0 today"
    compact, index_map = compact_digits_mapped(text)
    assert compact == compact_digits(text)
    assert len(index_map) == len(compact) + 1 and index_map[-1] == len(text)
    start = compact.index("9876543210")
    end = start + 10
    assert text[index_map[start] : index_map[end - 1] + 1] == "98765-43210"


def test_compact_mapped_without_joins_is_identity():
    text = "nothing to join here 12"
    compact, index_map = compact_digits_mapped(text)
    assert compact == text and index_map == list(range(len(text))) + [len(text)]
