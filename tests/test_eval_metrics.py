import numpy as np

from jevloan.eval.metrics import auc, binary_metrics, logistic_cv_auc


def test_binary_calibration_reports_brier_mce_and_confident_wrong():
    result = binary_metrics([True, False, True, False], [.95, .95, .05, .05], bins=10)
    assert result["n"] == 4
    assert result["confident_wrong_count"] == 2
    assert result["confident_wrong_rate"] == .5
    assert result["brier"] == .4525
    assert result["ece"] > .4


def test_auc_handles_ties_and_numpy_logistic_probe_is_cross_validated():
    assert auc(np.array([0, 1, 0, 1]), np.array([0., 1., 1., 0.])) == .5
    rng = np.random.default_rng(9)
    y = np.tile([0, 1], 50)
    x = np.column_stack([y + rng.normal(0, .1, len(y)), rng.normal(size=len(y))])
    assert logistic_cv_auc(x, y, folds=5) > .95
