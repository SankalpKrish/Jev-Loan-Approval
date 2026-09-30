# Data card: the synthetic Indian loan book

Generator version `gen-1.0`. Code: `src/jevloan/data/` and `src/jevloan/finance.py`. Policy files:
`config/policy/sanction_grid_v1.yaml` (`grid-v1`) and `config/policy/kfs_disclosures_v1.yaml` (`kfs-v1`).
Regenerate with `jevloan data generate --n 2000 --seed 7`; read the rates with `jevloan data stats`.

## 1. Purpose, and what this data must never be used for

The book exists so that the loan appraisal support system can be built, tested and evaluated when no real
borrower data is available and none may be used. It provides:

* loan files with known ground truth (would a credit policy sanction it, is it fraud, what is missing, what
  happens in 12 months), so that every module's agreement with the truth and every question's calibration can be measured;
* engineered disparities, so that the fairness tests have something real to catch;
* documents full of realistic raw personal data, so that the redactor and the PII gate are tested on something
  that looks like the real thing.

**Non-use.** Every person, employer, business, address, identifier and document here is invented. Do not use this
data to estimate real-world credit risk, to set real policy thresholds, to train a model that will score real
people, or to claim the system works on real files. Numbers measured on it (including on the simulator) are
development evidence only. Identifiers are random and format-valid, so an invented phone or Aadhaar number can
coincide with a real one; never dial, email or look up anything in this book.

## 2. How the book is generated

`generate_book(n, seed)` builds file `i` from its own `random.Random(f"{seed}:{i}")`. The same seed gives the same
book byte for byte, one file can be regenerated alone, and a smaller book is a prefix of a larger one.
2,000 files take about 2 seconds.

1. **Segment.** A fixed 20-file cycle gives exactly 30% salaried personal, 20% self-employed, 25% MSME and 25%
   secured home in any multiple of 20 files (and any prefix is close to representative).
2. **Demographics.** Gender, language (en 44%, hi 20%, ta 10%, bn 9%, mr 9%, te 8%), PIN cluster PC1 to PC8 and city
   are drawn without reference to any feature or label. The name comes from the region of the city or language.
   The PIN cluster is a synthetic label; it is not derived from the PIN. The three disparity draws happen here.
3. **Employment and fraud.** Home-loan applicants are salaried 65% of the time. Self-employed files have a GST
   registration 60% of the time. The fraud type is one categorical draw (section 5).
4. **Latent quality `z ~ N(0,1)`.** Higher `z` means a better borrower. It drives the bureau score
   (`725 + 55z + noise`), the chance and severity of delinquency, write-offs, bounces, income volatility and card
   utilisation, so the features correlate as real ones do.
5. **Income and loan size.** Verified income is log-normal per segment. A target FOIR (MSME: target DSCR) sets the
   room for the new EMI; the principal is what that EMI repays at the file's rate and tenure, rounded to a sensible
   ticket. Tenure is capped by the sanction grid and by retirement age (60 salaried, 70 otherwise). Rates are a
   segment base plus a bureau adjustment plus noise.
6. **Blocks.** Bank behaviour, GST (turnover is bank credits times a ratio near 1.03; a fraud file gets a ratio
   above 2 or below 0.5), property (LTV target around 70%, two valuations, title status and legal opinion),
   identity signals, co-applicant.
7. **Defects.** Missing items and memo defects are independent Bernoulli draws (section 5), each shown in the
   documents, memo or KFS.
8. **Labels.** Computed from the true features by `data/risk.py` (sections 3 and 4), then the 12-month outcome is
   drawn from the risk function, six months of repayment are generated consistent with the outcome, and the MSME
   covenants are drawn correlated with the outcome.
9. **Documents, memo, KFS.** Rendered last, from the plan of defects. Every raw identifier written is recorded in
   `pii_inventory`.

### Document limits

At most 6 documents per file (mean 5.6), each at most 70 words (mean 31, max 55). Memo at most 150 words (mean 67,
max 98); KFS at most 150 words (mean 120, max 126). The evidence is in the first sentences of each snippet.
About 1.4 KB of document JSON per file on average.

## 3. The risk function

`risk_pd(features) = sigmoid(intercept + sum(beta_j * x_j))`, clipped to [0.002, 0.95]. The features `x_j` are
standardised so that 0 is a typical file. All coefficients live in one dict, `COEFFICIENTS` in `data/risk.py`.

| name | beta | feature x |
|---|---|---|
| `intercept` | -3.90 | log-odds for a typical salaried file with every x = 0 (about 2%) |
| `seg_self_employed` | 0.15 | 1 if self-employed |
| `seg_msme_business` | -0.10 | 1 if MSME |
| `seg_secured_home` | -0.90 | 1 if secured home loan (secured lending defaults less) |
| `bureau_score` | -0.80 | (score - 725) / 60; 0 for new-to-credit |
| `ntc` | 0.55 | 1 if the real score is missing (new to credit) |
| `foir` | 0.60 | (FOIR - segment centre) / 0.15; centres 0.40 salaried, 0.38 self-employed, 0.60 MSME, 0.45 home |
| `income_volatility` | 0.30 | (CV - centre) / scale; centre 0.07, 0.28, 0.30 for salaried, self-employed, business owner |
| `max_dpd` | 0.55 | worst DPD in 12 months / 30 (capped at 120 days) |
| `emi_bounces` | 0.35 | bounces in 6 months (capped at 4) / 2 |
| `writeoff` | 0.90 | 1 if any write-off or settlement |
| `ltv` | 0.50 | (LTV - 65) / 12, home loans only |
| `title_risk` | 0.60 | disputed 1.0, pending mutation 0.5, plus 0.5 for an adverse legal opinion; home loans only |
| `business_vintage` | -0.35 | (min(years, 15) - centre) / 5; centres 5, 6, 9, 6 by segment |
| `gst_inconsistency` | 0.35 | max(0, abs(ln(GST turnover / bank credits)) - 0.12) / 0.25 (capped at 3); GST files only |
| `income_gap` | 0.30 | max(0, declared / verified - 1 - 0.05) / 0.15 (capped at 3) |
| `card_utilization` | 0.25 | (utilisation - 0.35) / 0.2 |
| `fraud_flag` | 1.20 | 1 if the file is fraud |

Two things to know. First, `fraud_flag` is an addition to the feature list in the plan: a fraud file is very
unlikely to repay, and without it the monitoring module would have nothing realistic to find. Second, the
documentation type (`informal_declared`), the language, gender, PIN cluster and the reported bureau score of a
thin-file subset file are **not** features, so the engineered disparities cannot move a label (section 6).

**12-month outcome.** `defaults` with probability `pd`, `slips` with probability `min(0.3, 1.5 * pd)`, `repays`
otherwise (`draw_outcome`).

## 4. The synthetic credit policy (what `sanctionable` means)

`synthetic_credit_decision(file)` returns `(sanctionable, failed_rules)` on the true features. A file is
sanctionable only if **none** of these rules fails:

| rule id | rule |
|---|---|
| `fraud` | the file is not fraud |
| `foir_above_limit` | FOIR = (existing + proposed EMI) / verified monthly income is at most 55% salaried, 50% self-employed, 60% home |
| `dscr_below_1_25` | MSME: DSCR = annual cash accruals / annual debt service is at least 1.25 (the MSME "income" field is monthly cash accruals, so DSCR = 1 / FOIR and this is the same as FOIR at most 80%) |
| `bureau_below_650` | bureau score at least 650 |
| `ntc_not_eligible` | new to credit is allowed only for a salaried personal loan with verified income of at least Rs. 50,000 a month |
| `max_dpd_60_plus` | worst DPD in 12 months is below 60 |
| `writeoff_or_settlement` | no write-offs or settlements |
| `ltv_above_limit` | home loans: LTV = loan / min(market value, second valuation) is within the grid limit (80% up to Rs. 75 lakh, 75% above; the plan's 80% is the ceiling and the grid tightens it to the RBI 75% for large tickets) |
| `title_not_clear` | home loans: title status is clear |
| `business_vintage_below_2y` | business vintage of at least 2 years (self-employed, MSME, and self-employed home applicants) |
| `pd_above_cutoff` | `risk_pd` is below 0.08 salaried, 0.10 self-employed, 0.10 MSME, 0.05 home |

`closeness_level` (0 to 4) is built from the same rules. Each rule gets a signed margin (positive passes; 1.0 is
comfortable), including the risk margin `(cutoff - pd) / (0.5 * cutoff)`; fraud is -3. The smallest margin decides:
at least 1.0 is level 4, at least 0.3 is 3, within 0.3 of the boundary is 2, at least -1.0 is 1, below that is 0.
Levels 3 and 4 are always sanctionable; 0 and 1 never are; level 2 straddles the boundary.

`primary_weakness` sums the risk-increasing contributions (`beta_j * x_j` above zero) per category and names the
largest: `repayment_history` (score, delinquency, bounces, write-off), `debt_burden` (FOIR, card use),
`employment_or_business_stability` (volatility, vintage), `income_documentation` (GST inconsistency, income gap),
`collateral` (LTV, title; home loans only), `bureau_thin_file` (real new-to-credit). If nothing pushes risk up by at
least 0.05, the category whose continuous features sit closest to the risky end is named.

## 5. Labels

### Base rates, measured (n = 2,000, seed 7, from `jevloan data stats`)

| rate | measured | target in the plan |
|---|---|---|
| sanctionable | 61.7% | 55 to 65% |
| fraud | 5.2% | about 5% |
| identity mismatch | 1.7% | 1.5% |
| salary pattern mismatch | 1.2% | 1.5% (salaried applicants only) |
| GST/bank mismatch | 1.5% | 1.5% (GST-registered business files only) |
| synthetic identity | 0.9% | 1% |
| at least one missing item | 15.5% | about 15% |
| income proof / address proof / statements / field coherence | 5.5% / 4.7% / 3.6% / 3.1% | |
| at least one memo defect | 19.5% | about 20% |
| memo condition mismatch / APR math wrong / a missing disclosure | 7.6% / 6.8% / 5.9% | |
| outcome: repays / slips / defaults | 87.1% / 7.0% / 5.9% | |
| mean `risk_pd` | 0.0628 | |
| engineered disparity subsets | 9.4% (189 files) | about 10% |

Sanctionable by segment: salaried 66.3%, self-employed 59.2%, MSME 64.4%, home 55.2%. Closeness levels 0 to 4:
23.5%, 8.8%, 13.5%, 20.5%, 33.7%. The fraud split adds up to 5.5% by design (1.5 + 1.5 + 1.5 + 1.0), so 5.2% is
sampling noise around it. Each draw is independent, so at n = 2,000 any rate moves by about half a percentage
point between seeds.

### Fraud, and the evidence for it

| `fraud_type` | how it is made | where the evidence is |
|---|---|---|
| `identity_mismatch` | one or two of: PAN, name, birth year differ | the `pan_card_text` document shows a different PAN, name or birth year (6 to 15 years off) from the applicant, salary slip or ITR; `identity.pan_aadhaar_linked` is more often False |
| `salary_pattern_mismatch` | salaried applicants (salaried personal and salaried home) | the salary slip names `application.employer_name`; the bank statement narration shows credits from a different employer, also in `bank.salary_narration_employer` |
| `gst_bank_mismatch` | GST-registered business files | `gst.turnover_12m_inr` is more than 2 times or under 0.5 times `gst.bank_credits_12m_inr`; both figures are written in the GST summary and the bank statement |
| `synthetic_identity` | any segment | new-to-credit with at most 3 months of history, phone under 3 months old, disposable email, `bureau_history_vs_age_consistent` False, address shared by 3 or more applications (each signal with 60 to 90% probability), a job or business under about one year, and every document ends "Auto-generated document; no signature required." |

Non-fraud files also carry a little noise in the identity signals (a disposable email in under 1%, a young phone
in about 1.5%), so no single signal is decisive.

### Missing items

| item | truth is "missing" when | evidence |
|---|---|---|
| `income_proof` | the income document (`salary_slip` for salaried applicants, `itr` otherwise) is absent, or its `month_age` is 3 or more | the document list and `month_age`; the "Received" stamp and the pay month or filing date |
| `address_proof` | expired, wrong type or mismatched | a utility bill older than 3 months, a rent agreement whose term ended before the application date, a passport whose expiry date has passed, a prepaid recharge receipt, an unregistered rent agreement on plain paper, a passport photo page without the address page, or an address whose PIN and line differ from the application |
| `statements` | `bank.months_covered` is under 6 (salaried personal, home) or 12 (self-employed, MSME) | the statement header says "(n months)" |
| `fields_coherence` | any of: declared income at least 1.5 times verified; salaried tenure more than 6 months past retirement at 60; years in job or business more than age minus 16 | `application` fields; the employer letter states months to superannuation and the start month |

These are drawn independently of credit quality, and the credit policy ignores them. A file that needs a
deficiency notice is not thereby a bad credit.

### Memo defects

| defect | truth | evidence |
|---|---|---|
| `memo_condition_mismatch` | the memo tenure exceeds the grid maximum for the product and ticket band, or a required condition of that grid row is missing from the memo | `sanction_memo.tenure_months`, `.conditions` and `.text` against `sanction_grid_v1.yaml` |
| `apr_math_wrong` | the stated APR is the plain rate ignoring fees, or is 0.5 percentage points or more away from the recomputed APR | `finance.apr_from_components(kfs.principal_inr, kfs.fees_inr, kfs.emi_inr, kfs.tenure_months)` against `kfs.apr_stated_pct`. Correct files are within 0.05. The plain-rate variant is used only when fees make the true APR at least 0.5 above the rate |
| `disclosure_missing:<id>` | a KFS section is left out | `kfs.disclosures_present` and the section headings in `kfs.text`. The `apr` section is never dropped, so `E_disclosure_apr` is always true |

APR convention: the nominal annualised IRR of the borrower's cash flows, `12 * monthly IRR * 100`, the convention of
the RBI KFS illustration. It is not the effective annual rate.

### Repayment and early warnings

Six months of repayment are generated from the outcome. `defaults`: onset of delinquency in month 2 to 6 (mostly 3
or 4), DPD rising by 10 to 35 days a month, bounces and partial payments, balance falling to 25 to 55%. `slips`: one
to three isolated events (a bounce or a partial payment), a small chance of a single month at 30 days or more.
`repays`: mostly clean, with a little noise (a few days late, a rare bounce).

`labels.ews_truth` is computed from those months:

* `F_ews_dpd_rising`: DPD strictly higher than the previous month in at least 3 of months 3 to 6, or any month at 30 or more;
* `F_ews_emi_bounces`: 2 or more bounced EMIs in the six months;
* `F_ews_partial_payments`: 2 or more partial payments;
* `F_ews_balance_stress`: the mean balance of months 5 and 6 is at least 40% below the mean of months 1 and 2.

Measured: dpd rising 6.8%, bounces 7.4%, partial payments 3.7%, balance stress 8.2% of files; among files that
default the rates are 91%, 87%, 32% and 84%, and among files that repay under 4% for each.

### MSME covenants

`post_disbursal.covenants` has four entries for every MSME file. `required` is the threshold and `reported_value`
what the borrower reported; a breach compares them, and `evidence_text` says the same in words.

| covenant | `required` | `reported_value` | breach when |
|---|---|---|---|
| `dscr_min_1_25` | 1.25 | DSCR for the last audited year (float) | reported below 1.25 |
| `stock_statement_monthly` | 6 | months of the last 6 in which the stock statement was on time (int) | reported below 6 |
| `no_unapproved_borrowing` | 0 | new facilities taken without consent (int) | reported above 0 |
| `insurance_current` | True | whether the insurance is current (bool) | reported False |

Breach probability by outcome (repays / slips / defaults): DSCR 5% / 25% / 60%, stock statements 8% / 30% / 55%,
borrowing 3% / 12% / 30%, insurance 5% / 18% / 35%.

### `labels.question_truth`

One entry for every applicable question in section 3.8 of the plan: `bool` for a Noul (true means the answer is
"yes" as the question is worded) and `int` 0 to 4 for a Score. The set is `applicable_qids(file)`.

| question | truth |
|---|---|
| `A_income_proof_current` | `income_proof` not in `missing_items` |
| `A_address_proof_valid` | `address_proof` not in `missing_items` |
| `A_statements_cover_months` | `statements` not in `missing_items` |
| `A_fields_cohere` | `fields_coherence` not in `missing_items` |
| `B_identity_coheres` | `fraud_type` is neither `identity_mismatch` nor `synthetic_identity` |
| `B_salary_matches_employer` | `fraud_type` is not `salary_pattern_mismatch`. Applies to salaried personal files and to salaried home-loan applicants |
| `B_gst_bank_consistent` | `fraud_type` is not `gst_bank_mismatch`. Applies to MSME files and to self-employed files with a GST block |
| `B_synthetic_identity_signals` | `fraud_type` is `synthetic_identity` (yes is bad) |
| `C_willingness` (score 0 to 4) | 0: write-off or worst DPD 90 or more; 1: DPD 60 to 89; 2: DPD 30 to 59 or 2 or more bounces; 3: DPD 1 to 29 or one bounce; 4: spotless |
| `C_recent_delinquency` | `max_dpd_12m` is 30 or more (yes is bad) |
| `C_capacity` (score 0 to 4) | with r = FOIR / segment limit (MSME: 1.25 / DSCR): r at most 0.6 is 4, at most 0.8 is 3, at most 1.0 is 2, at most 1.2 is 1, above that 0; one level lost (floor 0) when volatility CV is 0.35 or more |
| `C_foir_within_limit` | true FOIR is within the segment limit (MSME: DSCR at least 1.25) |
| `C_income_stable` | `volatility_cv` under 0.25 and `months_history` at least 12 |
| `C_collateral_adequacy` (home, score 0 to 4) | headroom under the LTV limit: 15 points or more is 4, 8 or more is 3, 0 or more is 2, down to -5 is 1, worse is 0; minus 2 for a disputed title, 1 for a pending mutation, 1 for an adverse legal opinion, 1 for a valuation spread of 15% or more; floor 0 |
| `C_collateral_title_clear` (home) | title status is clear and legal opinion is positive |
| `D_closeness` (score 0 to 4) | `closeness_level` |
| `D_doubt_income_documentation`, `_repayment_history`, `_debt_burden`, `_stability`, `_thin_file` | `primary_weakness` equals `income_documentation`, `repayment_history`, `debt_burden`, `employment_or_business_stability`, `bureau_thin_file` (yes is bad) |
| `D_doubt_collateral` (home) | `primary_weakness` is `collateral` |
| `E_memo_matches_grid` | `memo_condition_mismatch` not in `memo_defects` |
| `E_rate_math_correct` | `apr_math_wrong` not in `memo_defects` |
| `E_disclosures_complete` | no `disclosure_missing:*` in `memo_defects` |
| `E_disclosure_<id>` for each of the 8 disclosures | `disclosure_missing:<id>` not in `memo_defects` |
| `F_ews_dpd_rising`, `_emi_bounces`, `_partial_payments`, `_balance_stress` | `ews_truth` (yes is bad) |
| `F_covenant_<id>` (MSME) | `<id>` not in `covenant_breaches` |

Measured "yes" rates (of applicable files): income proof current 94.6%, address proof valid 95.3%, statements
cover 96.4%, fields cohere 96.9%, identity coheres 97.5%, salary matches 97.3%, GST consistent 96.0%, FOIR within
limit 88.4%, income stable 62.3%, recent delinquency 7.4%, memo matches grid 92.4%, rate math correct 93.2%,
disclosures complete 94.1%. Covenants kept: DSCR 87.0%, stock statements 83.8%, no unapproved borrowing 94.0%,
insurance current 91.8%. Because defect rates are small, several questions have few negatives: read
per-question calibration with the negative count in mind.

## 6. Engineered disparities

About 9.4% of the book is labelled in `meta.disparity_subset`. Subset membership is decided by draws that look at
no feature and no label, and the engineered attribute is kept out of every label computation. That is what makes
them a fair test: the *true* outcome is unchanged, so any difference in how the system treats them is the system's.

| subset | files | what is engineered | why the labels are unaffected |
|---|---|---|---|
| `lang_doc_script` | 75 (3.8%) | 18% of Tamil and Bengali speakers (and 2% of Hindi, 5% of Marathi speakers) have an address proof in their native script, with the name in native script (`applicant.name_native`); the rest of the file is English | whether an address proof is valid is decided by the defect draw, not by the script; a native document can be valid, expired or mismatched exactly as an English one |
| `gender_income_proxy` | 56 (2.8%) | 60% of self-employed women get `documentation_type = informal_declared` and a self-declaration in place of an ITR | documentation type is not a feature of the risk function or the policy; income proof is missing at the same rate as for everyone |
| `pincode_bureau_thin` | 58 (2.9%) | 30% of PC7 files have their bureau score reported as missing (`bureau.score` is None, history 0 to 5 months); PC7's new-to-credit rate is 31.6% against 7.5% elsewhere | labels use the real score, kept in `meta.true_bureau_score`; undoing the engineering leaves the decision, the pd, the weakness and the closeness unchanged (a test checks this) |

Members are a uniform random sample of their group, so their true sanctionable and default rates equal those of the
rest of their segment up to sampling noise (tests allow 15 and 8 percentage points at these sizes). A file belongs
to at most one subset, in the order language, gender, pincode.

Group sizes at n = 2,000: gender F 498, M 1,496, X 6 (X is far below the pre-registered `min_group_n` of 100, so it
reports INSUFFICIENT_N); PIN clusters 152 to 306 each; languages 161 to 899 each.

## 7. Where things are (for builders of the state and the packs)

* Everything the state may use is in the structured blocks; `demographics` and `meta` are for fairness evaluation
  only, and `labels` for evaluation only. `meta.true_bureau_score` must never reach a state.
* Money is in rupees as integers. `foir` and `ltv` figures are percentages; FOIR and DSCR are computed by
  `jevloan.data.risk.true_foir_pct` and `true_dscr`, or directly with `jevloan.finance`.
* For MSME files `income.verified_monthly_income_inr` is monthly cash accruals.
* `month_age` is the age of the date that decides freshness (bill date, pay month, ITR filing, last statement
  month, GST period, rent agreement start). It is 0 for passports, PAN cards, registration certificates, title
  reports, valuation reports and employer letters, which state their own validity. Every document ends with
  "Received: 14 Sep 2026." (the application date), so expiry and staleness are readable without knowing today's date.
* `pii_inventory`: person names are canonical (title case), applicant first; when the applicant has a native-script
  name it is second, and it is an alias of the applicant, not another person (`applicant.name_native`). Phones are
  the bare 10-digit number, Aadhaar and account numbers bare digits, PANs upper case, emails lower case,
  address lines exactly as written (with an upper-case variant when a document prints it that way) and the same
  address as one line with city, state and PIN, pincodes as ASCII digits (even when a native document writes
  them in native digits). GSTINs are not listed: the PAN inside is (in `pans`). Masked forms
  (`XXXX XXXX 0123`, `XXXXXXX3456`) are partial and not listed. Nothing is listed that is not written or in a
  structured field, and everything written is listed (both directions are tested).
* Documents are written with varied formats on purpose: `+91 98765 43210`, `98765-43210`, `09876543210`, spaced,
  dashed, plain and masked Aadhaar, upper-case names, honorifics (Mr., Shri, Smt.), initials (`K. Venkatesh`),
  `Rs. 1,45,000`, `Rs 1,45,000/-`, `₹1,45,000`, `INR 1,45,000.00`, `dd Mon yyyy`, `dd/mm/yyyy`, `dd-mm-yyyy`, and
  Tamil, Bengali and Devanagari text. Digit runs are written so that a gate that joins runs separated by a single
  space, dash, dot or slash never sees an account-number-like run other than a listed identifier.

## 8. Known unrealistic aspects

* Every feature comes from one latent quality plus independent noise. Real books have regime shifts, seasonality,
  lender-specific policies and missing data patterns that are not here.
* The risk function is a single logistic layer chosen by hand, and the outcome is drawn from it. A model cannot
  learn anything here that the coefficient table does not say. The pd includes the fraud flag.
* Truth is exact. Real labels are noisy, late and sometimes wrong; this book has none of that, so agreement rates
  measured here are an upper bound.
* Documents are short English snippets from a small set of templates (plus three native scripts with a fixed
  vocabulary and a small name and street list). Real documents are scans, tables and photographs. Fraud evidence is
  clean and one-dimensional, so the fraud module will look easier than it is.
* Missing items and memo defects are independent of everything else, at a few percent each. In practice deficiencies
  cluster (by branch, by channel, by borrower type).
* A self-declaration of income counts as present income proof when it is fresh. A real credit team would not accept it.
* The thin-file subset keeps its delinquency and loan-count fields although the score is hidden; a real thin file
  would have none.
* MSME "income" is cash accruals and DSCR is 1 / FOIR; real DSCR needs a full financial statement.
* Bank, utility, employer and business names are fictional but built from ordinary words and may coincide with real
  ones. Personal names are common given names with common surnames. Phone numbers, Aadhaar numbers and account
  numbers are random and format-valid (Aadhaar with a valid Verhoeff check digit, GSTIN with a valid check
  character), so they can coincide with real ones.
* The KFS is a simplified section list, not the full RBI format. Fees are one processing fee, its GST, a
  documentation charge and an optional insurance premium.
* All dates are relative to a fixed application date of 14 Sep 2026.
* PIN clusters are independent of the PIN. Cities are a list of 30 with one or two PIN prefixes each.
* The gender X group is tiny (0.5%) and only present so that the schema and the reports handle it.
