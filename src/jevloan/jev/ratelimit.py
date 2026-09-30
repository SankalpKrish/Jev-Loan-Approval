"""Async token bucket for the Jev request rate limit."""

import asyncio
import time
from collections.abc import Awaitable, Callable


class AsyncRateLimiter:
    """At most `rpm` requests per minute, with a burst of `burst` (default: about one second's worth).

    `acquire()` takes a token straight away and, if the bucket was empty, sleeps for the time it takes
    the deficit to refill. Callers are therefore served in arrival order with no lock, and the clock
    and sleep can be swapped for fakes in tests.
    """

    def __init__(
        self,
        rpm: int,
        burst: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        if rpm < 1:
            raise ValueError("rpm must be at least 1")
        self._rate = rpm / 60.0  # tokens per second
        self._capacity = float(max(1, rpm // 60) if burst is None else burst)
        if self._capacity < 1:
            raise ValueError("burst must be at least 1")
        self._clock = clock
        self._sleep = sleep
        self._tokens = self._capacity
        self._updated = clock()

    async def acquire(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        self._tokens -= 1.0  # may go negative: the debt is what this caller waits out
        if self._tokens < 0:
            await self._sleep(-self._tokens / self._rate)
