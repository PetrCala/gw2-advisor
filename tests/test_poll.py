import pytest

from collector import poll


class _Clock:
    """Monotonic clock that only moves when something says it did."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def spend(self, seconds):
        self.now += seconds


def _run(collect, clock=None, interval=600, deadline=3000):
    c = clock or _Clock()
    return poll.run(collect=collect, sleep=c.sleep, clock=c, interval=interval, deadline=deadline)


def test_polls_until_the_deadline():
    calls = []
    polls, failures = _run(lambda: calls.append(1), interval=600, deadline=3000)
    # 5 polls at 0, 600, 1200, 1800, 2400; a sixth would land at 3000, past the deadline
    assert (polls, failures) == (5, 0)
    assert len(calls) == 5


def test_cadence_absorbs_the_time_collect_takes():
    clock = _Clock()
    starts = []

    def slow_collect():
        starts.append(clock.now)
        clock.spend(90)  # a real collect run takes 40-120s

    _run(slow_collect, clock=clock, interval=600, deadline=1800)
    # polls stay on the 600s grid rather than drifting out by 90s each time
    assert starts == [0, 600, 1200]


def test_collect_slower_than_the_interval_does_not_sleep_negative():
    clock = _Clock()

    def very_slow():
        clock.spend(700)

    polls, _ = _run(very_slow, clock=clock, interval=600, deadline=2000)
    assert polls == 3  # started at 0, 700 and 1400; a fourth would start past 2000


def test_never_sleeps_negative_on_a_clock_that_keeps_moving():
    """A real monotonic clock advances between reads; the fake one above does not.

    Sampling the clock twice to compute the wait made the second read later
    than the first and asked time.sleep for a negative duration, which it
    rejects. Only a clock that ticks on every read catches it.
    """

    class _Drifting:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 0.5  # every read costs time, as a real one does
            return self.now

        def sleep(self, seconds):
            assert seconds >= 0, f"asked to sleep {seconds}"
            self.now += seconds

    c = _Drifting()
    poll.run(collect=lambda: None, sleep=c.sleep, clock=c, interval=10, deadline=60)


def test_one_failure_does_not_end_the_run(capsys):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("s3 hiccup")

    polls, failures = _run(flaky, interval=600, deadline=3000)
    assert (polls, failures) == (5, 1)
    assert "s3 hiccup" in capsys.readouterr().out


def test_consecutive_failures_fail_the_job():
    def broken():
        raise RuntimeError("bucket gone")

    with pytest.raises(RuntimeError, match="bucket gone"):
        _run(broken, interval=600, deadline=3000)


def test_a_success_resets_the_failure_streak():
    calls = []

    def alternating():
        calls.append(1)
        if len(calls) % 2 == 0:  # never two in a row
            raise RuntimeError("flaky")

    polls, failures = _run(alternating, interval=600, deadline=3000)
    assert (polls, failures) == (5, 2)


def test_deadline_shorter_than_the_interval_still_polls_once():
    calls = []
    polls, _ = _run(lambda: calls.append(1), interval=600, deadline=60)
    assert polls == 1


def test_defaults_fit_six_polls_inside_the_oidc_credential_hour():
    """The first live job polled 5 times, not 6.

    A 50-minute budget refuses the poll starting at exactly 50 minutes,
    because a poll landing on the deadline does not run, so a sixth was
    dropped every job. STS caps the session at an hour while the role has no
    MaxSessionDuration, so the budget still has to leave real margin.
    """
    clock = _Clock()
    starts = []

    def collect():
        starts.append(clock.now)
        clock.spend(45)  # what a real collect run costs

    polls, _ = poll.run(
        collect=collect, sleep=clock.sleep, clock=clock,
        interval=poll.INTERVAL, deadline=poll.DEADLINE,
    )
    assert polls == 6
    assert starts == [0, 600, 1200, 1800, 2400, 3000]
    assert 3600 - clock.now >= 8 * 60  # job done with margin before the session expires
