"""Small, dependency-light metrics used by the evaluation harness."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np


def safe_div(n: float, d: float) -> float | None:
    return float(n / d) if d else None


def binary_metrics(y: Iterable[bool], p: Iterable[float], bins: int = 10, threshold: float = .5) -> dict:
    truth = np.asarray(list(y), dtype=float)
    prob = np.asarray(list(p), dtype=float)
    if len(truth) != len(prob):
        raise ValueError("truth and probability lengths differ")
    if not len(truth):
        return {"n": 0, "ece": None, "mce": None, "brier": None, "confident_wrong_count": 0,
                "confident_wrong_rate": None, "accuracy": None}
    prob = np.clip(prob, 0, 1)
    edges = np.linspace(0.0, 1.0, bins + 1)
    gaps = []
    ece = 0.0
    for i in range(bins):
        mask = (prob >= edges[i]) & ((prob <= edges[i + 1]) if i == bins - 1 else (prob < edges[i + 1]))
        if np.any(mask):
            gap = abs(float(prob[mask].mean() - truth[mask].mean()))
            gaps.append(gap)
            ece += float(mask.mean()) * gap
    confident_wrong = ((prob >= .9) & (truth == 0)) | ((prob <= .1) & (truth == 1))
    return {"n": len(truth), "ece": float(ece), "mce": max(gaps, default=0.0),
            "brier": float(np.mean((prob - truth) ** 2)), "confident_wrong_count": int(confident_wrong.sum()),
            "confident_wrong_rate": float(confident_wrong.mean()),
            "accuracy": float(np.mean((prob >= threshold) == truth.astype(bool)))}


def auc(y: np.ndarray, score: np.ndarray) -> float | None:
    """Mann-Whitney AUC with average ranks for ties."""
    y = np.asarray(y, dtype=bool)
    score = np.asarray(score, dtype=float)
    pos, neg = int(y.sum()), int((~y).sum())
    if not pos or not neg:
        return None
    order = np.argsort(score, kind="mergesort")
    sorted_score = score[order]
    ranks = np.empty(len(score), dtype=float)
    i = 0
    while i < len(score):
        j = i + 1
        while j < len(score) and sorted_score[j] == sorted_score[i]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg))


def logistic_cv_auc(x: np.ndarray, labels: np.ndarray, folds: int = 5, seed: int = 71) -> float | None:
    """Deterministic 5-fold numpy-only L2 logistic probe, with stratified folds."""
    x = np.asarray(x, dtype=float)
    labels = np.asarray(labels)
    if len(x) != len(labels) or len(x) < folds * 2 or len(set(labels.tolist())) < 2:
        return None
    classes = np.unique(labels)
    rng = np.random.default_rng(seed)
    fold_ids = np.full(len(labels), -1, dtype=int)
    for cls in classes:
        idx = np.flatnonzero(labels == cls)
        rng.shuffle(idx)
        fold_ids[idx] = np.arange(len(idx)) % folds
    scores_by_class = []
    aucs = []
    for cls_index, cls in enumerate(classes):
        y = (labels == cls).astype(float)
        fold_auc = []
        for fold in range(folds):
            train, test = fold_ids != fold, fold_ids == fold
            if not test.any() or len(np.unique(y[train])) < 2:
                continue
            mu = x[train].mean(axis=0)
            sd = x[train].std(axis=0)
            sd[sd < 1e-8] = 1
            xt = np.column_stack([np.ones(train.sum()), (x[train] - mu) / sd])
            xv = np.column_stack([np.ones(test.sum()), (x[test] - mu) / sd])
            yt = y[train]
            w = np.zeros(xt.shape[1])
            for _ in range(300):
                z = np.clip(xt @ w, -30, 30)
                pred = 1 / (1 + np.exp(-z))
                grad = xt.T @ (pred - yt) / len(yt) + .01 * np.r_[0, w[1:]]
                w -= .2 * grad
            pred = 1 / (1 + np.exp(-np.clip(xv @ w, -30, 30)))
            value = auc(y[test].astype(bool), pred)
            if value is not None:
                fold_auc.append(value)
        if fold_auc:
            aucs.append(float(np.mean(fold_auc)))
    return float(np.mean(aucs)) if aucs else None


def percentile(values: Iterable[float], q: float) -> float | None:
    xs = np.asarray(list(values), dtype=float)
    return float(np.percentile(xs, q)) if len(xs) else None
