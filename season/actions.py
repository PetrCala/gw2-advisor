"""Timing verdicts and the action queue over seasonal picks.

The pattern score deliberately ignores today's price and calendar, so the
top of the score ranking can be a bad buy today. This module answers the
daily questions instead: buy now or wait, at what limit price, how many
units, and which festival runs next. Pure stdlib, computed at report build
time so the answers track the fresh snapshot rather than the weekly
artifact.

The pay-up-to ceiling is the price at which selling at the recent median
sell-window price still nets RETURN_HURDLE after fees; a current price
above it means the cheap entry is gone regardless of how good the pattern
looks. Recent medians use the last RECENT_CYCLES cycles, matching the
entry-premium basis in season/compute.py.
"""

from datetime import date, timedelta
from statistics import median

from season import events
from season.cycles import TAX
from season.score import days_to_buy

RETURN_HURDLE = 0.20  # net return demanded at the recent median sell price
RECENT_CYCLES = 3  # price basis; old cycles carry pre-inflation prices
CAPTURE = 0.25  # share of window flow we absorb, as in scorers.flip
SEASON_CAPITAL = 500_000  # copper per pick; capital sits locked for months
OPENS_SOON_DAYS = 30
UNCONFIRMED_WARN_DAYS = 60

BUCKETS = ("buy_now", "opens_soon", "sell_active", "dormant")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt(d):
    return f"{_MONTHS[d.month - 1]} {d.day:02d}"


def _doy(d):
    return min(d.timetuple().tm_yday, 365)


def in_span(today, span_doy):
    """Wrap-aware membership of today in a 1-based day-of-year span."""
    a, b = span_doy
    t = _doy(today)
    return a <= t <= b if a <= b else t >= a or t <= b


def span_dates(today, span_doy):
    """Concrete dates of the span occurrence that is current or next.

    Inside the span the returned occurrence contains today (a wrapped span
    may have started last year); outside it is the next upcoming one.
    """
    a, b = span_doy
    t = _doy(today)
    length = (b - a) % 365
    if in_span(today, span_doy):
        start_year = today.year if t >= a else today.year - 1
    else:
        start_year = today.year if a > t else today.year + 1
    start = date(start_year, 1, 1) + timedelta(days=a - 1)
    return start, start + timedelta(days=length)


def recent_median(cyc, key):
    vals = [c[key] for c in cyc[-RECENT_CYCLES:]]
    return median(vals) if vals else None


def limit_price(cyc):
    """Pay-up-to ceiling in copper; doubles as the in-game limit order."""
    med_sell = recent_median(cyc, "sell")
    if not med_sell:
        return None
    return int(med_sell * (1 - TAX) / (1 + RETURN_HURDLE))


def entry_premium(cur_price, cyc):
    """Current price relative to the recent median buy-window price."""
    med_buy = recent_median(cyc, "buy")
    if not med_buy or not cur_price:
        return None
    return round(cur_price / med_buy - 1, 3)


def suggested_qty(flow, days_remaining, limit):
    """Units to buy: our flow share over the window, capped by capital."""
    if not flow or not limit or not days_remaining:
        return None
    return int(min(CAPTURE * flow * days_remaining, SEASON_CAPITAL // limit))


def enrich(pick, today, fresh_price=None):
    """Recompute calendar and price fields, attach bucket and verdict.

    Mutates and returns the pick. The artifact's stored days_to_buy,
    entry_premium and cur_price go up to a week stale between season runs;
    everything here is recomputed from buy_doy/sell_doy, the cycles
    evidence and the fresh snapshot price (falling back to the artifact
    price when the snapshot lacks the item).
    """
    buy_doy = tuple(pick["buy_doy"])
    sell_doy = tuple(pick["sell_doy"])
    cyc = pick["cycles"]
    price = fresh_price or pick.get("cur_price")
    b0, b1 = span_dates(today, buy_doy)
    s0, s1 = span_dates(today, sell_doy)
    limit = limit_price(cyc)
    prem = entry_premium(price, cyc)
    dtb = days_to_buy(today, buy_doy)
    in_buy = dtb == 0
    left = (b1 - today).days if in_buy else None
    window_len = (buy_doy[1] - buy_doy[0]) % 365 + 1
    qty = suggested_qty(
        pick.get("window_flow"), left if in_buy else window_len, limit
    )

    if in_buy and limit is not None and price is not None and price <= limit:
        bucket, verdict = "buy_now", f"buy now, closes {_fmt(b1)}"
    elif in_buy:
        over = f"{prem:+.0%} over trough" if prem and prem > 0 else "over the ceiling"
        bucket, verdict = "dormant", f"too late: {over}"
    elif in_span(today, sell_doy):
        bucket, verdict = "sell_active", f"sell window, closes {_fmt(s1)}"
    elif dtb <= OPENS_SOON_DAYS:
        bucket, verdict = "opens_soon", f"opens {_fmt(b0)} ({dtb}d)"
    else:
        bucket, verdict = "dormant", f"opens {_fmt(b0)} ({dtb}d)"

    rank = BUCKETS.index(bucket)
    pick.update(
        cur_price=price,
        days_to_buy=dtb,
        days_left=left,
        entry_premium=prem,
        limit_price=limit,
        suggested_qty=qty,
        buy_opens=b0.isoformat(),
        buy_closes=b1.isoformat(),
        sell_opens=s0.isoformat(),
        sell_closes=s1.isoformat(),
        bucket=bucket,
        verdict=verdict,
        act=round((len(BUCKETS) - 1 - rank) * 10 + pick["score"], 3),
    )
    return pick


def _next_run(name, today):
    """Next (start, end) of a festival; estimated when unannounced."""
    for year in (today.year - 1, today.year, today.year + 1):
        run = events.year_span(name, year)
        if run and run[1] >= today:
            return run, False
    return span_dates(today, events.typical_span(name)), True


def next_festivals(today, picks=()):
    """Every festival's next run with its linked pick count, soonest first."""
    counts = {}
    for p in picks:
        if p.get("event"):
            counts[p["event"]] = counts.get(p["event"], 0) + 1
    out = []
    for name in events.EVENTS:
        (start, end), estimated = _next_run(name, today)
        out.append(
            {
                "name": name,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "estimated": estimated,
                "picks": counts.get(name, 0),
            }
        )
    out.sort(key=lambda f: f["start"])
    return out


def unconfirmed_soon(today, horizon_days=UNCONFIRMED_WARN_DAYS):
    """Festivals starting within the horizon on estimated dates only.

    The season workflow prints these so the events calendar gets its
    once-a-year update before the window matters.
    """
    cutoff = (today + timedelta(days=horizon_days)).isoformat()
    return [
        f
        for f in next_festivals(today)
        if f["estimated"] and f["start"] <= cutoff
    ]
