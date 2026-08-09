"""Market radar (M6.d, docs/INVESTING.md strategy S2's "cheap screens"):
three passes over the daily dw2 snapshot that need no thesis to run.

- Abnormal-activity radar: z-scores of price (daily log-return), sold,
  listed and delisted (each side of the book summed) against a
  self-collected rolling baseline, plus a same-day book-imbalance proxy.
  Answers "what is moving that we have no thesis for" - a live example
  from the design doc: Wintersday Gifts trading 1.29M units/day in August
  is exactly the kind of anomaly this exists to flag and a human to
  explain.
- Pump filter: price up 4%+ on 4 of the trailing 5 days in a low-float
  item is a retail pump pattern, not drift; with a 15% tax the pumper's
  own exit rarely works, so these are fade-or-avoid, never follow.
- Sink-anticipation watchlist: materials pinned at the vendor floor with
  high sell flow are exactly the candidates a future Mystic Forge sink or
  recipe gets aimed at (ArenaNet's own economist posts describe this
  reaction function). Needs no history at all, unlike the two screens
  above.

The rolling history is self-collected starting from this milestone: there
is no backfill bootstrap, so the z-score screen has a real burn-in where
its table is empty. RADAR_MIN_OBS (below) is what actually gates a single
zscore() call, so the screen starts flagging once RADAR_MIN_OBS + 1 days
of history exist, not after RADAR_HISTORY_CAP (90) days; report.invest
renders an explicit "still collecting the baseline" message until then,
not a blank section. The baseline keeps deepening toward the 90-day cap
after that point, so flags in the first few weeks rest on a thinner
sample than flags months in - read early hits with that in mind.

Usage:
    python -m invest.radar    # needs S3_BUCKET; no GW2_API_KEY required
"""

import math
from statistics import mean, pstdev

TAX = 0.15  # trading post cut; duplicated per this repo's per-module-constant convention

RADAR_HISTORY_CAP = 90  # rolling baseline depth; not the five-year MAX_POINTS other series use
RADAR_MIN_OBS = 5  # baseline points required before a z-score is trusted at all
# Gaussian-tail justification: |z| >= 4 over ~28,000 items x 4 metrics/day
# expects ~7 false positives/day, against ~302/day at |z| >= 3 - a starting
# point meant to be retuned once a few weeks of real flagged-count data
# exists, not a final number.
RADAR_Z_THRESHOLD = 4.0
RADAR_PUMP_PCT = 0.04  # GE-Central-style: a single day's move counted as "up"
RADAR_LOW_FLOAT = 300  # resting depth (both sides) under which a pump is retail-thin
SINK_MIN_FLOW = 100  # units/day, 7-day average, floor for the sink watchlist

# The widened field list is dw2.SNAPSHOT_FIELDS plus the four listed/
# delisted columns, confirmed live on the same endpoint at zero extra
# request cost. Built in main() (see _radar_fields()), not here, so this
# module stays importable with stdlib only, matching invest.nav/luxury/craft.

RADAR_KEY = "invest/radar/latest.json.gz"
HISTORY_KEY = "invest/radar/history.json.gz"


# --- history -----------------------------------------------------------


def build_point(snapshot):
    """{item_id_str: {price, sold, listed, delisted, depth, imbalance}} for
    one day's dw2 snapshot (widened with RADAR_FIELDS). sold/listed/
    delisted are each the buy-side plus sell-side count, matching how the
    strategy is stated (no side distinction, just "is something moving").
    imbalance is the same-day (buy_quantity - sell_quantity) / (buy_quantity
    + sell_quantity) proxy: cheap, not the literal 10-minute order-book
    reconstruction the design doc describes, which would need streaming
    ~1.5GB/day of collect.py's compact parquet - out of scope for this
    slice, see docs/ROADMAP.md.
    """
    out = {}
    for it in snapshot:
        item_id = it.get("id")
        if item_id is None:
            continue
        bq, sq = it.get("buy_quantity") or 0, it.get("sell_quantity") or 0
        out[str(item_id)] = {
            "price": it.get("sell_price"),
            "sold": (it.get("1d_buy_sold") or 0) + (it.get("1d_sell_sold") or 0),
            "listed": (it.get("1d_buy_listed") or 0) + (it.get("1d_sell_listed") or 0),
            "delisted": (it.get("1d_buy_delisted") or 0) + (it.get("1d_sell_delisted") or 0),
            "depth": bq + sq,
            "imbalance": round((bq - sq) / (bq + sq), 4) if (bq + sq) else None,
        }
    return out


def append(history, point, cap=RADAR_HISTORY_CAP):
    """The history with today's point added, one per day, oldest dropped
    past `cap`; same add-today/replace-same-day-rerun/sort/trim shape as
    invest.nav.append, just capped much shorter (a rolling baseline, not a
    multi-year series)."""
    points = [p for p in (history.get("points") or []) if p.get("d") != point["d"]]
    points.append(point)
    points.sort(key=lambda p: p["d"])
    return {"points": points[-cap:]}


# --- abnormal-activity radar --------------------------------------------


def zscore(values, latest, min_obs=RADAR_MIN_OBS):
    """(latest - mean(values)) / stdev(values); None below `min_obs`
    baseline observations or when the baseline has zero spread, rather
    than guessing either way."""
    vals = [v for v in values if v is not None]
    if len(vals) < min_obs:
        return None
    sd = pstdev(vals)
    if sd == 0:
        return None
    return (latest - mean(vals)) / sd


def _price_log_return(prev_price, cur_price):
    if not prev_price or not cur_price or prev_price <= 0 or cur_price <= 0:
        return None
    return math.log(cur_price / prev_price)


def _item_series(points, item_id, metric):
    """[value, ...] for one item/metric across ordered daily `points`."""
    out = []
    for p in points:
        row = (p.get("items") or {}).get(item_id)
        out.append(row.get(metric) if row else None)
    return out


def radar_rows(points, threshold=RADAR_Z_THRESHOLD, min_obs=RADAR_MIN_OBS):
    """Abnormal-activity rows for the latest of `points` (oldest first):
    every earlier point is the baseline, the last one is today. Price is
    z-scored on its daily log-return, sold/listed/delisted on raw daily
    counts, book imbalance on the same-day proxy; a row survives when any
    one metric clears |z| >= threshold, sorted by the largest |z| first.
    """
    if len(points) < 2:
        return []
    *baseline, latest = points
    today_items = latest.get("items") or {}
    rows = []
    for item_id, cur in today_items.items():
        z = {}
        prices = _item_series(baseline, item_id, "price")
        rets = [r for a, b in zip(prices, prices[1:]) if (r := _price_log_return(a, b)) is not None]
        cur_ret = _price_log_return(prices[-1] if prices else None, cur.get("price"))
        if cur_ret is not None:
            z["price"] = zscore(rets, cur_ret, min_obs)
        for metric in ("sold", "listed", "delisted", "imbalance"):
            v = cur.get(metric)
            if v is not None:
                z[metric] = zscore(_item_series(baseline, item_id, metric), v, min_obs)
        flags = sorted(k for k, v in z.items() if v is not None and abs(v) >= threshold)
        if flags:
            rows.append({"id": int(item_id), "z": z, "flags": flags})
    rows.sort(key=lambda r: -max(abs(v) for v in r["z"].values() if v is not None))
    return rows


# --- pump filter ---------------------------------------------------------


def pump_flag(history_tail, low_float=RADAR_LOW_FLOAT):
    """True on 4-of-trailing-5-days each up >= RADAR_PUMP_PCT AND today's
    resting depth under `low_float`. `history_tail` is [{price, depth},
    ...], oldest first, at least 6 points (5 day-over-day changes)."""
    if len(history_tail) < 6:
        return False
    tail = history_tail[-6:]
    ups = 0
    for prev, cur in zip(tail, tail[1:]):
        ret = _price_log_return(prev.get("price"), cur.get("price"))
        if ret is not None and (math.exp(ret) - 1) >= RADAR_PUMP_PCT:
            ups += 1
    depth = tail[-1].get("depth")
    return ups >= 4 and depth is not None and depth < low_float


def pump_candidates(points, low_float=RADAR_LOW_FLOAT):
    """Item ids flagged by pump_flag over the trailing 6 of `points`."""
    if len(points) < 6:
        return []
    tail_points = points[-6:]
    ids = set()
    for p in tail_points:
        ids.update((p.get("items") or {}).keys())
    out = []
    for item_id in ids:
        series = [
            {
                "price": ((p.get("items") or {}).get(item_id) or {}).get("price"),
                "depth": ((p.get("items") or {}).get(item_id) or {}).get("depth"),
            }
            for p in tail_points
        ]
        if pump_flag(series, low_float):
            out.append(int(item_id))
    return sorted(out)


# --- sink-anticipation watchlist ------------------------------------------


def sink_watchlist(snapshot, min_flow=SINK_MIN_FLOW):
    """Rows pinned at the vendor floor (sell_price net of tax at or below
    vendor_value) with 7-day-average sell flow at or above `min_flow`;
    pure function of one day's snapshot, no history needed."""
    out = []
    for it in snapshot:
        sell, vendor = it.get("sell_price"), it.get("vendor_value")
        if not sell or not vendor or sell * (1 - TAX) > vendor:
            continue
        flow = (it.get("7d_sell_sold") or 0) / 7
        if flow < min_flow:
            continue
        out.append(
            {
                "id": it.get("id"),
                "name": it.get("name") or f"item {it.get('id')}",
                "sell_price": sell,
                "vendor_value": vendor,
                "flow_7d_avg": round(flow, 1),
            }
        )
    out.sort(key=lambda r: -r["flow_7d_avg"])
    return out


# --- entry point -----------------------------------------------------------


def _radar_fields(dw2):
    """SNAPSHOT_FIELDS widened with the four listed/delisted columns."""
    return dw2.SNAPSHOT_FIELDS + ",1d_buy_listed,1d_sell_listed,1d_buy_delisted,1d_sell_delisted"


def main():
    import os
    from datetime import datetime, timezone

    from collector import dw2
    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    snap = dw2.fetch_snapshot(fields=_radar_fields(dw2))
    today = datetime.now(timezone.utc).date()
    point = {"d": today.isoformat(), "items": build_point(snap)}

    history = append(store.get_json_gz(HISTORY_KEY) or {}, point)
    store.put_json_gz(HISTORY_KEY, history)
    points = history["points"]

    abnormal = radar_rows(points)
    pumps = pump_candidates(points)
    sinks = sink_watchlist(snap)

    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": today.isoformat(),
        "history_days": len(points),
        "abnormal": abnormal,
        "pumps": pumps,
        "sinks": sinks,
    }
    store.put_json_gz(RADAR_KEY, artifact)
    print(
        f"radar {today.isoformat()}: {len(points)} history days, "
        f"{len(abnormal)} abnormal, {len(pumps)} pump flags, "
        f"{len(sinks)} sink candidates -> s3://{store.bucket}/{RADAR_KEY}"
    )


if __name__ == "__main__":
    main()
