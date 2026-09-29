# Build plan: Jev loan appraisal support system

Source spec: `jev-loan-build-prompt.md`. Vendor docs, saved locally: `docs/vendor/typesafe/`.
This plan is the contract every build agent works from. If code and plan disagree, fix one of them on purpose. Don't let them drift.

---

## 0. How the build runs

| Role | Who | Does |
|---|---|---|
| Orchestrator | Opus (main session) | Owns this plan, the contracts, and the pre-registered thresholds. Reviews every diff, runs the tests, fixes bugs, integrates the pieces, and runs the acceptance checks. Commits once per milestone. |
| Builders | Sonnet 5.5 subagents | Write all production code, tests, and fixtures. Each one gets a brief that names the files it owns and the plan sections it implements. |

Rules for builders:
1. Touch only the files you own. If a contract has to change, stop and report it; don't change it quietly.
2. Code against the contracts in section 3. If a dependency isn't written yet, use a small local stub in your tests that matches the contract.
3. Every module ships with tests. `pytest -q` must pass for everything you own before you report back.
4. No real data, no network calls in tests (except the opt-in `@pytest.mark.real_jev` tests), and no secrets in code.
5. Use Python 3.14 from `.venv`. Don't add dependencies beyond section 1.3 without saying so.

Loop for each wave: spawn builders (in parallel where the files they own don't overlap) → orchestrator runs `pytest`, reads `git diff`, fixes defects → milestone acceptance check → commit.

---

## 1. Decisions and assumptions

### 1.1 Where this plan resolves ambiguity in the spec

| # | Topic | Decision | Why |
|---|---|---|---|
| D1 | Noul confidence | Nouls return no `confidence`. We derive `conf = abs(2p - 1)`. Choice and Score answers use the API's `confidence`. | Per the vendor's Confidence doc, Nouls don't carry it. |
| D2 | Arithmetic | Code does all math and date handling: FOIR, LTV, months covered, APR by IRR, DSCR. Jev only sees the results as bands or small integers. Jev judges semantic things: whether a document is what it claims to be, whether the evidence holds together, and how the text compares with the rubric. | Vendor jaggedness doc: "keep the arithmetic in code". |
| D3 | Stages | 3 stages, one Jev call per file per stage. `appraisal` covers modules A+B+C+D, with D asked speculatively. `sanction_docs` covers E. `monitoring` covers F. | The spec asks for one call per stage. Speculative fan-out costs almost nothing. |
| D4 | Replay mode | During offline replay, every file runs all 3 stages whatever its appraisal outcome, because E and F need book-wide metrics. Online, `sanction_docs` runs only after a human sanction decision. | Evaluation coverage. |
| D5 | Policy precedence (appraisal) | 1) Model failure or PII block → HUMAN_REVIEW. 2) High-confidence fraud signal → FRAUD_INVESTIGATION. 3) Any readiness fail → DEFICIENCY_NOTICE. 4) C band: pass → PROCEED_TO_SANCTIONING_AUTHORITY, borderline → HUMAN_REVIEW ordered by D, fail → DECLINE_RECOMMENDED. 5) Low confidence on any routing question → HUMAN_REVIEW. | Fraud outranks paperwork. Nothing ever auto-sanctions. |
| D6 | "Decline" | DECLINE_RECOMMENDED goes to a `decline_confirmation` queue. A human confirms or overrides it. The switch is `require_human_confirmation: true` in the YAML and has no code path to turn it off. | RBI constraint (b) and the non-goals. |
| D7 | Queue order | `closeness` ∈ [0,1] is the D score normalised: 0 = clearly unsanctionable, 1 = clearly sanctionable. The borderline queue sorts by closeness descending (nearest to passing first). Ties go to the oldest item first. | This is our reading of "order by closeness". It's documented in the YAML. |
| D8 | Identifiers | The redactor swaps each identifier for a token that's consistent within a file: `[APPLICANT]`, `[PERSON_2]`, `[EMPLOYER_A]`, `[PAN_1]`, `[UID_1]`, `[PHONE_1]`, `[EMAIL_1]`, `[ACCT_1]`, `[ADDR_1]`. The same value always gets the same token, and different values get different tokens. That lets Jev see a mismatch without seeing the identity. | Constraint (d), plus it keeps the fraud signal. |
| D9 | Named owners | The YAML names a role and an `owner_name` for each decision type. In dev, `owner_name: UNASSIGNED` is allowed. `serve --mode production` refuses to start while any owner is unassigned. | Constraint (b). We don't have real people to name. |
| D10 | Simulator | `SimulatedJevTransport` plugs in at the SDK's httpx2 transport layer, so the real SDK code path runs even offline. The audit log records its model version as `sim-jev-*`, and any memo built from simulator numbers is stamped PROVISIONAL, NOT A GO. | Development and tests run without a key. Simulator numbers can't be mistaken for real ones. |
| D11 | Rules baseline | The eval compares every module against a deterministic rules-only baseline. | If Jev doesn't beat the rules, the go/no-go memo has to say so. |
| D12 | Language leakage | Documents are mostly in English. The fairness disparity subset includes some native-script documents, and the proxy probe should catch them. | Gives the fairness tests something real to detect. |

### 1.2 Out of scope (spec non-goals, restated)
No autonomous sanctioning, no model-set pricing, no PII in outbound requests, no partner-facing decision API, no UI beyond the reviewer page, and no real borrower data. M5 only gets built if M3 comes out a go on real Jev numbers.

### 1.3 Stack
Python 3.14 in `.venv`. Runtime deps: `typesafe-sdk`, `fastapi`, `uvicorn`, `pyyaml`, `pydantic` (v2), `jinja2`, `python-dotenv`, `numpy`. Dev deps: `pytest`, `pytest-asyncio`, `httpx` (for FastAPI's TestClient). Storage is SQLite through the stdlib `sqlite3`. The CLI uses `argparse`. We're not using sklearn: the proxy probe is a small numpy logistic regression.

---

## 2. Repo layout and who owns what

```
pyproject.toml                  [W1-core]
config/
  runtime.yaml                  [W1-core]  backend, model, timeout budget, concurrency, rate limit, circuit breaker, db paths, token price
  parity_thresholds.yaml        [orchestrator, pre-registered]
  policy/policy_v1.yaml         [W2-policy] weights, thresholds, routing, owners, reason codes
  policy/sanction_grid_v1.yaml  [W1-data]  product × ticket grid (spec in §3.9)
  policy/kfs_disclosures_v1.yaml[W1-data]  required KFS disclosures (spec in §3.9)
  pricing/pricing_v1.yaml       [W2-policy]
src/jevloan/
  __init__.py, __main__.py      [W1-core]
  cli.py                        [W1-core]  argparse; loads COMMAND_MODULES (see §3.1)
  config.py                     [W1-core]
  canonical.py                  [W1-core]  canonical_json(), sha256_hex()
  audit/log.py, audit/commands.py         [W1-core]
  jev/gateway.py, jev/breaker.py, jev/ratelimit.py, jev/chaos.py,
  jev/simulator.py, jev/sim_rules/__init__.py, jev/commands.py (hello)   [W1-jev]
  pii/normalize.py, pii/detectors.py, pii/lexicon.py, pii/gate.py,
  pii/redact.py, pii/egress.py, pii/fixtures.py, pii/commands.py        [W1-pii]
  finance.py                    [W1-data]  emi(), apr_from_components() (IRR), foir(), ltv(), dscr()
  data/schema.py, data/generator.py, data/risk.py, data/documents.py,
  data/names.py, data/commands.py         [W1-data]
  state/base.py, state/salaried.py, state/self_employed.py, state/msme.py,
  state/secured_home.py, state/stages.py, state/tokens.py, state/commands.py   [W2-state]
  modules/base.py, modules/a_readiness.py, modules/b_fraud.py, modules/c_appraisal.py  [W2-packs1]
  modules/d_triage.py, modules/e_memo_kfs.py, modules/f_monitoring.py                 [W2-packs2]
  jev/sim_rules/a_readiness.py … c_appraisal.py   [W2-packs1]
  jev/sim_rules/d_triage.py … f_monitoring.py     [W2-packs2]
  policy/outcomes.py, policy/engine.py, policy/pricing.py, queue/store.py            [W2-policy]
  pipeline/runner.py, pipeline/replay.py, pipeline/trail.py, pipeline/commands.py    [W3-pipeline]
  eval/metrics.py, eval/calibration.py, eval/fairness.py, eval/proxy_probe.py,
  eval/baseline.py, eval/disagreement.py, eval/memo.py, eval/harness.py, eval/commands.py   [W4-eval]
  api/app.py, api/templates/*.html, api/commands.py (serve)                          [W5-api]
  monitors/ (M5, only if M3 comes out a go)                                           [W6-monitors]
tests/                          each builder owns tests/test_<its area>*.py
tests/chaos/                    [W5-chaos]
docs/
  GO_NO_GO_MEMO_TEMPLATE.md     [orchestrator, written before any eval run]
  DATA_CARD.md                  [W1-data] generator, risk function coefficients, engineered disparities
  RUBRICS.md                    [W2-packs*] generated from the packs by `jevloan rubrics export`
  GO_NO_GO_MEMO.md              [W4-eval output, filled]
README.md                       [W6-docs] written for a risk officer
data/ (gitignored)              book.jsonl, pii_fixtures.jsonl, jevloan.db, runs/
reports/                        eval outputs (summary json and markdown kept in git)
```

---

## 3. Contracts

### 3.1 Core (W1-core)
- `canonical_json(obj) -> str`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. `sha256_hex(s: str|bytes) -> str`.
- `config.py` (pydantic v2 models):
  - `RuntimeConfig`: `backend: Literal["real","sim"]`, `model: str = "jev-latest"`, `timeout_budget_s: float = 2.0`, `max_concurrency: int = 16`, `rate_limit_rpm: int = 1000`, `breaker_failure_threshold: int = 5`, `breaker_cooldown_s: float = 30.0`, `db_path: str = "data/jevloan.db"`, `book_path: str = "data/book.jsonl"`, `usd_per_million_input_tokens: float = 0.042`, `force_human_review: bool = False`.
  - `load_runtime(path="config/runtime.yaml") -> RuntimeConfig`: loads `.env` through python-dotenv, then applies env overrides `JEVLOAN_BACKEND`, `JEVLOAN_DB_PATH`, `TYPESAFE_DEFAULT_MODEL`.
  - `load_yaml_versioned(path) -> tuple[dict, str version, str sha256_of_file]`. It raises if the YAML has no top-level `version`.
- `cli.py`: `jevloan <group> <cmd>`. It uses `COMMAND_MODULES = ["jevloan.audit.commands", "jevloan.jev.commands", "jevloan.pii.commands", "jevloan.data.commands", "jevloan.state.commands", "jevloan.modules.commands", "jevloan.pipeline.commands", "jevloan.eval.commands", "jevloan.api.commands", "jevloan.monitors.commands"]`. For each entry, if `importlib.util.find_spec` returns None the module is skipped. Otherwise it's imported, and an import error must crash, never be swallowed. Then its `register(subparsers)` is called.

### 3.2 Audit log (W1-core): `jevloan.audit.log`
SQLite table `audit_log` (the audit log is the only append-only store; the queue and everything else live in separate tables):

| column | type | notes |
|---|---|---|
| seq | INTEGER PK AUTOINCREMENT | |
| event_type | TEXT NOT NULL | `MODEL_CALL`, `MODEL_FAILURE`, `PII_BLOCK`, `POLICY_OUTCOME`, `PRICING`, `QUEUED`, `HUMAN_DECISION`, `MONITOR_ALERT`, `SYSTEM` |
| file_id, segment, stage | TEXT | file_id and stage are NULL only for `SYSTEM` |
| policy_version | TEXT | |
| model_version | TEXT | the version from the response (e.g. `jev-1.13.0` or `sim-jev-0.1`) |
| state_hash | TEXT | `sha256_hex(canonical_json(state))` |
| state_json | TEXT | the redacted state that went out, stored only if it passed the gate |
| questions_json, answers_json | TEXT | canonical JSON; answers include the probabilities, confidence, and derived Noul confidence |
| policy_outcome | TEXT | |
| human_decision | TEXT NULL | `accept`, `modify`, `reject` |
| human_reason_code, reviewer_id | TEXT NULL | |
| request_ts, response_ts, decision_ts | TEXT NULL | ISO-8601 UTC, millisecond precision |
| latency_ms | REAL NULL | |
| input_tokens | INTEGER NULL | |
| detail_json | TEXT | event-specific: failure kind, PII findings (masked only), pricing line items, queue item id, reasons |
| created_ts | TEXT NOT NULL | |
| prev_hash, entry_hash | TEXT NOT NULL | the chain starts from `"0"*64` |

- `entry_hash = sha256_hex(prev_hash + canonical_json({every column except seq and entry_hash}))`.
- Triggers `BEFORE UPDATE` and `BEFORE DELETE` on `audit_log` → `RAISE(ABORT, 'audit_log is append-only')`.
- `class AuditLog(db_path)`: `append(**fields) -> dict` (thread-safe and async-safe; serialized with a `threading.Lock` and `BEGIN IMMEDIATE`), `verify() -> VerifyResult(ok, entries, first_bad_seq, reason)`, `trail(file_id) -> list[dict]`, `iter_all()`.
- CLI: `jevloan audit verify [--db]` exits non-zero on failure. `jevloan audit dump --file-id F000123 [--out path.json]`.
- PII never goes into the audit log: `PII_BLOCK` entries store detector names, JSON paths, and masked values (`AB******4F`) only.

### 3.3 Jev gateway (W1-jev): `jevloan.jev.gateway`
```python
@dataclass
class JevCallResult:
    ok: bool
    answers: dict[str, dict] | None      # wire-format answer dicts; nouls get an added "derived_confidence"
    model_version: str | None
    input_tokens: int | None
    latency_ms: float
    request_ts: str
    response_ts: str | None
    failure: Literal["timeout","api_error","rate_limited","connection_error","circuit_open","pii_blocked","forced_human"] | None
    failure_detail: str | None
    state_hash: str
    audit_seq: int                        # seq of the MODEL_CALL / MODEL_FAILURE / PII_BLOCK entry

class JevGateway:
    def __init__(self, runtime: RuntimeConfig, audit: AuditLog, gate: "PIIGate",
                 transport: httpx2.AsyncBaseTransport | None = None): ...
    async def ask(self, *, file_id: str, segment: str, stage: str, policy_version: str,
                  state: dict, questions: dict[str, dict]) -> JevCallResult: ...
    async def aclose(self): ...
    @property
    def human_only(self) -> bool: ...     # breaker open, or runtime.force_human_review
```
- Questions travel as wire-format dicts: `{"type":"noul","instructions":...,"criteria":{"true":...,"false":...}}`, and the same shape for choice and score. The gateway converts them to SDK `Noul`/`Choice`/`Score`, unless the SDK accepts dicts as they are.
- Order inside `ask`: (1) if `runtime.force_human_review` → failure `forced_human`. (2) `gate.check({"state": state, "questions": questions})` → on `PIIBlocked`, write a `PII_BLOCK` entry and return failure `pii_blocked`; nothing leaves the process. (3) Breaker open → `circuit_open`. (4) Rate limiter token (async token bucket at `rate_limit_rpm`). (5) `asyncio.wait_for(client.system_one(...), timeout=runtime.timeout_budget_s)` with SDK retries off (one attempt) and the SDK timeout set to the budget. (6) Map exceptions: `asyncio.TimeoutError`/`TypeSafeAPITimeoutError` → `timeout`, `TypeSafeRateLimitError` → `rate_limited`, `TypeSafeAPIConnectionError` → `connection_error`, other `TypeSafeError` → `api_error`. (7) Record breaker success or failure. (8) Write `MODEL_CALL` (on success) or `MODEL_FAILURE`.
- The transport the gateway uses is always wrapped in `PIIEgressGuardTransport` (§3.5). That's a second, independent check at the network boundary.
- Backend: when `runtime.backend == "sim"` and no transport is passed, it uses `SimulatedJevTransport`. When `"real"`, it uses `httpx2.AsyncHTTPTransport()` and needs `TYPESAFE_API_KEY`.
- `CircuitBreaker(failure_threshold, cooldown_s, clock)`: closed → open after N consecutive failures → half-open after the cooldown (one probe allowed) → closed on success.
- `jev/chaos.py` transports, all `httpx2.AsyncBaseTransport`: `SlowTransport(inner, delay_s)`, `StatusTransport(status: int)`, `ConnectionDropTransport()`, `SwitchableTransport(inner)` with `.kill()`/`.restore()`, `CountingTransport(inner)` which records the request bodies it sees.
- `jevloan jev hello [--backend real|sim]`: sends one fixed synthetic state (no PII) with one Noul, one Choice, and one Score. It prints the typed answers and model version, writes an audit entry, and exits 0.

### 3.4 Simulator (W1-jev framework, W2-packs rules)
- `SimulatedJevTransport(rules: dict[str, SimRule] | None = None, latency_ms=(30, 120), seed=0, model_version="sim-jev-0.1")` parses the POST body and returns API-shaped JSON (see `docs/vendor/typesafe/api.md`), including `usage.input_tokens ≈ len(body)/4`.
- `SimRule = Callable[[dict state, dict question, random.Random], dict answer]`. It's keyed by question id. Rules register through `@sim_rule("A_income_proof_current")` into the `jevloan.jev.sim_rules.REGISTRY`, and every `sim_rules/*.py` module is imported by `jevloan.jev.sim_rules.load_all()`. An unknown id falls back to a Noul of 0.5, a uniform Choice, or a mid Score.
- The RNG is seeded per call from `sha256(seed, state_hash, question_id)`, so it's deterministic.
- Rules may read the **state only**, the same thing real Jev sees. Target behaviour: roughly 85–95% agreement with truth, probabilities that are mostly calibrated, and a few deliberate weaknesses, e.g. native-script address proofs get a lower p for `A_address_proof_valid`. The deliberate weaknesses are what make the fairness tests catch something.
- Production code never imports `jevloan.jev.simulator` or `sim_rules` except through the gateway's backend switch.

### 3.5 PII gate (W1-pii): `jevloan.pii`
- `normalize.py`: `normalize(s) -> str` applies NFKC, strips zero-width and format characters (`​-‏`, `⁠`, `﻿`), maps Devanagari, Bengali, Tamil, Telugu, Gujarati, Gurmukhi, Kannada, Malayalam, Odia, and fullwidth digits to ASCII, and converts spelled-out English digit sequences ("nine eight seven …", "double nine") to digits. It also produces a *digit-compacted view* in which runs of digits separated by single separator characters (space, `-`, `.`, `/`, `_`) are joined.
- `detectors.py`: each detector returns `list[Finding(detector, span, masked)]`:
  `pan` (`[A-Z]{5}\d{4}[A-Z]`, any case, and also inside a GSTIN), `aadhaar` (12 digits in any grouping, first digit 2–9; Verhoeff checksum is **not** required, we block anyway), `aadhaar_vid` (16 digits), `account_number` (9–18 digit run in the compacted view), `card_number` (13–19 digits that pass Luhn), `phone_in` (`(\+?91|0)?[6-9]\d{9}` in the compacted view, landlines with STD codes too), `email`, `upi_id` (`[\w.\-]+@[a-z]{2,}` bank handles), `ifsc` (`[A-Z]{4}0[A-Z0-9]{6}`), `passport` (`[A-Z]\d{7}`), `voter_id` (`[A-Z]{3}\d{7}`), `driving_licence` (a 2-letter state code, 2 digits, then 11 digits, with separators allowed), `person_name` (honorific or relation markers such as Mr, Mrs, Ms, Shri, Smt, Sri, Kumari, Dr, S/o, D/o, W/o, C/o followed by name-like tokens; label patterns like `Name:`, `Applicant:`, `Account holder:`, `नाम:`, `பெயர்:`, `নাম:`; and hits against the lexicon of Indian first names and surnames, in Latin script plus common Devanagari forms, for capitalised or ALL-CAPS tokens in free text).
- Allowlist: redaction tokens that match `\[[A-Z]+(_[A-Z0-9]+)?\]`, and the state's enum vocabulary (§3.7).
- `gate.py`: `class PIIGate: scan(payload: Any) -> list[PIIFinding(detector, path, masked)]` walks JSON keys **and** values recursively. `check(payload) -> None` raises `PIIBlocked(findings)`.
- `redact.py`: `class Redactor(known: KnownEntities)`, where `KnownEntities` comes from the raw file (names, employer and business names, PAN, Aadhaar, phones, emails, account numbers, address lines). `redact(text) -> str` swaps known values for consistent tokens (D8), then runs the detectors and tokenises anything unknown they find. It also turns explicit dates into `Mon YYYY`.
- `egress.py`: `PIIEgressGuardTransport(inner, gate)` parses each outgoing JSON body and runs `gate.check`. On a hit it raises `PIIEgressBlocked` and the request is never sent.
- `fixtures.py`: `adversarial_fixtures() -> list[Fixture(id, category, payload, expected_detectors)]`, at least 150 fixtures. They cover every detector, plus these obfuscations: spacing, dashes, dots, lowercase, zero-width chars, Devanagari and fullwidth digits, spelled digits, `+91`/`0091`/`0` prefixes, values inside JSON keys, deep nesting, lists, values inside question instructions, GSTIN-embedded PAN, UPI handles, names in native script, names in ALL CAPS, names given as initials (`K. Venkatesh`). There's also a **clean** set of at least 100 realistic redacted states and question texts that must *not* be blocked, which gives the false-positive rate.
- `jevloan pii fixtures-check` prints the catch rate (it must be 100%) and the clean-set false-positive rate.

### 3.6 Loan file schema (W1-data): `jevloan.data.schema`
Pydantic models, one file per line in `data/book.jsonl`:
```
LoanFile:
  file_id: "F000001"   segment: salaried_personal | self_employed | msme_business | secured_home
  applicant: {name, name_native: str|None, gender: F|M|X, dob_year, pan, aadhaar, phone, email,
              address: {line1, city, pincode, state}, preferred_language: en|hi|ta|bn|mr|te}   # synthetic PII, raw book only
  co_applicant: {name, pan, relation} | None
  demographics: {gender, pincode_cluster: PC1..PC8, language}    # used by fairness eval only; never in state
  application: {product, loan_amount_inr, tenure_months, purpose, employment_type, employer_name|None,
                business_name|None, years_in_job_or_business, declared_monthly_income_inr, city_tier}
  bureau: {score: int|None (None = NTC), active_loans, max_dpd_12m, enquiries_6m, writeoffs_or_settlements: int, history_months}
  income: {verified_monthly_income_inr, volatility_cv, months_history, documentation_type: salary_slip|itr|gst_and_bank|informal_declared}
  obligations: {existing_emi_inr, proposed_emi_inr, credit_card_utilization}
  bank: {account_number, months_covered, most_recent_month_age, salary_credits_months: int|None, salary_narration_employer: str|None,
         avg_monthly_credits_inr, emi_bounces_6m, cash_deposit_share, min_balance_breaches_6m}
  gst: {gstin, filings_on_time_12m, turnover_12m_inr, bank_credits_12m_inr, months_filed} | None     # self_employed(optional), msme
  property: {market_value_inr, valuation_2_inr, property_type, title_status: clear|disputed|pending_mutation,
             legal_opinion: positive|adverse|pending, ltv} | None                                   # secured_home
  identity: {pan_aadhaar_linked, phone_vintage_months, email_domain_type: corporate|free|disposable,
             address_shared_with_other_apps, bureau_history_vs_age_consistent}
  documents: [{doc_id, doc_type, month_age, language, script: latin|devanagari|tamil|bengali, text}]
      doc_type ∈ salary_slip, form16, itr, bank_statement_header, address_proof_utility_bill, address_proof_rent_agreement,
                 address_proof_passport, pan_card_text, employer_letter, gst_return_summary, business_registration, property_title, valuation_report
  sanction_memo: {text, product, ticket_band, tenure_months, conditions: [str], rate_pct}
  kfs: {text, principal_inr, fees_inr, rate_pct, emi_inr, tenure_months, apr_stated_pct, disclosures_present: [str]}
  post_disbursal: {months: [{m, dpd, emi_bounced, partial_payment, avg_balance_inr}] (6 months),
                   covenants: [{covenant_id, required, reported_value, evidence_text}] (msme_business only)}
  labels: {sanctionable, fraud, fraud_type: identity_mismatch|salary_pattern_mismatch|gst_bank_mismatch|synthetic_identity|None,
           missing_items: [income_proof|address_proof|statements|fields_coherence], outcome_12m: repays|slips|defaults, risk_pd,
           primary_weakness: income_documentation|repayment_history|debt_burden|employment_or_business_stability|collateral|bureau_thin_file,
           closeness_level: 0..4, memo_defects: [memo_condition_mismatch|apr_math_wrong|disclosure_missing:<name>],
           ews_truth: {<F_ews_* id>: bool}, covenant_breaches: [covenant_id], question_truth: {<question_id>: bool|int}}
  meta: {generator_version, seed, disparity_subset: lang_doc_script|gender_income_proxy|pincode_bureau_thin|None}
```
- The book has at least 2,000 files: 30% salaried_personal, 20% self_employed, 25% msme_business, 25% secured_home. A seed makes it deterministic.
- Base rates: fraud about 5%, split as identity 1.5%, salary pattern 1.5% (salaried only), GST/bank 1.5% (business only), synthetic 1%. Files with at least one missing item about 15%. Files with at least one memo defect about 20%. Sanctionable about 55–65%.
- The risk function lives in `data/risk.py`: `risk_pd = sigmoid(β·x)` over standardised features (bureau score or NTC, FOIR, income volatility, max DPD, bounces, LTV, business vintage, GST consistency). The coefficients are written out in `docs/DATA_CARD.md`. The 12-month outcome is drawn as `defaults` with probability pd, `slips` with probability `min(0.3, 1.5·pd)`, and `repays` otherwise.
- `sanctionable` is the documented synthetic credit policy applied to the **true** features. It's true only when: not fraud, true FOIR ≤ segment limit (salaried 55%, self-employed 50%, MSME DSCR ≥ 1.25, home 60%), bureau ≥ 650 (NTC is allowed only for salaried with income ≥ ₹50k), max DPD over 12 months < 60, no write-offs, LTV ≤ 80% with clear title for home loans, business vintage ≥ 2 years, and `risk_pd < pd_cutoff[segment]` (0.08 / 0.10 / 0.10 / 0.05).
- `closeness_level` buckets how far the file sits from the sanctionable boundary, from 0 (clearly not) to 4 (clearly yes).
- The documents have to *contain* the evidence the truth is based on. For example, a salary_pattern_mismatch file carries a salary slip from `[EMPLOYER_A]` while the bank narration names another employer. A stale income proof has `month_age` ≥ 3.
- Engineered disparities make up about 10% of the book and are labelled in `meta.disparity_subset`:
  1. `lang_doc_script`: Tamil and Bengali speakers get address-proof text in native script. Their truth is still valid.
  2. `gender_income_proxy`: women in self_employed get `documentation_type = informal_declared` with no effect on their true outcome.
  3. `pincode_bureau_thin`: PC7 gets an inflated NTC rate while their true PD stays the same.
- `jevloan data generate --n 2000 --seed 7 [--out data/book.jsonl]` and `jevloan data stats`.

### 3.7 State schema (W2-state): `jevloan.state`
`build_state(file: LoanFile, stage) -> dict`, dispatched by segment. It's deterministic, and every value is either a band, an enum, a small integer, or redacted text. Money never appears as a raw rupee figure: money is always a band from this set: `<1L, 1-3L, 3-5L, 5-10L, 10-25L, 25-50L, 50L-1Cr, 1-2Cr, 2-5Cr, >5Cr` for amounts, and `<10k, 10-25k, 25-50k, 50-75k, 75k-1L, 1-2L, 2-5L, >5L` per month.

Appraisal stage (top-level keys are fixed, and a segment leaves out the blocks it doesn't have):
```
{ "schema": "jevloan.state.v1", "stage": "appraisal", "segment": ...,
  "application": {product, loan_amount_band, tenure_months, purpose, employment_type, years_in_job_or_business_band, declared_monthly_income_band, applicant_age_band, city_tier},
  "bureau": {score_band: NTC|<600|600-649|650-699|700-749|750-799|800+, active_loans, max_dpd_12m_band: 0|1-29|30-59|60-89|90+, enquiries_6m, writeoffs_or_settlements, history_length_band},
  "income": {verified_monthly_income_band, volatility: low|moderate|high, months_history, documentation_type},
  "obligations": {existing_emi_band, proposed_emi_band, foir_pct_band: <30|30-40|40-50|50-55|55-60|60-70|>70, segment_foir_limit_pct, credit_card_utilization_band},
  "bank": {months_covered, months_required, most_recent_month_age, salary_credits_months, salary_narration_employer_token, avg_monthly_credits_band, emi_bounces_6m, cash_deposit_share_band, min_balance_breaches_6m},
  "gst": {filings_on_time_12m, months_filed, gst_turnover_band_12m, bank_credits_band_12m, gst_to_bank_ratio_band: <0.5|0.5-0.8|0.8-1.2|1.2-2|>2},     # business only
  "business": {dscr_band: <1|1-1.25|1.25-1.5|1.5-2|>2, vintage_years_band},                                                                       # msme / self_employed
  "property": {property_type, market_value_band, ltv_pct_band: <60|60-70|70-75|75-80|80-85|>85, ltv_limit_pct, title_status, legal_opinion, valuation_spread_band: <5%|5-10%|10-20%|>20%},  # secured_home only
  "identity_signals": {pan_aadhaar_linked, phone_vintage_band: <3m|3-12m|1-3y|>3y, email_domain_type, address_shared_with_other_apps_band: 0|1-2|3+, bureau_history_consistent_with_age},
  "documents": [{doc_type, month_age, script, text}] }       # text redacted, at most 120 words per doc, at most 7 docs
```
Sanction-docs stage: `{schema, stage, segment, "sanction_memo": {text, product, ticket_band, tenure_months, conditions}, "kfs": {text, apr_stated_pct, apr_recomputed_pct, rate_pct, tenure_months}}`. `apr_recomputed_pct` comes from `finance.apr_from_components`. Percentages are rounded to 2 decimals and aren't personal data.

Monitoring stage: `{schema, stage, segment, "loan": {product, loan_amount_band, tenure_months, months_since_disbursal}, "repayment": [{m, dpd_band, emi_bounced, partial_payment, avg_balance_band}], "covenants": [{covenant_id, required, reported_value_band, evidence_text}]}`.

Checks (W2-state):
- `state/tokens.py`: `estimate_tokens(obj) = ceil(len(canonical_json(obj)) / 3.5)`. Once real Jev runs, the orchestrator calibrates this against `usage.input_tokens`.
- `jevloan state stats`: token distribution by segment and stage. **M1 requirement: mean under 3k and p99 under 3.5k, measured on appraisal states.**
- `jevloan state leak-scan`: for every file and stage, (a) searches every known raw PII value from the file (normalised, case-folded, digits compacted) as a substring of the state, and (b) runs `PIIGate.scan`. **M1 requirement: (a) is 0 hits. For (b), every block is reported and reviewed.** A block is either a leak the gate caught or a false positive, and in either case the state builder gets fixed.

### 3.8 Question catalogue (W2-packs1 and W2-packs2): `jevloan.modules`
`modules/base.py`:
```python
@dataclass(frozen=True)
class QuestionDef:
    qid: str; module: Literal["A","B","C","D","E","F"]; stage: str; qtype: Literal["noul","choice","score"]
    segments: frozenset[str]            # the segments it applies to
    risk_polarity: Literal["yes_is_good","yes_is_bad","ordinal_high_is_good"]
    wire: dict                          # instructions + criteria in wire format with the rubric
    routing_relevant: bool              # counted by the ECE stop condition
def questions_for(stage: str, segment: str, state: dict) -> dict[str, dict]   # qid -> wire dict; may be parameterised by state (e.g. one noul per covenant, grid row)
ALL_QUESTIONS: dict[str, QuestionDef]
```
Rubric format, which is mandatory for every question. It follows the vendor's structured-criteria guidance:
- Noul: `instructions = {"question": ..., "focus": ..., "refer_to": ["`bank.months_covered`", ...]}`; `criteria = {"true": {"what": ..., "examples": [2–3]}, "false": {"what": ..., "not_for": ..., "examples": [2–3]}}`. Wording is literal and points at state fields in backticks. No double negatives, and the true side always means "yes" to the question as it's worded.
- Score: `criteria = [ {"level": name, "what": ..., "not_for": ..., "examples": [2–3]}, ... ]` ordered from worst to best.
- Choice: `criteria = {option: {"what": ..., "not_for": ..., "examples": [...]}}`.
- A test checks that every question passes `PIIGate.check` and that every rubric has 2–3 examples per side or level.

| qid | type | segments | polarity | truth (the generator computes this into `labels.question_truth`) |
|---|---|---|---|---|
| **A: readiness** (appraisal) |
| A_income_proof_current | noul | all | yes_is_good | `income_proof` not in missing_items |
| A_address_proof_valid | noul | all | yes_is_good | `address_proof` not in missing_items |
| A_statements_cover_months | noul | all | yes_is_good | `statements` not in missing_items |
| A_fields_cohere | noul | all | yes_is_good | `fields_coherence` not in missing_items |
| **B: fraud** (appraisal) |
| B_identity_coheres | noul | all | yes_is_good | fraud_type not in {identity_mismatch, synthetic_identity} |
| B_salary_matches_employer | noul | salaried_personal, secured_home* | yes_is_good | fraud_type != salary_pattern_mismatch (*when the applicant is salaried) |
| B_gst_bank_consistent | noul | self_employed (with GST), msme_business | yes_is_good | fraud_type != gst_bank_mismatch |
| B_synthetic_identity_signals | noul | all | yes_is_bad | fraud_type == synthetic_identity |
| **C: appraisal support** (appraisal) |
| C_willingness | score (5) | all | ordinal_high_is_good | level from max_dpd_12m, writeoffs, bounces (0 serious delinquency … 4 spotless) |
| C_recent_delinquency | noul | all | yes_is_bad | max_dpd_12m ≥ 30 |
| C_capacity | score (5) | all | ordinal_high_is_good | level from the true FOIR (or DSCR) relative to the limit, and volatility |
| C_foir_within_limit | noul | all | yes_is_good | true FOIR ≤ limit (for MSME: DSCR ≥ 1.25) |
| C_income_stable | noul | all | yes_is_good | volatility_cv < 0.25 and months_history ≥ 12 |
| C_collateral_adequacy | score (5) | secured_home | ordinal_high_is_good | from LTV vs limit, title, legal opinion, valuation spread |
| C_collateral_title_clear | noul | secured_home | yes_is_good | title_status == clear and legal_opinion == positive |
| **D: triage** (appraisal, speculative) |
| D_closeness | score (5) | all | ordinal_high_is_good | labels.closeness_level |
| D_doubt_income_documentation | noul | all | yes_is_bad | primary_weakness == income_documentation |
| D_doubt_repayment_history | noul | all | yes_is_bad | primary_weakness == repayment_history |
| D_doubt_debt_burden | noul | all | yes_is_bad | primary_weakness == debt_burden |
| D_doubt_stability | noul | all | yes_is_bad | primary_weakness == employment_or_business_stability |
| D_doubt_collateral | noul | secured_home | yes_is_bad | primary_weakness == collateral |
| D_doubt_thin_file | noul | all | yes_is_bad | primary_weakness == bureau_thin_file |
| **E: memo and KFS** (sanction_docs) |
| E_memo_matches_grid | noul | all | yes_is_good | `memo_condition_mismatch` not in memo_defects. The instructions include the applicable grid row from `sanction_grid_v1.yaml` as structured data. |
| E_rate_math_correct | noul | all | yes_is_good | `apr_math_wrong` not in memo_defects. The deterministic check in code is authoritative; the Noul corroborates it and catches memo/KFS rate inconsistency. |
| E_disclosures_complete | noul | all | yes_is_good | no `disclosure_missing:*` in memo_defects |
| E_disclosure_<name> | noul | all | yes_is_good | one per required disclosure, speculative, used as the reason |
| **F: monitoring** (monitoring) |
| F_ews_dpd_rising | noul | all | yes_is_bad | ews_truth |
| F_ews_emi_bounces | noul | all | yes_is_bad | ews_truth (≥ 2 bounces in 6 months) |
| F_ews_partial_payments | noul | all | yes_is_bad | ews_truth |
| F_ews_balance_stress | noul | all | yes_is_bad | ews_truth (average balance falling ≥ 40%) |
| F_covenant_<covenant_id> | noul | msme_business | yes_is_good | covenant_id not in covenant_breaches |

MSME covenants: `dscr_min_1_25`, `stock_statement_monthly`, `no_unapproved_borrowing`, `insurance_current`.

### 3.9 Policy config (W2-policy; the grid and disclosures come from W1-data)
`config/policy/policy_v1.yaml`, top-level shape:
```yaml
version: "policy-2026.09-v1"
owners:            # D9
  credit_exceptions: {role: "Zonal Credit Head", owner_name: UNASSIGNED}
  fraud_investigation: {role: "Head, Fraud Risk Control Unit", owner_name: UNASSIGNED}
  deficiency_ops: {role: "Branch Operations Manager", owner_name: UNASSIGNED}
  decline_confirmation: {role: "Credit Manager (sanctioning authority)", owner_name: UNASSIGNED}
  disbursal_correction: {role: "Central Processing Unit Lead", owner_name: UNASSIGNED}
  watchlist_review: {role: "Credit Monitoring Head", owner_name: UNASSIGNED}
  model_risk: {role: "Chief Risk Officer (model risk)", owner_name: UNASSIGNED}
noul_confidence: "abs(2p-1)"
min_confidence: {noul: 0.5, choice: 0.5, score: 0.45}     # below this on a routing question → HUMAN_REVIEW, reason LOW_CONFIDENCE
modules:
  A: {fail_if_p_below: 0.5, on_fail: DEFICIENCY_NOTICE, missing_item_names: {...qid: human-readable item}}
  B: {investigate_if: {yes_is_bad: {p_at_least: 0.8}, yes_is_good: {p_at_most: 0.2}}, on_signal: FRAUD_INVESTIGATION, never_auto_decline: true}
  C: {segments: {salaried_personal: {weights: {C_willingness: .30, C_capacity: .30, C_foir_within_limit: .15, C_income_stable: .10, C_recent_delinquency: .15}, bands: {pass: 0.70, borderline: 0.50}}, ...}}
     # score features are normalised to [0,1] as expected_level/(levels-1); yes_is_bad nouls use 1-p
  D: {order_by: closeness_desc, attach_top_doubt: true}
  E: {fail_if_p_below: 0.5, deterministic_apr_tolerance_pp: 0.10, on_fail: DISBURSAL_BLOCKED}
  F: {warning_threshold_p: 0.6, tiers: {T1: 1, T2: 2, T3: 3}, covenant_breach_counts_as: 2}
decline: {require_human_confirmation: true}
reason_codes: {...}   # e.g. ACCEPT_AS_ADVISED, MODIFY_TERMS, REJECT_MODEL_MISREAD_DOCS, REJECT_MODEL_MISREAD_CONDUCT, OVERRIDE_POLICY_EXCEPTION, FRAUD_CONFIRMED, FRAUD_CLEARED, DEFICIENCY_CURED, ...
```
- `sanction_grid_v1.yaml` has one row per product (personal_loan_unsecured, business_loan_self_employed, msme_term_loan, home_loan) and ticket band. Each row gives `max_tenure_months`, `required_conditions` (e.g. guarantor for an unsecured loan over 10L, property insurance and an equitable mortgage for a home loan, hypothecation of stock for MSME over 50L, CGTMSE cover for MSME collateral-free loans), and `max_ltv_pct`.
- `kfs_disclosures_v1.yaml` lists: `apr`, `total_cost_of_credit`, `fees_and_charges_breakup`, `cooling_off_period`, `grievance_redressal_officer`, `recovery_agent_policy`, `repayment_schedule`, `penal_charges`.
- `pricing_v1.yaml`: version, `benchmark_rate_pct` (EBLR, config), `segment_spread_pct`, `risk_premium_pct_by_band` (keyed by the C band and composite quantile), `bureau_adjustment_pct`, `floor_pct`, `cap_pct`, `processing_fee_pct`. `policy/pricing.py: price(file, c_result) -> PricingResult(rate_pct, apr_pct, line_items=[{name, value_pct, source}], formula_version)`. It's written to the log as a `PRICING` event for every file whose outcome is PROCEED or a human-approved borderline. The model never sets a price: the only model-derived input is the C band, and it comes through the policy engine.

### 3.10 Policy engine, outcomes, queue (W2-policy)
- `policy/outcomes.py`: `class Outcome(StrEnum)` with PROCEED_TO_SANCTIONING_AUTHORITY, HUMAN_REVIEW, DECLINE_RECOMMENDED, DEFICIENCY_NOTICE, FRAUD_INVESTIGATION, DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF, DISBURSAL_BLOCKED, WATCHLIST_T0, WATCHLIST_T1, WATCHLIST_T2, WATCHLIST_T3. Reason codes for failure routing: MODEL_TIMEOUT, MODEL_ERROR, MODEL_RATE_LIMITED, MODEL_UNREACHABLE, CIRCUIT_OPEN, PII_BLOCKED, FORCED_HUMAN_REVIEW, LOW_CONFIDENCE.
- `policy/engine.py`:
```python
@dataclass class Reason: qid: str; p: float; confidence: float; text: str        # the text comes from the rubric, never from the model
@dataclass class ModuleResult: module: str; outcome: str; score: float | None; band: str | None; flags: list[str]; reasons: list[Reason]; low_confidence: bool
@dataclass class StageDecision: file_id; stage; outcome: Outcome; queue: str | None; closeness: float | None; top_reason: Reason | None; modules: dict[str, ModuleResult]; reason_codes: list[str]
class PolicyEngine:
    def __init__(self, policy: dict, policy_version: str, grid: dict, disclosures: list[str]): ...
    def decide(self, *, file_id, segment, stage, call: JevCallResult, state: dict) -> StageDecision   # pure; no I/O
```
  The engine is pure and gets unit-tested against hand-built answer dicts. A failed `call` always produces HUMAN_REVIEW with the matching reason code.
- `queue/store.py`: table `queue_items(id, file_id, stage, queue, closeness, top_reason_json, reasons_json, status open|decided, created_ts, decided_ts)`. `HumanQueue(db_path, audit)` provides `enqueue(decision) -> id`, `list(queue=None, status="open")` (credit_review sorts by closeness desc then created_ts), `get(id)`, and `decide(id, decision: accept|modify|reject, reason_code, reviewer_id, notes) -> audit entry`. `decide` writes a `HUMAN_DECISION` audit entry and rejects unknown reason codes. Queues: `credit_review`, `fraud_investigation`, `deficiency_ops`, `decline_confirmation`, `disbursal_correction`, `watchlist_review`.

### 3.11 Pipeline (W3-pipeline)
```python
class Pipeline:
    def __init__(self, runtime, policy_engine, pricing, gateway: JevGateway, audit: AuditLog, queue: HumanQueue): ...
    async def run_stage(self, file: LoanFile, stage: str) -> StageDecision
        # build_state → questions_for → gateway.ask → engine.decide → audit POLICY_OUTCOME → (queue.enqueue + audit QUEUED) → (pricing + audit PRICING)
    async def process_file(self, file, stages=("appraisal","sanction_docs","monitoring")) -> dict[str, StageDecision]
async def replay(book_path, runtime, limit=None, concurrency=None) -> ReplaySummary    # jevloan pipeline replay
def trail_complete(audit, file_id, stages) -> tuple[bool, list[str]]                    # pipeline/trail.py
```
- Complete trail for each stage: exactly one of MODEL_CALL / MODEL_FAILURE / PII_BLOCK, then POLICY_OUTCOME, then QUEUED if the outcome routes to a queue, and PRICING if the outcome is PROCEED. `jevloan pipeline check-trails` exits non-zero if any file is incomplete.
- The per-file results of a replay go to `data/runs/<run_id>/decisions.jsonl` (file_id, stage, outcome, module scores, every answer's p and confidence, latency, tokens), which the eval harness reads.

### 3.12 Evaluation (W4-eval)
`jevloan eval run --run-id X` reads a replay's decisions plus the book labels and writes `reports/<run_id>/metrics.json`, `disagreements.md`, and `GO_NO_GO_MEMO.md` (the template with every value filled in).
- Agreement per module: A (deficiency vs non-empty missing_items; item-level too), B (investigation vs fraud; precision, recall, F1 at the configured threshold), C (PASS vs sanctionable on non-borderline files, plus the borderline share, plus agreement for PASS ∪ borderline-accepted), D (Spearman correlation of closeness with closeness_level; top-doubt accuracy against primary_weakness), E (block vs non-empty memo_defects), F (watchlist tier ≥ T2 vs outcome ∈ {slips, defaults}; covenant agreement). The rules baseline is computed alongside each one.
- Per-question calibration: Nouls get binary ECE (10 equal-width bins) plus MCE, Brier, n, and a **count and rate of confident-wrong answers** (p ≥ 0.9 and truth false, or p ≤ 0.1 and truth true). Scores and Choices get top-label ECE of `confidence` against argmax correctness, plus expected-level MAE. Every question is reported on its own and never aggregated.
- Parity, using `config/parity_thresholds.yaml`, by gender, pincode_cluster, and language: PROCEED rate ratio against the reference group, the equal-opportunity gap (PROCEED rate among sanctionable files), per-group ECE of routing questions, and calibration-in-the-large. Each is reported with and without the disparity subset. Groups smaller than `min_group_n` report INSUFFICIENT_N.
- Proxy probe: a numpy logistic regression fitted on flattened state features plus doc-script flags, with 5-fold CV. It reports AUC per protected attribute against the pre-registered maximum.
- Engineered-disparity detection: for each of the 3 subsets, did parity or the probe flag it? A subset that goes undetected means the test had no power. That counts as a failure.
- PII: fixture catch rate, clean-set false-positive rate, and the result of the full-book leak scan.
- Cost and latency: input tokens per call and per file, USD per file at the configured price, and p50/p95/p99 latency per call and per file, plus the failure-fallback rate. All broken down by segment.
- Disagreement report: files where a module's outcome disagrees with its label, grouped by module → driver (the qid with the largest weighted contribution, or top doubt) → segment. Each group gets counts plus 3 example files showing the key state fields and answers. Written in Markdown for a human reader.

### 3.13 Advisory API (W5-api)
FastAPI app from `create_app(runtime, *, transport=None)`:
- `GET /api/v1/health` → `{backend, model, breaker_state, human_only, policy_version, audit_ok}`
- `POST /api/v1/files/{file_id}/score?stage=appraisal`: runs the stage for a file from the synthetic book and returns the advisory JSON, with `"advisory_only": true` and the disclaimer text. It never returns anything worded as an approval.
- `GET /api/v1/queue?queue=credit_review&status=open`, `GET /api/v1/queue/{item_id}`
- `POST /api/v1/queue/{item_id}/decision` with body `{decision, reason_code, reviewer_id, notes}`
- `GET /api/v1/files/{file_id}/trail` returns the audit dump
- `GET /review` (queue list), `GET /review/{item_id}` (probabilities table, confidence, reasons drawn from rubric text, top doubt factor, and accept/modify/reject buttons with a reason-code dropdown), `POST /review/{item_id}/decision` (form post, then redirect). Server-rendered Jinja2, no JS framework, and a banner stating that the tool is advisory and humans make the decisions.
- `jevloan api serve [--mode dev|production]`. Production mode refuses to start while any owner is UNASSIGNED (D9) or while backend=sim.

---

## 4. Waves and milestone acceptance

| Wave | Builders, in parallel within a wave | Depends on | Milestone gate, checked by the orchestrator |
|---|---|---|---|
| W0 | orchestrator: PLAN.md, `config/parity_thresholds.yaml`, `docs/GO_NO_GO_MEMO_TEMPLATE.md` | none | Thresholds pre-registered and dated before any eval code exists |
| W1a | **W1-core** | W0 | `pytest` green; `jevloan audit verify` passes on an empty db; tamper test detects a modified row |
| W1b | **W1-jev**, **W1-pii**, **W1-data** in parallel | W1a | **M0:** `jevloan jev hello --backend sim` returns typed answers, the log verifies, and the real hello passes once the key exists. **M1 part:** generator produces 2,000 labelled files; PII fixture catch = 100% |
| W2 | **W2-state**, **W2-packs1**, **W2-packs2**, **W2-policy** in parallel | W1b | **M1:** state mean < 3k tokens, leak-scan 0 hits. Unit tests green for every pack and the engine |
| W3 | **W3-pipeline** | W2 | **M2:** full-book replay (sim) end to end; `check-trails` shows 100% complete; `audit verify` ok |
| W4 | **W4-eval**, then an orchestrator run on sim, then an orchestrator run on real Jev | W3 + key | **M3:** memo filled with measured numbers (a real-Jev run is needed for a GO) |
| W5 | **W5-api**, **W5-chaos** in parallel | W3 | **M4:** API tests plus chaos: timeout, 5xx, 429, connection drop, full outage, network killed mid-batch and mid-review (including a subprocess test that kills a live stub server). Every file either queued with a reason or fully logged; chain verifies |
| W6 | **W6-docs** (README, DATA_CARD pass); **W6-monitors** only if M3 is a go | W4, W5 | Definition of done (§11 of the spec) |

Agent briefs are written at spawn time. Each one includes: the plan sections, the files it owns, the contracts it consumes, and the tests that must pass.

---

## 5. Test strategy

| Layer | What gets proven | Where |
|---|---|---|
| Unit | config loading and validation, canonical hashing, the breaker state machine, the rate limiter, finance math (EMI/APR checked against hand-computed values), each detector, the redactor's consistency, state banding, rubric shape, engine precedence and every routing branch, the pricing line items that add up to the rate | `tests/test_*.py` |
| Adversarial PII | at least 150 fixtures caught at 100%; at least 100 clean samples used to measure false positives; every question pack passes the gate; the egress guard blocks even if gateway checks are bypassed (test calls the transport directly) | `tests/test_pii_*.py` |
| Property-style | a random book of 200 files → build states → none contains any known raw PII value | `tests/test_state_leaks.py` |
| Audit | the chain verifies; tampering is detected (drop the trigger, edit a row); UPDATE and DELETE are refused; dump shows the full trail | `tests/test_audit*.py` |
| Integration (sim) | 50-file replay: every trail complete, decisions written, eval runs on the output | `tests/test_pipeline*.py`, `tests/test_eval*.py` |
| Chaos | timeout at budget+1s → queued in under budget+0.5s; 500/529/429/401/422; connection drop; breaker opens and the system degrades to human-only; kill switch mid-batch of 60 concurrent files; API-level kill during review; a subprocess stub server killed with SIGKILL mid-run | `tests/chaos/` |
| Fairness power | on the sim run, all 3 engineered disparities are detected | `tests/test_fairness_power.py` |
| Real Jev (opt-in) | `pytest -m real_jev`: the hello call and a 20-file smoke replay | skipped when there's no key |
| Acceptance scripts | `scripts/accept_m0.sh` … `accept_m4.sh` run the milestone gates end to end | orchestrator |

---

## 6. Pre-registered thresholds and stop conditions
These are fixed in `config/parity_thresholds.yaml` and `docs/GO_NO_GO_MEMO_TEMPLATE.md` **before** any evaluation runs. Changing one after results come in has to be recorded in the memo as a deviation, with the reason.

---

## 7. Risks and how we handle them
| Risk | Mitigation |
|---|---|
| Jev struggles with numbers and dates | D2: every numeric comparison is precomputed as a band, and the rules baseline shows where Jev adds nothing |
| A 2 s budget against a real p95 | The eval measures the fallback rate, and the memo stop condition fires if it's over 5% |
| Gate false positives send too many files to humans | The clean-set FP rate is tracked. The state builder avoids free text and the redactor normalises dates and amounts |
| The simulator flatters the system | Simulator numbers are never a GO (D10) |
| Name detection is incomplete, especially in native scripts | Known entities get redacted first; the lexicon and pattern gate is a backstop; the limits are named in the README and memo |
| Rubric token cost | Measured and reported. Irrelevant at about $0.04 per million tokens, but kept visible |
