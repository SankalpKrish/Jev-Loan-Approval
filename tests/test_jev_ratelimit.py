import asyncio
import time

import pytest

from jevloan.jev.ratelimit import AsyncRateLimiter


class FakeTime:
    """A clock that only moves when a sleeper wakes or the test advances it. Sleepers overlap, as real ones do."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        wake_at = self.now + seconds
        await asyncio.sleep(0)  # let the other callers run before time moves
        self.now = max(self.now, wake_at)


def limiter(fake: FakeTime, rpm: int, burst: int | None = None) -> AsyncRateLimiter:
    return AsyncRateLimiter(rpm, burst, clock=fake.clock, sleep=fake.sleep)


async def test_burst_passes_without_waiting():
    fake = FakeTime()
    bucket = limiter(fake, rpm=60, burst=3)
    for _ in range(3):
        await bucket.acquire()
    assert fake.sleeps == []


async def test_after_the_burst_requests_wait_for_the_refill():
    fake = FakeTime()
    bucket = limiter(fake, rpm=60, burst=2)  # 1 token per second
    for _ in range(3):
        await bucket.acquire()
    assert fake.sleeps == [pytest.approx(1.0)]
    await bucket.acquire()  # the clock has moved to t=1, and that token was spent: another second
    assert fake.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]


async def test_tokens_refill_with_time_up_to_the_burst():
    fake = FakeTime()
    bucket = limiter(fake, rpm=120, burst=2)  # 2 tokens per second
    await bucket.acquire()
    await bucket.acquire()
    fake.now += 100  # a long idle period refills to the burst, not beyond it
    for _ in range(2):
        await bucket.acquire()
    assert fake.sleeps == []
    await bucket.acquire()
    assert fake.sleeps == [pytest.approx(0.5)]


async def test_concurrent_callers_are_spaced_out_in_arrival_order():
    fake = FakeTime()
    bucket = limiter(fake, rpm=60, burst=1)
    await asyncio.gather(*(bucket.acquire() for _ in range(5)))
    assert sorted(fake.sleeps) == [pytest.approx(s) for s in (1.0, 2.0, 3.0, 4.0)]


async def test_sustained_rate_is_n_per_minute():
    fake = FakeTime()
    bucket = limiter(fake, rpm=600, burst=1)  # 10 per second
    for _ in range(101):
        await bucket.acquire()
    assert fake.now == pytest.approx(10.0)  # 100 requests after the first took 10 s


def test_default_burst_is_about_one_second_of_traffic_and_at_least_one():
    fake = FakeTime()

    async def drain(rpm: int) -> int:
        bucket = limiter(fake, rpm)
        free = 0
        while True:
            before = len(fake.sleeps)
            await bucket.acquire()
            if len(fake.sleeps) > before:
                return free
            free += 1

    assert asyncio.run(drain(1200)) == 20
    assert asyncio.run(drain(10)) == 1


async def test_real_clock_smoke():
    bucket = AsyncRateLimiter(rpm=1200, burst=1)  # 20 per second
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    assert 0.1 < time.monotonic() - start < 0.5


def test_rejects_nonsense():
    with pytest.raises(ValueError):
        AsyncRateLimiter(0)
    with pytest.raises(ValueError):
        AsyncRateLimiter(60, burst=0)
