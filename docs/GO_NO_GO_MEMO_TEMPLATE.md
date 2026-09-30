# Go / no-go memo: Jev loan appraisal support

> This template was written on 2026-09-29, before any evaluation ran. Thresholds come from
> `config/parity_thresholds.yaml` (version `prereg-2026.09.29-v1`). The harness fills every
> `{{placeholder}}`. A placeholder left unfilled means the memo is incomplete and can't be used.

| Field | Value |
|---|---|
| Run ID | {{run_id}} |
| Date | {{run_date}} |
| Backend | {{backend}} (model version(s): {{model_versions}}) |
| Policy version | {{policy_version}} |
| Pricing version | {{pricing_version}} |
| Book | {{book_n}} files, generator {{generator_version}}, seed {{seed}} |
| Split | {{split}}: headline numbers come from the holdout (80%), and policy was tuned only on dev (20%) |
| Pre-registration | {{prereg_version}} |

## 1. Recommendation

**{{recommendation}}** (GO / NO-GO / PROVISIONAL: NOT A GO)

{{recommendation_rationale}}

Rule: a run on the simulator backend can never be a GO. It is PROVISIONAL at best.

## 2. Stop conditions (pre-registered)

The project stops, or the named module stops, if any of these is true. Each row gets a measured value and PASS/FAIL.

| # | Condition | Threshold | Measured | Result |
|---|---|---|---|---|
| S1 | PII gate catch rate on adversarial fixtures | = 100% | {{pii_catch_rate}} | {{s1}} |
| S2 | Known-PII hits in the full-book outbound leak scan | = 0 | {{leak_hits}} | {{s2}} |
| S3 | Audit chain verification on the full replay | passes | {{audit_verify}} | {{s3}} |
| S4 | Trail completeness | = 100% | {{trail_completeness}} | {{s4}} |
| S5 | Share of routing questions failing calibration (ECE > 0.08 or confident-wrong > 2%) | ≤ 25% | {{calib_fail_share}} | {{s5}} |
| S6 | Module B fraud recall at the investigation threshold | ≥ 0.80 | {{b_recall}} | {{s6}} |
| S7 | Module C agreement on non-borderline files | ≥ 0.85 | {{c_agreement}} | {{s7}} |
| S8 | Module C borderline share | ≤ 35% | {{c_borderline_share}} | {{s8}} |
| S9 | Parity breach not explained by an engineered disparity | none | {{unexplained_parity_breaches}} | {{s9}} |
| S10 | Engineered disparities the tests missed (no test power) | none | {{undetected_disparities}} | {{s10}} |
| S11 | Call fallback rate (timeouts and errors) | ≤ 5% | {{fallback_rate}} | {{s11}} |
| S12 | Cost per file | ≤ $0.01 | {{usd_per_file}} | {{s12}} |
| S13 | Backend is real Jev | required for GO | {{backend}} | {{s13}} |

Per-module no-go: a module that fails to beat the rules-only baseline by at least 2 pp agreement should be replaced by rules. See section 3.

## 3. Agreement with the synthetic credit decision

{{agreement_table}}

The table has these columns: module, metric, system, rules baseline, delta (pp), n, and verdict.

## 4. Calibration by question (not aggregated)

{{calibration_table}}

The table has these columns: qid, type, n, ECE, MCE, Brier or level-MAE, confident-wrong count, confident-wrong rate, routing-relevant, and verdict. Every question in the catalogue gets its own row.

## 5. Fairness

### 5.1 Approval and calibration parity
{{parity_table}}

### 5.2 Proxy reconstruction probe
{{proxy_table}}

### 5.3 Engineered disparities: did the tests catch them?
{{disparity_detection_table}}

### 5.4 Failures, named plainly
{{fairness_failures_narrative}}

## 6. PII

{{pii_section}}

## 7. Cost and latency at production state sizes

{{cost_latency_table}}

## 8. Disagreements

Summary is below. The full report is `{{disagreement_report_path}}`.

{{disagreement_summary}}

## 9. Deviations from pre-registration

{{deviations}}

## 10. Conditions under which this project should stop, now or later

1. Any outbound request found to contain a personal identifier (S1, S2).
2. The audit chain fails to verify, or any file's trail is incomplete (S3, S4).
3. Routing-relevant questions stay miscalibrated after one rubric revision (S5).
4. The fraud screen misses more than 20% of fraud (S6).
5. A parity breach can't be explained or fixed within one policy revision (S9), or the fairness tests are shown to lack power (S10).
6. Real-Jev latency pushes the fallback rate above 5% (S11). The online advisory service stays off until it doesn't.
7. Any module fails to beat the rules-only baseline. Retire that module in favour of rules.
8. A regulator, internal audit, or model-risk review finds that the scoring layer is being treated as the decision-maker.

## 11. Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Chief Risk Officer (model risk) | {{owner_model_risk}} | | |
| Head of Credit Policy | | | |
| Head, Fraud Risk Control Unit | {{owner_fraud}} | | |
