"""Fetch a full price snapshot and upload the delta against the previous state.

Layout in the bucket:
  raw/YYYY/MM/DD/HHMMSS.json.gz   changed items only (full table on first run),
                                  expired by lifecycle after 30 days
  state/latest.json.gz            full table, overwritten every run, used for diffing

Snapshots are stamped with the actual fetch time: Actions cron jitter means
intervals are irregular and nothing downstream may assume 10-minute spacing.
Item ids that disappear from the API are dropped from state and not recorded
as deletions; delisted items simply stop appearing.

The API blips unavailable for minutes at a time, and a poll this frequent has
nothing to gain by failing over one: the next run picks the prices up and the
delta chain never notices. So a transient upstream error is a skip, not a
failure, for as long as the stored snapshot is fresh. Past STALE_AFTER it
turns back into a failure, because by then the difference between a blip and
a break matters and silence would be the wrong answer.
"""

import os
from datetime import datetime, timedelta, timezone

from . import gw2api
from .s3store import Store

STATE_KEY = "state/latest.json.gz"
STALE_AFTER = timedelta(hours=3)  # several missed polls even at the observed ~1/hour


def main():
    store = Store(os.environ["S3_BUCKET"])
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        items = gw2api.fetch_all_prices()
    except gw2api.TransientError as e:
        skip_if_fresh(store, now, e)
        return

    if not items:
        raise RuntimeError("empty price table from API, refusing to write")
    prev = store.get_json_gz(STATE_KEY)

    if prev is None:
        kind = "full"
        changed = items
    else:
        kind = "delta"
        prev_items = {int(k): v for k, v in prev["items"].items()}
        changed = {i: v for i, v in items.items() if prev_items.get(i) != v}

    raw_key = now.strftime("raw/%Y/%m/%d/%H%M%S.json.gz")
    store.put_json_gz(raw_key, {"ts": ts, "type": kind, "items": {str(k): v for k, v in changed.items()}})
    store.put_json_gz(STATE_KEY, {"ts": ts, "type": "full", "items": {str(k): v for k, v in items.items()}})
    print(f"{ts} {kind}: {len(changed)}/{len(items)} items changed -> {raw_key}")


def skip_if_fresh(store, now, err):
    """Swallow a transient API error, unless state has gone stale.

    Raises `err` when there is no usable stored snapshot to fall back on, so
    an outage that outlasts STALE_AFTER, an unreadable state object, and a
    first-ever run all still fail loudly.
    """
    age = _state_age(store, now)
    if age is None:
        print(f"upstream unavailable and no readable state to fall back on: {err}")
        raise err
    if age > STALE_AFTER:
        print(f"upstream unavailable and state is {_hours(age)} old, past the {_hours(STALE_AFTER)} limit")
        raise err
    print(f"skipping this poll, upstream unavailable and state is only {_hours(age)} old: {err}")


def _state_age(store, now):
    prev = store.get_json_gz(STATE_KEY)
    if not prev or not prev.get("ts"):
        return None
    try:
        last = datetime.strptime(prev["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return now - last


def _hours(delta):
    return f"{delta.total_seconds() / 3600:.1f}h"


if __name__ == "__main__":
    main()
