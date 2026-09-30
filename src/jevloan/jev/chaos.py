"""Fault-injecting httpx2 transports for chaos tests. They plug in wherever a gateway takes a transport."""

import asyncio
import json
from typing import Any

import httpx2


class _Wrapping(httpx2.AsyncBaseTransport):
    """A transport that delegates to `inner`; subclasses change what happens on the way."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class SlowTransport(_Wrapping):
    """Waits `delay_s` before forwarding, to exercise the timeout budget."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, delay_s: float) -> None:
        super().__init__(inner)
        self.delay_s = delay_s

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(self.delay_s)
        return await super().handle_async_request(request)


class StatusTransport(httpx2.AsyncBaseTransport):
    """Answers every request with `status` and a JSON error body, without forwarding anything."""

    def __init__(self, status: int) -> None:
        self.status = status

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(self.status, json={"error": f"chaos: injected HTTP {self.status}"})


class ConnectionDropTransport(httpx2.AsyncBaseTransport):
    """Fails every request the way a dropped connection does."""

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("chaos: connection dropped", request=request)


class SwitchableTransport(_Wrapping):
    """Forwards to `inner` until `kill()` is called (a network cut), and again after `restore()`."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        super().__init__(inner)
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def restore(self) -> None:
        self.killed = False

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.killed:
            raise httpx2.ConnectError("chaos: network killed", request=request)
        return await super().handle_async_request(request)


class CountingTransport(_Wrapping):
    """Forwards to `inner` and keeps the decoded JSON body of every request it sees in `requests`."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        super().__init__(inner)
        self.requests: list[Any] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(await request.aread()))
        return await super().handle_async_request(request)
