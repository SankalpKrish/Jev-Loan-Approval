import random
import sys
import textwrap

import pytest

from jevloan.jev import sim_rules
from jevloan.jev.sim_rules import choice, get, level_probs, load_all, lookup, noisy_p, noul, score, sim_rule


@pytest.fixture
def registry(monkeypatch) -> dict:
    load_all()  # import the shipped rule modules first: a module imported while REGISTRY is patched would register into the patch
    fresh: dict = {}
    monkeypatch.setattr(sim_rules, "REGISTRY", fresh)
    return fresh


def rule_returning(tag: str):
    def rule(state, question, rng):
        return noul(0.5)

    rule.__qualname__ = f"rule_{tag}"
    return rule


# --- registry ---------------------------------------------------------------------------------------------


def test_exact_id_lookup(registry):
    @sim_rule("A_income_proof_current")
    def exact(state, question, rng):
        return noul(0.9)

    assert lookup("A_income_proof_current") is exact
    assert lookup("A_income_proof") is None


def test_glob_lookup(registry):
    @sim_rule("E_disclosure_*")
    def glob(state, question, rng):
        return noul(0.9)

    assert lookup("E_disclosure_apr") is glob
    assert lookup("E_disclosure_penal_charges") is glob
    assert lookup("E_memo_matches_grid") is None
    assert lookup("E_disclosures_complete") is None  # "E_disclosure_" needs the underscore


def test_exact_id_beats_a_matching_glob(registry):
    @sim_rule("E_disclosure_*")
    def glob(state, question, rng):
        return noul(0.9)

    @sim_rule("E_disclosure_apr")
    def exact(state, question, rng):
        return noul(0.1)

    assert lookup("E_disclosure_apr") is exact
    assert lookup("E_disclosure_penal_charges") is glob


def test_more_specific_glob_beats_less_specific(registry):
    @sim_rule("F_*")
    def broad(state, question, rng):
        return noul(0.9)

    @sim_rule("F_covenant_*")
    def narrow(state, question, rng):
        return noul(0.1)

    assert lookup("F_covenant_insurance_current") is narrow
    assert lookup("F_ews_dpd_rising") is broad


def test_registering_a_different_rule_under_a_taken_id_is_an_error(registry):
    sim_rule("X_q")(rule_returning("first"))
    with pytest.raises(ValueError, match="already registered"):
        sim_rule("X_q")(rule_returning("second"))


def test_registering_the_same_rule_again_is_fine(registry):
    first = rule_returning("same")
    sim_rule("X_q")(first)
    sim_rule("X_q")(first)
    assert lookup("X_q") is first


def test_decorator_returns_the_function_unchanged(registry):
    def rule(state, question, rng):
        return noul(0.3)

    assert sim_rule("X_q")(rule) is rule


def test_load_all_imports_every_rule_module(tmp_path, monkeypatch, registry):
    (tmp_path / "extra_rules.py").write_text(
        textwrap.dedent(
            """
            from jevloan.jev.sim_rules import noul, sim_rule

            @sim_rule("extra_*")
            def extra(state, question, rng):
                return noul(0.25)
            """
        )
    )
    monkeypatch.setattr(sim_rules, "__path__", [*sim_rules.__path__, str(tmp_path)])
    monkeypatch.delitem(sys.modules, "jevloan.jev.sim_rules.extra_rules", raising=False)
    try:
        load_all()
        assert lookup("extra_anything") is not None
    finally:
        sys.modules.pop("jevloan.jev.sim_rules.extra_rules", None)


def test_load_all_registers_the_shipped_hello_rules():
    load_all()
    load_all()  # idempotent
    for qid in ("hello_foir_within_limit", "hello_primary_weakness", "hello_repayment_conduct"):
        assert sim_rules.lookup(qid) is not None


def test_a_broken_rule_module_crashes_load_all(tmp_path, monkeypatch, registry):
    (tmp_path / "broken_rules.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.setattr(sim_rules, "__path__", [*sim_rules.__path__, str(tmp_path)])
    monkeypatch.delitem(sys.modules, "jevloan.jev.sim_rules.broken_rules", raising=False)
    with pytest.raises(RuntimeError, match="boom"):
        load_all()


# --- answer builders --------------------------------------------------------------------------------------


def test_noul_builder_clamps_and_has_the_api_shape():
    assert noul(0.93) == {"type": "noul", "noul": 0.93}
    assert noul(1.7)["noul"] == 1.0
    assert noul(-0.2)["noul"] == 0.0
    with pytest.raises(ValueError):
        noul(float("nan"))


def test_choice_builder_normalises_and_picks_the_argmax():
    answer = choice({"a": 1, "b": 6, "c": 3})
    assert answer["type"] == "choice"
    assert answer["choice"] == "b"
    assert answer["probabilities"] == {"a": 0.1, "b": 0.6, "c": 0.3}
    assert answer["confidence"] == pytest.approx((3 * 0.6 - 1) / 2)


def test_choice_confidence_is_zero_when_flat_and_one_when_certain():
    assert choice({"a": 1, "b": 1, "c": 1, "d": 1})["confidence"] == 0.0
    assert choice({"a": 1, "b": 0})["confidence"] == 1.0
    assert choice({"only": 1})["confidence"] == 1.0
    assert choice({"a": 1, "b": 1})["choice"] == "a"  # a tie goes to the first option


@pytest.mark.parametrize("bad", [{}, {"a": 0, "b": 0}, {"a": -1, "b": 2}, {"a": float("inf"), "b": 1}])
def test_choice_builder_rejects_unusable_weights(bad):
    with pytest.raises(ValueError):
        choice(bad)


def test_score_builder_gives_the_expected_level_and_a_legend():
    answer = score([0.1, 0.2, 0.7], legend=["low", "mid", "high"])
    assert answer["type"] == "score"
    assert answer["score"] == pytest.approx(0.2 + 1.4)
    assert answer["probabilities"] == {"0": 0.1, "1": 0.2, "2": 0.7}
    assert answer["legend"] == {"0": "low", "1": "mid", "2": "high"}
    assert answer["confidence"] == pytest.approx((3 * 0.7 - 1) / 2)


def test_score_builder_normalises_and_defaults_the_legend():
    answer = score([1, 1, 2])
    assert answer["probabilities"] == {"0": 0.25, "1": 0.25, "2": 0.5}
    assert answer["legend"] == {"0": "0", "1": "1", "2": "2"}


def test_score_builder_needs_two_levels():
    with pytest.raises(ValueError):
        score([1.0])


# --- rule helpers -----------------------------------------------------------------------------------------


def test_get_reads_dotted_paths_through_dicts_and_lists():
    state = {"bank": {"months_covered": 6}, "documents": [{"text": "first"}, {"text": "second"}]}
    assert get(state, "bank.months_covered") == 6
    assert get(state, "documents.1.text") == "second"
    assert get(state, "bank.missing") is None
    assert get(state, "bank.missing", 0) == 0
    assert get(state, "documents.5.text", "n/a") == "n/a"
    assert get(state, "bank.months_covered.deeper", "n/a") == "n/a"


def test_noisy_p_is_right_about_accuracy_of_the_time():
    rng = random.Random(1)
    n = 20_000
    signals = [rng.random() < 0.5 for _ in range(n)]
    hits = sum((noisy_p(signal, rng, accuracy=0.9) > 0.5) == signal for signal in signals)
    assert 0.88 < hits / n < 0.92


def test_noisy_p_is_roughly_calibrated():
    """Among answers that state a confidence near c, about a fraction c are on the right side."""
    rng = random.Random(2)
    n = 30_000
    bins: dict[int, list[tuple[float, bool]]] = {}
    for _ in range(n):
        signal = rng.random() < 0.5
        p = noisy_p(signal, rng, accuracy=0.9)
        stated = max(p, 1 - p)
        bins.setdefault(min(int(stated * 10), 9), []).append((stated, (p > 0.5) == signal))
    ece = 0.0
    for members in bins.values():
        mean_stated = sum(s for s, _ in members) / len(members)
        accuracy = sum(hit for _, hit in members) / len(members)
        ece += len(members) / n * abs(mean_stated - accuracy)
    assert ece < 0.03


def test_noisy_p_lands_on_the_signal_side_when_correct_and_leans_high():
    rng = random.Random(3)
    yes = [noisy_p(True, rng) for _ in range(2000)]
    no = [noisy_p(False, rng) for _ in range(2000)]
    assert sum(p > 0.5 for p in yes) > 1700
    assert sum(p < 0.5 for p in no) > 1700
    assert sum(yes) / len(yes) > 0.75 > sum(no) / len(no) + 0.4


def test_noisy_p_can_be_wrong_with_high_confidence():
    rng = random.Random(4)
    wrong = [p for p in (noisy_p(True, rng, accuracy=0.9) for _ in range(5000)) if p < 0.5]
    assert wrong and min(wrong) < 0.1  # a real model is sometimes confidently wrong


def test_noisy_p_perfect_accuracy_is_always_right_and_extreme():
    rng = random.Random(5)
    assert {noisy_p(True, rng, accuracy=1.0) for _ in range(50)} == {1.0}
    assert {noisy_p(False, rng, accuracy=1.0) for _ in range(50)} == {0.0}


def test_noisy_p_is_deterministic_per_rng_seed():
    assert [noisy_p(True, random.Random(7)) for _ in range(3)] == [noisy_p(True, random.Random(7)) for _ in range(3)]


def test_noisy_p_rejects_an_accuracy_below_a_coin_flip():
    with pytest.raises(ValueError):
        noisy_p(True, random.Random(0), accuracy=0.3)


def test_level_probs_is_a_distribution_peaked_near_the_true_level():
    rng = random.Random(6)
    n = 5000
    hits = 0
    for _ in range(n):
        probs = level_probs(2, 5, rng)
        assert sum(probs) == pytest.approx(1.0)
        assert len(probs) == 5
        hits += probs.index(max(probs)) == 2
    assert 0.83 < hits / n < 0.97


@pytest.mark.parametrize("true_level", [0, 4])
def test_level_probs_at_the_ends_of_the_scale(true_level):
    rng = random.Random(8)
    hits = sum(max(range(5), key=(p := level_probs(true_level, 5, rng)).__getitem__) == true_level for _ in range(2000))
    assert hits / 2000 > 0.85


def test_wider_spread_misses_more_and_is_less_confident():
    def hit_rate_and_confidence(spread: float) -> tuple[float, float]:
        rng = random.Random(9)
        answers = [score(level_probs(2, 5, rng, spread=spread)) for _ in range(3000)]
        hit = sum(max(a["probabilities"], key=a["probabilities"].get) == "2" for a in answers) / 3000
        return hit, sum(a["confidence"] for a in answers) / 3000

    tight_hit, tight_conf = hit_rate_and_confidence(0.3)
    wide_hit, wide_conf = hit_rate_and_confidence(1.0)
    assert tight_hit > wide_hit
    assert tight_conf > wide_conf


def test_level_probs_confidence_is_roughly_calibrated_at_the_default_spread():
    rng = random.Random(10)
    answers = [score(level_probs(2, 5, rng)) for _ in range(5000)]
    accuracy = sum(max(a["probabilities"], key=a["probabilities"].get) == "2" for a in answers) / 5000
    mean_confidence = sum(a["confidence"] for a in answers) / 5000
    assert abs(accuracy - mean_confidence) < 0.12


def test_default_answers_carry_no_information():
    assert sim_rules.default_answer({"type": "noul"}) == noul(0.5)
    flat = sim_rules.default_answer({"type": "choice", "criteria": {"a": None, "b": None, "c": None}})
    assert flat["confidence"] == 0.0
    assert set(flat["probabilities"]) == {"a", "b", "c"}
    mid = sim_rules.default_answer({"type": "score", "criteria": ["a", "b", "c", "d", "e"]})
    assert max(mid["probabilities"], key=mid["probabilities"].get) == "2"
    assert mid["score"] == pytest.approx(2.0, abs=1e-3)
