import importlib
import importlib.util

import pytest

from jevloan.modules import base
from jevloan.modules.base import (
    ALL_SEGMENTS,
    STAGES,
    QuestionDef,
    choice_wire,
    noul_wire,
    questions_for,
    register,
    score_wire,
    validate_wire,
)

REF = ["`bank.months_covered`"]


def good_noul(**changes) -> dict:
    args = dict(
        refer_to=REF,
        true_what="months_covered is at least months_required",
        true_examples=["6 with 6", "12 with 6"],
        false_what="months_covered is below months_required",
        false_examples=["5 with 6", "9 with 12"],
        false_not_for="equal numbers",
        focus="Compare only these two.",
    )
    args.update(changes)
    return noul_wire("Is it enough?", **args)


def level(name: str, n: int = 2, **changes) -> dict:
    spec = {"level": name, "what": f"what {name}", "not_for": f"not {name}", "examples": [f"ex {i}" for i in range(n)]}
    spec.update(changes)
    return spec


def make_def(qid="A_test_one", **changes) -> QuestionDef:
    fields = dict(
        qid=qid,
        module="A",
        stage="appraisal",
        qtype="noul",
        segments=frozenset({"salaried_personal"}),
        risk_polarity="yes_is_good",
        routing_relevant=True,
        reason_text="a reason",
        wire=good_noul(),
    )
    fields.update(changes)
    return QuestionDef(**fields)


@pytest.fixture
def registry(monkeypatch) -> dict:
    """An empty registry for the test. The packs are loaded first: a module imported while REGISTRY is patched would
    register into the patch and never into the real registry."""
    base.load_all()
    fresh: dict = {}
    monkeypatch.setattr(base, "REGISTRY", fresh)
    return fresh


# --- constants ---------------------------------------------------------------------------------------------


def test_constants():
    assert ALL_SEGMENTS == {"salaried_personal", "self_employed", "msme_business", "secured_home"}
    assert isinstance(ALL_SEGMENTS, frozenset)
    assert STAGES == ("appraisal", "sanction_docs", "monitoring")


def test_all_questions_is_the_loaded_registry():
    catalog = base.ALL_QUESTIONS  # resolved on first access, so it is never an empty dict by accident
    assert catalog is base.REGISTRY
    assert "A_income_proof_current" in catalog and "C_willingness" in catalog
    from jevloan.modules.base import ALL_QUESTIONS

    assert ALL_QUESTIONS is catalog
    with pytest.raises(AttributeError):
        base.NO_SUCH_THING


# --- QuestionDef -------------------------------------------------------------------------------------------


def test_a_valid_definition_is_accepted():
    q = make_def()
    assert q.qid == "A_test_one" and q.segments == frozenset({"salaried_personal"})


def test_segments_are_coerced_to_a_frozenset():
    q = make_def(segments={"salaried_personal", "secured_home"})
    assert isinstance(q.segments, frozenset)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"module": "Z"}, "module"),
        ({"qid": "B_test_one"}, "must start with 'A_'"),
        ({"stage": "closing"}, "stage"),
        ({"qtype": "text"}, "qtype"),
        ({"risk_polarity": "sideways"}, "risk_polarity"),
        ({"segments": frozenset()}, "segments"),
        ({"segments": frozenset({"pawn_shop"})}, "segments"),
        ({"reason_text": "  "}, "reason_text"),
        ({"wire": None}, "exactly one of wire and build"),
        ({"build": lambda state: good_noul()}, "exactly one of wire and build"),
        ({"qtype": "score"}, "does not match qtype"),
    ],
)
def test_an_invalid_definition_is_refused(changes, match):
    with pytest.raises(ValueError, match=match):
        make_def(**changes)


def test_to_wire_gives_a_fresh_copy():
    q = make_def()
    wire = q.to_wire({})
    wire["criteria"]["true"]["what"] = "changed"
    wire["instructions"]["refer_to"].append("`x`")
    assert q.to_wire({}) == good_noul()


def test_to_wire_uses_build_with_the_state():
    def build(state: dict) -> dict:
        return good_noul(true_what=f"months_covered is at least {state['n']}")

    q = make_def(wire=None, build=build)
    assert "at least 7" in q.to_wire({"n": 7})["criteria"]["true"]["what"]
    assert "at least 9" in q.to_wire({"n": 9})["criteria"]["true"]["what"]


def test_to_wire_checks_the_built_type():
    q = make_def(wire=None, build=lambda state: score_wire("How?", refer_to=REF, levels=[level("a"), level("b")]))
    with pytest.raises(ValueError, match="does not match qtype"):
        q.to_wire({})


def test_is_applicable_needs_the_segment_and_the_state_test():
    q = make_def(segments={"salaried_personal", "secured_home"}, applies=lambda s: s.get("employment") == "salaried")
    assert q.is_applicable("salaried_personal", {"employment": "salaried"})
    assert not q.is_applicable("salaried_personal", {"employment": "self_employed"})
    assert not q.is_applicable("msme_business", {"employment": "salaried"})
    assert make_def().is_applicable("salaried_personal", {})
    assert not make_def().is_applicable("self_employed", {})


def test_definitions_are_frozen():
    q = make_def()
    with pytest.raises(AttributeError):
        q.qid = "A_other"  # type: ignore[misc]


# --- registry ----------------------------------------------------------------------------------------------


def test_register_returns_the_definition_and_refuses_duplicates(registry):
    q = make_def()
    assert register(q) is q
    assert registry == {"A_test_one": q}
    with pytest.raises(ValueError, match="already registered"):
        register(make_def())


def test_catalog_loads_every_pack_and_returns_a_copy():
    catalog = base.catalog()
    assert {"A", "B", "C"} <= {q.module for q in catalog.values()}
    catalog.pop("A_income_proof_current")
    assert "A_income_proof_current" in base.catalog()


def test_load_all_skips_a_module_that_does_not_exist_yet(monkeypatch):
    monkeypatch.setattr(base, "_PACK_MODULES", (*base._PACK_MODULES, "zz_not_written_yet"))
    base.load_all()  # no error


def test_load_all_lets_an_import_error_inside_a_pack_propagate(monkeypatch):
    real = importlib.import_module

    def broken(name, *args, **kwargs):
        if name.endswith(".b_fraud"):
            raise ImportError("broken pack")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(base.importlib, "import_module", broken)
    with pytest.raises(ImportError, match="broken pack"):
        base.load_all()


def test_questions_for_filters_by_stage_segment_and_state(registry):
    register(make_def("A_zeta"))
    register(make_def("A_alpha"))
    register(make_def("B_beta", module="B", segments={"salaried_personal", "secured_home"}, applies=lambda s: s.get("ok")))
    register(make_def("D_later", module="D", stage="sanction_docs"))
    register(make_def("A_other_segment", segments={"msme_business"}))

    # deterministic order (module, then qid), whatever order they were registered in
    assert list(questions_for("appraisal", "salaried_personal", {"ok": True})) == ["A_alpha", "A_zeta", "B_beta"]
    assert list(questions_for("appraisal", "salaried_personal", {"ok": False})) == ["A_alpha", "A_zeta"]
    assert list(questions_for("appraisal", "msme_business", {})) == ["A_other_segment"]
    assert list(questions_for("sanction_docs", "salaried_personal", {})) == ["D_later"]
    assert questions_for("monitoring", "salaried_personal", {}) == {}
    wires = questions_for("appraisal", "salaried_personal", {})
    assert wires["A_alpha"] == good_noul()


# --- wire builders and validators --------------------------------------------------------------------------


def test_noul_wire_has_the_documented_shape():
    wire = good_noul(data={"order": ["a", "b"]})
    assert wire == {
        "type": "noul",
        "instructions": {"question": "Is it enough?", "focus": "Compare only these two.", "refer_to": REF, "data": {"order": ["a", "b"]}},
        "criteria": {
            "true": {"what": "months_covered is at least months_required", "examples": ["6 with 6", "12 with 6"]},
            "false": {"what": "months_covered is below months_required", "not_for": "equal numbers", "examples": ["5 with 6", "9 with 12"]},
        },
    }


def test_optional_parts_are_left_out_when_not_given():
    wire = good_noul(focus=None, false_not_for=None)
    assert "focus" not in wire["instructions"] and "data" not in wire["instructions"]
    assert "not_for" not in wire["criteria"]["false"]


def test_score_wire_and_choice_wire_shapes():
    scored = score_wire("How clean?", refer_to=REF, levels=[level("bad"), level("ok", 3), level("good")], focus="Only these.")
    assert [entry["level"] for entry in scored["criteria"]] == ["bad", "ok", "good"]
    assert scored["type"] == "score" and validate_wire(scored) == []
    chosen = choice_wire(
        "Which one?",
        refer_to=REF,
        options={"a": {"what": "first", "not_for": "second", "examples": ["x", "y"]}, "b": {"what": "second", "examples": ["p", "q"]}},
    )
    assert chosen["type"] == "choice" and list(chosen["criteria"]) == ["a", "b"] and validate_wire(chosen) == []
    assert "not_for" not in chosen["criteria"]["b"]


@pytest.mark.parametrize("examples", [[], ["only one"], ["a", "b", "c", "d"]])
def test_the_number_of_examples_is_2_or_3(examples):
    with pytest.raises(ValueError, match="examples needs 2 to 3"):
        good_noul(true_examples=examples)
    with pytest.raises(ValueError, match="examples needs 2 to 3"):
        good_noul(false_examples=examples)
    with pytest.raises(ValueError, match="examples needs 2 to 3"):
        score_wire("How?", refer_to=REF, levels=[level("a"), {**level("b"), "examples": examples}])


def test_examples_must_be_non_empty_strings():
    with pytest.raises(ValueError, match="non-empty strings"):
        good_noul(true_examples=["fine", " "])


@pytest.mark.parametrize("what", ["", "   ", None])
def test_what_must_not_be_empty(what):
    with pytest.raises(ValueError, match="what"):
        good_noul(true_what=what)
    with pytest.raises(ValueError, match="what"):
        good_noul(false_what=what)
    with pytest.raises(ValueError, match="what"):
        score_wire("How?", refer_to=REF, levels=[level("a"), level("b", what=what)])


def test_the_question_must_not_be_empty():
    with pytest.raises(ValueError, match="question"):
        noul_wire("", refer_to=REF, true_what="a", true_examples=["1", "2"], false_what="b", false_examples=["1", "2"])


@pytest.mark.parametrize(
    "entry",
    ["bank.months_covered", "`bank.months_covered", "`bank. months`", "`bank..x`", "`1bank`", "`bank.months_covered` and more", "", "``"],
)
def test_refer_to_entries_must_be_backticked_state_paths(entry):
    with pytest.raises(ValueError, match="refer_to"):
        good_noul(refer_to=[entry])


@pytest.mark.parametrize("entry", ["`documents`", "`bank.months_covered`", "`documents.0.text`", "`entity_roles.applicant.residence_address`", "`documents[0].text`"])
def test_valid_refer_to_entries_are_accepted(entry):
    assert good_noul(refer_to=[entry])["instructions"]["refer_to"] == [entry]


def test_refer_to_must_not_be_empty():
    with pytest.raises(ValueError, match="refer_to"):
        good_noul(refer_to=[])


def test_score_levels_need_names_and_at_least_two_levels():
    with pytest.raises(ValueError, match="2 to 10 levels"):
        score_wire("How?", refer_to=REF, levels=[level("only")])
    with pytest.raises(ValueError, match="distinct"):
        score_wire("How?", refer_to=REF, levels=[level("same"), level("same")])
    with pytest.raises(ValueError, match="level"):
        score_wire("How?", refer_to=REF, levels=[level("a"), level("", what="x")])


def test_choice_needs_two_options():
    with pytest.raises(ValueError, match="at least 2 options"):
        choice_wire("Which?", refer_to=REF, options={"a": {"what": "w", "examples": ["1", "2"]}})


def test_validate_wire_reports_every_problem_on_a_hand_built_dict():
    problems = validate_wire({"type": "noul", "instructions": {"question": "", "refer_to": ["x"]}, "criteria": {"true": {"what": "", "examples": ["one"]}, "false": {}}})
    text = " ".join(problems)
    for expected in ("instructions.question", "refer_to", "criteria.true.what", "criteria.true.examples", "criteria.false.examples"):
        assert expected in text
    assert validate_wire({"type": "essay"}) and validate_wire("nope")
