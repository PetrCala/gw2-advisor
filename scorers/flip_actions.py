"""Exit prices and an at-a-glance verdict for one rescored flip.

Pure functions over a rescored pick's own numbers: no report building, no
network calls, so this is testable in isolation (tests/test_flip_actions.py).
flip.rescore() calls into this module to attach the fields below to every
pick it returns.

Exit prices
-----------
A flip's sell listing degrades over its hold: the ask can fall while the
position sits unfilled, and the in-game question is when to keep relisting
lower and when to give up and dump into the buy book instead. Two prices
answer that, both the smallest listing price whose after-fee net clears a
floor, the same shape as the margin check flip.rescore already runs:

- break_even_sell: floor is what was paid (buy_at). Below this listing the
  round trip is stuck at a loss versus cost, though it may still beat
  dumping.
- bail_price: floor is dump_net_per_unit, what dumping the lot into the buy
  book nets per unit at today's depth. Below this listing, relisting nets
  less than walking away right now, so dump instead.

Read together: list at sell_at; if the ask falls, keep relisting down to
break_even_sell; below bail_price, dump.

Verdict
-------
act_now needs all three: margin comfortably clear of the profit floor, at
least medium confidence, and a calm book (repricing under the caution rate,
exit floor not too thin). Each weak signal below is one flag; one flag is
caution, two or more (missing reprice data counts as a flag: it means the
book can't be checked at all) is skip_today.
"""

import math

ACT_MARGIN_PCT = 0.10  # comfortably over flip.MIN_MARGIN_PCT's 5% floor
REPRICE_CAUTION_PER_DAY = 75.0  # half of flip.PENNY_WAR_PER_DAY's kill rate
THIN_EXIT_PCT = -0.15  # dumping the lot loses more than this share

BUCKETS = ("act_now", "caution", "skip_today")
BUCKET_SCALE = 1_000_000_000  # copper/day; dwarfs any real ev_day


def _min_sell_price(floor, tax):
    """Smallest listing price s with int((s - 1) * (1 - tax)) >= floor.

    Same after-fee formula flip.rescore uses for margin, so a sell_at at or
    above the result clears the floor under that exact formula. math.ceil
    gives a close starting guess; the two passes below correct it against
    int()'s truncation, which float division alone can land a copper off.
    """
    s = math.ceil(floor / (1 - tax)) + 1
    while int((s - 1) * (1 - tax)) < floor:
        s += 1
    while s > 1 and int((s - 2) * (1 - tax)) >= floor:
        s -= 1
    return s


def break_even_sell(buy_at, tax):
    """Lowest sell listing that still recovers what was paid, after fees."""
    return _min_sell_price(buy_at, tax)


def bail_price(dump_net_per_unit, tax):
    """Lowest sell listing that still beats dumping the lot right now."""
    return _min_sell_price(dump_net_per_unit, tax)


def verdict(margin_pct, confidence, exit_pct, outbid_day, undercut_day):
    """(bucket, verdict text) at-a-glance for one rescored flip."""
    stale = outbid_day is None or undercut_day is None
    reprice = (outbid_day or 0) + (undercut_day or 0)

    weak_margin = margin_pct < ACT_MARGIN_PCT
    low_conf = confidence == "low"
    hot_book = not stale and reprice > REPRICE_CAUTION_PER_DAY
    thin_exit = exit_pct < THIN_EXIT_PCT

    reasons = []
    if weak_margin:
        reasons.append("margin barely clears the floor")
    if low_conf:
        reasons.append("low confidence")
    if hot_book:
        reasons.append("book is repricing fast")
    if thin_exit:
        reasons.append("exit floor is thin")
    if stale:
        reasons.append("no reprice data")

    if not reasons:
        return "act_now", "act now: healthy margin, calm book"
    if len(reasons) == 1:
        return "caution", f"caution: {reasons[0]}"
    return "skip_today", "skip today: " + ", ".join(reasons)


def act_score(bucket, ev_day):
    """Default sort key: actionable buckets first, EV/day breaks ties."""
    rank = BUCKETS.index(bucket)
    return round((len(BUCKETS) - 1 - rank) * BUCKET_SCALE + ev_day, 3)
