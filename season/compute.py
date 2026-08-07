"""Weekly seasonal decomposition over history/dw2/ into season/latest.json.gz.

Usage:
    python -m season.compute [max_candidates]

Streams every history/dw2/chunk-NNNN.parquet and keeps rows for a snapshot
derived candidate set, so the id to chunk mapping is never reconstructed.
Per item: daily sell prices (pre-2019 rows fall back to the min/max
midpoint), weekly log-price resample, STL (period 52, robust), buy/sell
windows from the seasonal profile, then the realized return of every past
cycle. Event-linked candidates anchor their windows to each year's actual
festival dates. The top picks land in S3 for the daily report to render.

Runtime is bounded by MAX_CANDIDATES (S3 reads finish in the first minutes
and the single small write comes last, so the 1 hour OIDC session is not a
risk). Heavy imports stay inside functions so the module imports with
stdlib only, matching features/reprice.py.
"""

import io
import os
import sys
from datetime import date, datetime, timedelta, timezone
from statistics import median

from season import actions, cycles, events, windows
from season import score as season_score

MIN_SEASON_FLOW = 50.0  # units/day sold (1m average); we need exit liquidity
MIN_PRICE = 30  # copper, current sell; cheaper items cannot pay the spread
MAX_CANDIDATES = 4000  # bounds runtime and memory
MIN_HISTORY_DAYS = 3 * 365  # anything younger cannot show MIN_CYCLES cycles
STL_YEARS = 8  # decompose recent years; older regimes only feed cycle returns
MAX_FFILL_WEEKS = 2  # bridge short weekly gaps only
MAX_INTERP_FRAC = 0.2  # skip items whose weekly series needed more filling
FLOW_DAYS = 500  # keep sell_sold this far back for the flow evidence

HISTORY_PREFIX = "history/dw2/"
SEASON_KEY = "season/latest.json.gz"


def prefilter(snap, max_candidates):
    """Liquid, non-vendor-floor items worth decomposing, best flow first."""
    out = {}
    for it in snap:
        item_id = it.get("id")
        price = it.get("sell_price") or 0
        flow = (it.get("1m_sell_sold") or 0) / 30.0
        vendor = it.get("vendor_value") or 0
        if not item_id or price < MIN_PRICE or flow < MIN_SEASON_FLOW:
            continue
        if price * (1 - cycles.TAX) <= vendor:
            continue  # pinned at the vendor floor, no room for a trough
        out[item_id] = {
            "name": it.get("name") or f"item {item_id}",
            "rarity": it.get("rarity") or "",
            "cur_price": price,
            "flow": flow,
        }
    ranked = sorted(out, key=lambda i: out[i]["flow"], reverse=True)
    return {i: out[i] for i in ranked[:max_candidates]}


def load_series(store, cand_ids):
    """Stream all history chunks; daily prices and recent flow per candidate.

    Returns ({item_id: {date_iso: price}}, {item_id: {date_iso: sold}},
    last_date). Filtering every chunk against the id set sidesteps the
    id to chunk mapping entirely.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    wanted = pa.array(sorted(cand_ids), pa.int32())
    flow_floor = (date.today() - timedelta(days=FLOW_DAYS)).isoformat()
    series, flows = {}, {}
    last = None
    for key in store.list_keys(HISTORY_PREFIX):
        data = store.get_bytes(key)
        if data is None:
            continue
        t = pq.read_table(
            io.BytesIO(data),
            columns=["date", "item_id", "sell_price_avg", "sell_price_min",
                     "sell_price_max", "sell_sold"],
        )
        t = t.filter(pc.is_in(t["item_id"], value_set=wanted))
        if not t.num_rows:
            continue
        rows = zip(
            t["date"].to_pylist(),
            t["item_id"].to_pylist(),
            t["sell_price_avg"].to_pylist(),
            t["sell_price_min"].to_pylist(),
            t["sell_price_max"].to_pylist(),
            t["sell_sold"].to_pylist(),
        )
        for ts, item_id, avg, lo, hi, sold in rows:
            price = cycles.daily_price(avg, lo, hi)
            if price is None or price <= 0:
                continue
            day = ts.date().isoformat()
            series.setdefault(item_id, {})[day] = price
            if last is None or day > last:
                last = day
            if sold is not None and day >= flow_floor:
                flows.setdefault(item_id, {})[day] = sold
    return series, flows, date.fromisoformat(last) if last else None


def decompose(daily):
    """STL over the weekly log-price series; (profile52, strength) or None.

    Restricted to the last STL_YEARS so one long-dead regime cannot shape
    the profile. Short gaps are forward-filled, longer internal gaps
    interpolated; items needing too much fill are dropped rather than
    decomposed against invented data.
    """
    import numpy as np
    import pandas as pd
    from statsmodels.tsa.seasonal import STL

    s = pd.Series(daily)
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=STL_YEARS * 365)]
    if (s.index[-1] - s.index[0]).days < MIN_HISTORY_DAYS:
        return None
    weekly = np.log(s).resample("W-MON").mean()
    filled = weekly.ffill(limit=MAX_FFILL_WEEKS)
    filled = filled[filled.first_valid_index():filled.last_valid_index()]
    if len(filled) < 3 * 52:
        return None
    if filled.isna().mean() > MAX_INTERP_FRAC:
        return None
    full = filled.interpolate()
    res = STL(full.to_numpy(), period=52, robust=True).fit()
    strength = windows.strength(list(res.seasonal), list(res.resid))
    idx = full.index
    recent = idx >= idx[-1] - pd.Timedelta(days=3 * 365)
    wks = [min((ts.dayofyear - 1) // 7, 51) for ts in idx[recent]]
    profile = windows.weekly_profile(wks, list(res.seasonal[recent]))
    return profile, strength


def median_flow(flow, d0, d1):
    """Median daily units sold inside [d0, d1], or None with no observations."""
    vals = [v for d, v in flow.items() if d0.isoformat() <= d <= d1.isoformat()]
    return round(median(vals)) if vals else None


def build_pick(item_id, meta, daily, flow, profile, strength, through):
    """Assemble one scored pick with its full per-cycle evidence, or None."""
    if windows.amplitude(profile) < season_score.MIN_AMPLITUDE:
        return None
    buy_wk = windows.trough_span(profile)
    sell_wk = windows.peak_span(profile)
    hold = windows.hold_weeks(buy_wk, sell_wk)
    if not hold:
        return None
    buy_doy = windows.span_to_doy(buy_wk)
    sell_doy = windows.span_to_doy(sell_wk)
    event = events.overlapping_event(buy_doy) or events.overlapping_event(sell_doy)
    first_year = int(min(daily)[:4])
    wins = cycles.cycle_windows(
        range(first_year, through.year + 1), buy_doy, sell_doy, event_name=event
    )
    cyc = cycles.cycle_returns(daily, wins, through=through)
    sc = season_score.score(cyc, strength)
    if sc is None:
        return None
    rets = [c["ret"] for c in cyc]
    med_buy = median(c["buy"] for c in cyc[-3:])
    b0, b1, s0, s1 = wins[cyc[-1]["year"]]
    return {
        "id": item_id,
        "name": meta["name"],
        "rarity": meta["rarity"],
        "event": event,
        "buy_window": season_score.fmt_window(*cycles.window_dates(2025, buy_doy)),
        "sell_window": season_score.fmt_window(*cycles.window_dates(2025, sell_doy)),
        "buy_doy": list(buy_doy),
        "sell_doy": list(sell_doy),
        "hold_days": hold * 7,
        "days_to_buy": season_score.days_to_buy(through, buy_doy),
        "cycles": cyc,
        "n_cycles": len(cyc),
        "med_ret": round(median(rets), 3),
        "hit_rate": round(cycles.hit_rate(cyc), 2),
        "worst_ret": min(rets),
        "last_ret": cyc[-1]["ret"],
        "strength": round(strength, 2),
        "cur_price": meta["cur_price"],
        "entry_premium": round(meta["cur_price"] / med_buy - 1, 3),
        "window_flow": median_flow(flow, b0, s1),
        "buy_window_flow": median_flow(flow, b0, b1),
        "sell_window_flow": median_flow(flow, s0, s1),
        "confidence": season_score.confidence(cyc),
        "score": round(sc, 4),
    }


def main():
    from collector import dw2
    from collector.s3store import Store

    max_candidates = MAX_CANDIDATES
    if len(sys.argv) > 1 and sys.argv[1]:
        max_candidates = int(sys.argv[1])

    store = Store(os.environ["S3_BUCKET"])
    snap = dw2.fetch_snapshot()
    cands = prefilter(snap, max_candidates)
    print(f"{len(cands)} candidates of {len(snap)} scanned", flush=True)

    series, flows, through = load_series(store, set(cands))
    if not series or through is None:
        raise SystemExit(f"no history under {HISTORY_PREFIX}, run the backfill first")
    print(f"history through {through} for {len(series)} items", flush=True)

    picks, decomposed = [], 0
    for item_id, meta in cands.items():
        daily = series.get(item_id)
        if not daily:
            continue
        result = decompose(daily)
        if result is None:
            continue
        decomposed += 1
        pick = build_pick(
            item_id, meta, daily, flows.get(item_id, {}), result[0], result[1], through
        )
        if pick:
            picks.append(pick)

    picks = season_score.rank(picks)
    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_through": through.isoformat(),
        "items_considered": len(cands),
        "items_decomposed": decomposed,
        "params": {
            "period_weeks": 52,
            "tax": cycles.TAX,
            "min_cycles": season_score.MIN_CYCLES,
            "min_median_ret": season_score.MIN_MEDIAN_RET,
            "min_hit_rate": season_score.MIN_HIT_RATE,
            "min_strength": season_score.MIN_STRENGTH,
        },
        "picks": picks,
    }
    store.put_json_gz(SEASON_KEY, artifact)
    print(
        f"{len(picks)} picks from {decomposed} decomposed "
        f"-> s3://{store.bucket}/{SEASON_KEY}"
    )
    for f in actions.unconfirmed_soon(date.today()):
        print(
            f"warning: {f['name']} expected around {f['start']} but this "
            "year's dates are unconfirmed; update season/events.py"
        )


if __name__ == "__main__":
    main()
