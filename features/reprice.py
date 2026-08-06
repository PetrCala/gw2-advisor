"""Undercut/outbid rates from our own delta snapshots.

An item whose best buy keeps creeping up by 1-2c is being outbid; a best
sell creeping down by 1-2c is being undercut. High rates mean our
queue-front position dies in minutes and the capture assumption is
fiction for that item (penny wars); the scorer filters those out.

Rows come from compact/YYYY-MM-DD.parquet (one row per changed item per
snapshot) with today's raw delta snapshots appended, so the rates stay
current even right after the 02:40 compaction boundary. Only price moves
count as events; quantity-only changes appear as rows but not as deltas.
"""

from datetime import datetime, timezone

REPRICE_MAX_STEP = 2  # copper; bigger moves are real repricing, not penny wars
MIN_SPAN_DAYS = 0.25  # avoid wild rates from a short observation window


def load_events(store, days=3):
    """Return ([(ts_epoch, item_id, buy, sell), ...], span_days) from S3.

    Reads up to `days` newest compact parquet files plus today's raw
    snapshots. Empty list when the bucket has no delta history yet.
    """
    import io

    import pyarrow.parquet as pq

    rows = []
    for key in store.list_keys("compact/")[-days:]:
        body = store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read()
        t = pq.read_table(io.BytesIO(body), columns=["ts", "item_id", "buy", "sell"])
        ts = t["ts"].cast("int64").to_pylist()  # timestamp[s] casts to epoch seconds
        item = t["item_id"].to_pylist()
        buy = t["buy"].to_pylist()
        sell = t["sell"].to_pylist()
        rows.extend(zip(ts, item, buy, sell))

    today = datetime.now(timezone.utc).strftime("raw/%Y/%m/%d/")
    for key in store.list_keys(today):
        snap = store.get_json_gz(key)
        ts = int(
            datetime.strptime(snap["ts"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        for item_id, v in snap["items"].items():
            rows.append((ts, int(item_id), v[0], v[2]))

    if not rows:
        return [], 0.0
    span = (max(r[0] for r in rows) - min(r[0] for r in rows)) / 86400
    return rows, max(span, MIN_SPAN_DAYS)


def reprice_rates(rows, span_days):
    """Return {item_id: (outbids_per_day, undercuts_per_day)}."""
    if not rows or span_days <= 0:
        return {}
    by_item = {}
    for ts, item_id, buy, sell in rows:
        by_item.setdefault(item_id, []).append((ts, buy, sell))

    rates = {}
    for item_id, seq in by_item.items():
        seq.sort()
        outbid = undercut = 0
        for (_, b0, s0), (_, b1, s1) in zip(seq, seq[1:]):
            if 0 < b1 - b0 <= REPRICE_MAX_STEP:
                outbid += 1
            if 0 < s0 - s1 <= REPRICE_MAX_STEP:
                undercut += 1
        rates[item_id] = (outbid / span_days, undercut / span_days)
    return rates
