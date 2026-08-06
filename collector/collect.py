"""Fetch a full price snapshot and upload the delta against the previous state.

Layout in the bucket:
  raw/YYYY/MM/DD/HHMMSS.json.gz   changed items only (full table on first run),
                                  expired by lifecycle after 30 days
  state/latest.json.gz            full table, overwritten every run, used for diffing

Snapshots are stamped with the actual fetch time: Actions cron jitter means
intervals are irregular and nothing downstream may assume 10-minute spacing.
Item ids that disappear from the API are dropped from state and not recorded
as deletions; delisted items simply stop appearing.
"""

import os
from datetime import datetime, timezone

from . import gw2api
from .s3store import Store

STATE_KEY = "state/latest.json.gz"


def main():
    store = Store(os.environ["S3_BUCKET"])
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    items = gw2api.fetch_all_prices()
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


if __name__ == "__main__":
    main()
