"""The fast paths in detectors.py (windowed PAN, anchored IFSC, candidate PIN) must agree with plain regexes."""

import random
import re

from jevloan.pii import detectors as D

PAN_REFERENCE = [
    (re.compile(r"(?<![A-Za-z])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z])"), None),
    (re.compile(r"(?<=[A-Za-z])[A-Za-z]{3}[PCHFATBLJGpchfatbljg][A-Za-z][0-9]{4}[A-Za-z](?![A-Za-z])"), None),
    (D._PAN_SEP_RE, D._pan_sep_ok),
]
IFSC_REFERENCE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{4}[ \-]?0[A-Za-z0-9]{6}(?![A-Za-z0-9])")

PIECES = [
    "ABCDE", "abcde", "A", "B", "x", "PAN", "PANABC", "PK", "1234", "2026", "12", "3", "0", "560034", "560 034", "0001234", " ", " ", "-", ".", "/",
    "_", "F", "Z", "1Z5", "29", "Bengaluru", "Delhi", "PIN", "Pincode", ":", ",", "SBIN", "HDFC", "March", "April", "Mar", "the", "of", "7", "9",
]


def random_texts(n: int, seed: int):
    rng = random.Random(seed)
    for _ in range(n):
        yield "".join(rng.choice(PIECES) for _ in range(rng.randint(2, 14)))


def reference_pan(text: str) -> set[tuple[int, int]]:
    found: set[tuple[int, int]] = set()
    for rx, validate in PAN_REFERENCE:
        for m in rx.finditer(text):
            if validate is not None and not validate(m.group(0), m):
                continue
            found.add(m.span())
    return found


def covered(spans) -> set[int]:
    return {i for a, b in spans for i in range(a, b)}


def test_fast_pan_agrees_with_plain_regexes_on_random_text():
    for text in random_texts(6000, 1):
        fast = {f.span for f in D.detect_pan(text)}
        ref = reference_pan(text)
        # a separated and a plain variant may report overlapping spans of the same PAN, so compare coverage
        assert covered(fast) == covered(ref), (text, sorted(fast), sorted(ref))


def test_fast_ifsc_agrees_with_the_regex_on_random_text():
    for text in random_texts(6000, 2):
        fast = {f.span for f in D.detect_ifsc(text)}
        ref = {m.span() for m in IFSC_REFERENCE.finditer(text) if any(ch.isdigit() for ch in re.sub(r"[ \-]", "", m.group(0))[5:])}
        assert fast == ref, (text, fast, ref)


def test_candidate_pin_agrees_with_full_regexes_on_random_text():
    ctx = re.compile(
        r"(?<![A-Za-z])(?:pin[\s\-]?code|pincode|pin|postal[\s\-]?code|post[\s\-]?code|zip(?:[\s\-]?code)?)[ \t]*(?:no\.?|number|#)?[ \t]*[:=\-]?[ \t]*(?P<pin>[1-9][0-9]{2}[ ]?[0-9]{3})(?![0-9])",
        re.IGNORECASE,
    )
    place = re.compile(
        rf"(?<![A-Za-z])(?:{D._PLACE_ALT})[ \t]*[,\-–]?[ \t]*(?:pin(?:code)?[ \t]*:?[ \t]*)?(?P<pin>[1-9][0-9]{{2}}[ ]?[0-9]{{3}})(?![0-9])", re.IGNORECASE
    )
    for text in random_texts(6000, 3):
        fast = {f.span for f in D.detect_pincode_ctx(text)}
        ref = {m.span("pin") for rx in (ctx, place) for m in rx.finditer(text)}
        assert ref <= fast, (text, fast, ref)  # the fast path is never weaker than the plain regexes
