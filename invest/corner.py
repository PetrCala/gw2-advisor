"""Corner screen (M6.e part 1, docs/INVESTING.md strategy S6): buy the sell
book out to a price, relist above it, and only where the exit is designed
first.

Usage:
    python -m invest.corner    # needs S3_BUCKET; no GW2_API_KEY required

Two stages, so the whole screen costs one dw2 snapshot mirror call, one
small scoped gw2api.fetch_prices and exactly one gw2api.fetch_listings page:

- prefilter() runs free over the widened snapshot and keeps rows that could
  physically be cornered at all: priced above a gold, not pinned at the
  vendor floor, a book small enough to swallow, enough observed monthly
  sales for the demand rate to be an estimate rather than a guess, and a
  book our own capture share could unload inside the 90-day horizon. Ranked
  by the race term and truncated at UNIVERSE_CAP.
- plan() walks each survivor's real ladder against the S7 ceiling, scores
  it, and either returns a scored pick with an unload schedule or a
  rejection carrying the reason, never both.

THE SCORE. The design doc's "days of demand the removed book represents
divided by days of refill" is (Q/D) / (Q/R) = R/D: the book size cancels and
the ratio reads backwards (higher is worse). Implemented here inverted, with
book size moved to where it belongs, which is a hard feasibility gate rather
than a knob:

    race  = min(refill_days / unload_days, RACE_CAP)   # = UNLOAD_SHARE * D / R
    mult  = relist net of tax / average cost
    score = min(race, mult)

min() is what "bounded by ceiling headroom" means concretely, and it puts
break-even at 1.0 on both axes: a pick has to beat re-supply AND leave room
under the ceiling after tax. Both halves are published as their own columns
so the reader always sees which one bound the score.

WHY THE CEILING IS NOT OPTIONAL, and why this module fails closed without
one. Walk a thin ladder to whatever maximises net and the top of the book,
which is full of fantasy asks, becomes a plan with a gold figure attached: a
naive run on live data relisted Prosperity Dye (trades at 1.74g) at 163g.
The 12-month traded high is a statistic of the LOWEST ask over the trailing
year, not of any listing resting in the book, so it bounds the fantasy even
where no structural route exists. With no structural ceiling AND no traded
high, the row is rejected as "no ceiling evidence" rather than scored off
the ladder's own top. A broken invest.bounds parse therefore produces an
empty table, never a four-figure plan on an item that trades for a few gold.
A structural ceiling of zero copper is a BINDING ceiling for the same
reason, never a missing one: the archive really does hold recipes whose
whole non-self ingredient list is catalysts, and reading that as absence
would swap the strictest bound available for the loosest one.

MISSING EVIDENCE IS NOT GOOD NEWS, the same rule applied to the other half
of the score. An item where the window observed no new listings at all has
no re-supply rate; flooring the divisor would hand it the maximum race and
so waive the race half of min(race, mult) exactly where nothing was
measured. Those rows are rejected, not floored, and land in the near-miss
list with their reason.

WHAT THIS SCREEN CANNOT SEE. Demand elasticity, first: every plan assumes
the observed demand survives at the raised price, and it will not. And
catalysts: the one well-documented winning corner in this game's record
(Mini Kasmeer, 2015) rested on a datamined catalyst, and this screen would
reject it today on unload days. S6 finds structurally weak books; S2's
thesis registry is where a human supplies a catalyst. Neither is sufficient
alone and the page says so.

PRIVACY. Swallow cost, average cost, relist and ceiling are market data
about a recommended trade, published in gold exactly like invest.luxury and
invest.craft already publish theirs. Nothing bankroll-derived is emitted:
no per-corner capital figure and no within-cap flag, because either one
published beside the plan's public cost would bracket a share of BANKROLL_G
and tighten by the day. This module deliberately does not import
invest.bankroll and its workflow step deliberately does not receive
BANKROLL_G; the page tells the reader to check the swallow cost against
their own per-corner cap instead.
"""

import math

from features import book
from invest import bounds, radar

CORNER_KEY = "invest/corner/latest.json.gz"
BOOKS_PREFIX = "invest/corner/books/"
# The one thing no aggregate field can give us is refill BELOW a given price.
# Accumulating the day's candidate ladders is the only way it ever becomes
# measurable; ~42 KiB gzipped a day is nothing against the 25GB bucket quota.
BOOK_HISTORY_DAYS = 30

STRATEGY = "corners"  # matches invest.bankroll.CAPS's key; checked by a test

TAX = 0.15  # trading post cut; kept in sync with scorers.flip.TAX by a test
# Numerically scorers.flip.CAPTURE, and deliberately not named CAPTURE: in a
# flip 0.25 is our share of a contested flow, in a corner we are the only
# seller by construction, so here it is a demand haircut for the raised
# price. A test keeps the number in sync and carries this same comment.
UNLOAD_SHARE = 0.25

MIN_SELL_PRICE = 10_000  # 1g; below this the copper tick and the 15% cut eat the trade
# 300g of book, S6's "swallow cost under a few hundred gold". Enforced
# TWICE, and the second time is the one that binds: stage 1 tests it against
# the touch, which is a strict lower bound on the real ladder cost, and
# plan() re-tests the walked cost. A pick needs a ceiling well above spot,
# which on a thin book means a steep ladder, so the real cost routinely runs
# several times the touch-priced estimate.
MAX_SWALLOW = 3_000_000
MIN_SALES_1M = 20  # evidence floor: under ~20 sales/month the demand rate is not an estimate
MAX_EXIT_DAYS = 90  # docs/INVESTING.md risk rule 2's vault/corner exit horizon
UNIVERSE_CAP = 200  # exactly one gw2api.fetch_listings page (invest.luxury.MAX_UNIVERSE's reasoning)
REFILL_FLOOR = 1 / 30  # "fewer than one unit a month", the smallest the window could observe
RACE_CAP = 10.0  # a very thin measured refill ranks well, never infinitely well
# The buy book has to absorb at least this share of the lot before a
# bail-out percentage means anything. Below it the visible depth is a
# rounding error against the lot and the number would describe a trade
# nobody can make.
BAIL_MIN_COVERAGE = 0.25
# A relist may not exceed the 12-month traded high. Hardcoded at 1.0 on
# purpose: it is the one knob that manufactures picks (a live run produced 1
# scored pick at 1.0, and all 200 candidates passing at 2.0). Any premium
# above it belongs in a thesis-registry entry naming a catalyst, signed off
# by a human, never in a constant here.
CEILING_PREMIUM = 1.0
# S6's "never corner anything actively farmable at less than 2x current
# price", re-keyed onto whether a producible route exists at all. It has to
# be re-keyed: invest.tags.FARMABLE_CLASSES (t6/festival/staples) covers
# almost none of the real corner universe, which is dyes, skins, stat-gear
# exotics and recipes, every one of which classifies as "other".
PRODUCIBLE_MIN_MULT = 2.0
EMPIRICAL_MIN_MULT = 1.20  # no recipe route: the traded high is the only bound
MIN_SCORE = 1.0  # break-even on both axes of min(race, mult)
MAX_CONCURRENT_CORNERS = 2  # with PER_CORNER_CAP, fits inside bankroll.CAPS["corners"]
# S6's text says 5-10% of bankroll per corner while the rung cap is 10%
# total. Kept here as strategy mechanics rather than in invest.bankroll,
# which is allocation policy; nothing sizes real gold against it yet.
PER_CORNER_CAP = 0.05

PRODUCIBLE_SOURCES = frozenset({"forge", "merchant", "double-click"})


def _corner_fields(dw2):
    """The widened dw2 field list this screen and report.track both need.

    invest.radar's list (which already carries 1d_sell_listed, the
    re-supply column the paper tracker measures against) plus the rolling
    listed counts the refill rate blends and the 12-month traded high the
    empirical ceiling reads. Same endpoint, no extra request.
    """
    return radar._radar_fields(dw2) + ",7d_sell_listed,1m_sell_listed,12m_sell_price_max"


# --- rates ----------------------------------------------------------------


def _blend(w7, m1):
    """The invest.luxury.velocity blend: the 7-day and 1-month rolling
    counts as per-day rates, averaged, so one heavy or dead day cannot swing
    a thin estimate on its own. Same shape, different sides of the book."""
    return ((w7 or 0) / 7 + (m1 or 0) / 30) / 2


def demand(it):
    """Units/day bought off sell listings: the flow our relist sells into."""
    return _blend(it.get("7d_sell_sold"), it.get("1m_sell_sold"))


def refill(it):
    """Units/day of new sell listings: the flow racing our unload.

    Floored at REFILL_FLOOR so a rate rounded down out of two integer
    counters cannot divide the race into the sky. It is a guard on thin
    arithmetic, not a stand-in for missing evidence: a row that observed no
    listings at all is rejected by plan() rather than floored, because the
    floor would hand it the maximum race and waive the whole race half of
    the score for exactly the rows with nothing behind it.
    """
    return max(_blend(it.get("7d_sell_listed"), it.get("1m_sell_listed")), REFILL_FLOOR)


def refill_unobserved(it):
    """True when the window saw no new listings at all, so there is no
    re-supply rate to race against and plan() refuses to score the row."""
    return _blend(it.get("7d_sell_listed"), it.get("1m_sell_listed")) <= 0


def race_term(it):
    """min(UNLOAD_SHARE x demand / refill, RACE_CAP): how much faster we can
    unload than the book refills. Above 1.0 we are winning that race."""
    return min(UNLOAD_SHARE * demand(it) / refill(it), RACE_CAP)


# --- stage 1: the free prefilter ------------------------------------------


def is_candidate(it):
    """Whether one widened snapshot row could physically be cornered at all."""
    if not it.get("id"):
        return False
    sell = it.get("sell_price") or 0
    if sell < MIN_SELL_PRICE:
        return False
    if bounds.at_vendor_floor(sell, it.get("vendor_value")):
        return False
    depth = it.get("sell_quantity") or 0
    if not depth:
        return False
    # Every unit valued at the touch, so this is a strict lower bound on the
    # real ladder cost: permissive here on purpose, stage 2 recomputes the
    # real cost off the ladder and drops whatever no longer fits.
    if sell * depth > MAX_SWALLOW:
        return False
    if (it.get("1m_sell_sold") or 0) < MIN_SALES_1M:
        return False
    d = demand(it)
    if d <= 0:
        return False
    return depth / (UNLOAD_SHARE * d) <= MAX_EXIT_DAYS


def prefilter(snapshot, cap=UNIVERSE_CAP):
    """Candidate rows, best race first, truncated to one listings page."""
    rows = [it for it in snapshot if is_candidate(it)]
    rows.sort(key=race_term, reverse=True)
    return rows[:cap]


# --- stage 2: the ladder walk ---------------------------------------------


def walk(levels, break_even):
    """(qty, cost) sweeping every sell level priced strictly under
    `break_even`, cheapest first, and no further.

    With a hard relist cap the argmax is interior: a unit bought at or above
    break_even can never be resold profitably under the ceiling, so the
    sweep depth is not a search, it is one line.
    """
    qty = cost = 0
    for price, level_qty in levels or ():
        if price >= break_even:
            break
        qty += level_qty
        cost += price * level_qty
    return qty, cost


def _relist_at(levels, break_even, ceiling_copper):
    """One copper under whichever binds first: the cheapest listing we chose
    not to buy, or the ceiling itself."""
    above = next((p for p, _ in levels or () if p >= break_even), None)
    cap = ceiling_copper if above is None else min(above, ceiling_copper)
    return cap - 1


def unload_schedule(qty, demand_per_day, relist_at):
    """The exit, computed before any buy, per S6's own instruction.

    per_day is UNLOAD_SHARE of the item's own demand, the same
    capture-share-of-flow convention as season.actions.suggested_qty, then
    ceiled into weekly tranches.

    abort_qty is one week of our own planned sales, and it is a threshold on
    a NET quantity, not on a cumulative one: report.track.tick_corner trips
    it when re-supply since open exceeds our own sales since open by this
    much. Compared against gross re-supply instead it would fire purely as a
    function of elapsed time (any refill rate reaches any stock eventually),
    so a pick would abort on the calendar rather than on the moat breaking.
    Net, it fires exactly when new listings are outrunning our unload, which
    is what "the moat is gone" means.
    """
    per_day = UNLOAD_SHARE * demand_per_day
    if per_day <= 0 or qty <= 0:
        return None
    weeks, left, week = [], qty, 1
    while left > 0 and week <= 60:
        units = min(left, per_day * 7)
        weeks.append({"week": week, "units": int(round(units))})
        left -= units
        week += 1
    return {
        "qty": qty,
        "relist_at": relist_at,
        "per_day": round(per_day, 2),
        "days": math.ceil(qty / per_day),
        "weeks": weeks,
        "abort_qty": min(qty, int(round(per_day * 7))),
        "horizon_days": MAX_EXIT_DAYS,
    }


def bail_out(buys, qty, avg_cost):
    """(per-unit loss from dumping into today's buy book, share of the lot
    that book can actually absorb).

    Definition of the loss itself is season.actions.exit_pct's, re-derived
    here so this module never reaches invest.bankroll, not even transitively
    (see the privacy note in the module docstring). Shown as a bail-out
    estimate and deliberately NOT a gate, and bounded_qty is deliberately
    not applied: a thin buy book is precisely what makes a corner possible,
    so gating on it would reject every genuine candidate.

    Only the units the visible book really covers are valued.
    features.book.dump_value values everything past the deepest visible bid
    AT that bid, which is fine for its own callers and catastrophic here:
    corner candidates are selected for thin books, so the lot routinely
    exceeds total bid depth by two orders of magnitude, and one lucky unit
    resting above cost would otherwise print a POSITIVE bail-out for a lot
    the buy side cannot absorb at all. Under BAIL_MIN_COVERAGE of the lot no
    percentage is returned at all, only the coverage, so the page can say
    the book cannot cover it rather than quote a friendly number.

    It is an estimate, never a floor, even at full coverage: the 2021 mystic
    coin wall dropped 22% overnight when its owner was banned
    (docs/RESEARCH.md), which is what reading buy-side depth as guaranteed
    liquidity costs.
    """
    if not buys or not qty or not avg_cost:
        return None, None
    depth = sum(q for _, q in buys if q)
    covered = min(qty, depth)
    if covered <= 0:
        return None, 0.0
    coverage = round(covered / qty, 3)
    if covered / qty < BAIL_MIN_COVERAGE:
        return None, coverage
    net = book.dump_value(buys, covered) * (1 - TAX)
    return round((net / covered - avg_cost) / avg_cost, 3), coverage


def _reject(it, reason):
    return {
        "id": it.get("id"),
        "name": it.get("name") or f"item {it.get('id')}",
        "reason": reason,
    }


def plan(it, item_book, ceiling_copper, ceiling_source):
    """One scored corner pick, or {"id", "name", "reason"} and nothing else.

    A screen that returns a great-looking score on missing data is worse
    than one that returns nothing, so every degenerate case leaves through
    the reason branch and never carries a number, a placeholder, or a
    half-filled pick dict.
    """
    sell = it.get("sell_price") or 0
    high12 = it.get("12m_sell_price_max") or 0
    empirical = int(high12 * CEILING_PREMIUM) if high12 else None
    # `is not None`, not truthiness. A structural ceiling of ZERO is the
    # strictest bound there is ("the alternative route costs nothing we can
    # price"), and 24 real archive recipes produce one, every ingredient
    # being a catalyst. Dropping it as falsy would silently promote the
    # LOOSER traded high in its place, which inverts the whole fail-closed
    # design: the one number that caps the relist would be replaced by a
    # weaker one exactly where the structural evidence is strongest.
    options = [c for c in (ceiling_copper, empirical) if c is not None]
    if not options:
        return _reject(it, "no ceiling evidence")
    cap = min(options)
    # Which bound actually binds is what the reader needs to know: a
    # structural moat survives a patch differently from a historical one.
    source = (
        ceiling_source
        if (ceiling_copper is not None and cap == ceiling_copper)
        else "traded high"
    )
    if cap <= sell:
        return _reject(it, "already above the alternative route")

    d = demand(it)
    if d <= 0:
        # Unreachable through main() (is_candidate filters it first) and
        # guarded anyway: plan() is a public entry point, and the promise in
        # the docstring above is that no degenerate input leaves through
        # anything but the reason branch.
        return _reject(it, "no measurable demand")
    if refill_unobserved(it):
        # No listings observed in the whole window means there is no
        # re-supply rate, not a tiny one. Scoring it would floor the divisor
        # and hand the row the maximum race, waiving the race half of the
        # score and its MIN_SCORE gate for precisely the rows with no
        # evidence behind them, which would then outrank measured ones.
        return _reject(it, "no observed re-supply to race against")

    levels = (item_book or {}).get("sells") or []
    break_even = cap * (1 - TAX)
    qty, cost = walk(levels, break_even)
    if qty <= 0:
        return _reject(it, "already at the ceiling")
    if cost > MAX_SWALLOW:
        # Stage 1 bounded this at the touch; here it is the real ladder.
        return _reject(
            it,
            f"swallow cost {cost / 10_000:,.0f}g over the "
            f"{MAX_SWALLOW / 10_000:,.0f}g cap",
        )

    avg = cost / qty
    relist = _relist_at(levels, break_even, cap)
    margin = relist * (1 - TAX) - avg
    if margin <= 0:
        return _reject(it, "no room under the ceiling")

    unload_days = qty / (UNLOAD_SHARE * d)
    if unload_days > MAX_EXIT_DAYS:
        # Not sized down. Every other strategy shrinks a lot the book cannot
        # absorb; a corner cannot, because leaving cheap listings behind
        # means nothing was cornered and they undercut the relist at once.
        return _reject(it, f"book is {unload_days:.0f} days of unload")

    mult = relist * (1 - TAX) / avg
    # refill_days / unload_days, which is algebraically race_term's
    # UNLOAD_SHARE x demand / refill: the lot size cancels, which is exactly
    # why it is a gate above and not part of the score.
    race = race_term(it)
    score = min(race, mult)
    # A producible route existing at all is what makes an item farmable, so
    # the 2x rule keys on that rather than on which bound happened to bind.
    min_mult = PRODUCIBLE_MIN_MULT if ceiling_source in PRODUCIBLE_SOURCES else EMPIRICAL_MIN_MULT
    if mult < min_mult:
        return _reject(it, f"multiple {mult:.2f}x under the {min_mult:.2f}x floor for a {source} ceiling")
    if score < MIN_SCORE:
        return _reject(it, f"score {score:.2f} under the {MIN_SCORE:.2f} minimum")

    unload = unload_schedule(qty, d, int(relist))
    bail_pct, bail_coverage = bail_out((item_book or {}).get("buys"), qty, avg)
    return {
        "id": it["id"],
        "name": it.get("name") or f"item {it['id']}",
        "rarity": it.get("rarity") or "",
        "strategy": STRATEGY,
        "sell_price": sell,
        "qty": qty,
        "cost": int(cost),
        "avg_cost": int(avg),
        "relist_at": int(relist),
        "ceiling": cap,
        "ceiling_source": source,
        "margin": int(margin),
        "mult": round(mult, 3),
        "demand_per_day": round(d, 2),
        "refill_per_day": round(refill(it), 3),
        "race": round(race, 3),
        "score": round(score, 3),
        "unload_days": round(unload_days, 1),
        "bail_out_pct": bail_pct,
        "bail_out_coverage": bail_coverage,
        "unload": unload,
    }


def build_picks(candidates, books, ceilings):
    """(picks best score first, rejected rows carrying their reason).

    `ceilings` is {item_id: (copper, source)} from invest.bounds.ceiling; a
    missing entry is treated as no structural route, which leaves the
    traded high as the only bound and, absent that too, a rejection.
    """
    picks, rejected = [], []
    for it in candidates:
        item_id = it.get("id")
        ceiling_copper, ceiling_source = ceilings.get(item_id) or (None, None)
        out = plan(it, books.get(item_id), ceiling_copper, ceiling_source)
        (rejected if "reason" in out else picks).append(out)
    picks.sort(key=lambda p: p["score"], reverse=True)
    return picks, rejected


# --- entry point ----------------------------------------------------------


def main():
    import os
    from datetime import datetime, timezone

    from collector import dw2, gw2api
    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    snap = dw2.fetch_snapshot(fields=_corner_fields(dw2))
    candidates = prefilter(snap)
    ids = [it["id"] for it in candidates]

    graph = bounds.load_graph()
    price_ids = sorted(set(bounds.ingredient_ids(ids, graph)) | set(ids))
    prices = gw2api.fetch_prices(price_ids) if price_ids else {}
    books = gw2api.fetch_listings(ids) if ids else {}

    today = datetime.now(timezone.utc).date()
    # One dated object per day under its own prefix, the same shape as
    # invest.luxury's book collection; this is the only path to a
    # price-conditional refill rate, which no aggregate field carries.
    store.put_json_gz(
        f"{BOOKS_PREFIX}{today.isoformat()}.json.gz",
        {"date": today.isoformat(), "books": {str(k): v for k, v in books.items()}},
    )
    history_days = len(store.list_keys(BOOKS_PREFIX)[-BOOK_HISTORY_DAYS:])

    ceilings = {i: bounds.ceiling(i, prices, graph) for i in ids}
    picks, rejected = build_picks(candidates, books, ceilings)
    structural = sum(1 for p in picks if p["ceiling_source"] != "traded high")

    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": today.isoformat(),
        "candidates": len(candidates),
        "recipes": graph["n_recipes"],
        "books_fetched": len(books),
        "book_history_days": history_days,
        "picks": picks,
        "rejected": rejected,
    }
    store.put_json_gz(CORNER_KEY, artifact)
    # Counts and ratios only: nothing here is derived from the user's own
    # holdings or from BANKROLL_G, and this module never reads either.
    print(
        f"corner screen {today.isoformat()}: {len(candidates)} candidates, "
        f"{graph['n_recipes']} recipes in the conversion graph, "
        f"{len(picks)} actionable ({structural} on a structural ceiling), "
        f"{len(rejected)} screened out -> s3://{store.bucket}/{CORNER_KEY}"
    )


if __name__ == "__main__":
    main()
