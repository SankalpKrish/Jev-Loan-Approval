"""Regression coverage for keeping state preparation off the asyncio event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.pipeline.runner import build_pipeline


@pytest.mark.slow
async def test_slow_state_preparation_does_not_block_concurrent_gateway_calls(tmp_path, monkeypatch):
    files = generate_book(8, 89)
    runtime = RuntimeConfig(
        backend="sim",
        db_path=str(tmp_path / "audit.db"),
        timeout_budget_s=0.1,
        max_concurrency=8,
        rate_limit_rpm=1000,
        breaker_failure_threshold=10,
    )
    pipeline = build_pipeline(runtime)

    import jevloan.pipeline.runner as runner

    original_build_state = runner.build_state
    preparation_lock = threading.Lock()
    active_preparations = 0

    def delayed_build_state(file, stage):
        nonlocal active_preparations
        # Simulate CPU or serialization work accidentally left on the event-loop thread.
        with preparation_lock:
            active_preparations += 1
        try:
            time.sleep(0.03)
            return original_build_state(file, stage)
        finally:
            with preparation_lock:
                active_preparations -= 1

    monkeypatch.setattr(runner, "build_state", delayed_build_state)

    async def fast_stub_ask(**_kwargs):
        # Keep this test isolated to state prep rather than exercising network parsing and PII scans.
        await asyncio.sleep(0)
        return SimpleNamespace(
            ok=True, answers={}, model_version="stub", input_tokens=1, latency_ms=1.0,
            failure=None, failure_detail=None, audit_seq=1,
        )

    monkeypatch.setattr(pipeline.gateway, "ask", fast_stub_ask)
    stop = asyncio.Event()
    heartbeats_during_preparation = 0

    async def heartbeat():
        nonlocal heartbeats_during_preparation
        while not stop.is_set():
            await asyncio.sleep(0.005)
            with preparation_lock:
                if active_preparations > 0:
                    heartbeats_during_preparation += 1

    ticker = asyncio.create_task(heartbeat())
    try:
        decisions = await asyncio.gather(*(pipeline.run_stage(file, "appraisal") for file in files))
        stop.set()
        await ticker

        assert len(decisions) == len(files)
        # If preparation runs on the event-loop thread, it cannot tick while sleeping here.
        assert heartbeats_during_preparation > 0
        assert all(str(decision.outcome) == "HUMAN_REVIEW" for decision in decisions)
        assert pipeline.audit.verify().ok
    finally:
        stop.set()
        if not ticker.done():
            await ticker
        await pipeline.gateway.aclose()
