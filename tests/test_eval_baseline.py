import pytest

from jevloan.data.generator import generate_book
from jevloan.modules.base import catalog, questions_for
from jevloan.state import STAGES, build_state
from jevloan.eval.baseline import rule_answers


def test_rules_baseline_answers_every_catalogue_question_across_fifty_files():
    definitions = catalog()
    seen: set[str] = set()
    examples = []
    for file in generate_book(50, seed=38):
        for stage in STAGES:
            state = build_state(file, stage)
            questions = questions_for(stage, file.segment, state)
            answers = rule_answers(state, questions)
            assert set(answers) == set(questions)
            seen.update(questions)
            examples.append((state, questions, answers))
            for qid, answer in answers.items():
                assert answer["type"] == definitions[qid].qtype
                if answer["type"] == "noul":
                    assert answer["noul"] in (0.0, 1.0)
                    assert answer["derived_confidence"] == 1.0
                elif answer["type"] == "score":
                    probabilities = answer["probabilities"]
                    assert answer["score"] == int(answer["score"])
                    assert 0 <= answer["score"] < len(probabilities)
                    assert sum(probabilities.values()) == 1.0
                    assert probabilities[str(answer["score"])] == 1.0
                    assert answer["confidence"] == 1.0

    assert seen == set(definitions)
    # The baseline is driven only by the supplied state; label-like fields cannot change its answers.
    state, questions, answers = examples[0]
    relabeled_state = {**state, "labels": {"fraud": not state.get("labels", {}).get("fraud", False)}}
    assert rule_answers(relabeled_state, questions) == answers


def test_rules_baseline_rejects_unknown_qids_and_type_mismatches_explicitly():
    with pytest.raises(ValueError, match="no deterministic rules-only baseline"):
        rule_answers({}, {"X_unregistered": {"type": "noul"}})

    file = generate_book(1, seed=9)[0]
    state = build_state(file, "appraisal")
    questions = questions_for("appraisal", file.segment, state)
    qid = next(iter(questions))
    wrong_type = {qid: {**questions[qid], "type": "choice"}}
    with pytest.raises(ValueError, match="question wire type does not match catalogue"):
        rule_answers(state, wrong_type)


def test_rules_baseline_rejects_questions_without_deterministic_evidence():
    with pytest.raises(ValueError, match="gst ratio band is missing"):
        rule_answers({"gst": {}}, {"B_gst_bank_consistent": {"type": "noul"}})
    with pytest.raises(ValueError, match="statement coverage or required months is missing"):
        rule_answers({"bank": {}}, {"A_statements_cover_months": {"type": "noul"}})
