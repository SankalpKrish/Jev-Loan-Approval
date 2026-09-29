# BUILD PROMPT: Jev-powered loan appraisal support system (India, RBI-compliant)

Copy everything below the line into your coding agent. It is written to be
executed in order, milestone by milestone, with no missing context.

---

## 1. What you are building

A loan appraisal support system for Indian lending that uses TypeSafe AI's
Jev model as a documented scoring layer inside the bank's own credit
appraisal. The system scores loan files, flags fraud and incompleteness,
triages borderline cases to human credit managers, and checks sanction
memos and Key Fact Statements for consistency. It never sanctions a loan.
Humans and documented bank policy own every approval.

You are building a working end-to-end system with synthetic data, because
real borrower data is not available and must never be used in development.

## 2. The model you are building on

Jev is a System One model from TypeSafe AI. You send it application state
plus typed questions. It returns typed probabilistic answers. It does not
generate text and it cannot hallucinate.

Three question types, which can be mixed in a single API call and are
evaluated in parallel against the same state:

- Choice: pick one option from a defined set. Returns the choice, a
  probability per option, and confidence. Cardinality up to 255.
- Score: rate against ordered descriptive levels you define. Returns the
  score, a probability per level, and confidence.
- Noul: a yes or no question. Returns the probability the answer is yes,
  from 0 to 1.

Rules for using it well:

- One question asks one narrow thing. A broad judgment like "is this
  borrower good" must be decomposed into atomic questions whose answers
  your code combines with weights you control.
- Instructions, Choice options, Score levels, and Noul true/false
  boundaries all accept JSON structure. Encode each rubric as
  what-it-is, what-it-is-not, and two or three examples per side.
- Use confidence as a second axis. The answer tells you what. Confidence
  tells you whether to act or route to a human.
- Ask extra speculative questions for free. Latency barely moves as
  questions are added. Your code decides which answers matter per case.
- The context budget is 64k tokens per request, with 32k for state plus
  the longest question. Keep state to 1k-3k tokens. Quantize numbers.
- The model is not fine-tuned. You shape behavior through the state and
  the question criteria, never through weights.
- Published figures: roughly 70-500ms per call, $0.042 per million input
  tokens with outputs free, 250k tokens/sec and 1200 requests/min limits.
  Treat these as vendor-reported. The official docs at
  https://docs.typesafe.ai/llms.txt are the source of truth for the API
  contract. Read them before writing integration code, and use the
  official Python SDK.

## 3. Hard constraints (non-negotiable, from Indian regulation)

These come from RBI's Digital Lending Directions 2025, the Working Group
report behind them, and the FREE-AI report of August 2025. The build must
satisfy all of them:

a. The regulated entity owns credit appraisal. A partner model may only do
   work incidental to underwriting. Your system is a scoring input with
   documentation, never the decision maker.
b. AI informs, it does not own decisions. Every auto-action needs a
   documented policy, a named human owner for exceptions and overrides,
   and an audit trail.
c. Algorithmic features feeding lending decisions must be documented and
   auditable. Your audit log (section 6) is how this requirement is met.
d. No personal identifiers ever leave the building. No names, PAN,
   Aadhaar, account numbers, or phone numbers in any model request. The
   state carries derived features and redacted document content only.
   Enforce this with automated tests, not discipline.
e. Pricing must be traceable. Any risk signal that moves pricing flows
   through a versioned formula in code, logged per file, so the Key Fact
   Statement rate can be defended line by line.
f. Fairness must be tested, not assumed. Measure approval rates and
   calibration across gender, pin code clusters, and language before
   launch and on a schedule after. Names and pin codes can proxy for
   religion and caste, so strip them from state and verify the model is
   not reconstructing them from what remains.

## 4. Architecture

Default stack: Python, FastAPI, SQLite, the official TypeSafe Python SDK,
PyYAML for policy config. Equivalents are acceptable if justified. No
framework heavier than needed. No frontend beyond a minimal review page
for the advisory pane in Milestone 4.

Services and flow:

```
synthetic files -> state builder -> PII gate -> Jev fan-out
    -> policy engine -> audit log -> {auto-process | human queue | decline}
                                    -> evaluation harness (offline)
                                    -> advisory API (online, Milestone 4)
```

- State builder: turns one loan file into a 1k-3k token state object.
  Separate builders per segment: salaried personal loan, self-employed,
  MSME business loan, secured (home) loan.
- PII gate: a hard filter between the state builder and the network.
  Regex plus rules for PAN, Aadhaar/UID, account numbers, phone numbers,
  emails, names in document text. Any match blocks the request and logs
  the block. Tested with adversarial fixtures.
- Fan-out: one Jev call per file per stage, all stage questions in that
  single call.
- Policy engine: reads a versioned YAML file of weights, thresholds, and
  routing rules. Combines question probabilities into scores and routing
  decisions. No model output ever bypasses it.
- Audit log: append-only per file. File ID, policy version, hash of the
  state sent, questions asked, probabilities returned, policy outcome,
  human decision with reason codes where applicable. Hash-chained so
  tampering is detectable.
- Human queue: files below confidence thresholds land here with the
  model's reasons attached. Reviewers record accept, modify, or reject
  plus a reason code. Those codes are training and calibration data.

Timeout and failure behavior, tested not assumed: any model call slower
than your configured budget (default 2 seconds) or any API error routes
the file to the human queue. A full outage degrades to human-only
processing. Simulate both in tests.

## 5. Module specs (build all six)

Each module is a question pack plus its policy rules plus its fixtures.
Write the rubric criteria (what, not-for, examples) for every question.

Module A, file readiness. Nouls: income proof present and current,
address proof valid, statements cover required months, application fields
cohere. Policy: any fail routes to a deficiency notice naming the missing
item, not to a credit manager.

Module B, fraud screen. Nouls: identity elements cohere, salary credits
match claimed employer pattern, GST filings and bank credits tell the
same story (business files), synthetic identity signals present. Policy:
any high-confidence fraud signal routes to investigation, never to
auto-decline without human review.

Module C, appraisal support. Scores and Nouls for willingness (conduct on
existing obligations), capacity (income stability versus proposed EMI
burden), and for secured files, collateral adequacy. Policy: weighted
composite per segment defined in YAML, with per-segment thresholds for
pass, borderline, and fail bands.

Module D, exception triage. For files in the borderline band: a closeness
score plus one Noul per factor naming the driver of doubt. Policy: order
the human queue by closeness, attach the top doubt factor as the reason.

Module E, memo and KFS consistency. Nouls: memo conditions match the
policy grid for product and ticket size, annualized rate math is correct,
all required disclosures present. Policy: any fail blocks disbursal
pending correction.

Module F, post-disbursal monitoring. Nouls per early warning signal from
repayment behavior, and one Noul per covenant for commercial files.
Policy: warnings aggregate to watchlist tiers with human review.

## 6. Audit log schema (minimum fields)

file_id, segment, stage, policy_version, state_hash, questions_json,
answers_json (probabilities plus confidence), policy_outcome,
human_decision (nullable), human_reason_code (nullable), timestamps for
request, response, and decision, model_version. Hash-chain each entry to
the previous. Provide a verify command that checks the chain and a dump
command that exports one file's complete trail as JSON.

## 7. Synthetic data generator

You have no real data, so generate a labeled Indian loan book:

- 2,000 files minimum across four segments: salaried personal,
  self-employed, MSME business, secured home.
- Each file: application fields, a bureau score band, income and
  obligation summaries, bank statement derived features, GST summary for
  business files, property summary for secured files, plus document text
  snippets for the readiness and fraud modules.
- Ground truth labels per file: sanctionable or not, fraud or clean,
  missing items list, and a simulated 12-month outcome (repays, slips,
  defaults) drawn from a documented risk function of the features.
- Demographic attributes for fairness testing: gender, pin code cluster,
  language. Engineer known disparities into a small labeled subset so
  the fairness tests have something to catch.
- Adversarial PII fixtures: documents containing PAN, Aadhaar, account
  numbers, and phone numbers that the PII gate must catch.

## 8. Evaluation harness (this decides go or no-go)

Offline replay over the synthetic book, reporting:

- Agreement rate with the synthetic credit decision per module.
- Expected Calibration Error per question, not aggregated. A confident
  wrong answer is the failure mode. Report it directly.
- Approval rate and calibration parity across gender, pin code cluster,
  and language. Pre-register the parity thresholds before running.
- PII gate catch rate on adversarial fixtures. Must be 100 percent.
- Cost and latency per file at production state sizes.
- A disagreement report: the files where the system and the label
  differ, grouped by driver, readable by a human.

The harness outputs a go or no-go memo: the numbers, the parity results,
and the named conditions under which the project should stop. Write the
memo template first, before running anything.

## 9. Milestones and acceptance criteria

M0, scaffold. Repo layout, SDK wired, one real Jev call returning typed
answers, config loader, empty audit log with verify command. Done when
the hello call passes and the log verifies.

M1, data and state. Generator producing 2,000 labeled files, state
builders per segment, PII gate with adversarial fixtures at 100 percent
catch. Done when states average under 3k tokens and zero PII leaks in a
full-book scan.

M2, modules and policy. All six question packs with written rubrics,
policy engine with versioned YAML, audit logging per file. Done when a
full-book replay runs end to end and every file has a complete trail.

M3, evaluation. Harness reporting agreement, per-question calibration,
parity, cost, latency, plus the disagreement report and a filled go or
no-go memo. Done when the memo exists with real numbers. A no-go with
reasons counts as done.

M4, advisory service. FastAPI service exposing score-file and
review-queue endpoints, a minimal reviewer page showing probabilities
with reasons and accept/modify/reject buttons, timeout and outage
fallback proven by chaos tests. Done when killing the network mid-review
leaves every file safely queued and every completed file fully logged.

M5, monitors (only after M3 is a go). Nightly jobs recomputing
calibration and parity on recent decisions, with a pre-registered drift
threshold that pages and reverts new files to human review.

## 10. Non-goals

No autonomous sanctioning. No pricing set by the model. No PII in any
outbound request. No partner-facing decision API that could be mistaken
for the bank's appraisal. No frontend beyond the reviewer page. No real
borrower data at any stage.

## 11. Definition of done

All milestones M0 through M4 complete with acceptance criteria met, the
go or no-go memo filled with measured numbers, the audit chain verifying
clean on the full synthetic book, PII tests green, fairness results
documented with any failures named honestly, and a README a risk officer
could read that explains what the system decides, what it only advises
on, who owns each decision, and where every number came from.
