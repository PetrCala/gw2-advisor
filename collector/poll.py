"""Run collect on a fixed cadence for the life of one Actions job.

The cron expression is not what sets the sampling rate. GitHub drops most
high-frequency schedule fires: `*/10` delivered 1.2 runs an hour over the
first week, median gap 44 minutes, never once under 15. Asking for `*/5`
would only get more of them dropped.

Sampling that coarse does not merely thin the data, it inverts a signal.
features.reprice counts a price move as a penny war only when consecutive
snapshots differ by 1-2c, so an hour of 1c undercuts arrives as one large
move and scores as no repricing at all: the busiest items look the calmest
and the flip scorer stops filtering them.

So the cadence lives inside a job instead. One run polls every INTERVAL
until DEADLINE, and the workflow's concurrency group means a later fire
waits and takes over when this run ends.

DEADLINE stays inside the hour that the OIDC role's credentials are good
for (MaxSessionDuration is unset on the role, so STS caps the session at
one hour and every S3 write after that would fail). Raising it there and
in the workflow's role-duration-seconds is what buys longer jobs.
"""

import os
import time

from .collect import main as collect_once

INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "600"))
DEADLINE = int(os.environ.get("POLL_DEADLINE_SECONDS", str(50 * 60)))
MAX_CONSECUTIVE_FAILURES = 3


def run(collect=collect_once, sleep=time.sleep, clock=time.monotonic, interval=None, deadline=None):
    """Poll until the next poll would land past the deadline.

    A failing poll does not end the run: collect already swallows the
    transient upstream errors, so anything reaching here is real, but one
    bad S3 call is not worth surrendering the rest of the job over. A
    problem that persists across MAX_CONSECUTIVE_FAILURES polls is, and
    that one propagates and fails the job.
    """
    interval = INTERVAL if interval is None else interval
    deadline = DEADLINE if deadline is None else deadline

    started = clock()
    polls = failures = consecutive = 0
    while True:
        tick = clock()
        polls += 1
        try:
            collect()
            consecutive = 0
        except Exception as e:
            failures += 1
            consecutive += 1
            print(f"poll {polls} failed, {consecutive} in a row: {type(e).__name__}: {e}", flush=True)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                raise

        # keep to the interval grid rather than drifting by however long
        # collect took, but never schedule into the past when it overran.
        # One clock sample serves all three uses: a second read advances on a
        # real clock and turns the remaining wait negative.
        now = clock()
        next_start = max(now, tick + interval)
        if next_start - started >= deadline:
            break
        sleep(next_start - now)

    print(f"polled {polls} times over {(clock() - started) / 60:.0f} min, {failures} failed", flush=True)
    return polls, failures


if __name__ == "__main__":
    run()
