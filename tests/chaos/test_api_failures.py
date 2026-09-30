from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from jevloan.api.app import create_app
from jevloan.audit.log import AuditLog
from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.data.schema import write_book
from jevloan.jev.chaos import (
    ConnectionDropTransport,
    CountingTransport,
    SlowTransport,
    StatusTransport,
    SwitchableTransport,
)
from jevloan.jev.sim_rules import REGISTRY
from jevloan.jev.simulator import SimulatedJevTransport
from jevloan.pipeline.runner import build_pipeline
from jevloan.policy.config import load_policy


def _setup(tmp_path: Path, *, timeout=0.15, failure_threshold=5, force_human=False):
    files = generate_book(60, 71)
    book = tmp_path / "book.jsonl"
    write_book(files, book)
    runtime = RuntimeConfig(
        backend="sim", db_path=str(tmp_path / "audit.db"), book_path=str(book),
        timeout_budget_s=timeout, breaker_failure_threshold=failure_threshold,
        breaker_cooldown_s=60, force_human_review=force_human,
    )
    return files, runtime


@pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
def test_api_http_errors_fail_closed_to_named_human_queue(tmp_path, status):
    files, runtime = _setup(tmp_path)
    transport = CountingTransport(StatusTransport(status))
    app = create_app(runtime, transport=transport)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{files[0].file_id}/score")
        assert response.status_code == 200
        assert response.json()["outcome"] == "HUMAN_REVIEW"
        assert response.json()["advisory_only"] is True
        queue_items = client.get("/api/v1/queue").json()["items"]
        assert queue_items and queue_items[0]["file_id"] == files[0].file_id
        assert transport.requests
        assert client.get("/api/v1/health").json()["audit_ok"] is True


def test_timeout_returns_human_review_within_a_bounded_time(tmp_path):
    files, runtime = _setup(tmp_path, timeout=0.05)
    inner = SimulatedJevTransport(rules=REGISTRY, latency_ms=(250, 250))
    app = create_app(runtime, transport=SlowTransport(inner, delay_s=0.25))
    started = time.monotonic()
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{files[0].file_id}/score")
        assert response.status_code == 200
        assert response.json()["outcome"] == "HUMAN_REVIEW"
        assert time.monotonic() - started < 0.6
        assert client.get("/api/v1/queue").json()["items"]


def test_connection_drop_fails_closed_and_logs_chain(tmp_path):
    files, runtime = _setup(tmp_path)
    app = create_app(runtime, transport=ConnectionDropTransport())
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{files[0].file_id}/score")
        assert response.status_code == 200
        assert response.json()["outcome"] == "HUMAN_REVIEW"
        trail = client.get(f"/api/v1/files/{files[0].file_id}/trail").json()["entries"]
        assert {row["event_type"] for row in trail} >= {"MODEL_FAILURE", "POLICY_OUTCOME", "QUEUED"}
        assert client.get("/api/v1/health").json()["audit_ok"] is True


def test_repeated_outage_opens_breaker_then_stays_human_only(tmp_path):
    files, runtime = _setup(tmp_path, failure_threshold=2)
    transport = CountingTransport(StatusTransport(500))
    app = create_app(runtime, transport=transport)
    with TestClient(app) as client:
        for file in files[:2]:
            response = client.post(f"/api/v1/files/{file.file_id}/score")
            assert response.status_code == 200 and response.json()["outcome"] == "HUMAN_REVIEW"
        assert client.get("/api/v1/health").json()["breaker_state"] == "open"
        sent = len(transport.requests)
        response = client.post(f"/api/v1/files/{files[2].file_id}/score")
        assert response.status_code == 200 and response.json()["outcome"] == "HUMAN_REVIEW"
        assert len(transport.requests) == sent
        assert client.get("/api/v1/health").json()["human_only"] is True


def test_api_review_stays_usable_after_network_kill(tmp_path):
    files, runtime = _setup(tmp_path)
    network = SwitchableTransport(SimulatedJevTransport(rules=REGISTRY, latency_ms=(5, 10)))
    network.kill()
    app = create_app(runtime, transport=network)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/files/{files[0].file_id}/score")
        assert response.status_code == 200
        item = client.get("/api/v1/queue").json()["items"][0]
        assert client.get(f"/review/{item['id']}").status_code == 200
        code = next(iter(load_policy().reason_codes_for(item["queue"])))
        decided = client.post(f"/review/{item['id']}/decision", data={
            "decision": "accept", "reason_code": code, "reviewer_id": "Human reviewer",
        }, follow_redirects=False)
        assert decided.status_code == 303
        assert client.get(f"/api/v1/queue/{item['id']}").json()["reviewer_id"] == "Human reviewer"
        assert client.get("/api/v1/health").json()["audit_ok"] is True


async def test_network_killed_mid_batch_leaves_every_file_with_complete_audit(tmp_path):
    files, runtime = _setup(tmp_path)
    network = SwitchableTransport(SimulatedJevTransport(rules=REGISTRY, latency_ms=(20, 80)))
    pipeline = build_pipeline(runtime, transport=network)
    tasks = [asyncio.create_task(pipeline.run_stage(file, "appraisal")) for file in files]
    await asyncio.sleep(0.015)
    network.kill()
    results = await asyncio.gather(*tasks)
    try:
        assert len(results) == len(files)
        for file in files:
            kinds = {row["event_type"] for row in pipeline.audit.trail(file.file_id)}
            assert kinds >= {"MODEL_FAILURE", "POLICY_OUTCOME", "QUEUED"} or kinds >= {
                "MODEL_CALL", "POLICY_OUTCOME", "PRICING"
            }
        assert pipeline.audit.verify().ok
    finally:
        await pipeline.gateway.aclose()


async def test_human_only_kill_switch_during_concurrent_batch_preserves_all_trails(tmp_path):
    files, runtime = _setup(tmp_path)
    runtime.max_concurrency = 12
    inner = SimulatedJevTransport(rules=REGISTRY, latency_ms=(40, 90))
    pipeline = build_pipeline(runtime, transport=inner)
    tasks = [asyncio.create_task(pipeline.run_stage(file, "appraisal")) for file in files]
    await asyncio.sleep(0.02)
    runtime.force_human_review = True
    try:
        decisions = await asyncio.gather(*tasks)
        assert len(decisions) == len(files)
        forced = 0
        for file in files:
            events = pipeline.audit.trail(file.file_id)
            kinds = {row["event_type"] for row in events}
            assert "POLICY_OUTCOME" in kinds
            gateway_events = [row for row in events if row["event_type"] in {"MODEL_CALL", "MODEL_FAILURE", "PII_BLOCK"}]
            assert len(gateway_events) == 1
            detail = gateway_events[0].get("detail_json") or ""
            forced += int("forced_human" in detail)
        assert forced > 0
        assert pipeline.audit.verify().ok
    finally:
        await pipeline.gateway.aclose()


class _SubprocessStubTransport(httpx2.AsyncBaseTransport):
    """Send SDK traffic to an isolated local stub process, never to the vendor endpoint."""

    def __init__(self, port: int):
        self.port = port

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            writer.write(
                f"POST {request.url.path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            headers = await reader.readuntil(b"\r\n\r\n")
            status = int(headers.split(b" ", 2)[1])
            payload = await reader.read()
            writer.close()
            await writer.wait_closed()
            return httpx2.Response(status, content=payload)
        except Exception as exc:
            raise httpx2.ConnectError("local stub process disappeared", request=request) from exc

    async def aclose(self) -> None:
        return None


async def test_sigkill_live_local_stub_server_mid_request_fails_human_only(tmp_path):
    files, runtime = _setup(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server_code = (
        "import http.server,time\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        " def do_POST(self):\n"
        "  self.rfile.read(int(self.headers.get('Content-Length','0')))\n"
        "  print('REQUEST_RECEIVED',flush=True)\n"
        "  time.sleep(60)\n"
        " def log_message(self,*args): pass\n"
        f"s=http.server.HTTPServer(('127.0.0.1',{port}),H)\n"
        "print('LISTENING',flush=True)\n"
        "s.serve_forever()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", server_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    pipeline = None
    try:
        ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=4)
        assert ready.strip() == "LISTENING"
        pipeline = build_pipeline(runtime, transport=_SubprocessStubTransport(port))
        running = asyncio.create_task(pipeline.run_stage(files[0], "appraisal"))
        seen = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=4)
        assert seen.strip() == "REQUEST_RECEIVED"
        process.kill()  # SIGKILL on POSIX; terminate the in-flight local HTTP request.
        await asyncio.to_thread(process.wait, 4)
        outcome = await asyncio.wait_for(running, timeout=3)
        assert str(outcome.outcome) == "HUMAN_REVIEW"
        kinds = {row["event_type"] for row in pipeline.audit.trail(files[0].file_id)}
        assert kinds >= {"MODEL_FAILURE", "POLICY_OUTCOME", "QUEUED"}
        assert pipeline.audit.verify().ok
    finally:
        if pipeline is not None:
            await pipeline.gateway.aclose()
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait)
