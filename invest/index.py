"""Weekly market-index build over history/dw2/ into invest/bench.json.gz.

Usage:
    python -m invest.index

The index is "the market" every long-book strategy is scored against
(docs/INVESTING.md, benchmarks): a turnover-weighted daily price index over
the trailing top 500 items, constituents and weights rebalanced monthly
from the prior month's sell-side turnover, single-item weight capped so no
staple becomes the whole index. Levels chain daily from item mid prices,
renormalized over whatever traded that day.

It starts in mid-2019, not 2013: datawars2 rows before then carry only
price extremes (no fills, no quantities; verified 2026-08-08), so turnover
weighting, and any defensible weighting, is impossible earlier. The ecto
series goes back to 2012 and the gem series to 2013 for long context.

Streaming follows season.compute: every chunk is read once, items are
reduced to dense daily mids plus monthly turnover, and everything below a
turnover floor no top-500 member could miss is dropped on the spot. Heavy
imports stay inside functions so the module imports with stdlib only.
"""

import io
import json
import os
from datetime import date, datetime, timedelta, timezone

from season.cycles import daily_price

INDEX_TOP_N = 500  # the top-500 carry 84% of turnover (live, 2026-08-08)
WEIGHT_CAP = 0.05  # ecto alone is ~15% of turnover; cap it or index ectos
MIN_MONTH_DAYS = 10  # prior-month price days needed to trust the ranking
MIN_MONTH_ELIGIBLE = 400  # the index starts when a real top-500 exists
MIN_DAY_WEIGHT = 0.5  # carry the level on days most of the basket is dark
RELATIVE_CLIP = (0.1, 10.0)  # bounds data glitches, not real repricings
KEEP_TURNOVER_FLOOR = 3_000_000  # copper/month; ~300g, far under any top-500 cut
BASE_LEVEL = 100.0
ECTO_ID = 19721  # Glob of Ectoplasm, the long-context benchmark line

AXIS_START = date(2019, 1, 1)  # fills begin mid-2019; the start rule trims the rest

HISTORY_PREFIX = "history/dw2/"
BENCH_KEY = "invest/bench.json.gz"

GW2TP_GEMS_URL = "https://www.gw2tp.com/api/gems"
EXCHANGE_COINS_URL = "https://api.guildwars2.com/v2/commerce/exchange/coins"
USER_AGENT = "gw2-advisor (github.com/PetrCala/gw2-advisor)"


def month_key(iso):
    """'YYYY-MM-DD' (or 'YYYY-MM') to a comparable month integer."""
    return int(iso[:4]) * 12 + int(iso[5:7]) - 1


def month_str(key):
    return f"{key // 12:04d}-{key % 12 + 1:02d}"


def _ok(v):
    """A usable price/level: present, not NaN, positive."""
    return v is not None and v == v and v > 0


def cap_weights(raw, cap=WEIGHT_CAP):
    """Normalize {id: weight} with no entry above cap.

    Excess above the cap is redistributed proportionally over the uncapped
    rest, the way capped value-weight indexes do it. With too few items for
    the cap to be feasible, weights fall back to equal.
    """
    total = sum(raw.values())
    if total <= 0 or not raw:
        return {}
    if len(raw) * cap <= 1.0:
        return {i: 1.0 / len(raw) for i in raw}
    w = {i: v / total for i, v in raw.items()}
    capped = set()
    while True:
        over = [i for i in w if i not in capped and w[i] > cap + 1e-12]
        if not over:
            return w
        capped.update(over)
        free = [i for i in w if i not in capped]
        spare = 1.0 - cap * len(capped)
        free_total = sum(w[i] for i in free)
        for i in capped:
            w[i] = cap
        if not free or free_total <= 0:
            return w
        for i in free:
            w[i] = w[i] * spare / free_total


def select_constituents(turn, cover, month, top_n=INDEX_TOP_N, min_days=MIN_MONTH_DAYS):
    """{id: prior-month turnover} for the top_n eligible items of `month`."""
    prev = month - 1
    eligible = [
        (t.get(prev, 0.0), i)
        for i, t in turn.items()
        if t.get(prev, 0) > 0 and cover.get(i, {}).get(prev, 0) >= min_days
    ]
    eligible.sort(reverse=True)
    return {i: t for t, i in eligible[:top_n]}


def first_index_month(turn, cover, months, min_eligible=None):
    """Earliest month with a real top-500 to select from, or None."""
    if min_eligible is None:
        min_eligible = MIN_MONTH_ELIGIBLE
    for m in months[1:]:
        n = len(select_constituents(turn, cover, m, top_n=min_eligible))
        if n >= min_eligible:
            return m
    return None


def chain_daily(days, mids, turn, cover, base=BASE_LEVEL, min_eligible=None,
                weight_cap=None):
    """Chain the daily index level over the axis.

    days is the ISO date axis, mids {id: per-day sequence with gaps}. Each
    day's return is the weighted mean of clipped price relatives over the
    current month's constituents that priced on both days, renormalized to
    the weight actually present; a day with most of the basket dark carries
    the level unchanged. Returns None when no month ever gets eligible.
    """
    month_of = [month_key(d) for d in days]
    months = sorted(set(month_of))
    start_month = first_index_month(turn, cover, months, min_eligible)
    if start_month is None:
        return None

    weights = {}
    for m in months:
        if m < start_month:
            continue
        cons = select_constituents(turn, cover, m)
        if cons:
            weights[m] = cap_weights(cons, weight_cap if weight_cap else WEIGHT_CAP)

    start_day = month_of.index(start_month)
    lo, hi = RELATIVE_CLIP
    dates, levels = [], []
    level = base
    cur_w = {}
    cur_m = None
    for t in range(start_day, len(days)):
        m = month_of[t]
        if m != cur_m:
            cur_m = m
            cur_w = weights.get(m) or cur_w
        if t > start_day:
            num = wsum = 0.0
            for i, w in cur_w.items():
                seq = mids.get(i)
                if seq is None:
                    continue
                a, b = seq[t - 1], seq[t]
                if _ok(a) and _ok(b):
                    # float() keeps numpy scalars out of the level, and out
                    # of the artifact json
                    num += w * min(hi, max(lo, float(b) / float(a)))
                    wsum += w
            if wsum >= MIN_DAY_WEIGHT:
                level *= num / wsum
        dates.append(days[t])
        levels.append(round(float(level), 4))

    months_meta = [{"m": month_str(m), "n": len(w)} for m, w in sorted(weights.items())]
    last_m = max(weights)
    return {
        "dates": dates,
        "levels": levels,
        "start": dates[0],
        "months": months_meta,
        "last_weights": weights[last_m],
        "last_turnover": select_constituents(turn, cover, last_m),
    }


def load(store):
    """Stream all history chunks into index inputs.

    Returns (days, mids, turn, cover, ecto, through): the ISO day axis from
    AXIS_START to the last observed day, dense per-item daily mid arrays,
    per-item monthly turnover and price-day counts, the full ecto daily
    series (pre-2019 included), and the last observed date.
    """
    import numpy as np
    import pyarrow.parquet as pq

    n_days = (date.today() - AXIS_START).days + 1
    mids, turn, cover = {}, {}, {}
    ecto = {}
    last_idx = -1

    for key in store.list_keys(HISTORY_PREFIX):
        data = store.get_bytes(key)
        if data is None:
            continue
        t = pq.read_table(
            io.BytesIO(data),
            columns=["date", "item_id", "buy_price_avg", "sell_price_avg",
                     "sell_price_min", "sell_price_max", "sell_sold"],
        )
        rows = zip(
            t["date"].to_pylist(),
            t["item_id"].to_pylist(),
            t["buy_price_avg"].to_pylist(),
            t["sell_price_avg"].to_pylist(),
            t["sell_price_min"].to_pylist(),
            t["sell_price_max"].to_pylist(),
            t["sell_sold"].to_pylist(),
        )
        pending = {}
        for ts, item_id, bp, sp, sp_lo, sp_hi, sold in rows:
            d = ts.date()
            if item_id == ECTO_ID:
                p = daily_price(sp, sp_lo, sp_hi)
                if p and p > 0:
                    ecto[d.isoformat()] = round(p, 2)
            idx = (d - AXIS_START).days
            if idx < 0 or idx >= n_days or not sp or sp <= 0:
                continue
            entry = pending.get(item_id)
            if entry is None:
                entry = pending[item_id] = (
                    np.full(n_days, np.nan, dtype=np.float32), {}, {},
                )
            arr, mturn, mcover = entry
            arr[idx] = (sp + bp) / 2 if bp and bp > 0 else sp
            m = d.year * 12 + d.month - 1
            mcover[m] = mcover.get(m, 0) + 1
            if sold:
                mturn[m] = mturn.get(m, 0.0) + sp * sold
            if idx > last_idx:
                last_idx = idx
        for item_id, (arr, mturn, mcover) in pending.items():
            if mturn and max(mturn.values()) >= KEEP_TURNOVER_FLOOR:
                mids[item_id] = arr
                turn[item_id] = mturn
                cover[item_id] = mcover

    if last_idx < 0:
        return None
    days = [(AXIS_START + timedelta(days=k)).isoformat() for k in range(last_idx + 1)]
    return days, mids, turn, cover, ecto, days[-1]


def fetch_gems():
    """Daily copper per 100 gems (buying with gold) from gw2tp, since 2013.

    The endpoint answers plain requests but labels the JSON text/html, so it
    is parsed by hand. Candles are [ms, open, high, low, close]; the close
    is the day's rate.
    """
    import requests

    resp = requests.get(
        GW2TP_GEMS_URL,
        params={"range": "all"},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    out = {}
    for candle in json.loads(resp.text).get("buy") or []:
        day = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc).date()
        close = candle[4]
        if close and close > 0:
            out[day.isoformat()] = close
    return out


def fetch_gem_spot():
    """Today's copper per 100 gems from the official exchange (1 request)."""
    import requests

    resp = requests.get(
        EXCHANGE_COINS_URL, params={"quantity": 10_000_000}, timeout=30
    )
    resp.raise_for_status()
    per_gem = resp.json().get("coins_per_gem")
    return per_gem * 100 if per_gem else None


def _series(mapping):
    """{iso: value} to the artifact's parallel-array form, date ordered."""
    dates = sorted(mapping)
    return {"dates": dates, "values": [mapping[d] for d in dates]}


def trailing_return(dates, values, days_back, through):
    """Return over the trailing window, or None without a usable base point."""
    if not dates:
        return None
    floor = (date.fromisoformat(through) - timedelta(days=days_back)).isoformat()
    base = next(
        ((d, v) for d, v in zip(dates, values) if d >= floor and _ok(v)), None
    )
    if base is None or base[0] == dates[-1] or not _ok(values[-1]):
        return None
    return round(values[-1] / base[1] - 1, 4)


def returns_summary(series, through):
    return {
        "30d": trailing_return(series["dates"], series["values"], 30, through),
        "90d": trailing_return(series["dates"], series["values"], 90, through),
        "365d": trailing_return(series["dates"], series["values"], 365, through),
        "all": trailing_return(series["dates"], series["values"], 36500, through),
    }


def main():
    from collector import dw2
    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    loaded = load(store)
    if loaded is None:
        raise SystemExit(f"no usable history under {HISTORY_PREFIX}, run the backfill first")
    days, mids, turn, cover, ecto, through = loaded
    print(f"{len(mids)} items above the turnover floor, axis through {through}", flush=True)

    chained = chain_daily(days, mids, turn, cover)
    if chained is None:
        raise SystemExit("no month ever reached the eligibility floor")

    previous = store.get_json_gz(BENCH_KEY) or {}

    gems = {}
    prev_gems = previous.get("gems")
    if prev_gems:
        gems.update(zip(prev_gems["dates"], prev_gems["values"]))
    try:
        gems.update(fetch_gems())
    except Exception as e:  # long context refines the page, it doesn't gate it
        print(f"gw2tp gems unavailable: {e}")
    try:
        spot = fetch_gem_spot()
        if spot:
            gems[date.today().isoformat()] = spot
    except Exception as e:
        print(f"gem spot unavailable: {e}")

    names = {}
    try:
        names = {it["id"]: it.get("name") for it in dw2.fetch_snapshot()}
    except Exception as e:  # names label the constituents, nothing else
        print(f"snapshot names unavailable: {e}")

    top = sorted(chained["last_weights"].items(), key=lambda kv: -kv[1])[:20]
    constituents = [
        {
            "id": i,
            "name": names.get(i) or f"item {i}",
            "weight": round(w, 4),
            # selection-month turnover over a nominal 30-day month
            "turnover_g_day": round(chained["last_turnover"].get(i, 0) / 10_000 / 30),
        }
        for i, w in top
    ]

    index_series = {"dates": chained["dates"], "values": chained["levels"]}
    ecto_series = _series(ecto)
    gems_series = _series(gems) if gems else None
    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_through": through,
        "params": {
            "top_n": INDEX_TOP_N,
            "weight_cap": WEIGHT_CAP,
            "rebalance": "monthly, by prior-month sell-side turnover",
            "min_month_days": MIN_MONTH_DAYS,
            "min_month_eligible": MIN_MONTH_ELIGIBLE,
            "relative_clip": list(RELATIVE_CLIP),
            "base_level": BASE_LEVEL,
            "start_reason": "datawars2 carries no fills or quantities before "
            "mid-2019, so turnover weighting cannot reach further back",
        },
        "index": {
            **index_series,
            "start": chained["start"],
            "months": chained["months"],
            "returns": returns_summary(index_series, through),
        },
        "constituents": constituents,
        "ecto": {**ecto_series, "returns": returns_summary(ecto_series, through)},
        "gems": (
            {**gems_series, "returns": returns_summary(gems_series, through)}
            if gems_series
            else None
        ),
    }
    store.put_json_gz(BENCH_KEY, artifact)
    print(
        f"index {chained['start']} -> {through}, level {chained['levels'][-1]:.1f} "
        f"({len(chained['months'])} months, {len(constituents)} constituents listed) "
        f"-> s3://{store.bucket}/{BENCH_KEY}"
    )


if __name__ == "__main__":
    main()
