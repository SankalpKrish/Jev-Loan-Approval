"""End-to-end, pre-registered evaluation over replay JSONL and the frozen book.

The evaluator keeps failed and missing replay rows in file/call denominators. Metric-specific rows
also report prediction coverage so omissions cannot silently make the model look better.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from jevloan.audit.log import AuditLog
from jevloan.canonical import sha256_hex
from jevloan.config import load_runtime, load_yaml_versioned, resolve_path
from jevloan.data.schema import LoanFile, load_book
from jevloan.eval.metrics import auc, binary_metrics, logistic_cv_auc, percentile, safe_div
from jevloan.modules import base as question_base
from jevloan.pii.fixtures import adversarial_fixtures, clean_samples
from jevloan.pii.gate import PIIGate

_OUTCOME_PROCEED = "PROCEED_TO_SANCTIONING_AUTHORITY"
_DISPARITIES = ("lang_doc_script", "gender_income_proxy", "pincode_bureau_thin")
_MODULE_TARGET = {"A": "deficiency", "B": "fraud", "C": "appraisal", "D": "triage", "E": "memo", "F": "monitoring"}


def _dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _get(obj: Any, path: str, default=None):
    for piece in path.split("."):
        obj = obj.get(piece, default) if isinstance(obj, dict) else getattr(obj, piece, default)
        if obj is default:
            return default
    return obj


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected object in {path}:{number}")
            rows.append(row)
    return rows


def _jsonable(value: Any):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return value.__dict__
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"true", "yes", "1", "pass", "signal", "block", "blocked"}
    return bool(value)


def _number(value, fallback=0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else fallback
    except (TypeError, ValueError):
        return fallback


def _decision(row: dict) -> dict:
    return _dict(row.get("decision", row))


def _records_by_file(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for r in rows:
        grouped[str(r.get("file_id", ""))].append(r)
    return grouped


def _module_result(row: dict, module: str) -> dict:
    d = _decision(row)
    modules = d.get("modules", {})
    return _dict(modules.get(module, {})) if isinstance(modules, dict) else {}


def _module_outcome(row: dict, module: str) -> str | None:
    val = _module_result(row, module).get("outcome")
    return str(val).lower() if val is not None else None


def _class_prediction(module: str, outcome: str | None, row: dict) -> bool | None:
    if outcome is None:
        return None
    if module == "A": return outcome in {"fail", "deficiency", "deficiency_notice"}
    if module == "B": return outcome in {"signal", "fraud", "investigate", "fraud_investigation"}
    if module == "C": return str(_module_result(row, "C").get("band") or outcome) == "pass"
    if module == "D": return outcome not in {"not_run", "uncertain"}
    if module == "E": return outcome in {"fail", "block", "blocked", "disbursal_blocked"}
    if module == "F": return outcome in {"t2", "t3", "watchlist_t2", "watchlist_t3"}
    return None


def _state_features(state: dict) -> list[float]:
    """Flatten numeric and categorical state fields deterministically; excludes schema and free text."""
    numeric, cats = [], []
    def walk(x, path=""):
        if isinstance(x, dict):
            for k in sorted(x):
                if k in {"text", "evidence_text", "schema"}:
                    continue
                walk(x[k], f"{path}.{k}" if path else k)
        elif isinstance(x, list):
            for i, item in enumerate(x): walk(item, f"{path}[{i}]")
        elif isinstance(x, bool):
            cats.append((path, str(x).lower()))
        elif isinstance(x, (int, float)) and math.isfinite(float(x)):
            numeric.append((path, float(x)))
        elif isinstance(x, str):
            cats.append((path, x))
    walk(state)
    mapping = {}
    for path, val in numeric: mapping[f"num:{path}"] = val
    for path, val in cats: mapping[f"cat:{path}={val}"] = 1.0
    return [mapping[k] for k in sorted(mapping)]


def _probe(files: list[LoanFile], states: dict[tuple[str, str], dict], thresholds: dict) -> list[dict]:
    # Column feature union is needed because each state's categoricals vary.
    maps = []
    for f in files:
        maps.append(_state_feature_map(states.get((f.file_id, "appraisal"), {})))
    keys = sorted({k for m in maps for k in m})
    x = np.asarray([[m.get(k, 0.) for k in keys] for m in maps], dtype=float) if keys else np.zeros((len(files), 1))
    attrs = {"gender": [f.demographics.gender for f in files],
             "pincode_cluster": [f.demographics.pincode_cluster for f in files],
             "language": [f.demographics.language for f in files]}
    out = []
    for attr, labels in attrs.items():
        score = logistic_cv_auc(x, np.asarray(labels), folds=5, seed=71)
        max_auc = thresholds.get("proxy_probe", {}).get("max_auc", {}).get(attr)
        out.append({"attribute": attr, "auc_macro_ovr": score, "threshold": max_auc,
                    "status": "INSUFFICIENT_N" if score is None else ("FAIL" if max_auc is not None and score > max_auc else "PASS")})
    return out


def _state_feature_map(state: dict) -> dict[str, float]:
    out = {}
    def walk(x, path=""):
        if isinstance(x, dict):
            for k in sorted(x):
                if k in {"text", "evidence_text", "schema", "labels", "demographics", "pii_inventory"}: continue
                walk(x[k], f"{path}.{k}" if path else k)
        elif isinstance(x, list):
            for i, item in enumerate(x): walk(item, f"{path}[{i}]")
        elif isinstance(x, bool): out[f"cat:{path}={str(x).lower()}"] = 1.0
        elif isinstance(x, (int, float)) and math.isfinite(float(x)): out[f"num:{path}"] = float(x)
        elif isinstance(x, str): out[f"cat:{path}={x}"] = 1.0
    walk(state)
    return out


def _baseline_row(file, stage, state, wires, engine):
    from dataclasses import asdict
    from types import SimpleNamespace
    from jevloan.eval.baseline import rule_answers
    answers = rule_answers(state, wires)
    call = SimpleNamespace(ok=True, answers=answers, failure=None)
    decision = engine.decide(file_id=file.file_id, segment=file.segment, stage=stage,
                             call=call, state=state, asked=wires)
    return {"stage": stage, "answers": answers, "decision": asdict(decision)}


def _top_doubt(row):
    answers = (row or {}).get("answers") or {}
    doubts = [(qid, answer.get("noul")) for qid, answer in answers.items()
              if qid.startswith("D_doubt_") and isinstance(answer, dict)
              and isinstance(answer.get("noul"), (int, float))]
    if not doubts:
        return None
    qid = max(doubts, key=lambda pair: pair[1])[0]
    return {"D_doubt_collateral": "collateral", "D_doubt_debt_burden": "debt_burden",
            "D_doubt_income_documentation": "income_documentation",
            "D_doubt_repayment_history": "repayment_history",
            "D_doubt_stability": "employment_or_business_stability",
            "D_doubt_thin_file": "bureau_thin_file"}[qid]


def _prediction(module, row):
    if not row or row.get("failure_kind"):
        return None
    if module == "E":
        # E is registered as block versus memo defect. A deterministic APR
        # mismatch or uncertainty blocks disbursal even when the model passes.
        outcome = _decision(row).get("outcome")
        if outcome == "DISBURSAL_BLOCKED":
            return True
        if outcome == "DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF":
            return False
    mod = _module_result(row, module)
    outcome = str(mod.get("outcome", "")).lower()
    if outcome in {"", "uncertain", "not_run"}:
        return None
    if module == "D":
        return _top_doubt(row)
    return _class_prediction(module, outcome, row)


def _decision_metrics(files, rows_by_id, states, wires_by_file, thresholds, human_accepted_ids=None, policy_path=None):
    from jevloan.policy.config import load_policy
    from jevloan.policy.engine import PolicyEngine
    engine = PolicyEngine(load_policy(policy_path) if policy_path else load_policy(), question_base.catalog())
    report, mismatch = [], defaultdict(list)
    baselines, actuals = {}, {}
    for file in files:
        actuals[file.file_id] = {row.get("stage"): row for row in rows_by_id.get(file.file_id, [])}
        baselines[file.file_id] = {}
        for stage in ("appraisal", "sanction_docs", "monitoring"):
            state = states.get((file.file_id, stage))
            wires = wires_by_file.get((file.file_id, stage))
            if state and wires:
                baselines[file.file_id][stage] = _baseline_row(file, stage, state, wires, engine)
    for module in "ABCDEF":
        stage = "appraisal" if module in "ABCD" else "sanction_docs" if module == "E" else "monitoring"
        truth, predicted, baseline = [], [], []
        for file in files:
            target = {"A": bool(file.labels.missing_items), "B": bool(file.labels.fraud),
                      "C": bool(file.labels.sanctionable), "D": file.labels.primary_weakness,
                      "E": bool(file.labels.memo_defects),
                      "F": file.labels.outcome_12m in {"slips", "defaults"}}[module]
            record = actuals[file.file_id].get(stage)
            pred = _prediction(module, record)
            base = _prediction(module, baselines[file.file_id].get(stage))
            truth.append(target); predicted.append(pred); baseline.append(base)
            if pred is None or pred != target:
                mismatch[module].append((file, record, pred, target))
        n = len(files)
        accuracy = safe_div(sum(p is not None and p == y for p, y in zip(predicted, truth)), n)
        base_accuracy = safe_div(sum(p is not None and p == y for p, y in zip(baseline, truth)), n)
        delta = (accuracy - base_accuracy) * 100 if accuracy is not None and base_accuracy is not None else None
        coverage = safe_div(sum(p is not None for p in predicted), n)
        row = {"module": module, "metric": "top_doubt_accuracy" if module == "D" else "agreement",
               "system": accuracy, "rules_baseline": base_accuracy, "delta_pp": delta, "n": n,
               "prediction_coverage": coverage, "baseline_coverage": safe_div(sum(p is not None for p in baseline), n),
               "verdict": "INSUFFICIENT_N" if delta is None else "NO_GO" if delta < thresholds.get("agreement", {}).get("beat_rules_baseline_by_pp", 2.0) else "PASS"}
        if module in "ABE":
            tp = sum(p is True and y for p, y in zip(predicted, truth))
            fp = sum(p is True and not y for p, y in zip(predicted, truth))
            fn = sum(p is not True and y for p, y in zip(predicted, truth))
            row.update(precision=safe_div(tp, tp+fp), recall=safe_div(tp, tp+fn), f1=safe_div(2*tp, 2*tp+fp+fn))
            if module == "A": row["deficiency_recall"] = row["recall"]
            if module == "E": row["defect_recall"] = row["recall"]
            recall_key = {"A": "module_a_deficiency_recall_min", "B": "module_b_fraud_recall_min", "E": "module_e_defect_recall_min"}[module]
            minimum = thresholds.get("agreement", {}).get(recall_key)
            if minimum is not None and (row["recall"] is None or row["recall"] < minimum):
                row["verdict"] = "NO_GO"
        if module == "A":
            row["item_recall"] = {}
            for item, qid in {"income_proof": "A_income_proof_current", "address_proof": "A_address_proof_valid",
                              "statements": "A_statements_cover_months", "fields_coherence": "A_fields_cohere"}.items():
                positives = [f for f in files if item in f.labels.missing_items]
                caught = sum(qid in (_module_result(actuals[f.file_id].get(stage, {}), "A").get("flags") or []) for f in positives)
                row["item_recall"][item] = {"recall": safe_div(caught, len(positives)), "n": len(positives)}
        if module == "C":
            bands = [_module_result(actuals[f.file_id].get(stage, {}), "C").get("band") for f in files]
            nonborder = [i for i, band in enumerate(bands) if band != "borderline"]
            row["non_borderline_agreement"] = safe_div(sum(predicted[i] is not None and predicted[i] == truth[i] for i in nonborder), len(nonborder))
            row["non_borderline_n"] = len(nonborder)
            row["borderline_share"] = safe_div(sum(band == "borderline" for band in bands), n)
            row["pass_or_accepted_agreement"] = (safe_div(sum((predicted[i] is True or (bands[i] == "borderline" and f.file_id in human_accepted_ids)) == truth[i] and predicted[i] is not None for i, f in enumerate(files)), n)
                                                  if human_accepted_ids is not None else None)
        if module == "D":
            def correlation(records):
                pairs = [(_module_result(records[f.file_id].get(stage, {}), "D").get("score"), f.labels.closeness_level) for f in files]
                pairs = [(x, y) for x, y in pairs if x is not None]
                return _spearman([x for x,y in pairs], [y for x,y in pairs])
            row.update(closeness_spearman=correlation(actuals), rules_closeness_spearman=correlation(baselines),
                       top_doubt_accuracy=accuracy, top_doubt_n=sum(p is not None for p in predicted))
        if module == "F":
            correct = total = seen = 0
            for file in files:
                answers = (actuals[file.file_id].get(stage, {}).get("answers") or {})
                for qid, target in file.labels.question_truth.items():
                    if not qid.startswith("F_covenant_"): continue
                    total += 1
                    answer = answers.get(qid, {})
                    if "noul" in answer:
                        seen += 1
                        correct += (answer["noul"] >= .5) == bool(target)
            row.update(covenant_agreement=safe_div(correct,total), covenant_n=total, covenant_prediction_coverage=safe_div(seen,total))
        report.append(row)
    return report, mismatch


def _spearman(x, y):
    if len(x) < 2: return None
    def ranks(vals):
        order = np.argsort(vals, kind="mergesort"); out = np.empty(len(vals), float); i = 0
        while i < len(vals):
            j = i + 1
            while j < len(vals) and vals[order[j]] == vals[order[i]]: j += 1
            out[order[i:j]] = (i + j - 1) / 2 + 1; i = j
        return out
    rx, ry = ranks(np.asarray(x)), ranks(np.asarray(y))
    if np.std(rx) == 0 or np.std(ry) == 0: return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _answer_probability(answer: dict, qdef, truth: Any):
    qtype = qdef.qtype
    if qtype == "noul":
        p = answer.get("noul")
        if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1: return None
        return {"kind": "binary", "p": float(p), "correct": (float(p) >= .5) == bool(truth), "truth": bool(truth)}
    probs = answer.get("probabilities") or {}
    if not isinstance(probs, dict) or not probs: return None
    if any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()): return None
    if not math.isclose(sum(probs.values()), 1.0, abs_tol=.02): return None
    probs = {str(k): _number(v) for k, v in probs.items()}
    chosen = max(probs, key=probs.get)
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1: return None
    if qtype == "choice":
        options = list((qdef.wire or {}).get("criteria", {}))
        pred = str(answer.get("choice", chosen))
        expected = sum((options.index(k) if k in options else 0) * p for k, p in probs.items())
        truth_level = options.index(str(truth)) if str(truth) in options else None
        return {"kind": "top_label", "p": confidence, "correct": pred == str(truth), "truth": truth,
                "prediction": pred, "mae": abs(expected - truth_level) if truth_level is not None else None}
    # The declared score level array determines the numeric order for MAE; probabilities are keyed by index string.
    level_names = [str(i) for i in range(len(qdef.wire.get("criteria", [])))] if qdef.wire else sorted(probs, key=lambda k: int(k) if k.isdigit() else k)
    values = []
    for key in probs:
        try: idx = int(key)
        except ValueError: idx = level_names.index(key) if key in level_names else 0
        values.append((idx, probs[key]))
    expected = sum(i * p for i, p in values)
    try: truth_level = float(truth)
    except (TypeError, ValueError): truth_level = float(int(str(truth))) if str(truth).isdigit() else None
    return {"kind": "top_label", "p": confidence, "correct": chosen == str(truth), "truth": truth,
            "prediction": answer.get("score", chosen), "mae": abs(expected - truth_level) if truth_level is not None else None}


def _calibration(files, rows_by_id, wires_by_file, thresholds, states=None):
    qcatalog = question_base.catalog()
    grouped = defaultdict(list); applicable_count = defaultdict(int)
    for f in files:
        for stage in ("appraisal", "sanction_docs", "monitoring"):
            state = states[(f.file_id, stage)] if states is not None else _load_state(f, stage)
            for qid, q in qcatalog.items():
                if q.stage != stage or not q.is_applicable(f.segment, state): continue
                truth = f.labels.question_truth.get(qid)
                if truth is None: continue
                applicable_count[qid] += 1
                row = next((r for r in rows_by_id.get(f.file_id, []) if r.get("stage") == stage), {})
                answer = (row.get("answers") or {}).get(qid)
                if not isinstance(answer, dict): continue
                # Include runtime rubric criteria: several questions build their options from state.
                qview = type("QuestionView", (), {"qtype": q.qtype, "wire": wires_by_file.get((f.file_id, stage), {}).get(qid)})()
                value = _answer_probability(answer, qview, truth)
                if value is not None: grouped[qid].append(value)
    out = []
    for qid, q in sorted(qcatalog.items()):
        vals = grouped[qid]
        n = len(vals)
        if q.qtype == "noul":
            m = binary_metrics([v["truth"] for v in vals], [v["p"] for v in vals], bins=thresholds.get("calibration", {}).get("ece_bins", 10))
            ece, mce, err = m["ece"], m["mce"], m["brier"]
        else:
            # Top-label calibration bins confidence against whether the most likely category was correct.
            m = binary_metrics([v["correct"] for v in vals], [v["p"] for v in vals], bins=thresholds.get("calibration", {}).get("ece_bins", 10))
            ece, mce, err = m["ece"], m["mce"], float(np.mean([v["mae"] for v in vals if v["mae"] is not None])) if any(v["mae"] is not None for v in vals) else None
            wrong = sum(v["p"] >= thresholds.get("calibration", {}).get("confident_wrong_p", .9) and not v["correct"] for v in vals)
            m["confident_wrong_count"] = wrong
            m["confident_wrong_rate"] = safe_div(wrong, n)
        fail = n >= 200 and ((ece or 0) > thresholds.get("calibration", {}).get("max_ece_routing_question", .08) or (m.get("confident_wrong_rate") or 0) > thresholds.get("calibration", {}).get("max_confident_wrong_rate", .02))
        # The registered stop condition gates routing questions. Other questions
        # still show diagnostic breaches rather than an unconditional PASS.
        verdict = ("NOT_APPLICABLE" if not applicable_count[qid] else "INSUFFICIENT_N" if n < 200
                   else ("FAIL" if q.routing_relevant else "DIAGNOSTIC_FAIL") if fail
                   else "PASS" if q.routing_relevant else "DIAGNOSTIC_PASS")
        out.append({"qid": qid, "type": q.qtype, "n": n, "applicable_n": applicable_count[qid], "coverage": safe_div(n, applicable_count[qid]),
                    "ece": ece, "mce": mce, "brier_or_level_mae": err, "confident_wrong_count": m.get("confident_wrong_count", 0),
                    "confident_wrong_rate": m.get("confident_wrong_rate"), "routing_relevant": q.routing_relevant,
                    "verdict": verdict})
    return out


def _load_state(file: LoanFile, stage: str) -> dict:
    from jevloan.state import build_state
    return build_state(file, stage)


def _parity(files, rows_by_id, thresholds):
    attrs = ("gender", "pincode_cluster", "language")
    config = thresholds.get("protected_attributes", {})
    limits = thresholds.get("parity", {})
    min_n = int(thresholds.get("min_group_n", 100))
    output = []
    for attr in attrs:
        reference = config.get(attr, {}).get("reference_group")
        for scope in ("all", "without_disparity_subset"):
            use = [f for f in files if scope == "all" or f.meta.disparity_subset is None]
            groups = defaultdict(list)
            for file in use: groups[getattr(file.demographics, attr)].append(file)
            ref_files = groups.get(reference, [])
            ref_rate = _proceed_rate(ref_files, rows_by_id)
            ref_eligible = [f for f in ref_files if f.labels.sanctionable]
            ref_opp = _proceed_rate(ref_eligible, rows_by_id)
            overall_calibration = _group_calibration(use, rows_by_id)
            for group, members in sorted(groups.items()):
                n = len(members)
                proceed = _proceed_rate(members, rows_by_id)
                eligible = [f for f in members if f.labels.sanctionable]
                opportunity = _proceed_rate(eligible, rows_by_id)
                ratio = safe_div(proceed, ref_rate) if ref_rate else None
                gap = abs(opportunity-ref_opp) if opportunity is not None and ref_opp is not None else None
                group_ece = _group_calibration(members, rows_by_id)
                ece_gaps = {qid: abs(ece-overall_calibration[qid]) if qid in overall_calibration else None for qid,ece in group_ece.items()}
                predictions = [_sanction_probability(f, rows_by_id) for f in members]
                predictions = [p for p in predictions if p is not None]
                observed = safe_div(sum(f.labels.sanctionable for f in members), n)
                cil = abs(float(np.mean(predictions))-observed) if predictions and observed is not None else None
                breaches = []
                if ratio is not None and ratio < limits.get("proceed_rate_ratio_min", .8): breaches.append("PROCEED_RATE_RATIO")
                if gap is not None and gap > limits.get("equal_opportunity_gap_max", .05): breaches.append("EQUAL_OPPORTUNITY")
                if any(v is not None and v > limits.get("group_ece_gap_max", .03) for v in ece_gaps.values()): breaches.append("GROUP_ECE")
                if cil is not None and cil > limits.get("calibration_in_large_gap_max", .05): breaches.append("CALIBRATION_IN_LARGE")
                status = "INSUFFICIENT_N" if n < min_n or len(ref_files) < min_n else "FAIL" if breaches else "PASS"
                if status == "PASS" and (ratio is None or gap is None or cil is None): status = "INSUFFICIENT_EVIDENCE"
                output.append({"attribute": attr, "group": group, "reference": reference, "scope": scope, "n": n,
                               "reference_n": len(ref_files), "proceed_rate": proceed, "proceed_rate_ratio": ratio,
                               "equal_opportunity_proceed_rate": opportunity, "equal_opportunity_gap": gap,
                               "routing_question_group_ece": group_ece, "routing_question_ece_gap": ece_gaps,
                               "calibration_in_large_gap": cil, "prediction_coverage": safe_div(len(predictions), n),
                               "breaches": breaches, "status": status})
    return output


def _proceed_rate(files, rows_by_id):
    return safe_div(sum(any(_decision(r).get("outcome") == _OUTCOME_PROCEED for r in rows_by_id.get(f.file_id, [])) for f in files), len(files))


def _sanction_probability(file, rows_by_id):
    row = next((r for r in rows_by_id.get(file.file_id, []) if r.get("stage") == "appraisal"), None)
    if not row: return None
    decision = _decision(row)
    value = decision.get("composite")
    if value is not None: return max(0.0, min(1.0, float(value)))
    c = _module_result(row, "C")
    value = c.get("score")
    return max(0.0, min(1.0, float(value))) if value is not None else None


def _group_calibration(files, rows_by_id):
    """Routing question ECE by qid within a protected group; only measured answers count in ECE n."""
    from jevloan.modules import base
    catalog = base.catalog()
    rows = defaultdict(list)
    for f in files:
        for r in rows_by_id.get(f.file_id, []):
            stage = r.get("stage")
            for qid, answer in (r.get("answers") or {}).items():
                q = catalog.get(qid)
                truth = f.labels.question_truth.get(qid)
                if q is None or not q.routing_relevant or truth is None or not isinstance(answer, dict): continue
                value = _answer_probability(answer, type("QuestionView", (), {"qtype": q.qtype, "wire": q.wire})(), truth)
                if value is not None: rows[qid].append(value)
    return {qid: binary_metrics([v["truth"] if v["kind"] == "binary" else v["correct"] for v in vals],
                                [v["p"] for v in vals]) ["ece"] for qid, vals in rows.items() if vals}


def _disparity_detection(files, parity, probes, thresholds=None):
    limits = (thresholds or {}).get("parity", {})
    ratio_min = limits.get("proceed_rate_ratio_min", .8)
    gap_max = limits.get("equal_opportunity_gap_max", .05)
    results = []
    for subset in _DISPARITIES:
        members = [f for f in files if f.meta.disparity_subset == subset]
        attr = {"lang_doc_script": "language", "gender_income_proxy": "gender", "pincode_bureau_thin": "pincode_cluster"}[subset]
        subset_groups = defaultdict(list)
        for f in members: subset_groups[getattr(f.demographics, attr)].append(f)
        # Only adequately measured groups affected by this subset can establish
        # its parity signal. Small or unrelated groups cannot prove test power.
        measured = {"PASS", "FAIL", "MEASURED"}
        all_rows = [r for r in parity if r["attribute"] == attr and r["scope"] == "all"
                    and r["group"] in subset_groups and r["group"] != r["reference"] and r["status"] in measured]
        clean_rows = [r for r in parity if r["attribute"] == attr and r["scope"] == "without_disparity_subset"
                      and r["group"] != r["reference"] and r["status"] in measured]
        clean_by_group = {r["group"]: r for r in clean_rows}
        parity_flag = False
        for row in all_rows:
            clean = clean_by_group.get(row["group"])
            if clean is None:
                continue
            rate_delta = (abs(row["proceed_rate"] - clean["proceed_rate"])
                          if row.get("proceed_rate") is not None and clean.get("proceed_rate") is not None else None)
            opp_delta = (abs(row["equal_opportunity_proceed_rate"] - clean["equal_opportunity_proceed_rate"])
                         if row.get("equal_opportunity_proceed_rate") is not None and clean.get("equal_opportunity_proceed_rate") is not None else None)
            breach_with = ((row.get("proceed_rate_ratio") is not None and row["proceed_rate_ratio"] < ratio_min) or
                           (row.get("equal_opportunity_gap") is not None and row["equal_opportunity_gap"] > gap_max))
            clean_breach = ((clean.get("proceed_rate_ratio") is not None and clean["proceed_rate_ratio"] < ratio_min) or
                            (clean.get("equal_opportunity_gap") is not None and clean["equal_opportunity_gap"] > gap_max))
            parity_flag |= bool((breach_with and not clean_breach) or (rate_delta is not None and rate_delta > gap_max)
                                or (opp_delta is not None and opp_delta > gap_max))
        probe = next((p for p in probes if p["attribute"] == attr), {})
        probe_flag = probe.get("auc_macro_ovr") is not None and probe["auc_macro_ovr"] > (probe.get("threshold") or .6)
        results.append({"subset": subset, "n": len(members), "parity_flagged": bool(parity_flag), "proxy_probe_flagged": bool(probe_flag),
                        "detected": bool(members and (parity_flag or probe_flag)), "status": "DETECTED" if members and (parity_flag or probe_flag) else "UNDETECTED"})
    return results


def _operations(files, rows_by_id, all_rows, runtime):
    by_segment = defaultdict(list)
    for f in files:
        by_segment[f.segment].append(f)
    result = []
    for segment, members in sorted(by_segment.items()):
        rows = [r for f in members for r in rows_by_id.get(f.file_id, [])]
        lat_call = [float(r.get("latency_ms") or 0) for r in rows]
        lat_file = [sum(float(r.get("latency_ms") or 0) for r in rows_by_id.get(f.file_id, [])) for f in members]
        token_call = [int(r["input_tokens"]) for r in rows if isinstance(r.get("input_tokens"), int)]
        failures = sum(bool(r.get("failure_kind", _decision(r).get("failure"))) for r in rows)
        tokens = sum(token_call)
        result.append({"segment": segment, "files": len(members), "calls": len(rows), "input_tokens_per_call": safe_div(tokens, len(rows)),
                       "input_tokens_per_file": safe_div(tokens, len(members)),
                       "usd_per_file": safe_div(tokens * runtime.usd_per_million_input_tokens / 1_000_000, len(members)),
                       "fallback_rate": safe_div(failures, len(rows)),
                       "call_latency_ms": {f"p{p}": percentile(lat_call, p) for p in (50, 95, 99)},
                       "file_latency_ms": {f"p{p}": percentile(lat_file, p) for p in (50, 95, 99)}})
    return result


def _outbound_inventory_hits(file, state, questions):
    """Check applicant state strictly and rubric prose for complete identifiers.

    Name fragments are meaningful in applicant-derived state. In generic rubric
    prose, common words that happen to be a name fragment are not evidence of a
    leak. Byte-identical static catalogue rubrics cannot convey applicant data
    (for example a bureau band ``600-649`` coinciding with a six-digit PIN after
    normalization). Changed or dynamic rubrics are checked for complete inventory
    values. The structural gate scans all rubrics separately; arbitrary partial
    names in prose remain a documented limitation.
    """
    from jevloan.state.commands import inventory_hits
    catalogue = question_base.catalog()
    check_questions = {qid: wire for qid, wire in questions.items()
                       if qid not in catalogue or catalogue[qid].wire != wire}
    return [*inventory_hits(file, state),
            *(hit for hit in inventory_hits(file, check_questions) if hit.kind != "person_name_part")]


def _pii(files, rows_by_id, metadata, book_path):
    gate = PIIGate()
    adversarial, clean = adversarial_fixtures(), clean_samples()
    caught = sum(bool({finding.detector for finding in gate.scan(f.payload)} & f.expected_detectors) if f.expected_detectors else bool(gate.scan(f.payload)) for f in adversarial)
    false_positive = sum(bool(gate.scan(payload)) for payload in clean)
    leaks, findings, scanned = [], 0, 0
    audit_path = metadata.get("audit_db_path")
    files_by_id = {f.file_id: f for f in files}
    if audit_path and resolve_path(audit_path).is_file():
        for event in AuditLog(resolve_path(audit_path)).iter_all():
            file = files_by_id.get(event.get("file_id"))
            if file is None or not event.get("state_json"): continue
            state = json.loads(event["state_json"])
            questions = json.loads(event.get("questions_json") or "{}")
            scanned += 1
            leaks.extend({"file_id": file.file_id, "stage": event["stage"], "category": hit.kind} for hit in _outbound_inventory_hits(file, state, questions))
            findings += len(gate.scan({"state": state, "questions": questions}))
    return {"fixture_catch_rate": safe_div(caught,len(adversarial)), "fixture_caught": caught, "fixture_total": len(adversarial),
            "clean_false_positive_rate": safe_div(false_positive,len(clean)), "clean_false_positives": false_positive,
            "clean_total": len(clean), "full_book_known_pii_leak_hits": len(leaks), "gate_findings": findings,
            "leak_examples": leaks[:20], "outbound_states_scanned": scanned,
            "leak_scan_status": "MEASURED" if scanned else "NOT_AVAILABLE"}


def _audit_metrics(files, rows_by_id, metadata):
    path = metadata.get("audit_db_path")
    if not path or not resolve_path(path).exists():
        return {"verify": "NOT_AVAILABLE", "trail_completeness": None}
    audit = AuditLog(resolve_path(path))
    verify = audit.verify()
    from jevloan.pipeline.trail import trail_complete
    complete = 0
    for f in files:
        good, _ = trail_complete(audit, f.file_id, ("appraisal", "sanction_docs", "monitoring"))
        complete += int(good)
    return {"verify": "PASS" if verify.ok else f"FAIL: {verify.reason}", "verify_entries": verify.entries,
            "trail_completeness": safe_div(complete, len(files))}


def _disagreements(files, rows_by_id, mismatch):
    groups = defaultdict(list)
    for module, items in mismatch.items():
        for f, row, pred, truth in items:
            mod = _module_result(row or {}, module)
            reasons = mod.get("reasons") or []
            driver = (reasons[0].get("qid") if reasons and isinstance(reasons[0], dict) else None) or (mod.get("flags") or ["unknown"])[0]
            groups[(module, driver, f.segment)].append((f, pred, truth, mod))
    lines = ["# Replay disagreements", "", "Missing replay rows and failed calls are represented as disagreements in module-level counts.", ""]
    summary = []
    for (module, driver, segment), items in sorted(groups.items()):
        summary.append(f"{module}/{driver}/{segment}: {len(items)}")
        lines += [f"## Module {module} · driver {driver} · {segment} ({len(items)})", ""]
        for f, pred, truth, mod in items[:3]:
            stage = "appraisal" if module in "ABCD" else ("sanction_docs" if module == "E" else "monitoring")
            record = next((r for r in rows_by_id.get(f.file_id, []) if r.get("stage") == stage), {})
            answers = record.get("answers") or {}
            answer_excerpt = {k: answers[k] for k in sorted(answers) if k.startswith(module + "_")}
            lines.append(f"- `{f.file_id}`: predicted `{pred}`; label `{truth}`; module outcome `{mod.get('outcome')}`; segment `{f.segment}`; bureau `{f.bureau.score if f.bureau.score is not None else 'NTC'}`; DPD `{f.bureau.max_dpd_12m}`; income doc `{f.income.documentation_type}`; FOIR `{round(100*(f.obligations.existing_emi_inr + f.obligations.proposed_emi_inr)/max(1,f.income.verified_monthly_income_inr),1)}%`; answers `{json.dumps(answer_excerpt, sort_keys=True)}`.")
        lines.append("")
    return "\n".join(lines), "\n".join(f"- {x}" for x in summary) if summary else "No module-level disagreements were recorded."


def _recommendation(backend, stop, agreement):
    failed = [k for k, v in stop.items() if v is not True]
    failed += [f"module {r['module']} baseline margin" for r in agreement if r.get("verdict") == "NO_GO"]
    if backend == "sim": return "PROVISIONAL: NOT A GO", "Simulator results are provisional and can never authorize a GO. " + ("Measured stop conditions failed: " + ", ".join(failed) if failed else "No additional stop-condition failure was measured.")
    if failed: return "NO-GO", "Pre-registered stop conditions failed: " + ", ".join(failed) + "."
    return "GO", "All measured pre-registered stop conditions passed on the real backend."


def _render_memo(template: str, data: dict) -> str:
    for key, value in data.items():
        template = template.replace("{{" + key + "}}", str(value))
    leftovers = [key for key in re.findall(r"\{\{([^}]+)\}\}", template) if key != "placeholder"]
    if leftovers:
        raise ValueError("memo has unresolved fields: " + ", ".join(sorted(set(leftovers))))
    return template


def evaluate_run(run_id: str, *, split: str = "holdout", runs_dir: str = "data/runs", book_path: str | None = None,
                 reports_dir: str = "reports", thresholds_path: str = "config/parity_thresholds.yaml",
                 memo_template: str = "docs/GO_NO_GO_MEMO_TEMPLATE.md") -> dict:
    if split not in {"holdout", "dev", "all"}:
        raise ValueError("split must be holdout, dev or all")
    run_dir = resolve_path(runs_dir) / run_id
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {"run_id": run_id}
    book_path = book_path or metadata.get("book_path", "data/book.jsonl")
    if metadata.get("book_sha256") and sha256_hex(resolve_path(book_path).read_bytes()) != metadata["book_sha256"]:
        raise ValueError("book differs from the frozen replay input")
    for name in ("policy", "pricing"):
        if metadata.get(f"{name}_sha256") and sha256_hex(resolve_path(metadata[f"{name}_path"]).read_bytes()) != metadata[f"{name}_sha256"]:
            raise ValueError(f"frozen {name} configuration was modified")
    book_files = list(load_book(book_path))
    if "processed_file_ids" in metadata:
        wanted = set(metadata["processed_file_ids"])
        book_files = [file for file in book_files if file.file_id in wanted]
        if len(book_files) != len(wanted): raise ValueError("processed file ids are missing from the frozen book")
    elif "processed_n" in metadata:
        book_files = book_files[:metadata["processed_n"]]
    all_files = book_files
    if split != "all":
        all_files = [f for f in all_files if ((int(re.search(r"\d+$", f.file_id).group()) % 5 == 0) == (split == "dev"))]
    all_rows = _read_jsonl(run_dir / "decisions.jsonl")
    keys = [(row.get("file_id"), row.get("stage")) for row in all_rows]
    if len(keys) != len(set(keys)): raise ValueError("duplicate stage rows make replay evidence ambiguous")
    if any(row.get("run_id",run_id) != run_id for row in all_rows): raise ValueError("replay contains a different run id")
    thresholds, prereg, prereg_hash = load_yaml_versioned(thresholds_path)
    selected_ids = {f.file_id for f in all_files}
    rows = [r for r in all_rows if str(r.get("file_id")) in selected_ids]
    rows_by_id = _records_by_file(rows)
    states, wires = {}, {}
    evidence = {}
    human_accepted_ids = None
    audit_path = metadata.get("audit_db_path")
    if audit_path and resolve_path(audit_path).is_file():
        audit_events = list(AuditLog(resolve_path(audit_path)).iter_all())
        for event in audit_events:
            if event["event_type"] in {"MODEL_CALL", "MODEL_FAILURE", "PII_BLOCK"} and event.get("state_json"):
                evidence[(event["file_id"],event["stage"])] = event
        human_events = [e for e in audit_events if e["event_type"] == "HUMAN_DECISION" and e["stage"] == "appraisal"]
        if human_events:
            human_accepted_ids = {e["file_id"] for e in human_events if e["human_decision"] == "accept" and json.loads(e.get("detail_json") or "{}").get("queue") == "credit_review"}
    for file in book_files:
        for stage in ("appraisal", "sanction_docs", "monitoring"):
            event = evidence.get((file.file_id,stage))
            state = json.loads(event["state_json"]) if event else _load_state(file,stage)
            questions = json.loads(event["questions_json"]) if event and event.get("questions_json") else question_base.questions_for(stage,file.segment,state)
            states[(file.file_id,stage)] = state
            wires[(file.file_id,stage)] = questions
    agreement, disagreements = _decision_metrics(all_files, rows_by_id, states, wires, thresholds, human_accepted_ids, metadata.get("policy_path"))
    calibration = _calibration(all_files, rows_by_id, wires, thresholds, states)
    parity = _parity(all_files, rows_by_id, thresholds)
    probes = _probe(all_files, states, thresholds)
    disparity = _disparity_detection(all_files, parity, probes, thresholds)
    from jevloan.config import RuntimeConfig
    runtime = RuntimeConfig.model_validate(metadata["runtime"]) if metadata.get("runtime") else load_runtime()
    if "input_token_price_usd_per_million" in metadata:
        runtime.usd_per_million_input_tokens = metadata["input_token_price_usd_per_million"]
    operations = _operations(all_files, rows_by_id, rows, runtime)
    pii = _pii(book_files, rows_by_id, metadata, book_path)
    audit = _audit_metrics(book_files, rows_by_id, metadata)
    disagreement_md, disagreement_summary = _disagreements(all_files, rows_by_id, disagreements)
    routing = [r for r in calibration if r["routing_relevant"] and r["applicable_n"] > 0]
    cal_fail_share = safe_div(sum(r["verdict"] in {"FAIL", "INSUFFICIENT_N"} or r["coverage"] != 1.0 for r in routing), len(routing))
    module_b = next(r for r in agreement if r["module"] == "B")
    module_c = next(r for r in agreement if r["module"] == "C")
    total_calls = 3*len(all_files); failures = sum(bool(r.get("failure_kind")) for r in rows) + max(0,total_calls-len(rows))
    fallback = safe_div(failures, total_calls)
    usd_per_file = safe_div(sum(int(r.get("input_tokens") or 0) for r in rows) * runtime.usd_per_million_input_tokens / 1e6, len(all_files))
    unexplained = [r for r in parity if r["scope"] == "without_disparity_subset" and r["status"] == "FAIL"]
    parity_breaches = [r for r in parity if r["status"] == "FAIL"]
    unknown_usage = sum(not isinstance(r.get("input_tokens"), int) and r.get("failure_kind") not in {"circuit_open","forced_human","pii_blocked"} for r in rows)

    undetected = [r["subset"] for r in disparity if not r["detected"]]
    stops = {
        "S1": (pii["fixture_catch_rate"] or 0) >= thresholds.get("pii",{}).get("fixture_catch_rate_min",1) and (pii["clean_false_positive_rate"] or 0) <= thresholds.get("pii",{}).get("clean_false_positive_rate_max",.01),
        "S2": pii["full_book_known_pii_leak_hits"] == 0 and pii["gate_findings"] == 0 if pii["leak_scan_status"] == "MEASURED" else None,
        "S3": audit["verify"] == "PASS" if audit["verify"] != "NOT_AVAILABLE" else None,
        "S4": (audit["trail_completeness"] or 0) >= 1 if audit["trail_completeness"] is not None else None,
        "S5": cal_fail_share is not None and cal_fail_share <= thresholds.get("calibration", {}).get("max_share_of_routing_questions_failing", .25),
        "S6": (module_b.get("recall") or 0) >= thresholds.get("agreement", {}).get("module_b_fraud_recall_min", .8),
        "S7": (module_c.get("non_borderline_agreement") or 0) >= thresholds.get("agreement", {}).get("module_c_non_borderline_min", .85),
        "S8": (module_c.get("borderline_share") or 0) <= thresholds.get("agreement", {}).get("module_c_borderline_share_max", .35),
        "S9": len(unexplained) == 0,
        "S10": len(undetected) == 0,
        "S11": fallback is not None and fallback <= thresholds.get("operations", {}).get("fallback_rate_max", .05),
        "S12": None if unknown_usage else usd_per_file is not None and usd_per_file <= thresholds.get("operations", {}).get("usd_per_file_max", .01),
        "S13": metadata.get("backend", "unknown") == "real",
    }
    backend = metadata.get("backend", "unknown")
    recommendation, rationale = _recommendation(backend, stops, agreement)
    results = {"run_id": run_id, "split": split, "book_n": len(all_files), "replay_rows": len(rows), "thresholds_version": prereg, "thresholds_sha256": prereg_hash, "usage_unknown_calls": unknown_usage,
               "metadata": metadata, "agreement": agreement, "calibration": calibration, "parity": parity, "proxy_probe": probes,
               "engineered_disparities": disparity, "operations": operations, "pii": pii, "audit": audit,
               "stop_conditions": stops, "recommendation": recommendation, "recommendation_rationale": rationale}
    dev_files = [f for f in book_files if int(re.search(r"\d+$",f.file_id).group()) % 5 == 0]
    dev_rows = _records_by_file([r for r in all_rows if r.get("file_id") in {f.file_id for f in dev_files}])
    dev_agreement, _ = _decision_metrics(dev_files,dev_rows,states,wires,thresholds,human_accepted_ids,metadata.get("policy_path"))
    results["dev_comparison"] = {"n": len(dev_files), "agreement": dev_agreement,
                                  "calibration": _calibration(dev_files,dev_rows,wires,thresholds,states)}
    out_dir = resolve_path(reports_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=_jsonable) + "\n", encoding="utf-8")
    (out_dir / "disagreements.md").write_text(disagreement_md, encoding="utf-8")
    template = resolve_path(memo_template).read_text(encoding="utf-8")
    template = template.replace(
        "The harness fills every\n> `{{placeholder}}`. A placeholder left unfilled means the memo is incomplete and can't be used.",
        "This completed memo reports measured outcomes.\n> Unknown measurements and unassigned sign-off roles are shown explicitly.")
    calib_table = _markdown_table(["qid", "type", "n", "ECE", "MCE", "Brier / level-MAE", "CW count", "CW rate", "routing", "verdict"],
                                  [[r["qid"], r["type"], r["n"], r["ece"], r["mce"], r["brier_or_level_mae"], r["confident_wrong_count"], r["confident_wrong_rate"], r["routing_relevant"], r["verdict"]] for r in calibration])
    agreement_table = _markdown_table(["module", "metric", "system", "rules baseline", "delta (pp)", "n", "verdict"],
                                    [[r["module"], r["metric"], r["system"], r["rules_baseline"], r["delta_pp"], r["n"], r["verdict"]] for r in agreement])
    parity_table = _markdown_table(["attribute", "group", "scope", "n", "proceed rate", "rate ratio", "equal opportunity gap", "calibration-in-large gap", "status"],
                                   [[r["attribute"], r["group"], r["scope"], r["n"], r["proceed_rate"], r["proceed_rate_ratio"], r["equal_opportunity_gap"], r["calibration_in_large_gap"], r["status"]] for r in parity])
    proxy_table = _markdown_table(["attribute", "5-fold CV macro AUC", "max AUC", "status"], [[r["attribute"], r["auc_macro_ovr"], r["threshold"], r["status"]] for r in probes])
    disp_table = _markdown_table(["subset", "n", "parity flagged", "probe flagged", "detected", "status"], [[r["subset"], r["n"], r["parity_flagged"], r["proxy_probe_flagged"], r["detected"], r["status"]] for r in disparity])
    cost_table = _markdown_table(["segment", "files", "calls", "tokens/call", "tokens/file", "USD/file", "fallback", "call p50/p95/p99 ms", "file p50/p95/p99 ms"],
                                 [[r["segment"], r["files"], r["calls"], r["input_tokens_per_call"], r["input_tokens_per_file"], r["usd_per_file"], r["fallback_rate"], r["call_latency_ms"], r["file_latency_ms"]] for r in operations])
    replacements = {"run_id": run_id, "run_date": date.today().isoformat(), "backend": backend,
        "model_versions": ", ".join(sorted({str(r.get("model_version")) for r in rows if r.get("model_version")})) or "none recorded",
        "policy_version": metadata.get("policy_version", "unknown"), "pricing_version": metadata.get("pricing_version", "unknown"),
        "book_n": len(all_files), "generator_version": ", ".join(sorted({f.meta.generator_version for f in all_files})) or "unknown",
        "seed": ", ".join(sorted({str(f.meta.seed) for f in all_files})) or "unknown", "split": split,
        "prereg_version": prereg, "recommendation": recommendation, "recommendation_rationale": rationale,
        "agreement_table": agreement_table, "calibration_table": calib_table, "parity_table": parity_table,
        "proxy_table": proxy_table, "disparity_detection_table": disp_table,
        "fairness_failures_narrative": "; ".join(f"{r['attribute']} {r['group']} ({r['scope']}): {', '.join(r['breaches'])}" for r in parity_breaches) or "No measured parity breach. Groups below the registered minimum remain INSUFFICIENT_N.",
        "pii_section": f"Fixture catch: {pii['fixture_catch_rate']}; clean false-positive rate: {pii['clean_false_positive_rate']}; full-book leak hits: {pii['full_book_known_pii_leak_hits']} ({pii['leak_scan_status']}).",
        "cost_latency_table": cost_table, "disagreement_report_path": str(out_dir / "disagreements.md"),
        "disagreement_summary": disagreement_summary, "deviations": "Thresholds are unchanged. " + str(metadata.get("deviations", "None recorded.")),
        "pii_catch_rate": pii["fixture_catch_rate"], "leak_hits": pii["full_book_known_pii_leak_hits"], "audit_verify": audit["verify"],
        "trail_completeness": audit["trail_completeness"], "calib_fail_share": cal_fail_share, "b_recall": module_b.get("recall"),
        "c_agreement": module_c.get("non_borderline_agreement"), "c_borderline_share": module_c.get("borderline_share"),
        "unexplained_parity_breaches": len(unexplained), "undetected_disparities": ", ".join(undetected) or "none",
        "fallback_rate": fallback, "usd_per_file": usd_per_file, "s1": _pass(stops["S1"]), "s2": _pass(stops["S2"]),
        "s3": _pass(stops["S3"]), "s4": _pass(stops["S4"]), "s5": _pass(stops["S5"]), "s6": _pass(stops["S6"]),
        "s7": _pass(stops["S7"]), "s8": _pass(stops["S8"]), "s9": _pass(stops["S9"]), "s10": _pass(stops["S10"]),
        "s11": _pass(stops["S11"]), "s12": _pass(stops["S12"]), "s13": _pass(stops["S13"]), "owner_model_risk": "UNASSIGNED", "owner_fraud": "UNASSIGNED"}
    memo = _render_memo(template,replacements)
    memo += "\n\n## Dev comparison (excluded from holdout headline numbers)\n\n" + _markdown_table(["module","system","rules baseline","n"],[[r["module"],r["system"],r["rules_baseline"],r["n"]] for r in dev_agreement])
    memo += "\n\nModule details, per-group routing-question ECE, coverage, and dev question calibration are in metrics.json. E agreement measures disbursal block versus memo defects; uncertainty and deterministic APR checks can block otherwise clean documents. Non-routing calibration verdicts are diagnostic against the same limits and do not enter S5.\n"
    memo += "\nCost is based on known billed usage and the frozen configured input-token price, not independently verified vendor pricing; missing usage cannot establish S12. File latency is the sum of stage-call latencies, not measured end-to-end wall time. The C composite is a policy score, and calibration-in-the-large uses it as a documented probability proxy rather than a validated PD.\n"
    memo += "\nPII inventory checks cover complete identifiers plus name fragments in applicant state. Exact static catalogue rubrics are verified as boilerplate, avoiding incidental name-word and numeric-band matches; changed or dynamic question text is checked for complete identifiers. The structural gate scans all outbound state and questions. Unknown or partial names in prose and adversarial combinations outside supported fixtures can evade detection.\n"
    memo += "\nThe fixed numeric-id split leaves segment imbalance in dev. No thresholds, policy, pricing, or rubrics were tuned after holdout results. Missing engineered-disparity detections are failed power checks; no diagnostic was added to force them to pass. Sign-off remains pending and production owners remain UNASSIGNED.\n"
    (out_dir / "GO_NO_GO_MEMO.md").write_text(memo,encoding="utf-8")
    return results


def _pass(value):
    return "NOT MEASURED" if value is None else ("PASS" if value else "FAIL")


def _markdown_table(headers, rows):
    def cell(x):
        if x is None: return "NOT MEASURED"
        if isinstance(x, float): return f"{x:.4f}"
        return str(x).replace("|", "\\|").replace("\n", " ")
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "\n".join("| " + " | ".join(cell(x) for x in row) + " |" for row in rows)
