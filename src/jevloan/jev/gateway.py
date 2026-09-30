"""The only path from this system to Jev (PLAN section 3.3).

`ask()` never raises for a model-side problem. It returns a `JevCallResult` whose `failure` says what went
wrong, and writes exactly one audit entry (MODEL_CALL, MODEL_FAILURE or PII_BLOCK) so that the policy
engine can route the file to a human with the reason on record. Nothing leaves the process unless the PII
gate passed, and the transport is wrapped in a second, independent PII guard at the network boundary.
"""

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    NoulAnswer,
    RetryPolicy,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeRateLimitError,
)
from typesafe_sdk.constants import API_KEY_ENV

from jevloan.audit.log import AuditLog, utc_now_iso
from jevloan.canonical import canonical_json, sha256_hex
from jevloan.config import ConfigError, RuntimeConfig
from jevloan.jev.breaker import CircuitBreaker
from jevloan.jev.ratelimit import AsyncRateLimiter

Failure = Literal["timeout", "api_error", "rate_limited", "connection_error", "circuit_open", "pii_blocked", "forced_human"]
_MAX_DETAIL = 300


class Gate(Protocol):
    """What the gateway needs from `jevloan.pii.gate.PIIGate`."""

    def check(self, payload: Any) -> None:
        """Return normally if the payload is clean; raise (PIIBlocked, carrying `.findings`) if not."""


EgressGuard = Callable[[httpx2.AsyncBaseTransport, Gate], httpx2.AsyncBaseTransport]


@dataclass
class JevCallResult:
    ok: bool
    answers: dict[str, dict] | None  # wire-format answers; nouls carry an added "derived_confidence"
    model_version: str | None
    input_tokens: int | None
    latency_ms: float
    request_ts: str
    response_ts: str | None
    failure: Failure | None
    failure_detail: str | None
    state_hash: str
    audit_seq: int  # seq of the MODEL_CALL / MODEL_FAILURE / PII_BLOCK entry


def _answers_from(response: SystemOneResponse) -> dict[str, dict]:
    """SDK answer objects to plain wire-format dicts (score levels keyed by strings, as on the wire)."""
    answers: dict[str, dict] = {}
    for qid, answer in response.answers.items():
        match answer:
            case NoulAnswer():
                answers[qid] = {"type": "noul", "noul": answer.noul, "derived_confidence": abs(2 * answer.noul - 1)}
            case ChoiceAnswer():
                answers[qid] = {
                    "type": "choice",
                    "choice": answer.choice,
                    "probabilities": dict(answer.probabilities),
                    "confidence": answer.confidence,
                }
            case ScoreAnswer():
                answers[qid] = {
                    "type": "score",
                    "score": answer.score,
                    "probabilities": {str(level): p for level, p in answer.probabilities.items()},
                    "legend": {str(level): text for level, text in answer.legend.items()},
                    "confidence": answer.confidence,
                }
    return answers


def _egress_block(exc: BaseException) -> BaseException | None:
    """The PIIEgressBlocked raised by the egress guard, found anywhere in `exc`'s cause/context chain.

    The SDK wraps httpx2 request errors, so the guard's exception can arrive buried inside another one."""
    try:
        from jevloan.pii.egress import PIIEgressBlocked
    except ImportError:
        return None
    pending, seen = [exc], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, PIIEgressBlocked):
            return current
        pending += [linked for linked in (current.__cause__, current.__context__) if linked is not None]
    return None


def _classify(exc: BaseException, budget_s: float) -> tuple[Failure, str]:
    """Map what the call raised to a failure kind. Unknown errors count as api_error: the file goes to a human."""
    if isinstance(exc, TimeoutError):  # asyncio.wait_for's, and the SDK's TypeSafeAPITimeoutError (also a TimeoutError)
        return "timeout", f"no answer within the {budget_s:g}s budget"
    detail = f"{type(exc).__name__}: {exc}"[:_MAX_DETAIL]
    if isinstance(exc, TypeSafeRateLimitError):
        return "rate_limited", detail
    if isinstance(exc, TypeSafeAPIConnectionError):
        return "connection_error", detail
    return "api_error", detail


class JevGateway:
    """One SDK client per gateway; safe for many concurrent `ask()` calls (at most `max_concurrency` in flight)."""

    def __init__(
        self,
        runtime: RuntimeConfig,
        audit: AuditLog,
        gate: Gate,
        transport: httpx2.AsyncBaseTransport | None = None,
        *,
        egress_guard: EgressGuard | None = None,
    ) -> None:
        self._runtime = runtime
        self._audit = audit
        self._gate = gate
        self.breaker = CircuitBreaker(runtime.breaker_failure_threshold, runtime.breaker_cooldown_s)
        self._limiter = AsyncRateLimiter(runtime.rate_limit_rpm)
        self._slots = asyncio.Semaphore(runtime.max_concurrency)

        if runtime.backend == "sim":
            from jevloan.jev.sim_rules import REGISTRY, load_all
            from jevloan.jev.simulator import SimulatedJevTransport

            load_all()
            api_key = "sim-not-a-real-key"
            inner = transport or SimulatedJevTransport(rules=REGISTRY)
        else:
            api_key = os.environ.get(API_KEY_ENV, "").strip()
            if not api_key:
                raise ConfigError(f"backend 'real' needs the {API_KEY_ENV} environment variable (or a .env file)")
            inner = transport or httpx2.AsyncHTTPTransport()
        if egress_guard is None:
            from jevloan.pii.egress import PIIEgressGuardTransport  # imported late: it needs the gate

            egress_guard = PIIEgressGuardTransport

        self._client = AsyncTypeSafeClient(
            api_key=api_key,
            model=runtime.model,
            retry=RetryPolicy(max_retries=0),  # one attempt: the budget is a hard deadline, not a per-try timeout
            timeout=runtime.timeout_budget_s,
            transport=egress_guard(inner, gate),
        )

    @property
    def human_only(self) -> bool:
        """True when files must go to a human without a Jev call: breaker open, or the kill switch is on."""
        return self._runtime.force_human_review or self.breaker.state == "open"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ask(
        self,
        *,
        file_id: str,
        segment: str,
        stage: str,
        policy_version: str,
        state: dict,
        questions: dict[str, dict],
    ) -> JevCallResult:
        state_text = canonical_json(state)  # one serialisation: what is hashed is exactly what is logged
        state_hash = sha256_hex(state_text)
        entry: dict[str, Any] = {
            "file_id": file_id,
            "segment": segment,
            "stage": stage,
            "policy_version": policy_version,
            "state_hash": state_hash,
        }
        request_ts = utc_now_iso()

        if self._runtime.force_human_review:
            # The gate has not looked at this state, so it must not be stored.
            return self._failed(entry, request_ts, "forced_human", "kill switch on (force_human_review): Jev was not called")
        try:
            self._gate.check({"state": state, "questions": questions})
        except Exception as exc:  # fail closed: a crashing gate blocks the request just like a hit
            return self._blocked(entry, request_ts, exc, layer="gate")
        entry |= {"state_json": state_text, "questions_json": canonical_json(questions)}  # vetted: safe to keep

        async with self._slots:
            if not self.breaker.allow():
                return self._failed(entry, request_ts, "circuit_open", "circuit breaker open: Jev was not called")
            await self._limiter.acquire()
            request_ts = utc_now_iso()
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    self._client.system_one(state=state, questions=questions), self._runtime.timeout_budget_s
                )
                answers = _answers_from(response)
                wrong = sorted(qid for qid, q in questions.items() if answers.get(qid, {}).get("type") != q.get("type"))
                if wrong:
                    raise ValueError(f"response has no answer of the asked type for: {wrong}")
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                if blocked := _egress_block(exc):
                    return self._blocked(entry, request_ts, blocked, layer="egress")
                self.breaker.record_failure()
                failure, detail = _classify(exc, self._runtime.timeout_budget_s)
                return self._failed(entry, request_ts, failure, detail, call_ms=latency_ms)
            latency_ms = (time.perf_counter() - started) * 1000
            self.breaker.record_success()

        response_ts = utc_now_iso()
        input_tokens = response.usage.input_tokens
        row = self._audit.append(
            event_type="MODEL_CALL",
            **entry,
            model_version=response.model,
            answers_json=answers,
            request_ts=request_ts,
            response_ts=response_ts,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            detail_json={"backend": self._runtime.backend},
        )
        return JevCallResult(
            True, answers, response.model, input_tokens, latency_ms, request_ts, response_ts, None, None, entry["state_hash"], row["seq"]
        )

    def _failed(
        self, entry: dict[str, Any], request_ts: str, failure: Failure, detail: str, *, call_ms: float | None = None
    ) -> JevCallResult:
        """Write the MODEL_FAILURE entry and build the result. `call_ms` is None if no call was made."""
        response_ts = None if call_ms is None else utc_now_iso()
        latency_ms = call_ms or 0.0
        row = self._audit.append(
            event_type="MODEL_FAILURE",
            **entry,
            request_ts=request_ts,
            response_ts=response_ts,
            latency_ms=latency_ms,
            detail_json={"failure": failure, "failure_detail": detail, "backend": self._runtime.backend},
        )
        return JevCallResult(
            False, None, None, None, latency_ms, request_ts, response_ts, failure, detail, entry["state_hash"], row["seq"]
        )

    def _blocked(self, entry: dict[str, Any], request_ts: str, exc: BaseException, *, layer: str) -> JevCallResult:
        """Write the PII_BLOCK entry (detector, path and masked value only: never the state or the questions)."""
        raw = getattr(exc, "findings", None)  # a copy of the exception (the SDK redacts and re-creates them) may lose it
        findings = [
            {"detector": getattr(f, "detector", None), "path": getattr(f, "path", None), "masked": getattr(f, "masked", None)}
            for f in (raw if isinstance(raw, (list, tuple)) else [])
        ]
        detectors = ", ".join(sorted({str(f["detector"]) for f in findings}))
        detail = f"PII {layer} blocked the request ({detectors or type(exc).__name__})"
        row = self._audit.append(
            event_type="PII_BLOCK",
            **{k: v for k, v in entry.items() if k not in ("state_json", "questions_json")},
            request_ts=request_ts,
            latency_ms=0.0,
            detail_json={"findings": findings, "error_type": type(exc).__name__, "layer": layer, "backend": self._runtime.backend},
        )
        return JevCallResult(False, None, None, None, 0.0, request_ts, None, "pii_blocked", detail, entry["state_hash"], row["seq"])
