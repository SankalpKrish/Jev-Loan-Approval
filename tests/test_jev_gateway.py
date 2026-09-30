import asyncio
import importlib
import json
import sys
import time
import types
from types import SimpleNamespace

import httpx2
import pytest

from jevloan.audit.log import AuditLog
from jevloan.canonical import canonical_json, sha256_hex
from jevloan.config import ConfigError, RuntimeConfig
from jevloan.jev.chaos import (
    ConnectionDropTransport,
    CountingTransport,
    SlowTransport,
    StatusTransport,
    SwitchableTransport,
)
from jevloan.jev.gateway import JevGateway
from jevloan.jev.simulator import SimulatedJevTransport

STATE = {"segment": "salaried_personal", "income": {"band": "50-75k"}, "bank": {"months_covered": 6}}
QUESTIONS = {
    "q_noul": {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}},
    "q_choice": {"type": "choice", "instructions": "Which?", "criteria": {"a": "first", "b": "second"}},
    "q_score": {"type": "score", "instructions": "How much?", "criteria": ["low", "mid", "high"]},
}
RAW_PAN = "ABCDE1234F"
FINDING = SimpleNamespace(detector="pan", path="state.income.note", masked="AB******4F")


def sim_transport(**kwargs) -> SimulatedJevTransport:
    kwargs.setdefault("latency_ms", (0, 0))
    return SimulatedJevTransport(**kwargs)


def passthrough(inner, gate):
    return inner


class StubGate:
    """Passes everything, and records what it saw, until `exc` is set: then it raises that."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.payloads: list = []

    def check(self, payload) -> None:
        self.payloads.append(payload)
        if self.exc is not None:
            raise self.exc


CleanGate = StubGate
RaisingGate = StubGate


class FakePIIBlocked(Exception):
    def __init__(self, findings) -> None:
        super().__init__("PII found")
        self.findings = findings


@pytest.fixture
async def make_gateway(tmp_db):
    """Build gateways over one audit log; they are closed at teardown. Defaults: sim backend, clean gate, no egress guard."""
    made: list[JevGateway] = []
    audit = AuditLog(tmp_db)

    def build(transport=None, *, gate=None, egress_guard=passthrough, **runtime) -> JevGateway:
        runtime.setdefault("rate_limit_rpm", 600_000)  # the limiter is tested on its own
        gateway = JevGateway(
            RuntimeConfig(backend="sim", **runtime), audit, gate or CleanGate(), transport, egress_guard=egress_guard
        )
        made.append(gateway)
        return gateway

    build.audit = audit  # type: ignore[attr-defined]
    yield build
    for gateway in made:
        await gateway.aclose()


def ask(gateway: JevGateway, *, file_id: str = "F000001", state: dict = STATE, questions: dict = QUESTIONS):
    return gateway.ask(
        file_id=file_id, segment="salaried_personal", stage="appraisal", policy_version="policy-test", state=state, questions=questions
    )


def only_entry(audit: AuditLog, file_id: str = "F000001") -> dict:
    (entry,) = audit.trail(file_id)
    return entry


def detail(entry: dict) -> dict:
    return json.loads(entry["detail_json"])


@pytest.fixture
def egress(monkeypatch):
    """`jevloan.pii.egress`: the real module once W1-pii has landed, else a stand-in with the same contract."""
    try:
        return importlib.import_module("jevloan.pii.egress")
    except ImportError:
        package, module = types.ModuleType("jevloan.pii"), types.ModuleType("jevloan.pii.egress")
        package.__path__ = []  # marks it as a package

        class PIIEgressBlocked(Exception):
            def __init__(self, message: str = "egress blocked", findings=()) -> None:
                super().__init__(message)
                self.findings = list(findings)

        module.PIIEgressBlocked = PIIEgressBlocked
        monkeypatch.setitem(sys.modules, "jevloan.pii", package)
        monkeypatch.setitem(sys.modules, "jevloan.pii.egress", module)
        return module


# --- success ------------------------------------------------------------------------------------------------


async def test_successful_call_writes_a_model_call_entry_with_answers(make_gateway):
    gateway = make_gateway(sim_transport())
    result = await ask(gateway)

    assert result.ok and result.failure is None and result.failure_detail is None
    assert set(result.answers) == set(QUESTIONS)
    assert result.model_version == "sim-jev-0.1"
    assert result.input_tokens and result.input_tokens > 0
    assert result.latency_ms >= 0
    assert result.request_ts <= result.response_ts
    assert result.state_hash == sha256_hex(canonical_json(STATE))

    entry = only_entry(make_gateway.audit)
    assert entry["seq"] == result.audit_seq
    assert entry["event_type"] == "MODEL_CALL"
    assert (entry["file_id"], entry["segment"], entry["stage"], entry["policy_version"]) == (
        "F000001",
        "salaried_personal",
        "appraisal",
        "policy-test",
    )
    assert entry["model_version"] == "sim-jev-0.1"
    assert entry["state_hash"] == result.state_hash
    assert entry["state_json"] == canonical_json(STATE)
    assert entry["questions_json"] == canonical_json(QUESTIONS)
    assert json.loads(entry["answers_json"]) == result.answers
    assert entry["request_ts"] == result.request_ts and entry["response_ts"] == result.response_ts
    assert entry["latency_ms"] == pytest.approx(result.latency_ms)
    assert entry["input_tokens"] == result.input_tokens
    assert detail(entry) == {"backend": "sim"}
    assert make_gateway.audit.verify().ok


async def test_answers_are_plain_wire_format_dicts(make_gateway):
    answers = (await ask(make_gateway(sim_transport()))).answers

    noul = answers["q_noul"]
    assert noul["type"] == "noul"
    assert noul["derived_confidence"] == pytest.approx(abs(2 * noul["noul"] - 1))

    choice = answers["q_choice"]
    assert choice["type"] == "choice"
    assert choice["choice"] in QUESTIONS["q_choice"]["criteria"]
    assert set(choice["probabilities"]) == {"a", "b"}
    assert 0 <= choice["confidence"] <= 1

    score = answers["q_score"]
    assert score["type"] == "score"
    assert set(score["probabilities"]) == {"0", "1", "2"}  # string keys, as on the wire
    assert score["legend"] == {"0": "low", "1": "mid", "2": "high"}
    assert 0 <= score["score"] <= 2 and 0 <= score["confidence"] <= 1
    json.dumps(answers)  # nothing but plain JSON types


async def test_default_sim_backend_uses_the_registered_rules(make_gateway):
    from jevloan.jev.commands import HELLO_QUESTIONS, HELLO_STATE

    result = await ask(make_gateway(), state=HELLO_STATE, questions=HELLO_QUESTIONS)  # no transport: the gateway's own simulator
    assert result.ok
    assert result.answers["hello_primary_weakness"]["choice"] == "none_material"  # a rule ran, not the flat default
    assert result.answers["hello_repayment_conduct"]["confidence"] > 0.3


async def test_state_and_questions_reach_the_wire_as_sent(make_gateway):
    counting = CountingTransport(sim_transport())
    await ask(make_gateway(counting))
    (body,) = counting.requests
    assert body["state"] == STATE
    assert body["questions"] == QUESTIONS
    assert body["model"] == "jev-latest"


async def test_gate_sees_state_and_questions_and_the_guard_wraps_the_transport(make_gateway):
    gate, wrapped = CleanGate(), []

    def guard(inner, given_gate):
        wrapped.append((inner, given_gate))
        return inner

    inner = sim_transport()
    await ask(make_gateway(inner, gate=gate, egress_guard=guard))
    assert gate.payloads == [{"state": STATE, "questions": QUESTIONS}]
    assert wrapped == [(inner, gate)]


# --- failure mapping --------------------------------------------------------------------------------------


async def test_a_slow_backend_times_out_at_the_budget(make_gateway):
    counting = CountingTransport(SlowTransport(sim_transport(), delay_s=5.0))
    gateway = make_gateway(counting, timeout_budget_s=0.3)
    start = time.monotonic()
    result = await ask(gateway)
    elapsed = time.monotonic() - start

    assert not result.ok and result.failure == "timeout"
    assert result.answers is None and result.model_version is None and result.input_tokens is None
    assert 0.25 < elapsed < 0.6
    assert 250 < result.latency_ms < 600
    assert len(counting.requests) == 1  # one attempt, no retry after the timeout
    entry = only_entry(make_gateway.audit)
    assert entry["event_type"] == "MODEL_FAILURE" and entry["seq"] == result.audit_seq
    assert entry["answers_json"] is None and entry["model_version"] is None
    assert entry["state_json"] == canonical_json(STATE)  # it passed the gate, so it is kept
    assert detail(entry) == {"failure": "timeout", "failure_detail": result.failure_detail, "backend": "sim"}
    assert make_gateway.audit.verify().ok


@pytest.mark.parametrize(
    ("status", "failure"),
    [(500, "api_error"), (529, "api_error"), (429, "rate_limited"), (401, "api_error"), (422, "api_error")],
)
async def test_http_errors_map_to_a_failure_and_are_never_retried(make_gateway, status, failure):
    counting = CountingTransport(StatusTransport(status))
    result = await ask(make_gateway(counting))

    assert not result.ok and result.failure == failure
    assert str(status) in result.failure_detail
    assert len(counting.requests) == 1  # the SDK's default policy would have tried 3 times for 429/5xx
    entry = only_entry(make_gateway.audit)
    assert entry["event_type"] == "MODEL_FAILURE"
    assert detail(entry)["failure"] == failure


async def test_connection_drop_is_a_connection_error_and_is_not_retried(make_gateway):
    class CountedDrop(ConnectionDropTransport):
        attempts = 0

        async def handle_async_request(self, request):
            type(self).attempts += 1
            return await super().handle_async_request(request)

    result = await ask(make_gateway(CountedDrop()))
    assert result.failure == "connection_error"
    assert CountedDrop.attempts == 1


async def test_a_malformed_response_body_is_an_api_error(make_gateway):
    result = await ask(make_gateway(httpx2.MockTransport(lambda request: httpx2.Response(200, json={"nonsense": True}))))
    assert result.failure == "api_error"


async def test_an_answer_the_response_left_out_is_an_api_error(make_gateway):
    def drop_one(request: httpx2.Request) -> httpx2.Response:
        questions = json.loads(request.content)["questions"]
        answers = {"q_noul": {"type": "noul", "noul": 0.9}}
        assert set(questions) > set(answers)
        return httpx2.Response(200, json={"model": "m", "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}})

    gateway = make_gateway(httpx2.MockTransport(drop_one))
    result = await ask(gateway)
    assert result.failure == "api_error" and result.answers is None
    assert "q_choice" in result.failure_detail and "q_score" in result.failure_detail
    assert gateway.breaker.state == "closed"  # one failure of five
    assert only_entry(make_gateway.audit)["event_type"] == "MODEL_FAILURE"


async def test_an_unexpected_error_is_an_api_error_not_an_exception(make_gateway):
    class Broken(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise RuntimeError("transport bug")

    result = await ask(make_gateway(Broken()))
    assert result.failure == "api_error"
    assert "RuntimeError" in result.failure_detail and "transport bug" in result.failure_detail


async def test_every_failure_carries_the_state_hash_and_its_audit_seq(make_gateway):
    result = await ask(make_gateway(StatusTransport(500)))
    assert result.state_hash == sha256_hex(canonical_json(STATE))
    assert result.audit_seq == only_entry(make_gateway.audit)["seq"]
    assert result.request_ts and result.response_ts


# --- circuit breaker ----------------------------------------------------------------------------------------


async def test_breaker_opens_after_n_failures_and_then_stops_touching_the_transport(make_gateway):
    counting = CountingTransport(ConnectionDropTransport())
    gateway = make_gateway(counting, breaker_failure_threshold=3)
    assert not gateway.human_only

    for i in range(3):
        assert (await ask(gateway, file_id=f"F{i}")).failure == "connection_error"
    assert gateway.breaker.state == "open" and gateway.human_only
    sent_before = len(counting.requests)

    result = await ask(gateway, file_id="F9")
    assert not result.ok and result.failure == "circuit_open"
    assert len(counting.requests) == sent_before  # nothing went to the transport
    entry = only_entry(make_gateway.audit, "F9")
    assert entry["event_type"] == "MODEL_FAILURE" and detail(entry)["failure"] == "circuit_open"
    assert entry["state_json"] == canonical_json(STATE)  # it passed the gate before the breaker was consulted
    assert entry["latency_ms"] == 0 and entry["response_ts"] is None
    assert make_gateway.audit.verify().ok


async def test_breaker_recovers_through_a_half_open_probe(make_gateway):
    network = SwitchableTransport(sim_transport())
    counting = CountingTransport(network)
    gateway = make_gateway(counting, breaker_failure_threshold=2, breaker_cooldown_s=0.2)

    network.kill()
    for _ in range(2):
        assert (await ask(gateway)).failure == "connection_error"
    assert gateway.human_only
    assert (await ask(gateway)).failure == "circuit_open"
    assert len(counting.requests) == 2

    network.restore()
    assert (await ask(gateway)).failure == "circuit_open"  # still cooling down
    await asyncio.sleep(0.25)
    assert gateway.breaker.state == "half_open" and not gateway.human_only

    assert (await ask(gateway)).ok  # the probe
    assert gateway.breaker.state == "closed"
    assert (await ask(gateway)).ok
    assert len(counting.requests) == 4


async def test_a_failed_probe_opens_the_breaker_again(make_gateway):
    network = SwitchableTransport(sim_transport())
    counting = CountingTransport(network)
    gateway = make_gateway(counting, breaker_failure_threshold=2, breaker_cooldown_s=0.2)

    network.kill()
    for _ in range(2):
        await ask(gateway)
    await asyncio.sleep(0.25)
    assert (await ask(gateway)).failure == "connection_error"  # the probe went out, and failed
    assert len(counting.requests) == 3
    assert (await ask(gateway)).failure == "circuit_open"
    assert len(counting.requests) == 3


async def test_http_and_timeout_failures_all_count_towards_the_breaker(make_gateway):
    gateway = make_gateway(StatusTransport(500), breaker_failure_threshold=2)
    await ask(gateway)
    await ask(gateway)
    assert gateway.breaker.state == "open"


# --- PII gate and egress guard -------------------------------------------------------------------------------


def leaky_state() -> dict:
    return {**STATE, "note": f"applicant PAN {RAW_PAN}"}


def every_stored_text(audit: AuditLog) -> str:
    return " ".join(str(value) for entry in audit.iter_all() for value in entry.values())


async def test_a_gate_hit_blocks_the_call_and_stores_no_state(make_gateway):
    counting = CountingTransport(sim_transport())
    gateway = make_gateway(counting, gate=RaisingGate(FakePIIBlocked([FINDING])))
    result = await ask(gateway, state=leaky_state())

    assert not result.ok and result.failure == "pii_blocked"
    assert "gate" in result.failure_detail and "pan" in result.failure_detail
    assert RAW_PAN not in result.failure_detail
    assert counting.requests == []  # nothing left the process
    assert result.answers is None and result.response_ts is None and result.latency_ms == 0
    assert result.state_hash == sha256_hex(canonical_json(leaky_state()))

    entry = only_entry(make_gateway.audit)
    assert entry["event_type"] == "PII_BLOCK" and entry["seq"] == result.audit_seq
    assert entry["state_json"] is None and entry["questions_json"] is None and entry["answers_json"] is None
    assert entry["state_hash"] == result.state_hash
    assert (entry["file_id"], entry["stage"], entry["policy_version"]) == ("F000001", "appraisal", "policy-test")
    info = detail(entry)
    assert info["findings"] == [{"detector": "pan", "path": "state.income.note", "masked": "AB******4F"}]
    assert info["error_type"] == "FakePIIBlocked" and info["layer"] == "gate"
    assert RAW_PAN not in every_stored_text(make_gateway.audit)
    assert make_gateway.audit.verify().ok


async def test_a_gate_that_crashes_still_blocks(make_gateway):
    """Fail closed: any exception from the gate, not only PIIBlocked, means the request is blocked."""
    counting = CountingTransport(sim_transport())
    result = await ask(make_gateway(counting, gate=RaisingGate(TypeError("unsupported payload"))), state=leaky_state())

    assert result.failure == "pii_blocked"
    assert counting.requests == []
    entry = only_entry(make_gateway.audit)
    assert entry["event_type"] == "PII_BLOCK" and entry["state_json"] is None
    info = detail(entry)
    assert info["findings"] == [] and info["error_type"] == "TypeError"
    assert "unsupported payload" not in every_stored_text(make_gateway.audit)  # an error message may echo the payload
    assert make_gateway.audit.verify().ok


async def test_a_cancelled_gate_is_not_swallowed(make_gateway):
    class CancellingGate:
        def check(self, payload) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ask(make_gateway(gate=CancellingGate()))


async def test_pii_blocks_never_count_as_breaker_failures(make_gateway):
    gateway = make_gateway(sim_transport(), gate=RaisingGate(FakePIIBlocked([FINDING])), breaker_failure_threshold=2)
    for i in range(6):
        assert (await ask(gateway, file_id=f"F{i}")).failure == "pii_blocked"
    assert gateway.breaker.state == "closed" and not gateway.human_only


async def test_the_gate_runs_before_the_breaker(make_gateway):
    gate = StubGate()
    gateway = make_gateway(ConnectionDropTransport(), gate=gate, breaker_failure_threshold=1)
    await ask(gateway)
    assert gateway.human_only
    gate.exc = FakePIIBlocked([FINDING])
    assert (await ask(gateway, file_id="F2")).failure == "pii_blocked"


async def test_the_egress_guard_blocking_is_reported_as_pii_blocked(make_gateway, egress):
    class Blocked(egress.PIIEgressBlocked):
        def __init__(self, message: str = "egress blocked") -> None:
            Exception.__init__(self, message)
            self.findings = [FINDING]

    class BlockingGuard(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise Blocked()

    counting = CountingTransport(sim_transport())
    gateway = make_gateway(counting, egress_guard=lambda inner, gate: BlockingGuard(), breaker_failure_threshold=2)
    results = [await ask(gateway, file_id=f"F{i}") for i in range(4)]

    assert [r.failure for r in results] == ["pii_blocked"] * 4
    assert counting.requests == []
    assert gateway.breaker.state == "closed"
    entry = only_entry(make_gateway.audit, "F0")
    assert entry["event_type"] == "PII_BLOCK" and entry["state_json"] is None and entry["questions_json"] is None
    info = detail(entry)
    assert info["layer"] == "egress" and info["error_type"] == "Blocked"
    assert info["findings"] == [{"detector": "pan", "path": "state.income.note", "masked": "AB******4F"}]
    assert make_gateway.audit.verify().ok


async def test_an_egress_block_wrapped_by_the_sdk_is_still_found(make_gateway, egress):
    """The SDK turns httpx2 request errors into TypeSafeAPIConnectionError; the guard's error is in the chain."""

    class Blocked(egress.PIIEgressBlocked):
        def __init__(self, message: str = "egress blocked") -> None:
            Exception.__init__(self, message)
            self.findings = [FINDING]

    class WrappingGuard(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx2.ConnectError("guard refused the request") from Blocked()

    result = await ask(make_gateway(egress_guard=lambda inner, gate: WrappingGuard()))
    assert result.failure == "pii_blocked"
    entry = only_entry(make_gateway.audit)
    assert entry["event_type"] == "PII_BLOCK" and entry["state_json"] is None
    assert detail(entry)["layer"] == "egress"


async def test_an_unrelated_connect_error_is_not_mistaken_for_an_egress_block(make_gateway, egress):
    result = await ask(make_gateway(ConnectionDropTransport()))
    assert result.failure == "connection_error"


# --- kill switch --------------------------------------------------------------------------------------------


async def test_force_human_review_sends_nothing_and_stores_no_unchecked_state(make_gateway):
    gate, counting = CleanGate(), CountingTransport(sim_transport())
    gateway = make_gateway(counting, gate=gate, force_human_review=True, breaker_failure_threshold=1)
    assert gateway.human_only

    results = [await ask(gateway, file_id=f"F{i}") for i in range(3)]
    assert [r.failure for r in results] == ["forced_human"] * 3
    assert counting.requests == [] and gate.payloads == []
    assert gateway.breaker.state == "closed"  # forced_human is not a failure of Jev

    entry = only_entry(make_gateway.audit, "F0")
    assert entry["event_type"] == "MODEL_FAILURE" and detail(entry)["failure"] == "forced_human"
    assert entry["state_json"] is None  # the gate never looked at it
    assert entry["state_hash"] == results[0].state_hash
    assert make_gateway.audit.verify().ok


# --- backend selection ---------------------------------------------------------------------------------------


async def test_kill_switch_blocks_a_request_already_waiting_for_capacity(make_gateway):
    counting = CountingTransport(sim_transport())
    gateway = make_gateway(counting, max_concurrency=1)
    await gateway._slots.acquire()
    pending = asyncio.create_task(ask(gateway))
    await asyncio.sleep(0)
    gateway._runtime.force_human_review = True
    gateway._slots.release()
    result = await pending
    assert result.failure == "forced_human"
    assert counting.requests == []
    assert make_gateway.audit.verify().ok


async def test_kill_switch_blocks_a_request_already_waiting_for_rate_limit(make_gateway):
    counting = CountingTransport(sim_transport())
    gateway = make_gateway(counting)
    entered, released = asyncio.Event(), asyncio.Event()

    async def wait_for_rate_limit():
        entered.set()
        await released.wait()

    gateway._limiter.acquire = wait_for_rate_limit
    pending = asyncio.create_task(ask(gateway))
    await entered.wait()
    gateway._runtime.force_human_review = True
    released.set()
    result = await pending
    assert result.failure == "forced_human"
    assert counting.requests == []
    assert make_gateway.audit.verify().ok


def test_real_backend_needs_an_api_key(tmp_db, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="TYPESAFE_API_KEY"):
        JevGateway(RuntimeConfig(backend="real"), AuditLog(tmp_db), CleanGate(), egress_guard=passthrough)
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    with pytest.raises(ConfigError, match="TYPESAFE_API_KEY"):
        JevGateway(RuntimeConfig(backend="real"), AuditLog(tmp_db), CleanGate(), egress_guard=passthrough)


async def test_real_backend_sends_the_key_and_model_through_the_given_transport(tmp_db, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-123")
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q_noul": {"type": "noul", "noul": 0.8}},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    gateway = JevGateway(
        RuntimeConfig(backend="real", model="jev-latest"),
        AuditLog(tmp_db),
        CleanGate(),
        httpx2.MockTransport(handler),
        egress_guard=passthrough,
    )
    result = await ask(gateway, questions={"q_noul": QUESTIONS["q_noul"]})
    await gateway.aclose()

    assert result.ok and result.model_version == "jev-1.13.0" and result.input_tokens == 10
    (request,) = seen
    assert request.headers["authorization"] == "Bearer test-key-123"
    assert request.url.path == "/v1/systemone"
    assert json.loads(request.content)["model"] == "jev-latest"


# --- concurrency ------------------------------------------------------------------------------------------


class Gauge(httpx2.AsyncBaseTransport):
    """Records how many requests are in flight at once."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner, self.active, self.peak = inner, 0, 0

    async def handle_async_request(self, request):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            return await self.inner.handle_async_request(request)
        finally:
            self.active -= 1


async def test_fifty_concurrent_asks_all_succeed_and_the_chain_verifies(make_gateway):
    gauge = Gauge(sim_transport(latency_ms=(5, 25)))
    gateway = make_gateway(gauge, max_concurrency=8)

    results = await asyncio.gather(
        *(ask(gateway, file_id=f"F{i:06d}", state={**STATE, "case": i}) for i in range(50))
    )

    assert all(r.ok for r in results)
    assert len({r.audit_seq for r in results}) == 50
    assert 1 < gauge.peak <= 8  # really concurrent, and capped by max_concurrency
    entries = list(make_gateway.audit.iter_all())
    assert len(entries) == 50 and {e["event_type"] for e in entries} == {"MODEL_CALL"}
    for result in results:
        assert entries[result.audit_seq - 1]["state_hash"] == result.state_hash
    verified = make_gateway.audit.verify()
    assert verified.ok and verified.entries == 50


async def test_concurrent_mixed_outcomes_each_get_exactly_one_entry(make_gateway):
    class Flaky(httpx2.AsyncBaseTransport):
        def __init__(self) -> None:
            self.inner = sim_transport(latency_ms=(1, 10))

        async def handle_async_request(self, request):
            state = json.loads(request.content)["state"]
            if state["case"] % 3 == 0:
                raise httpx2.ConnectError("flaky network")
            return await self.inner.handle_async_request(request)

    gateway = make_gateway(Flaky(), breaker_failure_threshold=1000)
    results = await asyncio.gather(*(ask(gateway, file_id=f"F{i}", state={**STATE, "case": i}) for i in range(60)))

    assert sum(r.ok for r in results) == 40
    assert {r.failure for r in results if not r.ok} == {"connection_error"}
    assert len(list(make_gateway.audit.iter_all())) == 60
    assert make_gateway.audit.verify().ok


async def test_a_timed_out_call_does_not_hold_a_concurrency_slot(make_gateway):
    gateway = make_gateway(SlowTransport(sim_transport(), delay_s=5.0), max_concurrency=1, timeout_budget_s=0.1, breaker_failure_threshold=100)
    start = time.monotonic()
    results = await asyncio.gather(*(ask(gateway, file_id=f"F{i}") for i in range(3)))
    assert [r.failure for r in results] == ["timeout"] * 3
    assert time.monotonic() - start < 1.0  # they ran one after another, each cut off at the budget


# --- the real PII gate and egress guard, once W1-pii has landed ----------------------------------------------


async def test_real_gate_and_real_egress_guard_with_the_simulator(make_gateway):
    pytest.importorskip("jevloan.pii.egress")
    gate_module = pytest.importorskip("jevloan.pii.gate")
    from jevloan.jev.commands import HELLO_QUESTIONS, HELLO_STATE

    counting = CountingTransport(sim_transport())
    gateway = make_gateway(counting, gate=gate_module.PIIGate(), egress_guard=None)  # None: the real guard, built by the gateway

    clean = await ask(gateway, file_id="CLEAN", state=HELLO_STATE, questions=HELLO_QUESTIONS)
    assert clean.ok
    assert len(counting.requests) == 1

    dirty = await ask(gateway, file_id="DIRTY", state={**HELLO_STATE, "note": f"PAN {RAW_PAN}"}, questions=HELLO_QUESTIONS)
    assert dirty.failure == "pii_blocked"
    assert len(counting.requests) == 1  # the blocked request never reached the transport
    entry = only_entry(make_gateway.audit, "DIRTY")
    assert entry["event_type"] == "PII_BLOCK" and entry["state_json"] is None
    assert detail(entry)["findings"] and detail(entry)["layer"] == "gate"
    assert RAW_PAN not in every_stored_text(make_gateway.audit)
    assert make_gateway.audit.verify().ok
