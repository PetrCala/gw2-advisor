"""Timing verdicts and the action queue over seasonal picks.

The pattern score deliberately ignores today's price and calendar, so the
top of the score ranking can be a bad buy today. This module answers the
daily questions instead: buy now or wait, at what limit price, how many
units, and which festival runs next. Pure stdlib, computed at report build
time so the answers track the fresh snapshot rather than the weekly
artifact.

Windows of event-linked picks follow the festival's announced run for the
year, the same anchoring past cycles get, and fall back to the typical
day-of-year span while the year's dates are unconfirmed.

The pay-up-to ceiling is the price at which selling at the recent median
sell-window price still nets RETURN_HURDLE after fees; a current price
above it means the cheap entry is gone regardless of how good the pattern
looks. Recent medians use the last RECENT_CYCLES cycles, matching the
entry-premium basis in season/compute.py.

The exit floor is the mirror check on the way out: EXIT_FLOOR_MIN bounds
how much dumping the suggested lot into today's buy book could lose, and
sizing shrinks the lot to whatever the book actually absorbs within it. A
book too thin for any size to clear the floor has no real bail-out, so
apply_exit_floor flags it and demotes a buy_now pick out of the queue.
"""

from datetime import date, timedelta
from statistics import median

from features import book
from season import cycles, events
from season.cycles import TAX

RETURN_HURDLE = 0.20  # net return demanded at the recent median sell price
RECENT_CYCLES = 3  # price basis; old cycles carry pre-inflation prices
CAPTURE = 0.25  # share of window flow we absorb, as in scorers.flip
SEASON_CAPITAL = 500_000  # copper per pick; capital sits locked for months
OPENS_SOON_DAYS = 30
UNCONFIRMED_WARN_DAYS = 60

# A dumped lot losing more than this is still tolerable: sizing caps the
# suggested qty to whatever the live buy book can absorb within it. A pick
# that can't clear the floor at any size (a thin or gapped book right under
# the touch, not a sizing problem) gets flagged exit risk and, if it was
# buy_now, demoted out of the buy queue instead of read as actionable.
EXIT_FLOOR_MIN = -0.40

BUCKETS = ("buy_now", "opens_soon", "sell_active", "dormant")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt(d, today):
    """'Mon DD', with the year appended only when it differs from today's."""
    s = f"{_MONTHS[d.month - 1]} {d.day:02d}"
    return s if d.year == today.year else f"{s}, {d.year}"


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


def anchored_span(today, span_doy, event=None):
    """(start, end, anchored) of the current or next occurrence of a span.

    Event-linked spans follow the announced run of their festival, shifted
    by the same offset cycles.cycle_windows applies to past years, so an
    early Halloween moves its buy window with it. Years the festival has
    not been announced for fall back to the typical day-of-year span.
    """
    if event:
        anchor = events.typical_span(event)[0]
        for year in (today.year - 1, today.year, today.year + 1):
            run = events.year_span(event, year)
            if run is None:
                continue
            start, end = cycles.anchor_dates(span_doy, anchor, run[0])
            if end >= today:
                return start, end, True
    return span_dates(today, span_doy) + (False,)


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


def sizing_flow(pick, side, fresh_flow=None):
    """(flow per day, estimated) for one side's window.

    Fill counts only start around 2019 and some windows saw no observed
    flow at all, so a missing window flow falls back to the item's recent
    overall daily flow. That fallback ignores the season entirely, hence
    the estimated flag.
    """
    flow = pick.get(f"{side}_window_flow")
    if not flow and side == "buy":
        flow = pick.get("window_flow")  # pre-split artifacts carry one number
    if flow:
        return flow, False
    return (round(fresh_flow), True) if fresh_flow else (None, False)


def suggested_qty(flow, days_remaining, limit, sell_flow=None, sell_days=None):
    """Units to buy: our flow share over the window, capped by capital.

    Exit liquidity bounds a hold as hard as entry liquidity does, so the
    sell window gets the same capture cap when its flow is known.
    """
    if not flow or not limit or not days_remaining:
        return None
    caps = [CAPTURE * flow * days_remaining, SEASON_CAPITAL // limit]
    if sell_flow and sell_days:
        caps.append(CAPTURE * sell_flow * sell_days)
    return int(min(caps))


def exit_pct(buys, qty, price):
    """Per-unit loss from dumping qty into today's buy book, or None.

    worst_ret says how bad past cycles got; this says how bad today is if
    the peak never comes and the position has to leave at market. Same
    definition as the flip table's exit %, over the suggested lot.
    """
    if not buys or not qty or not price:
        return None
    net = book.dump_value(buys, qty) * (1 - TAX)
    return round((net / qty - price) / price, 3)


def bounded_qty(buys, qty, price, floor=EXIT_FLOOR_MIN):
    """Largest lot at or below qty whose dump loss stays above floor.

    dump_value walks the book best-price-first, so exit_pct only improves
    (or holds) as qty shrinks; that monotonicity is what makes a binary
    search here safe. Returns qty unchanged when there is nothing to check
    it against, and 0 when even a single unit breaches the floor: the book
    is too thin right under the touch for any size to bail out within it.
    """
    if not buys or not qty or not price:
        return qty
    if exit_pct(buys, qty, price) >= floor:
        return qty
    if exit_pct(buys, 1, price) < floor:
        return 0
    lo, hi = 1, qty
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if exit_pct(buys, mid, price) >= floor:
            lo = mid
        else:
            hi = mid - 1
    return lo


def apply_exit_floor(pick, buys, floor=EXIT_FLOOR_MIN):
    """Cap suggested_qty to the book and flag/demote a catastrophic exit.

    Mutates and returns pick. exit_pct always ends up describing whatever
    lot is left suggested: unchanged when the book already absorbs it above
    the floor, shrunk to the largest lot that does, or, when no lot size at
    all clears the floor, cleared to None with exit_risk set and exit_pct
    left at the original (catastrophic) reading for the flag. A buy_now pick
    with no viable exit is demoted to dormant rather than read as actionable
    today; a sell_active one just gets the flag, since there's no buy
    decision left to gate there.
    """
    price = pick.get("cur_price")
    qty = pick.get("suggested_qty")
    pick["exit_pct"] = exit_pct(buys, qty, price)
    pick["exit_risk"] = False
    if not buys or not qty or not price:
        return pick
    capped = bounded_qty(buys, qty, price, floor)
    if capped == 0:
        pick["exit_risk"] = True
        pick["suggested_qty"] = None
        if pick["bucket"] == "buy_now":
            pick["bucket"] = "dormant"
            pick["verdict"] = f"skip: exit floor {pick['exit_pct']:+.0%}, no bail-out size"
            rank = BUCKETS.index(pick["bucket"])
            pick["act"] = round((len(BUCKETS) - 1 - rank) * 10 + pick["score"], 3)
    elif capped != qty:
        pick["suggested_qty"] = capped
        pick["exit_pct"] = exit_pct(buys, capped, price)
    return pick


def enrich(pick, today, fresh_price=None, fresh_flow=None):
    """Recompute calendar and price fields, attach bucket and verdict.

    Mutates and returns the pick. The artifact's stored days_to_buy,
    entry_premium and cur_price go up to a week stale between season runs;
    everything here is recomputed from buy_doy/sell_doy anchored to the
    festival calendar, the cycles evidence and the fresh snapshot price
    (falling back to the artifact price when the snapshot lacks the item).
    fresh_flow is the item's recent overall daily flow, used for sizing
    only when a window has no observed flow of its own.
    """
    buy_doy = tuple(pick["buy_doy"])
    sell_doy = tuple(pick["sell_doy"])
    cyc = pick["cycles"]
    price = fresh_price or pick.get("cur_price")
    event = pick.get("event")
    b0, b1, _ = anchored_span(today, buy_doy, event)
    s0, s1, _ = anchored_span(today, sell_doy, event)
    limit = limit_price(cyc)
    prem = entry_premium(price, cyc)
    in_buy = b0 <= today <= b1
    dtb = 0 if in_buy else (b0 - today).days
    left = (b1 - today).days if in_buy else None
    window_len = (b1 - b0).days + 1
    flow, flow_est = sizing_flow(pick, "buy", fresh_flow)
    sell_flow, _ = sizing_flow(pick, "sell", fresh_flow)
    qty = suggested_qty(
        flow, left if in_buy else window_len, limit,
        sell_flow, (s1 - s0).days + 1,
    )

    if in_buy and limit is not None and price is not None and price <= limit:
        bucket, verdict = "buy_now", f"buy now, closes {_fmt(b1, today)}"
    elif in_buy:
        over = f"{prem:+.0%} over trough" if prem and prem > 0 else "over the ceiling"
        bucket, verdict = "dormant", f"too late: {over}"
    elif s0 <= today <= s1:
        bucket, verdict = "sell_active", f"sell window, closes {_fmt(s1, today)}"
    elif dtb <= OPENS_SOON_DAYS:
        bucket, verdict = "opens_soon", f"opens {_fmt(b0, today)} ({dtb}d)"
    else:
        bucket, verdict = "dormant", f"opens {_fmt(b0, today)} ({dtb}d)"

    rank = BUCKETS.index(bucket)
    pick.update(
        cur_price=price,
        days_to_buy=dtb,
        days_left=left,
        entry_premium=prem,
        limit_price=limit,
        suggested_qty=qty,
        flow_used=flow,
        flow_estimated=flow_est,
        buy_window=f"{_fmt(b0, today)} - {_fmt(b1, today)}",
        sell_window=f"{_fmt(s0, today)} - {_fmt(s1, today)}",
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
