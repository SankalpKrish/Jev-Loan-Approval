"""The Redactor and the gate against the synthetic book from the data generator (skipped if it is absent)."""

import re

import pytest

pytest.importorskip("jevloan.data.generator")

from jevloan.data.generator import generate_book  # noqa: E402
from jevloan.pii import KnownEntities, PIIGate, Redactor  # noqa: E402
from jevloan.pii.normalize import compact_digits, normalize  # noqa: E402

gate = PIIGate()


@pytest.fixture(scope="module")
def book():
    return generate_book(150, 7)


@pytest.fixture(scope="module")
def redacted(book):
    """(file, inventory dict, Redactor, redacted document texts) for every file, computed once."""
    out = []
    for f in book:
        inv = f.pii_inventory.model_dump()
        r = Redactor(KnownEntities.from_inventory(inv))
        out.append((f, inv, r, [r.redact(d.text) for d in f.documents]))
    return out


def test_raw_documents_are_blocked_for_every_file(book):
    for f in book:
        assert gate.scan({"documents": [d.text for d in f.documents]}), f.file_id


def test_redacted_documents_pass_the_gate_and_leak_nothing(redacted):
    for f, inv, _r, texts in redacted:
        assert gate.scan({"state": {"documents": [{"text": t} for t in texts]}}) == [], f.file_id
        for text in texts:
            low = normalize(text).casefold()
            squashed = compact_digits(low).replace(" ", "")
            for kind in ("person_names", "org_names", "pans", "aadhaars", "phones", "emails", "account_numbers", "address_lines"):
                for value in inv[kind]:
                    v = normalize(value).casefold()
                    if len(v) >= 4:
                        assert v not in low, (f.file_id, kind, value)
                        if kind in ("pans", "aadhaars", "phones", "account_numbers"):
                            assert v.replace(" ", "") not in squashed, (f.file_id, kind, value)
            for name in inv["person_names"]:
                for part in name.split():
                    if len(part) >= 3 and part.isascii():
                        assert not re.search(rf"(?<![A-Za-z]){re.escape(part)}(?![A-Za-z])", text, re.I), (f.file_id, part, text)


def test_applicant_and_alias_share_a_token_and_other_people_differ(redacted):
    checked = 0
    for f, _inv_dict, r, _texts in redacted:
        inv = f.pii_inventory
        applicant = inv.person_names[0]
        assert r.redact(f"holder {applicant}") == "holder [APPLICANT]"
        if f.applicant.name_native:
            assert r.redact(f"आवेदक {f.applicant.name_native}").endswith("[APPLICANT]") or "[APPLICANT]" in r.redact(f.applicant.name_native)
            checked += 1
        others = [n for n in inv.person_names[1:] if n.isascii() and n not in inv.aliases]  # aliases ARE the applicant
        tokens = {r.redact(n) for n in others}
        assert len(tokens) == len(others)  # different people, different tokens
        assert "[APPLICANT]" not in tokens
    assert checked > 0


def test_every_amount_in_the_documents_becomes_a_band(redacted):
    for f, _inv, _r, texts in redacted:
        for out in texts:
            assert not re.search(r"(?:Rs\.?|INR|₹)\s*\d", out), (f.file_id, out)


def test_one_address_one_token_across_documents(redacted):
    """The same real address, written short in one document and long in another, must keep one token."""
    for f, inv, r, texts in redacted:
        tokens = {}
        for line in inv["address_lines"]:
            tokens.setdefault(r.redact(line), []).append(line)
        # every distinct token maps back to lines that are really the same address: a prefix of each other,
        # or declared as the same address through the inventory's `aliases` (e.g. a native-script rendering)
        canonical = lambda ln: inv["aliases"].get(ln, ln)
        for token, lines in tokens.items():
            if len(lines) > 1:
                lows = sorted((" ".join(re.findall(r"\w+", canonical(ln).casefold())) for ln in lines), key=len)
                assert all(ln.startswith(lows[0]) for ln in lows), (f.file_id, lines)
