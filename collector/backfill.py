"""One-time datawars2 history backfill into S3.

Usage:
    python -m collector.backfill          # all chunks, resumable
    python -m collector.backfill 17       # a single chunk, for debugging

The tradable id list is sorted and split into fixed chunks of CHUNK_IDS ids;
chunk N lands at history/dw2/chunk-NNNN.parquet holding full daily history
(2012+) for its ids. Chunks whose key already exists are skipped, so a rerun
after a failure resumes where it stopped. New item ids only appear at the
tail of the sorted list, so chunk boundaries stay stable across reruns.

Expected footprint: ~280 objects, low double-digit MB for the densest early
chunks, one to two GB total, well inside the bucket quota.
"""

import io
import os
import sys
import time
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import requests

from . import dw2, gw2api
from .s3store import Store

CHUNK_IDS = 100  # ids per parquet object
BATCH_IDS = 10  # ids per http request; larger batches 502
THROTTLE_S = 0.5  # pause between requests, out of politeness

INT_COLS = [
    "buy_price_min",
    "buy_price_max",
    "sell_price_min",
    "sell_price_max",
    "buy_sold",
    "sell_sold",
    "buy_listed",
    "sell_listed",
    "buy_delisted",
    "sell_delisted",
]
FLOAT_COLS = ["buy_price_avg", "sell_price_avg", "buy_quantity_avg", "sell_quantity_avg"]


def rows_to_table(rows):
    dates, ids = [], []
    cols = {c: [] for c in INT_COLS + FLOAT_COLS}
    for r in rows:
        dates.append(datetime.fromisoformat(r["date"].replace("Z", "+00:00")))
        ids.append(int(r["itemID"]))
        for c in INT_COLS:
            v = r.get(c)
            cols[c].append(int(float(v)) if v not in (None, "") else None)
        for c in FLOAT_COLS:
            v = r.get(c)
            cols[c].append(float(v) if v not in (None, "") else None)
    data = {
        "date": pa.array(dates, pa.timestamp("s", tz="UTC")),
        "item_id": pa.array(ids, pa.int32()),
    }
    for c in INT_COLS:
        data[c] = pa.array(cols[c], pa.int64())
    for c in FLOAT_COLS:
        data[c] = pa.array(cols[c], pa.float64())
    return pa.table(data)


def main():
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None
    store = Store(os.environ["S3_BUCKET"])
    session = requests.Session()

    ids = gw2api.fetch_tradable_ids(session)
    chunks = [ids[i : i + CHUNK_IDS] for i in range(0, len(ids), CHUNK_IDS)]
    have = set(store.list_keys("history/dw2/"))

    written = skipped = 0
    for n, chunk in enumerate(chunks):
        if only is not None and n != only:
            continue
        key = f"history/dw2/chunk-{n:04d}.parquet"
        if key in have:
            skipped += 1
            continue
        rows = []
        for i in range(0, len(chunk), BATCH_IDS):
            rows.extend(dw2.fetch_history(chunk[i : i + BATCH_IDS], session))
            time.sleep(THROTTLE_S)
        table = rows_to_table(rows)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        store.put_bytes(key, buf.getvalue())
        written += 1
        print(
            f"chunk {n:04d}: ids {chunk[0]}-{chunk[-1]}, {table.num_rows} rows, "
            f"{buf.getbuffer().nbytes} bytes -> {key}",
            flush=True,
        )
    print(f"backfill: {written} chunks written, {skipped} already present, {len(chunks)} total")


if __name__ == "__main__":
    main()
