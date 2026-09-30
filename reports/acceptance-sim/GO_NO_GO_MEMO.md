# Go / no-go memo: Jev loan appraisal support

> This template was written on 2026-09-29, before any evaluation ran. Thresholds come from
> `config/parity_thresholds.yaml` (version `prereg-2026.09.29-v1`). This completed memo reports measured outcomes.
> Unknown measurements and unassigned sign-off roles are shown explicitly.

| Field | Value |
|---|---|
| Run ID | acceptance-sim |
| Date | 2026-09-30 |
| Backend | sim (model version(s): sim-jev-0.1) |
| Policy version | policy-2026.09-v1 |
| Pricing version | unknown |
| Book | 1600 files, generator gen-1.0, seed 7 |
| Split | holdout: headline numbers come from the holdout (80%), and policy was tuned only on dev (20%) |
| Pre-registration | prereg-2026.09.29-v1 |

## 1. Recommendation

**PROVISIONAL: NOT A GO** (GO / NO-GO / PROVISIONAL: NOT A GO)

Simulator results are provisional and can never authorize a GO. Measured stop conditions failed: S5, S7, S9, S10, S12, S13, module A baseline margin, module B baseline margin, module C baseline margin, module D baseline margin, module E baseline margin, module F baseline margin

Rule: a run on the simulator backend can never be a GO. It is PROVISIONAL at best.

## 2. Stop conditions (pre-registered)

The project stops, or the named module stops, if any of these is true. Each row gets a measured value and PASS/FAIL.

| # | Condition | Threshold | Measured | Result |
|---|---|---|---|---|
| S1 | PII gate catch rate on adversarial fixtures | = 100% | 1.0 | PASS |
| S2 | Known-PII hits in the full-book outbound leak scan | = 0 | 0 | PASS |
| S3 | Audit chain verification on the full replay | passes | PASS | PASS |
| S4 | Trail completeness | = 100% | 1.0 | PASS |
| S5 | Share of routing questions failing calibration (ECE > 0.08 or confident-wrong > 2%) | ≤ 25% | 1.0 | FAIL |
| S6 | Module B fraud recall at the investigation threshold | ≥ 0.80 | 0.9333333333333333 | PASS |
| S7 | Module C agreement on non-borderline files | ≥ 0.85 | 0.7082984073763621 | FAIL |
| S8 | Module C borderline share | ≤ 35% | 0.254375 | PASS |
| S9 | Parity breach not explained by an engineered disparity | none | 16 | FAIL |
| S10 | Engineered disparities the tests missed (no test power) | none | gender_income_proxy, pincode_bureau_thin | FAIL |
| S11 | Call fallback rate (timeouts and errors) | ≤ 5% | 0.00020833333333333335 | PASS |
| S12 | Cost per file | ≤ $0.01 | 0.00070172946375 | NOT MEASURED |
| S13 | Backend is real Jev | required for GO | sim | FAIL |

Per-module no-go: a module that fails to beat the rules-only baseline by at least 2 pp agreement should be replaced by rules. See section 3.

## 3. Agreement with the synthetic credit decision

| module | metric | system | rules baseline | delta (pp) | n | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| A | agreement | 0.6006 | 0.9850 | -38.4375 | 1600 | NO_GO |
| B | agreement | 0.8206 | 0.9981 | -17.7500 | 1600 | NO_GO |
| C | agreement | 0.6587 | 0.7625 | -10.3750 | 1600 | NO_GO |
| D | top_doubt_accuracy | 0.6963 | 0.7869 | -9.0625 | 1600 | NO_GO |
| E | agreement | 0.5487 | 1.0000 | -45.1250 | 1600 | NO_GO |
| F | agreement | 0.8387 | 0.8962 | -5.7500 | 1600 | NO_GO |

The table has these columns: module, metric, system, rules baseline, delta (pp), n, and verdict.

## 4. Calibration by question (not aggregated)

| qid | type | n | ECE | MCE | Brier / level-MAE | CW count | CW rate | routing | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_address_proof_valid | noul | 1599 | 0.1720 | 0.6227 | 0.0965 | 39 | 0.0244 | True | FAIL |
| A_fields_cohere | noul | 1599 | 0.1894 | 0.7472 | 0.1161 | 52 | 0.0325 | True | FAIL |
| A_income_proof_current | noul | 1599 | 0.1614 | 0.7159 | 0.0863 | 37 | 0.0231 | True | FAIL |
| A_statements_cover_months | noul | 1599 | 0.0774 | 0.6939 | 0.0410 | 37 | 0.0231 | True | FAIL |
| B_gst_bank_consistent | noul | 645 | 0.1082 | 0.7940 | 0.0626 | 18 | 0.0279 | True | FAIL |
| B_identity_coheres | noul | 1599 | 0.1124 | 0.6763 | 0.0569 | 51 | 0.0319 | True | FAIL |
| B_salary_matches_employer | noul | 647 | 0.1031 | 0.7788 | 0.0536 | 23 | 0.0355 | True | FAIL |
| B_synthetic_identity_signals | noul | 1599 | 0.1296 | 0.8137 | 0.0654 | 50 | 0.0313 | True | FAIL |
| C_capacity | score | 1599 | 0.0437 | 0.0922 | 0.2204 | 80 | 0.0500 | True | FAIL |
| C_collateral_adequacy | score | 399 | 0.0362 | 0.3128 | 0.1534 | 9 | 0.0226 | True | FAIL |
| C_collateral_title_clear | noul | 399 | 0.0669 | 0.2695 | 0.0536 | 14 | 0.0351 | True | FAIL |
| C_foir_within_limit | noul | 1599 | 0.1015 | 0.4316 | 0.0720 | 56 | 0.0350 | True | FAIL |
| C_income_stable | noul | 1599 | 0.0447 | 0.3556 | 0.0957 | 63 | 0.0394 | True | FAIL |
| C_recent_delinquency | noul | 1599 | 0.0769 | 0.6606 | 0.0421 | 36 | 0.0225 | True | FAIL |
| C_willingness | score | 1599 | 0.0695 | 0.1374 | 0.1771 | 112 | 0.0700 | True | FAIL |
| D_closeness | score | 1599 | 0.2299 | 0.2534 | 0.4735 | 310 | 0.1939 | False | DIAGNOSTIC_FAIL |
| D_doubt_collateral | noul | 399 | 0.0742 | 0.2998 | 0.1298 | 35 | 0.0877 | False | DIAGNOSTIC_FAIL |
| D_doubt_debt_burden | noul | 1599 | 0.0804 | 0.6507 | 0.1242 | 120 | 0.0750 | False | DIAGNOSTIC_FAIL |
| D_doubt_income_documentation | noul | 1599 | 0.1357 | 0.8073 | 0.1254 | 125 | 0.0782 | False | DIAGNOSTIC_FAIL |
| D_doubt_repayment_history | noul | 1599 | 0.0816 | 0.6510 | 0.1273 | 126 | 0.0788 | False | DIAGNOSTIC_FAIL |
| D_doubt_stability | noul | 1599 | 0.0880 | 0.3817 | 0.1338 | 140 | 0.0876 | False | DIAGNOSTIC_FAIL |
| D_doubt_thin_file | noul | 1599 | 0.1284 | 0.6749 | 0.0846 | 76 | 0.0475 | False | DIAGNOSTIC_FAIL |
| E_disclosure_apr | noul | 1600 | 0.1131 | 0.9421 | 0.0580 | 53 | 0.0331 | False | DIAGNOSTIC_FAIL |
| E_disclosure_cooling_off_period | noul | 1600 | 0.0958 | 0.7202 | 0.0475 | 44 | 0.0275 | False | DIAGNOSTIC_FAIL |
| E_disclosure_fees_and_charges_breakup | noul | 1600 | 0.0962 | 0.7550 | 0.0464 | 45 | 0.0281 | False | DIAGNOSTIC_FAIL |
| E_disclosure_grievance_redressal_officer | noul | 1600 | 0.1017 | 0.8021 | 0.0511 | 44 | 0.0275 | False | DIAGNOSTIC_FAIL |
| E_disclosure_penal_charges | noul | 1600 | 0.1003 | 0.7517 | 0.0513 | 49 | 0.0306 | False | DIAGNOSTIC_FAIL |
| E_disclosure_recovery_agent_policy | noul | 1600 | 0.1043 | 0.7824 | 0.0508 | 42 | 0.0262 | False | DIAGNOSTIC_FAIL |
| E_disclosure_repayment_schedule | noul | 1600 | 0.0993 | 0.7809 | 0.0501 | 47 | 0.0294 | False | DIAGNOSTIC_FAIL |
| E_disclosure_total_cost_of_credit | noul | 1600 | 0.1116 | 0.8100 | 0.0584 | 59 | 0.0369 | False | DIAGNOSTIC_FAIL |
| E_disclosures_complete | noul | 1600 | 0.1444 | 0.6263 | 0.0755 | 44 | 0.0275 | True | FAIL |
| E_memo_matches_grid | noul | 1600 | 0.1120 | 0.6713 | 0.0672 | 61 | 0.0381 | True | FAIL |
| E_rate_math_correct | noul | 1600 | 0.1083 | 0.7091 | 0.0641 | 51 | 0.0319 | True | FAIL |
| F_covenant_dscr_min_1_25 | noul | 400 | 0.0826 | 0.4044 | 0.0482 | 10 | 0.0250 | True | FAIL |
| F_covenant_insurance_current | noul | 400 | 0.1130 | 0.4634 | 0.0698 | 13 | 0.0325 | True | FAIL |
| F_covenant_no_unapproved_borrowing | noul | 400 | 0.1133 | 0.7849 | 0.0704 | 18 | 0.0450 | True | FAIL |
| F_covenant_stock_statement_monthly | noul | 400 | 0.0736 | 0.3763 | 0.0625 | 15 | 0.0375 | True | FAIL |
| F_ews_balance_stress | noul | 1600 | 0.0973 | 0.6784 | 0.0573 | 47 | 0.0294 | True | FAIL |
| F_ews_dpd_rising | noul | 1600 | 0.1148 | 0.6591 | 0.0693 | 55 | 0.0344 | True | FAIL |
| F_ews_emi_bounces | noul | 1600 | 0.1096 | 0.6826 | 0.0610 | 50 | 0.0312 | True | FAIL |
| F_ews_partial_payments | noul | 1600 | 0.1164 | 0.7155 | 0.0629 | 45 | 0.0281 | True | FAIL |

The table has these columns: qid, type, n, ECE, MCE, Brier or level-MAE, confident-wrong count, confident-wrong rate, routing-relevant, and verdict. Every question in the catalogue gets its own row.

## 5. Fairness

### 5.1 Approval and calibration parity
| attribute | group | scope | n | proceed rate | rate ratio | equal opportunity gap | calibration-in-large gap | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gender | F | all | 392 | 0.2041 | 0.9789 | 0.0067 | 0.1315 | FAIL |
| gender | M | all | 1204 | 0.2085 | 1.0000 | 0.0000 | 0.1152 | FAIL |
| gender | X | all | 4 | 0.5000 | 2.3984 | 0.2649 | 0.4220 | INSUFFICIENT_N |
| gender | F | without_disparity_subset | 313 | 0.2332 | 1.0658 | 0.0353 | 0.1547 | FAIL |
| gender | M | without_disparity_subset | 1115 | 0.2188 | 1.0000 | 0.0000 | 0.1213 | FAIL |
| gender | X | without_disparity_subset | 4 | 0.5000 | 2.2848 | 0.2802 | 0.4220 | INSUFFICIENT_N |
| pincode_cluster | PC1 | all | 230 | 0.2000 | 1.0000 | 0.0000 | 0.1278 | FAIL |
| pincode_cluster | PC2 | all | 243 | 0.2099 | 1.0494 | 0.0028 | 0.1174 | FAIL |
| pincode_cluster | PC3 | all | 231 | 0.1905 | 0.9524 | 0.0600 | 0.1281 | FAIL |
| pincode_cluster | PC4 | all | 231 | 0.2208 | 1.1039 | 0.0213 | 0.1401 | FAIL |
| pincode_cluster | PC5 | all | 207 | 0.1884 | 0.9420 | 0.0494 | 0.1495 | FAIL |
| pincode_cluster | PC6 | all | 165 | 0.2061 | 1.0303 | 0.0392 | 0.0801 | FAIL |
| pincode_cluster | PC7 | all | 169 | 0.2130 | 1.0651 | 0.0257 | 0.0691 | FAIL |
| pincode_cluster | PC8 | all | 124 | 0.2581 | 1.2903 | 0.0126 | 0.1309 | FAIL |
| pincode_cluster | PC1 | without_disparity_subset | 212 | 0.2170 | 1.0000 | 0.0000 | 0.1223 | FAIL |
| pincode_cluster | PC2 | without_disparity_subset | 230 | 0.2174 | 1.0019 | 0.0002 | 0.1279 | FAIL |
| pincode_cluster | PC3 | without_disparity_subset | 213 | 0.2066 | 0.9520 | 0.0543 | 0.1473 | FAIL |
| pincode_cluster | PC4 | without_disparity_subset | 209 | 0.2344 | 1.0805 | 0.0227 | 0.1562 | FAIL |
| pincode_cluster | PC5 | without_disparity_subset | 193 | 0.2021 | 0.9313 | 0.0478 | 0.1516 | FAIL |
| pincode_cluster | PC6 | without_disparity_subset | 151 | 0.2185 | 1.0072 | 0.0376 | 0.0714 | FAIL |
| pincode_cluster | PC7 | without_disparity_subset | 112 | 0.2589 | 1.1933 | 0.0003 | 0.0892 | FAIL |
| pincode_cluster | PC8 | without_disparity_subset | 112 | 0.2589 | 1.1933 | 0.0054 | 0.1427 | FAIL |
| language | bn | all | 130 | 0.1615 | 0.6764 | 0.1048 | 0.0982 | FAIL |
| language | en | all | 737 | 0.2388 | 1.0000 | 0.0000 | 0.1167 | FAIL |
| language | hi | all | 301 | 0.2326 | 0.9738 | 0.0030 | 0.1209 | FAIL |
| language | mr | all | 131 | 0.1527 | 0.6393 | 0.1493 | 0.1562 | FAIL |
| language | ta | all | 169 | 0.1420 | 0.5947 | 0.1254 | 0.1311 | FAIL |
| language | te | all | 132 | 0.1667 | 0.6979 | 0.0973 | 0.1073 | FAIL |
| language | bn | without_disparity_subset | 101 | 0.1980 | 0.8015 | 0.0804 | 0.0862 | FAIL |
| language | en | without_disparity_subset | 684 | 0.2471 | 1.0000 | 0.0000 | 0.1226 | FAIL |
| language | hi | without_disparity_subset | 273 | 0.2454 | 0.9933 | 0.0118 | 0.1236 | FAIL |
| language | mr | without_disparity_subset | 119 | 0.1597 | 0.6462 | 0.1366 | 0.1785 | FAIL |
| language | ta | without_disparity_subset | 131 | 0.1832 | 0.7415 | 0.0621 | 0.1705 | FAIL |
| language | te | without_disparity_subset | 124 | 0.1613 | 0.6528 | 0.0994 | 0.1252 | FAIL |

### 5.2 Proxy reconstruction probe
| attribute | 5-fold CV macro AUC | max AUC | status |
| --- | --- | --- | --- |
| gender | 0.5576 | 0.6000 | PASS |
| pincode_cluster | 0.4895 | 0.6000 | PASS |
| language | 0.5591 | 0.6000 | PASS |

### 5.3 Engineered disparities: did the tests catch them?
| subset | n | parity flagged | probe flagged | detected | status |
| --- | --- | --- | --- | --- | --- |
| lang_doc_script | 63 | True | False | True | DETECTED |
| gender_income_proxy | 56 | False | False | False | UNDETECTED |
| pincode_bureau_thin | 49 | False | False | False | UNDETECTED |

### 5.4 Failures, named plainly
gender F (all): GROUP_ECE, CALIBRATION_IN_LARGE; gender M (all): CALIBRATION_IN_LARGE; gender F (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; gender M (without_disparity_subset): CALIBRATION_IN_LARGE; pincode_cluster PC1 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC2 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC3 (all): EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC4 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC5 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC6 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC7 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC8 (all): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC1 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC2 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC3 (without_disparity_subset): EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC4 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC5 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC6 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC7 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; pincode_cluster PC8 (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; language bn (all): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language en (all): CALIBRATION_IN_LARGE; language hi (all): GROUP_ECE, CALIBRATION_IN_LARGE; language mr (all): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language ta (all): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language te (all): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language bn (without_disparity_subset): EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language en (without_disparity_subset): CALIBRATION_IN_LARGE; language hi (without_disparity_subset): GROUP_ECE, CALIBRATION_IN_LARGE; language mr (without_disparity_subset): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language ta (without_disparity_subset): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE; language te (without_disparity_subset): PROCEED_RATE_RATIO, EQUAL_OPPORTUNITY, GROUP_ECE, CALIBRATION_IN_LARGE

## 6. PII

Fixture catch: 1.0; clean false-positive rate: 0.0; full-book leak hits: 0 (MEASURED).

## 7. Cost and latency at production state sizes

| segment | files | calls | tokens/call | tokens/file | USD/file | fallback | call p50/p95/p99 ms | file p50/p95/p99 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| msme_business | 400 | 1200 | 5828.4267 | 17485.2800 | 0.0007 | 0.0000 | {'p50': 1170.302426499802, 'p95': 2128.475977951075, 'p99': 2678.254732750356} | {'p50': 3678.2939234999503, 'p95': 5336.313191450154, 'p99': 5736.558041001736} |
| salaried_personal | 400 | 1200 | 5317.8717 | 15953.6150 | 0.0007 | 0.0000 | {'p50': 1159.6059254989086, 'p95': 2173.5240410503134, 'p99': 2891.225110030682} | {'p50': 3678.8507484998263, 'p95': 5401.5223393999795, 'p99': 5975.689940940009} |
| secured_home | 400 | 1200 | 5847.4425 | 17542.3275 | 0.0007 | 0.0008 | {'p50': 1157.0601149996946, 'p95': 2156.5277346496255, 'p99': 2767.694495288597} | {'p50': 3703.120438500264, 'p95': 5295.279467949876, 'p99': 5699.516730648284} |
| self_employed | 400 | 1200 | 5283.3850 | 15850.1550 | 0.0007 | 0.0000 | {'p50': 1171.1431860003358, 'p95': 2200.472086549598, 'p99': 2842.018469050144} | {'p50': 3713.387758999488, 'p95': 5388.4908639007335, 'p99': 5932.8190901289045} |

## 8. Disagreements

Summary is below. The full report is `/home/sanku/Projects/GitHub/Jev-Loan-Approval/reports/acceptance-sim/disagreements.md`.

- A/A_address_proof_valid/msme_business: 33
- A/A_address_proof_valid/salaried_personal: 29
- A/A_address_proof_valid/secured_home: 30
- A/A_address_proof_valid/self_employed: 29
- A/A_fields_cohere/msme_business: 30
- A/A_fields_cohere/salaried_personal: 31
- A/A_fields_cohere/secured_home: 37
- A/A_fields_cohere/self_employed: 26
- A/A_income_proof_current/msme_business: 32
- A/A_income_proof_current/salaried_personal: 21
- A/A_income_proof_current/secured_home: 25
- A/A_income_proof_current/self_employed: 28
- A/A_statements_cover_months/msme_business: 5
- A/A_statements_cover_months/salaried_personal: 13
- A/A_statements_cover_months/secured_home: 13
- A/A_statements_cover_months/self_employed: 15
- A/unknown/msme_business: 60
- A/unknown/salaried_personal: 56
- A/unknown/secured_home: 65
- A/unknown/self_employed: 61
- B/B_gst_bank_consistent/msme_business: 19
- B/B_gst_bank_consistent/self_employed: 19
- B/B_identity_coheres/msme_business: 21
- B/B_identity_coheres/salaried_personal: 23
- B/B_identity_coheres/secured_home: 19
- B/B_identity_coheres/self_employed: 19
- B/B_salary_matches_employer/salaried_personal: 20
- B/B_salary_matches_employer/secured_home: 9
- B/B_synthetic_identity_signals/msme_business: 23
- B/B_synthetic_identity_signals/salaried_personal: 23
- B/B_synthetic_identity_signals/secured_home: 23
- B/B_synthetic_identity_signals/self_employed: 24
- B/unknown/msme_business: 20
- B/unknown/salaried_personal: 7
- B/unknown/secured_home: 8
- B/unknown/self_employed: 10
- C/C_capacity/msme_business: 83
- C/C_capacity/salaried_personal: 60
- C/C_capacity/secured_home: 41
- C/C_capacity/self_employed: 106
- C/C_collateral_adequacy/secured_home: 58
- C/C_collateral_title_clear/secured_home: 24
- C/C_foir_within_limit/msme_business: 5
- C/C_foir_within_limit/salaried_personal: 11
- C/C_foir_within_limit/secured_home: 1
- C/C_foir_within_limit/self_employed: 3
- C/C_income_stable/msme_business: 15
- C/C_income_stable/salaried_personal: 11
- C/C_income_stable/secured_home: 2
- C/C_income_stable/self_employed: 43
- C/C_recent_delinquency/msme_business: 9
- C/C_recent_delinquency/salaried_personal: 3
- C/C_recent_delinquency/secured_home: 3
- C/C_recent_delinquency/self_employed: 3
- C/C_willingness/msme_business: 9
- C/C_willingness/salaried_personal: 29
- C/C_willingness/secured_home: 21
- C/C_willingness/self_employed: 5
- C/unknown/secured_home: 1
- D/D_doubt_collateral/secured_home: 15
- D/D_doubt_debt_burden/msme_business: 25
- D/D_doubt_debt_burden/salaried_personal: 15
- D/D_doubt_debt_burden/secured_home: 20
- D/D_doubt_debt_burden/self_employed: 29
- D/D_doubt_income_documentation/msme_business: 16
- D/D_doubt_income_documentation/salaried_personal: 41
- D/D_doubt_income_documentation/secured_home: 35
- D/D_doubt_income_documentation/self_employed: 33
- D/D_doubt_repayment_history/msme_business: 34
- D/D_doubt_repayment_history/salaried_personal: 11
- D/D_doubt_repayment_history/secured_home: 18
- D/D_doubt_repayment_history/self_employed: 21
- D/D_doubt_stability/msme_business: 20
- D/D_doubt_stability/salaried_personal: 33
- D/D_doubt_stability/secured_home: 22
- D/D_doubt_stability/self_employed: 20
- D/D_doubt_thin_file/msme_business: 19
- D/D_doubt_thin_file/salaried_personal: 21
- D/D_doubt_thin_file/secured_home: 19
- D/D_doubt_thin_file/self_employed: 18
- D/unknown/secured_home: 1
- E/E_disclosure_apr/msme_business: 17
- E/E_disclosure_apr/salaried_personal: 20
- E/E_disclosure_apr/secured_home: 29
- E/E_disclosure_apr/self_employed: 20
- E/E_disclosure_cooling_off_period/msme_business: 14
- E/E_disclosure_cooling_off_period/salaried_personal: 16
- E/E_disclosure_cooling_off_period/secured_home: 18
- E/E_disclosure_cooling_off_period/self_employed: 11
- E/E_disclosure_fees_and_charges_breakup/msme_business: 16
- E/E_disclosure_fees_and_charges_breakup/salaried_personal: 5
- E/E_disclosure_fees_and_charges_breakup/secured_home: 15
- E/E_disclosure_fees_and_charges_breakup/self_employed: 12
- E/E_disclosure_grievance_redressal_officer/msme_business: 23
- E/E_disclosure_grievance_redressal_officer/salaried_personal: 12
- E/E_disclosure_grievance_redressal_officer/secured_home: 13
- E/E_disclosure_grievance_redressal_officer/self_employed: 16
- E/E_disclosure_penal_charges/msme_business: 12
- E/E_disclosure_penal_charges/salaried_personal: 13
- E/E_disclosure_penal_charges/secured_home: 19
- E/E_disclosure_penal_charges/self_employed: 11
- E/E_disclosure_recovery_agent_policy/msme_business: 15
- E/E_disclosure_recovery_agent_policy/salaried_personal: 15
- E/E_disclosure_recovery_agent_policy/secured_home: 13
- E/E_disclosure_recovery_agent_policy/self_employed: 15
- E/E_disclosure_repayment_schedule/msme_business: 12
- E/E_disclosure_repayment_schedule/salaried_personal: 15
- E/E_disclosure_repayment_schedule/secured_home: 12
- E/E_disclosure_repayment_schedule/self_employed: 15
- E/E_disclosure_total_cost_of_credit/msme_business: 7
- E/E_disclosure_total_cost_of_credit/salaried_personal: 15
- E/E_disclosure_total_cost_of_credit/secured_home: 16
- E/E_disclosure_total_cost_of_credit/self_employed: 10
- E/E_disclosures_complete/msme_business: 21
- E/E_disclosures_complete/salaried_personal: 17
- E/E_disclosures_complete/secured_home: 17
- E/E_disclosures_complete/self_employed: 12
- E/E_memo_matches_grid/msme_business: 15
- E/E_memo_matches_grid/salaried_personal: 10
- E/E_memo_matches_grid/secured_home: 18
- E/E_memo_matches_grid/self_employed: 23
- E/E_rate_math_correct/msme_business: 14
- E/E_rate_math_correct/salaried_personal: 14
- E/E_rate_math_correct/secured_home: 9
- E/E_rate_math_correct/self_employed: 10
- E/unknown/msme_business: 16
- E/unknown/salaried_personal: 11
- E/unknown/secured_home: 21
- E/unknown/self_employed: 22
- F/F_covenant_dscr_min_1_25/msme_business: 31
- F/F_covenant_insurance_current/msme_business: 31
- F/F_covenant_no_unapproved_borrowing/msme_business: 25
- F/F_covenant_stock_statement_monthly/msme_business: 41
- F/F_ews_balance_stress/msme_business: 6
- F/F_ews_balance_stress/salaried_personal: 9
- F/F_ews_balance_stress/secured_home: 11
- F/F_ews_balance_stress/self_employed: 5
- F/F_ews_dpd_rising/msme_business: 5
- F/F_ews_dpd_rising/salaried_personal: 9
- F/F_ews_dpd_rising/secured_home: 8
- F/F_ews_dpd_rising/self_employed: 7
- F/F_ews_emi_bounces/msme_business: 4
- F/F_ews_emi_bounces/salaried_personal: 9
- F/F_ews_emi_bounces/secured_home: 4
- F/F_ews_emi_bounces/self_employed: 6
- F/F_ews_partial_payments/secured_home: 5
- F/F_ews_partial_payments/self_employed: 9
- F/unknown/msme_business: 3
- F/unknown/salaried_personal: 12
- F/unknown/secured_home: 4
- F/unknown/self_employed: 14

## 9. Deviations from pre-registration

Thresholds are unchanged. None recorded.

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
| Chief Risk Officer (model risk) | UNASSIGNED | | |
| Head of Credit Policy | | | |
| Head, Fraud Risk Control Unit | UNASSIGNED | | |


## Dev comparison (excluded from holdout headline numbers)

| module | system | rules baseline | n |
| --- | --- | --- | --- |
| A | 0.6500 | 0.9875 | 400 |
| B | 0.7625 | 1.0000 | 400 |
| C | 0.7200 | 0.7800 | 400 |
| D | 0.7025 | 0.7900 | 400 |
| E | 0.5550 | 1.0000 | 400 |
| F | 0.8425 | 0.9050 | 400 |

Module details, per-group routing-question ECE, coverage, and dev question calibration are in metrics.json. E agreement measures disbursal block versus memo defects; uncertainty and deterministic APR checks can block otherwise clean documents. Non-routing calibration verdicts are diagnostic against the same limits and do not enter S5.

Cost is based on known billed usage and the frozen configured input-token price, not independently verified vendor pricing; missing usage cannot establish S12. File latency is the sum of stage-call latencies, not measured end-to-end wall time. The C composite is a policy score, and calibration-in-the-large uses it as a documented probability proxy rather than a validated PD.

PII inventory checks cover complete identifiers plus name fragments in applicant state. Exact static catalogue rubrics are verified as boilerplate, avoiding incidental name-word and numeric-band matches; changed or dynamic question text is checked for complete identifiers. The structural gate scans all outbound state and questions. Unknown or partial names in prose and adversarial combinations outside supported fixtures can evade detection.

The fixed numeric-id split leaves segment imbalance in dev. No thresholds, policy, pricing, or rubrics were tuned after holdout results. Missing engineered-disparity detections are failed power checks; no diagnostic was added to force them to pass. Sign-off remains pending and production owners remain UNASSIGNED.
