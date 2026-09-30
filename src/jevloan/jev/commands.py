"""`jevloan jev hello`: one Noul, one Choice and one Score about a synthetic file, through the full gateway."""

import argparse
import asyncio
import sys

from jevloan.audit.log import AuditLog
from jevloan.config import ConfigError, load_runtime, resolve_path
from jevloan.jev.gateway import Gate, JevCallResult, JevGateway

# Banded and synthetic: no identifier, no name, no rupee figure. Questions use the structured rubric format (PLAN 3.8).
HELLO_STATE = {
    "schema": "jevloan.hello.v1",
    "segment": "salaried_personal",
    "application": {
        "product": "personal_loan_unsecured",
        "loan_amount_band": "3-5L",
        "tenure_months": 36,
        "employment_type": "salaried",
        "city_tier": "T1",
    },
    "bureau": {"score_band": "700-749", "max_dpd_12m_band": "0", "active_loans": 1, "enquiries_6m": 1},
    "income": {
        "verified_monthly_income_band": "50-75k",
        "volatility": "low",
        "months_history": 24,
        "documentation_type": "salary_slip",
    },
    "obligations": {"foir_pct_band": "30-40", "segment_foir_limit_pct": 55},
    "bank": {"emi_bounces_6m": 0, "min_balance_breaches_6m": 0},
}

HELLO_QUESTIONS = {
    "hello_foir_within_limit": {
        "type": "noul",
        "instructions": {
            "question": "Does the whole FOIR band lie at or below the segment FOIR limit?",
            "focus": "Compare the top of `obligations.foir_pct_band` with `obligations.segment_foir_limit_pct`. Ignore every other field.",
            "refer_to": ["`obligations.foir_pct_band`", "`obligations.segment_foir_limit_pct`"],
        },
        "criteria": {
            "true": {
                "what": "The top of the FOIR band is at or below the limit.",
                "examples": ["band 30-40 with a limit of 55", "band <30 with a limit of 50"],
            },
            "false": {
                "what": "Any part of the FOIR band is above the limit.",
                "not_for": "A band whose top is exactly the limit.",
                "examples": ["band 55-60 with a limit of 55", "band >70 with a limit of 55"],
            },
        },
    },
    "hello_primary_weakness": {
        "type": "choice",
        "instructions": {
            "question": "Which one factor is the main weakness of this file?",
            "focus": "Pick the factor with the clearest adverse signal. If no field shows an adverse signal, pick none_material.",
            "refer_to": [
                "`bureau.max_dpd_12m_band`",
                "`obligations.foir_pct_band`",
                "`obligations.segment_foir_limit_pct`",
                "`income.documentation_type`",
            ],
        },
        "criteria": {
            "repayment_history": {
                "what": "`bureau.max_dpd_12m_band` is anything other than 0.",
                "not_for": "A clean payment record.",
                "examples": ["max_dpd_12m_band of 30-59", "max_dpd_12m_band of 1-29"],
            },
            "debt_burden": {
                "what": "The FOIR band goes above the segment limit.",
                "not_for": "A FOIR band at or below the limit.",
                "examples": ["band 55-60 with a limit of 55", "band >70 with a limit of 50"],
            },
            "income_documentation": {
                "what": "`income.documentation_type` is informal_declared.",
                "not_for": "salary_slip, itr or gst_and_bank.",
                "examples": ["documentation_type of informal_declared"],
            },
            "none_material": {
                "what": "None of the three signals above is adverse.",
                "not_for": "Any adverse signal in the three fields above.",
                "examples": ["max_dpd_12m_band of 0, FOIR band within the limit, salary_slip"],
            },
        },
    },
    "hello_repayment_conduct": {
        "type": "score",
        "instructions": {
            "question": "How clean is the applicant's repayment conduct?",
            "focus": "Use days past due and the bounce counts only. Ignore income and leverage.",
            "refer_to": ["`bureau.max_dpd_12m_band`", "`bank.emi_bounces_6m`", "`bank.min_balance_breaches_6m`"],
        },
        "criteria": [
            {
                "level": "poor",
                "what": "A 30 or more days past due band, or 3 or more bounces and breaches combined.",
                "not_for": "A record with no days past due and no bounces.",
                "examples": ["max_dpd_12m_band of 30-59", "emi_bounces_6m of 3"],
            },
            {
                "level": "fair",
                "what": "A 1-29 band, or 1 to 2 bounces and breaches combined.",
                "not_for": "A record with no days past due and no bounces.",
                "examples": ["max_dpd_12m_band of 1-29", "emi_bounces_6m of 1"],
            },
            {
                "level": "clean",
                "what": "Days past due band of 0, and no bounces or breaches.",
                "not_for": "Any bounce, breach or days past due.",
                "examples": ["max_dpd_12m_band of 0 with emi_bounces_6m of 0", "all three fields at their lowest value"],
            },
        ],
    },
}


def register(subparsers: "argparse._SubParsersAction") -> None:
    jev = subparsers.add_parser("jev", help="talk to Jev through the guarded gateway")
    commands = jev.add_subparsers(dest="jev_command", required=True)
    hello = commands.add_parser("hello", help="one Noul, one Choice, one Score about a synthetic file; exits 2 on failure")
    hello.add_argument("--backend", choices=("real", "sim"), help="override the backend from config/runtime.yaml")
    hello.add_argument("--db", help="audit database (default: db_path from config/runtime.yaml)")
    hello.set_defaults(func=_hello)


def _make_gate() -> Gate:
    from jevloan.pii.gate import PIIGate

    return PIIGate()


def _hello(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run_hello(args))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


async def _run_hello(args: argparse.Namespace) -> int:
    runtime = load_runtime()
    if args.backend:
        runtime = runtime.model_copy(update={"backend": args.backend})
    db_path = args.db if args.db else resolve_path(runtime.db_path)
    gateway = JevGateway(runtime, AuditLog(db_path), _make_gate())
    try:
        result = await gateway.ask(
            file_id="HELLO",
            segment=HELLO_STATE["segment"],
            stage="hello",
            policy_version="hello",
            state=HELLO_STATE,
            questions=HELLO_QUESTIONS,
        )
    finally:
        await gateway.aclose()
    _print_result(runtime.backend, result, db_path)
    return 0 if result.ok else 2


def _print_result(backend: str, result: JevCallResult, db_path: object) -> None:
    if not result.ok:
        print(f"FAILED ({backend}): {result.failure}: {result.failure_detail}")
        print(f"audit entry {result.audit_seq} in {db_path}")
        return
    print(f"backend={backend} model={result.model_version} latency={result.latency_ms:.0f}ms input_tokens={result.input_tokens}")
    if backend == "sim":
        print("simulator output: PROVISIONAL, says nothing about real Jev")
    for qid, answer in (result.answers or {}).items():
        match answer["type"]:
            case "noul":
                print(f"  noul   {qid}: p={answer['noul']:.3f} derived_confidence={answer['derived_confidence']:.3f}")
            case "choice":
                print(f"  choice {qid}: {answer['choice']} confidence={answer['confidence']:.3f} probabilities={answer['probabilities']}")
            case _:
                print(f"  score  {qid}: {answer['score']:.2f} confidence={answer['confidence']:.3f} probabilities={answer['probabilities']}")
    print(f"audit entry {result.audit_seq} in {db_path}")
