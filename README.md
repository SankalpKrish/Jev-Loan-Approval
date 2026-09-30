# Jev Loan Approval Support

This project is an advisory workflow for reviewing synthetic loan files with Jev. It turns compact, redacted
stage data into model answers, applies versioned bank policy in code, records an audit trail, and routes cases that
need a person to a review queue. It does not sanction a loan, authorize disbursal, or make an autonomous decline.

The implementation through M4 is complete. The real-Jev evaluation is **NO-GO** for lending use: all six modules
missed the registered improvement over rules, non-borderline appraisal agreement was 73.0% against an 85%
minimum, and fairness checks failed. The simulator remains provisional. Read the measured decision record in
[docs/GO_NO_GO_MEMO.md](docs/GO_NO_GO_MEMO.md); operational success does not establish lending quality.

## Workflow

The three stages are `appraisal` (modules A through D), `sanction_docs` (module E), and `monitoring` (module F).
For each stage, the state builder sends a compact state through the PII gate, the gateway writes one model-call,
failure, or PII-block audit entry, and policy code converts the typed answers into a recommendation. Queue entries
are written for outcomes that need human review. A `PROCEED_TO_SANCTIONING_AUTHORITY` appraisal recommendation
may receive a code-computed price; the recommendation itself is not a sanction.

Offline replay deliberately evaluates all three stages for every file, regardless of the appraisal outcome. This
supports book-wide evaluation of memo checks and monitoring signals. It must not be read as permission to run those
stages online for an unapproved case.

Online `sanction_docs` requires a recorded, explicit human sanction authorization. The pipeline recognizes only an
`audit_log` `HUMAN_DECISION` record for the same file with all of these values:

- `stage`: `appraisal`
- `policy_outcome`: `PROCEED_TO_SANCTIONING_AUTHORITY`
- `human_decision`: `accept`
- `policy_version`: the current policy version
- a non-empty `reviewer_id`
- `detail_json.sanction_authorized`: `true`

A later reject, or any newer appraisal human decision under the current policy that is not itself a qualifying
sanction authorization, revokes this authorization. A generic queue acknowledgment or an appraisal recommendation
does not meet this requirement. The current queue decision API does not create this authorization marker. A bank
integration must record it only after its named, authorized sanctioning human has approved the sanction; that
integration is outside this repository's current workflow. The API returns a conflict response when sanction
document review is requested without the marker. Human approval remains the final sanction decision.

## Local setup and commands

Use Python 3.14 and install the project and development dependencies in the local virtual environment:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

The commands below use the simulator explicitly and do not require an API key:

```bash
export JEVLOAN_BACKEND=sim
.venv/bin/python -m jevloan data generate --n 2000 --seed 7 --out data/book.jsonl
.venv/bin/python -m jevloan pii fixtures-check
.venv/bin/python -m jevloan modules check
.venv/bin/python -m jevloan state stats --book data/book.jsonl
.venv/bin/python -m jevloan state leak-scan --book data/book.jsonl
.venv/bin/python -m jevloan pipeline replay --book data/book.jsonl --run-id local-sim --db data/local-sim.sqlite3
.venv/bin/python -m jevloan pipeline check-trails --book data/book.jsonl --db data/local-sim.sqlite3
.venv/bin/python -m jevloan audit verify --db data/local-sim.sqlite3
.venv/bin/python -m jevloan eval run --run-id local-sim
.venv/bin/python -m jevloan api serve --mode dev
```

Use `.venv/bin/python -m jevloan --help` and the group help, such as `... jevloan pipeline --help`, to list the
installed commands. The CLI includes `audit`, `jev`, `pii`, `data`, `state`, `modules`, `pipeline`, `eval`, and
`api` groups.

Replay artifacts are written to `data/runs/<run-id>/`. `decisions.jsonl` has one row per file and stage,
including the full policy decision, all typed answers and probabilities, model version, latency, token count, and
failure kind. `metadata.json` records the selected backend and model, policy and pricing versions, token price,
source book path and size, processed count, stages, total input tokens, and audit database path. Keep the artifacts
with the exact book, configuration, and audit database used for the run.

New replays also freeze policy and pricing YAML, record input hashes and the exact processed file ids, and retain
the runtime configuration. A run id cannot overwrite an existing replay. The default evaluation split is holdout;
files with a numeric id divisible by five are dev, and the memo includes their separate comparison. Missing answers
remain visible as missing evidence and cannot improve agreement or authorize GO.

`pipeline check-trails` returns a non-zero status for missing or out-of-order stage records. Repeated stage attempts
can be checked by repeating a name, for example `--stages appraisal,appraisal`. Audit verification checks the
append-only hash chain; it does not validate model quality or human authorization beyond the recorded fields.

## Acceptance runs and risk handoff

The acceptance scripts create isolated temporary directories and use `.venv/bin/python`. M0 through M2 set
`JEVLOAN_BACKEND=sim`; they never contact real Jev. M3 evaluates only a supplied replay and reports its recorded
backend from metadata; it does not start a replay or make a model call. M4 runs the API and chaos test selection.

```bash
scripts/accept_m0.sh
scripts/accept_m1.sh
scripts/accept_m2.sh
scripts/accept_m3.sh RUN_ID --split all
scripts/accept_m4.sh
```

M2 defaults to 2,000 files. `JEVLOAN_ACCEPTANCE_N` and `JEVLOAN_ACCEPTANCE_CONCURRENCY` allow smaller or slower
runs; report those overrides. M3 accepts a supplied run id and uses `JEVLOAN_RUNS_DIR` for a non-default replay
root. The corrected `acceptance-real-v2` run used the real backend by deliberate opt-in; it is not started by the
M0–M4 scripts above.

The recorded `acceptance-sim` and corrected `acceptance-real-v2` runs used the same synthetic 2,000-file book
(seed 7) and the immutable pre-registered thresholds in `config/parity_thresholds.yaml` (registered 2026-09-29).
The split is deterministic: file ids divisible by five are `dev`; all other ids are `holdout`. This leaves a
segment imbalance in `dev`, so treat that split as a diagnostic subset rather than a representative random sample.
Thresholds were not tuned after the holdout results.

The corrected real run completed 6,000 successful model calls with no call failures, complete trails for 2,000 of
2,000 files, and a verified 16,336-entry audit chain. It recorded model `jev-1.13.0` and an input price of
USD 0.042 per million tokens from local configuration; that price has not been independently verified against
the vendor's current pricing. An earlier real attempt (`acceptance-real`) exposed a scheduling-timeout defect;
threaded state preparation fixed the runtime issue. That first attempt is diagnostic only; the corrected
`acceptance-real-v2` run is the operational result referenced here.

PII checks caught all 264 of 264 supported adversarial fixtures, flagged 0 of 121 clean samples, and reported
zero findings in the 6,000 state inventory and gate scans. These checks are not complete protection: unknown names
and adversarial combinations outside the fixtures can still evade detection. The evaluator verifies exact static
rubric boilerplate to avoid incidental name-word or numeric-band matches, checks changed and dynamic questions
for complete inventoried identifiers, and runs the structural gate on every outbound payload. Partial names in
question prose remain a limitation. Do not send raw applicant records outside the approved bank environment.

Every configured owner name in `config/policy/policy_v1.yaml` is still `UNASSIGNED`:

| Queue or function | Required owner role |
| --- | --- |
| Credit exceptions | Zonal Credit Head |
| Fraud investigation | Head, Fraud Risk Control Unit |
| Deficiency operations | Branch Operations Manager |
| Decline confirmation | Credit Manager (sanctioning authority) |
| Disbursal correction | Central Processing Unit Lead |
| Watchlist review | Credit Monitoring Head |
| Model risk | Chief Risk Officer (model risk) |

Production startup refuses unassigned owners, but the reviewer-local API has no authentication or access control.
Production use requires bank authentication, reviewer identity, authorization, and integration around this
service; the local review app must not be exposed directly as a production decision channel.

The headline evaluation uses 1,600 holdout files; the 400 dev files are reported separately. S5, the aggregate
routing-calibration stop condition, passed, although two routing questions failed their individual calibration
limits. All three engineered disparities went undetected on the real run; the simulator missed the gender and
PIN-cluster disparities. These are failed fairness-power acceptance checks, not successful fairness validation.
Unexplained parity breaches also failed S9. Calibration-in-the-large uses the policy composite as a probability
proxy, so it must not be interpreted as validation of a calibrated probability of default.

The recorded real holdout cost was about USD 0.000951 per file at the configured price. The full run used
45,299,282 input tokens (about USD 1.90 under that price). Reported file latency sums stage-call latencies;
it is not end-to-end elapsed time. Pricing recommendations themselves come from the versioned grid in
`config/pricing/pricing_v1.yaml`; each `PRICING` audit entry records the base, adjustment line items, rate,
EMI, and recomputed APR. Jev does not choose a price.

Full machine-readable results and disagreements are in [reports/acceptance-real-v2](reports/acceptance-real-v2/)
and [reports/acceptance-sim](reports/acceptance-sim/). No tuning was performed against holdout data. M3 is complete
because the specification accepts a measured NO-GO with reasons; M5 monitors remain withheld until a future
pre-registered evaluation supports GO and bank sign-off is complete.

## Validation recorded on 2026-09-30

`.venv/bin/python -m pytest -q` passed: **2,401 passed, 2 deselected**. The two separately opted-in real-Jev
tests passed: hello plus a 20-file replay asserting successful, non-empty real model answers. API and chaos
coverage includes queued failures, breaker transitions, kill-switch changes during concurrent calls and review,
and a live stub server killed mid-batch. The local browser review flow recorded a human decision and removed
the decided item from the open queue; the current page shows distributions, confidence, rubric reasons, and
the advisory banner without console errors.

Acceptance data and SQLite databases are ignored by Git. This workspace retains the book and run evidence under
`data/acceptance/` and `data/runs/`; preserve those files alongside the reports for an independent replay audit.
The book's identifiers are synthetic but must still be treated as raw PII test material. Scripts pass syntax
validation and the working changes pass `git diff --check`.
