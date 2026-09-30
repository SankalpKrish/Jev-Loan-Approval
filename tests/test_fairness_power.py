"""The state-only proxy probe must have power against an engineered state proxy."""

import numpy as np
from types import SimpleNamespace

from jevloan.eval.core import _disparity_detection
from jevloan.eval.metrics import logistic_cv_auc


def test_probe_detects_reconstructable_protected_attribute():
    rng = np.random.default_rng(123)
    labels = np.array(["F" if i % 2 else "M" for i in range(300)])
    # Proxy effect resembles the documented gender_income_proxy subset: informal documentation exposes the label.
    feature = np.array([1.0 if label == "F" else 0.0 for label in labels])
    x = np.column_stack([feature, rng.normal(size=len(labels))])
    assert logistic_cv_auc(x, labels, folds=5, seed=71) > .95


def test_engineered_disparity_signals_are_detected_and_empty_subset_is_not_claimed():
    files = [
        SimpleNamespace(meta=SimpleNamespace(disparity_subset="lang_doc_script"), demographics=SimpleNamespace(language="ta")),
        SimpleNamespace(meta=SimpleNamespace(disparity_subset="gender_income_proxy"), demographics=SimpleNamespace(gender="F")),
        SimpleNamespace(meta=SimpleNamespace(disparity_subset="pincode_bureau_thin"), demographics=SimpleNamespace(pincode_cluster="PC7")),
    ]
    parity = []
    for attr, group in (("language", "ta"), ("gender", "F"), ("pincode_cluster", "PC7")):
        parity.extend([
            {"attribute": attr, "group": group, "reference": "ref", "scope": "all", "status": "MEASURED",
             "proceed_rate": .30, "proceed_rate_ratio": .70, "equal_opportunity_proceed_rate": .35, "equal_opportunity_gap": .10},
            {"attribute": attr, "group": group, "reference": "ref", "scope": "without_disparity_subset", "status": "MEASURED",
             "proceed_rate": .85, "proceed_rate_ratio": .95, "equal_opportunity_proceed_rate": .86, "equal_opportunity_gap": .02},
        ])
    probes = [{"attribute": attr, "auc_macro_ovr": .61, "threshold": .60}
              for attr in ("language", "gender", "pincode_cluster")]
    detected = _disparity_detection(files, parity, probes)
    assert {row["subset"]: row["detected"] for row in detected} == {
        "lang_doc_script": True, "gender_income_proxy": True, "pincode_bureau_thin": True,
    }

    undetected = _disparity_detection([], [], [])
    assert all(row["n"] == 0 and row["status"] == "UNDETECTED" for row in undetected)


def test_small_or_unrelated_parity_breach_does_not_count_as_disparity_detection():
    files = [SimpleNamespace(meta=SimpleNamespace(disparity_subset="pincode_bureau_thin"),
                             demographics=SimpleNamespace(pincode_cluster="PC7"))]
    parity = [
        {"attribute": "pincode_cluster", "group": "PC7", "reference": "PC1", "scope": "all", "status": "INSUFFICIENT_N",
         "proceed_rate": .1, "proceed_rate_ratio": .2, "equal_opportunity_proceed_rate": .1, "equal_opportunity_gap": .8},
        {"attribute": "pincode_cluster", "group": "PC7", "reference": "PC1", "scope": "without_disparity_subset", "status": "INSUFFICIENT_N",
         "proceed_rate": .9, "proceed_rate_ratio": .9, "equal_opportunity_proceed_rate": .9, "equal_opportunity_gap": .01},
        # A different cluster's measured breach cannot establish power for the engineered PC7 subset.
        {"attribute": "pincode_cluster", "group": "PC6", "reference": "PC1", "scope": "all", "status": "FAIL",
         "proceed_rate": .1, "proceed_rate_ratio": .2, "equal_opportunity_proceed_rate": .1, "equal_opportunity_gap": .8},
        {"attribute": "pincode_cluster", "group": "PC6", "reference": "PC1", "scope": "without_disparity_subset", "status": "PASS",
         "proceed_rate": .9, "proceed_rate_ratio": .9, "equal_opportunity_proceed_rate": .9, "equal_opportunity_gap": .01},
    ]
    probes = [{"attribute": "pincode_cluster", "auc_macro_ovr": .55, "threshold": .60}]
    result = _disparity_detection(files, parity, probes)
    pincode = next(row for row in result if row["subset"] == "pincode_bureau_thin")
    assert pincode["n"] == 1
    assert pincode["detected"] is False
