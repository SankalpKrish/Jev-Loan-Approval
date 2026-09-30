"""PIIEgressGuardTransport: blocks before the inner transport, passes clean requests, fails closed."""

import gzip
import json

import httpx2
import pytest

from jevloan.pii.egress import PIIEgressBlocked, PIIEgressGuardTransport
from jevloan.pii.gate import PIIFinding, PIIGate


class CountingInner(httpx2.AsyncBaseTransport):
    """Defined here on purpose: it records every request that actually reaches the 'network'."""

    def __init__(self) -> None:
        self.calls = 0
        self.bodies: list[bytes] = []
        self.closed = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        self.bodies.append(await request.aread())
        return httpx2.Response(200, json={"model": "sim", "answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}})

    async def aclose(self) -> None:
        self.closed += 1


def build(inner=None):
    inner = inner or CountingInner()
    return inner, PIIEgressGuardTransport(inner, PIIGate())


def request(body=None, *, url="https://api.typesafe.ai/v1/systemone", content=None, headers=None):
    if content is not None:
        return httpx2.Request("POST", url, content=content, headers=headers)
    return httpx2.Request("POST", url, json=body, headers=headers)


CLEAN_BODY = {
    "state": {"schema": "jevloan.state.v1", "documents": [{"text": "Salary slip for Mar 2026. Employee: [APPLICANT]. Net pay ₹[50-75k]. PAN: [PAN_1]."}]},
    "model": "jev-latest",
    "questions": {"A_income_proof_current": {"type": "noul", "instructions": {"question": "Is the income proof current?"}, "criteria": {"true": "yes", "false": "no"}}},
}


@pytest.mark.parametrize(
    "body",
    [
        {"state": "Applicant PAN ABCDE1234F", "model": "jev-latest", "questions": {}},
        {"state": {"documents": [{"text": "call 9876543210"}]}, "model": "jev-latest", "questions": {}},
        {"state": {"a": {"b": ["x", "Aadhaar 2345 6789 0123"]}}, "questions": {}},
        {"state": {"n": "Mr Rajesh Kumar"}, "questions": {}},
        {"state": {"ABCDE1234F": 1}, "questions": {}},  # PII in a key
        {"state": "ok", "questions": {"Q": {"type": "noul", "instructions": "Is PAN ABCDE1234F valid?"}}},
        {"state": {"email": "priya.sharma@gmail.com"}, "questions": {}},
        {"state": {"v": "９８７６５४३２१０"}, "questions": {}},  # fullwidth
    ],
)
async def test_blocks_before_the_inner_transport_is_called(body):
    inner, guard = build()
    with pytest.raises(PIIEgressBlocked) as info:
        await guard.handle_async_request(request(body))
    assert inner.calls == 0
    assert info.value.findings and all(isinstance(f, PIIFinding) for f in info.value.findings)


async def test_blocks_non_json_bodies_as_raw_text():
    inner, guard = build()
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(content=b"state=PAN ABCDE1234F&model=jev-latest"))
    assert inner.calls == 0
    inner2, guard2 = build()
    with pytest.raises(PIIEgressBlocked):
        await guard2.handle_async_request(request(content="call ३४".encode() + b" 9876543210"))
    assert inner2.calls == 0


async def test_blocks_pii_in_the_url_query_string_and_path():
    inner, guard = build()
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(CLEAN_BODY, url="https://api.typesafe.ai/v1/systemone?pan=ABCDE1234F"))
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(CLEAN_BODY, url="https://api.typesafe.ai/v1/systemone?ref=%39%38%37%36%35%34%33%32%31%30"))
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(CLEAN_BODY, url="https://api.typesafe.ai/v1/systemone/9876543210"))
    assert inner.calls == 0


async def test_blocks_a_gzip_body_after_decompressing_it():
    inner, guard = build()
    raw = json.dumps({"state": "PAN ABCDE1234F", "questions": {}}).encode()
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(content=gzip.compress(raw), headers={"content-encoding": "gzip"}))
    assert inner.calls == 0


async def test_fails_closed_on_a_body_it_cannot_inspect():
    inner, guard = build()
    with pytest.raises(PIIEgressBlocked) as info:
        await guard.handle_async_request(request(content=b"\x00\x01\x02", headers={"content-encoding": "br"}))
    assert inner.calls == 0
    assert any(f.detector == "unscannable_body" for f in info.value.findings)


async def test_fails_closed_when_the_gate_itself_crashes():
    class Broken:
        def scan(self, payload):
            raise RuntimeError("boom")

    inner = CountingInner()
    guard = PIIEgressGuardTransport(inner, Broken())  # type: ignore[arg-type]
    with pytest.raises(PIIEgressBlocked):
        await guard.handle_async_request(request(CLEAN_BODY))
    assert inner.calls == 0


async def test_clean_requests_pass_through_untouched():
    inner, guard = build()
    response = await guard.handle_async_request(request(CLEAN_BODY))
    assert response.status_code == 200
    assert inner.calls == 1
    assert json.loads(inner.bodies[0]) == CLEAN_BODY  # the body reaching the network is the body that was scanned


async def test_clean_request_with_a_query_string_passes():
    inner, guard = build()
    await guard.handle_async_request(request(CLEAN_BODY, url="https://api.typesafe.ai/v1/systemone?trace=abc&v=2"))
    assert inner.calls == 1


async def test_empty_body_passes():
    inner, guard = build()
    await guard.handle_async_request(httpx2.Request("GET", "https://api.typesafe.ai/v1/models"))
    assert inner.calls == 1


async def test_aclose_delegates_to_inner():
    inner, guard = build()
    await guard.aclose()
    assert inner.closed == 1


def test_piiegressblocked_is_a_plain_exception_rebuildable_from_one_string():
    err = PIIEgressBlocked("blocked at the boundary")
    assert isinstance(err, Exception)
    assert not isinstance(err, httpx2.HTTPError) and not isinstance(err, httpx2.TransportError)
    assert str(err) == "blocked at the boundary"
    assert err.findings == []
    copy = type(err)(str(err))  # what the SDK does when it re-creates a wrapped exception
    assert str(copy) == str(err)
    assert PIIEgressBlocked().findings == [] and "blocked" in str(PIIEgressBlocked()).lower()
    with_findings = PIIEgressBlocked("x", findings=[PIIFinding("pan", "state.a", "AB******4F")])
    assert with_findings.findings[0].detector == "pan"


async def test_block_message_holds_masked_values_only():
    inner, guard = build()
    with pytest.raises(PIIEgressBlocked) as info:
        await guard.handle_async_request(request({"state": "PAN ABCDE1234F", "questions": {}}))
    err = info.value
    assert "ABCDE1234F" not in str(err)
    assert all("ABCDE1234F" not in f.masked and "ABCDE1234F" not in f.path for f in err.findings)


async def test_works_underneath_an_httpx2_client_and_propagates_the_block():
    inner, guard = build()
    async with httpx2.AsyncClient(transport=guard, base_url="https://api.typesafe.ai") as client:
        ok = await client.post("/v1/systemone", json=CLEAN_BODY)
        assert ok.status_code == 200
        with pytest.raises(PIIEgressBlocked):
            await client.post("/v1/systemone", json={"state": "call 98765 43210", "questions": {}})
    assert inner.calls == 1  # only the clean request got through
    assert inner.closed == 1  # closing the client closes the wrapped transport
