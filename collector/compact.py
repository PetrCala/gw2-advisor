"""Compact one UTC day of raw delta snapshots into a parquet file.

Usage: python -m collector.compact [YYYY-MM-DD]
Defaults to yesterday (UTC). Idempotent: rewrites compact/YYYY-MM-DD.parquet.
Raw objects are left in place; the lifecycle rule expires them after 30 days,
which leaves a comfortable window to re-run a failed compaction.
"""

import io
import os
import sys
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from .s3store import Store


def main():
    if len(sys.argv) > 1:
        day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    store = Store(os.environ["S3_BUCKET"])
    keys = store.list_keys(day.strftime("raw/%Y/%m/%d/"))
    if not keys:
        print(f"no raw snapshots for {day}, nothing to compact")
        return

    cols = {"ts": [], "item_id": [], "buy": [], "buy_qty": [], "sell": [], "sell_qty": []}
    for key in keys:
        snap = store.get_json_gz(key)
        ts = datetime.strptime(snap["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        for item_id, v in snap["items"].items():
            cols["ts"].append(ts)
            cols["item_id"].append(int(item_id))
            cols["buy"].append(v[0])
            cols["buy_qty"].append(v[1])
            cols["sell"].append(v[2])
            cols["sell_qty"].append(v[3])

    table = pa.table(
        {
            "ts": pa.array(cols["ts"], pa.timestamp("s", tz="UTC")),
            "item_id": pa.array(cols["item_id"], pa.int32()),
            "buy": pa.array(cols["buy"], pa.int64()),
            "buy_qty": pa.array(cols["buy_qty"], pa.int64()),
            "sell": pa.array(cols["sell"], pa.int64()),
            "sell_qty": pa.array(cols["sell_qty"], pa.int64()),
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    out_key = f"compact/{day}.parquet"
    store.put_bytes(out_key, buf.getvalue())
    print(f"{len(keys)} snapshots, {table.num_rows} rows -> {out_key} ({buf.getbuffer().nbytes} bytes)")


if __name__ == "__main__":
    main()
