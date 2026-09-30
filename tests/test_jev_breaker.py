import pytest

from jevloan.jev.breaker import CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, cooldown_s=30.0, clock=clock)


def trip(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        assert breaker.allow()
        breaker.record_failure()


def test_starts_closed_and_allows(breaker):
    assert breaker.state == "closed"
    assert breaker.allow()


def test_opens_after_n_consecutive_failures(breaker):
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    assert not breaker.allow()


def test_success_resets_the_consecutive_count(breaker):
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"  # 2 + 2 failures, but never 3 in a row


def test_stays_open_until_the_cooldown_has_passed(breaker, clock):
    trip(breaker)
    clock.advance(29.9)
    assert breaker.state == "open"
    assert not breaker.allow()
    clock.advance(0.1)
    assert breaker.state == "half_open"


def test_half_open_lets_exactly_one_probe_through(breaker, clock):
    trip(breaker)
    clock.advance(30)
    assert breaker.allow()
    assert not breaker.allow()  # the probe is still in flight
    assert breaker.state == "half_open"


def test_probe_success_closes(breaker, clock):
    trip(breaker)
    clock.advance(30)
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow()
    assert breaker.allow()


def test_probe_failure_reopens_and_restarts_the_cooldown(breaker, clock):
    trip(breaker)
    clock.advance(30)
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.advance(29)
    assert not breaker.allow()
    clock.advance(1)
    assert breaker.allow()  # half-open again after a full new cooldown


def test_a_probe_that_never_reports_is_abandoned_after_a_cooldown(breaker, clock):
    trip(breaker)
    clock.advance(30)
    assert breaker.allow()  # this caller vanishes without reporting
    clock.advance(29)
    assert not breaker.allow()
    clock.advance(1)
    assert breaker.allow()


def test_a_late_success_does_not_close_an_open_breaker(breaker):
    trip(breaker)
    breaker.record_success()  # from a call that started before the breaker opened
    assert breaker.state == "open"


def test_failures_while_open_do_not_extend_the_cooldown(breaker, clock):
    trip(breaker)
    clock.advance(20)
    breaker.record_failure()
    clock.advance(10)
    assert breaker.state == "half_open"


def test_needs_a_positive_threshold():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
