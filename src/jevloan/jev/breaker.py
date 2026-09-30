"""Circuit breaker: after N consecutive failures stop calling Jev for a cooldown, then let one probe through."""

import time
from collections.abc import Callable
from typing import Literal

BreakerState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """closed -> open after `failure_threshold` consecutive failures -> half_open once `cooldown_s` has
    passed (one probe allowed) -> closed if the probe succeeds, open again if it fails.

    Methods never await, so calls from one asyncio loop cannot interleave. Not thread-safe.
    A probe that never reports back (its caller was cancelled) is abandoned after one more cooldown, so
    the breaker cannot stay half-open forever.
    """

    def __init__(
        self, failure_threshold: int = 5, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._state: BreakerState = "closed"
        self._failures = 0  # consecutive failures while closed
        self._opened_at = 0.0
        self._probe_at: float | None = None  # when the current half-open probe was let through

    @property
    def state(self) -> BreakerState:
        if self._state == "open" and self._clock() - self._opened_at >= self._cooldown_s:
            self._state, self._probe_at = "half_open", None
        return self._state

    def allow(self) -> bool:
        """May a call go out now? In half_open this hands out the single probe."""
        state = self.state
        if state == "closed":
            return True
        if state == "open":
            return False
        now = self._clock()
        if self._probe_at is not None and now - self._probe_at < self._cooldown_s:
            return False  # a probe is already in flight
        self._probe_at = now
        return True

    def record_success(self) -> None:
        # A late success from a call that started before the breaker opened says nothing about recovery.
        if self.state != "open":
            self._state, self._failures, self._probe_at = "closed", 0, None

    def record_failure(self) -> None:
        state = self.state
        if state == "open":
            return
        self._failures += 1
        if state == "half_open" or self._failures >= self._threshold:
            self._state, self._opened_at, self._failures, self._probe_at = "open", self._clock(), 0, None
