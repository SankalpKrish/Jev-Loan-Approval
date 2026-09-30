"""Engineered disparities (PLAN 3.6): about 10% of the book, labelled, and label-independent by construction."""

import re
from collections import Counter

from jevloan.data import risk
from test_data_common import ascii_digits, book_2000

SUBSETS = ("lang_doc_script", "gender_income_proxy", "pincode_bureau_thin")
SCRIPT_RANGE = {"tamil": r"[஀-௿]", "bengali": r"[ঀ-৿]", "devanagari": r"[ऀ-ॿ]"}


def members(name):
    return [f for f in book_2000() if f.meta.disparity_subset == name]


def segment_adjusted(name, value):
    """Rate of ``value`` among the subset vs the rate among non-members of the same segments, weighted by the
    subset's own segment mix."""
    sub = members(name)
    rest = [f for f in book_2000() if f.meta.disparity_subset is None]
    mix = Counter(f.segment for f in sub)
    expected = 0.0
    for seg, n in mix.items():
        pool = [f for f in rest if f.segment == seg]
        expected += n / len(sub) * sum(value(f) for f in pool) / len(pool)
    return sum(value(f) for f in sub) / len(sub), expected


def test_subsets_exist_and_total_about_ten_percent():
    counts = Counter(f.meta.disparity_subset for f in book_2000())
    for name in SUBSETS:
        assert counts[name] >= 40, counts
    share = sum(counts[n] for n in SUBSETS) / len(book_2000())
    assert 0.07 <= share <= 0.13
    assert set(counts) == set(SUBSETS) | {None}


def test_true_sanctionable_and_outcome_rates_match_the_rest_of_the_segment():
    for name in SUBSETS:
        got, expected = segment_adjusted(name, lambda f: f.labels.sanctionable)
        assert abs(got - expected) <= 0.15, (name, got, expected)
        got, expected = segment_adjusted(name, lambda f: f.labels.outcome_12m == "defaults")
        assert abs(got - expected) <= 0.08, (name, got, expected)
        got, expected = segment_adjusted(name, lambda f: f.labels.risk_pd)
        assert abs(got - expected) <= 0.025, (name, got, expected)
        got, expected = segment_adjusted(name, lambda f: bool(f.labels.missing_items))
        assert abs(got - expected) <= 0.12, (name, got, expected)


def test_lang_doc_script_gives_native_address_proofs_to_the_right_speakers():
    lang = members("lang_doc_script")
    assert {f.demographics.language for f in lang} >= {"ta", "bn"}
    script_of = {"ta": "tamil", "bn": "bengali", "hi": "devanagari", "mr": "devanagari"}
    for f in lang:
        assert f.demographics.language in script_of
        addr = [d for d in f.documents if d.doc_type.startswith("address_proof")]
        assert len(addr) == 1 and addr[0].script == script_of[f.demographics.language]
        assert addr[0].language == f.demographics.language
        assert re.search(SCRIPT_RANGE[addr[0].script], addr[0].text)
        assert f.applicant.name_native and f.applicant.name_native in addr[0].text
        assert f.applicant.name_native in f.pii_inventory.person_names
        assert sum(d.script != "latin" for d in f.documents) == 1  # only the address proof is in native script
    for f in book_2000():
        if f.meta.disparity_subset != "lang_doc_script":
            assert all(d.script == "latin" and d.language == "en" for d in f.documents)
            assert f.applicant.name_native is None
    ta_bn = [f for f in book_2000() if f.demographics.language in ("ta", "bn")]
    assert sum(f.meta.disparity_subset == "lang_doc_script" for f in ta_bn) / len(ta_bn) > 0.10
    # a native-script file's address proof is judged on its content, not its script
    valid = sum("address_proof" not in f.labels.missing_items for f in lang) / len(lang)
    assert valid > 0.85


def test_native_documents_carry_valid_addresses_or_a_real_defect():
    for f in members("lang_doc_script"):
        d = next(d for d in f.documents if d.doc_type.startswith("address_proof"))
        text = ascii_digits(d.text)
        has_pin = f.applicant.address.pincode in text
        if "address_proof" not in f.labels.missing_items:
            assert has_pin
            assert d.month_age < 3 or d.doc_type == "address_proof_rent_agreement"


def test_gender_income_proxy_is_informal_income_documentation_for_self_employed_women():
    sub = members("gender_income_proxy")
    for f in sub:
        assert f.demographics.gender == "F" and f.segment == "self_employed"
        assert f.income.documentation_type == "informal_declared"
        itr = next(d for d in f.documents if d.doc_type == "itr")
        assert "Self-declaration of income" in itr.text
    women = [f for f in book_2000() if f.segment == "self_employed" and f.demographics.gender == "F"]
    men = [f for f in book_2000() if f.segment == "self_employed" and f.demographics.gender == "M"]
    assert sum(f.income.documentation_type == "informal_declared" for f in women) / len(women) > 0.5
    assert sum(f.income.documentation_type == "informal_declared" for f in men) / len(men) < 0.10
    # informal documentation never enters a label: switching it changes nothing
    for f in sub[:20]:
        g = f.model_copy(deep=True)
        g.income.documentation_type = "itr"
        g.meta.disparity_subset = None
        assert risk.synthetic_credit_decision(g) == risk.synthetic_credit_decision(f)
        assert risk.pd_for_file(g) == risk.pd_for_file(f)
        assert risk.primary_weakness(g) == risk.primary_weakness(f) and risk.closeness_level(g) == risk.closeness_level(f)
    assert all(f.labels.sanctionable == risk.synthetic_credit_decision(f)[0] for f in sub)


def test_pincode_bureau_thin_hides_the_score_but_not_the_labels():
    sub = members("pincode_bureau_thin")
    hidden = 0
    for f in sub:
        assert f.demographics.pincode_cluster == "PC7" and f.bureau.score is None and f.bureau.history_months <= 5
        if f.meta.true_bureau_score is not None:
            hidden += 1
            assert 300 <= f.meta.true_bureau_score <= 900
            assert risk.effective_bureau_score(f) == f.meta.true_bureau_score
            # undoing the engineering leaves every label exactly as it was
            g = f.model_copy(deep=True)
            g.bureau.score, g.meta.disparity_subset, g.meta.true_bureau_score = f.meta.true_bureau_score, None, None
            assert risk.synthetic_credit_decision(g) == risk.synthetic_credit_decision(f)
            assert risk.pd_for_file(g) == risk.pd_for_file(f)
            assert risk.primary_weakness(g) == risk.primary_weakness(f) and risk.closeness_level(g) == risk.closeness_level(f)
    assert hidden / len(sub) > 0.8  # most members had a real score that was hidden
    assert all(f.meta.true_bureau_score is None for f in book_2000() if f.meta.disparity_subset != "pincode_bureau_thin")


def test_pc7_has_an_inflated_ntc_rate_but_the_same_true_risk():
    book = book_2000()
    pc7 = [f for f in book if f.demographics.pincode_cluster == "PC7"]
    rest = [f for f in book if f.demographics.pincode_cluster != "PC7"]
    ntc = lambda fs: sum(f.bureau.score is None for f in fs) / len(fs)  # noqa: E731
    assert ntc(pc7) > 2.5 * ntc(rest)
    true_ntc = lambda fs: sum(risk.effective_bureau_score(f) is None for f in fs) / len(fs)  # noqa: E731
    assert abs(true_ntc(pc7) - true_ntc(rest)) < 0.06
    assert abs(sum(f.labels.sanctionable for f in pc7) / len(pc7) - sum(f.labels.sanctionable for f in rest) / len(rest)) < 0.10
    assert abs(sum(f.labels.risk_pd for f in pc7) / len(pc7) - sum(f.labels.risk_pd for f in rest) / len(rest)) < 0.02


def test_demographics_are_independent_of_the_labels_outside_the_engineered_subsets():
    book = book_2000()
    for attr in ("gender", "language"):
        groups = {}
        for f in book:
            groups.setdefault(getattr(f.demographics, attr), []).append(f)
        overall = sum(f.labels.sanctionable for f in book) / len(book)
        for g, fs in groups.items():
            if len(fs) >= 150:
                assert abs(sum(f.labels.sanctionable for f in fs) / len(fs) - overall) < 0.09, (attr, g)
    for f in book:
        assert f.demographics.gender == f.applicant.gender
        assert f.demographics.language == f.applicant.preferred_language
