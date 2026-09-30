"""Regression contracts for missing replay evidence, calibration and fairness reporting."""

from collections import defaultdict
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from jevloan.data.generator import generate_book
from jevloan.eval.core import (
    _calibration,
    _decision_metrics,
    _parity,
    _prediction,
    _recommendation,
    _render_memo,
    _state_feature_map,
)
from jevloan.modules import base as question_base
from jevloan.state import build_state


def test_missing_audit_evidence_cannot_be_a_real_backend_go():
    stops = {f"S{i}": True for i in range(1, 14)}
    stops["S2"] = None  # no full-book outbound scan artifact
    stops["S3"] = None  # no audit chain artifact
    recommendation, _ = _recommendation("real", stops, [])
    assert recommendation != "GO"


def test_missing_module_prediction_is_not_scored_as_an_accurate_negative():
    file = generate_book(1, 92)[0]
    file.labels.sanctionable = False
    metrics, _ = _decision_metrics([file], defaultdict(list), {}, {}, {})
    c = next(row for row in metrics if row["module"] == "C")
    assert c.get("prediction_coverage") == 0
    assert c["system"] == 0


def test_top_label_confident_wrong_excludes_low_confidence_correct_scores():
    files = generate_book(2, 44)
    states = {(f.file_id, stage): build_state(f, stage) for f in files for stage in ("appraisal", "sanction_docs", "monitoring")}
    wires = {(f.file_id, stage): question_base.questions_for(stage, f.segment, states[(f.file_id, stage)])
             for f in files for stage in ("appraisal", "sanction_docs", "monitoring")}
    rows = defaultdict(list)
    for index, f in enumerate(files):
        truth = f.labels.question_truth["C_capacity"]
        predicted = int(truth) if index == 0 else (int(truth) + 1) % 5
        rows[f.file_id].append({"stage": "appraisal", "answers": {"C_capacity": {
            "type": "score", "score": str(predicted), "probabilities": {str(predicted): 1.0},
            "confidence": .05 if index == 0 else .95,
        }}})
    calibration = _calibration(files, rows, wires, {})
    score = next(row for row in calibration if row["qid"] == "C_capacity")
    assert score["confident_wrong_count"] == 1
    assert score["confident_wrong_rate"] == .5


def test_probe_features_do_not_include_labels_or_demographic_blocks():
    f = generate_book(1, 7)[0]
    state = build_state(f, "appraisal")
    flattened = _state_feature_map(state)
    # The probe can reconstruct attributes only from state-visible features, never hidden truth/demographic payloads.
    assert all("labels" not in key and "demographics" not in key and "pii_inventory" not in key for key in flattened)


def test_parity_question_ece_gap_is_measured_against_overall(monkeypatch):
    files = []
    for gender in ("F", "M"):
        files.append(SimpleNamespace(file_id=gender, demographics=SimpleNamespace(gender=gender, pincode_cluster="PC1", language="en"),
                                     labels=SimpleNamespace(sanctionable=True), meta=SimpleNamespace(disparity_subset=None)))
    def ece_for_group(members, rows):
        if len(members) == 2:
            return {"Q": .15}
        return {"Q": .2 if members[0].demographics.gender == "F" else .1}
    monkeypatch.setattr("jevloan.eval.core._group_calibration", ece_for_group)
    thresholds = {"min_group_n": 1, "protected_attributes": {
        "gender": {"reference_group": "M"}, "pincode_cluster": {"reference_group": "PC1"},
        "language": {"reference_group": "en"},
    }}
    rows = _parity(files, defaultdict(list), thresholds)
    female = next(r for r in rows if r["attribute"] == "gender" and r["group"] == "F" and r["scope"] == "all")
    assert female["routing_question_ece_gap"]["Q"] == pytest.approx(.05)


def test_stage_e_outcome_overrides_uncertain_or_passing_module_for_blocking_decisions():
    def row(outcome, module_outcome, failure_kind=None):
        return {"failure_kind": failure_kind, "decision": {"outcome": outcome, "modules": {"E": {"outcome": module_outcome}}}}

    assert _prediction("E", row("DISBURSAL_BLOCKED", "uncertain")) is True
    # The deterministic APR check may block disbursal even while the model's E module passes.
    assert _prediction("E", row("DISBURSAL_BLOCKED", "pass")) is True
    assert _prediction("E", row("DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF", "pass")) is False
    assert _prediction("E", row("DISBURSAL_BLOCKED", "fail", "timeout")) is None


def test_outbound_pii_audit_ignores_static_name_word_collision_but_catches_leaks():
    from jevloan.eval.core import _outbound_inventory_hits

    file = generate_book(1, 93)[0]
    file.pii_inventory.person_names = ["Clear Exampleperson"]
    state = {}
    assert not _outbound_inventory_hits(file, state, {"Q": {"instructions": {"question": "Clear this checklist"}}})
    assert _outbound_inventory_hits(file, {"text": "Exampleperson"}, {})
    assert _outbound_inventory_hits(file, {}, {"Q": {"instructions": {"question": "Confirm Clear Exampleperson"}}})
    pan = file.pii_inventory.pans[0]
    assert _outbound_inventory_hits(file, {}, {"Q": {"instructions": {"question": f"Review PAN {pan}"}}})


def test_outbound_pii_audit_ignores_exact_static_rubric_pincode_but_catches_added_pan_text():
    from jevloan.eval.core import _outbound_inventory_hits

    file = generate_book(1, 94)[0]
    file.pii_inventory.pincodes = ["600649"]
    static = deepcopy(question_base.catalog()["D_closeness"].wire)
    assert "600-649" in json.dumps(static)
    assert not _outbound_inventory_hits(file, {}, {"D_closeness": static})

    pan = file.pii_inventory.pans[0]
    added_text = deepcopy(static)
    added_text["instructions"]["question"] += f" PAN {pan}"
    assert _outbound_inventory_hits(file, {}, {"D_closeness": added_text})

    appended_property = deepcopy(static)
    appended_property["injected_review_value"] = pan
    assert _outbound_inventory_hits(file, {}, {"D_closeness": appended_property})


def test_memo_renderer_preserves_explanatory_placeholder_and_rejects_missing_metrics():
    rendered = _render_memo("Literal {{placeholder}}; run {{run_id}}", {"run_id": "R-17"})
    assert rendered == "Literal {{placeholder}}; run R-17"
    with pytest.raises(ValueError, match="missing_metric"):
        _render_memo("Run {{run_id}}; measured {{missing_metric}}", {"run_id": "R-17"})
