"""Luxury desk (M6.c, docs/INVESTING.md strategy S4): universe, velocity,
placement and sizing for the 500g+ tier.

The thesis: the luxury tier trades at wide (25-40%) spreads on 1-5 sales a
week, and the flip crowd cannot size a market where one unit ties up
thousands of gold. Buying at the bid via a patient order and relisting at
the ask nets 10-25% after tax per round trip, a pace of one or two round
trips a month per item, not per day.

Usage:
    python -m invest.luxury    # needs S3_BUCKET; no GW2_API_KEY required

Pipeline, stated plainly:

- universe(): items >=500g with nonzero weekly sales from the dw2 snapshot
  (collector/dw2.fetch_snapshot), minus a curated exclusion list. Ranked by
  liquidity and capped so the daily collection stays one gw2api.fetch_listings
  page (~120 ids live, docs/RESEARCH.md).
- velocity(): fills/day per side from dw2's 7-day and 1-month rolling
  aggregates, blended so one heavy or dead day cannot swing a 1-5-a-week
  estimate on its own.
- place(): the patient bid and the relist ask, gap-walked into the live book
  with features/book.py exactly as the flip desk does, sized by risk rule 2
  (qty <= capture x exit flow x 30 days for this tier) and the S4 hard cap
  of a few units per item so the book stays diversified across names rather
  than concentrated in whichever one is most liquid.
- patience_verdict(): S4's rule for a resting order that never filled - past
  three weeks it is reprice-or-withdraw by rule, not mood.

main() also collects one day of order books into invest/luxury/books/ and
folds book_diff_velocity() over the accumulating history as a cross-check
against the dw2-derived estimate above; see that function's docstring for
what it can and cannot claim.
"""

from features import book
from invest import tags

TAX = 0.15  # trading post cut; kept in sync with scorers.flip.TAX by a test
CAPTURE = 0.25  # docs/INVESTING.md risk rule 2; kept in sync with scorers.flip.CAPTURE by a test

STRATEGY = "luxury desk"  # matches invest.bankroll.CAPS's key; checked by a test

# --- universe ---------------------------------------------------------

# Generation-1 precursor weapons (ids 29166-29185, wiki "Precursor weapon",
# resolved from the live datawars2 snapshot 2026-08-08): wizard's vault
# starter kits hand out free ones every season and the price series has
# been policy-broken since 2023 (docs/INVESTING.md S4 sizing). None price
# above LUXURY_MIN_PRICE today, so this is insurance against the next spike
# rather than doing any work right now.
EXCLUDED_IDS = frozenset({
    29166,  # Tooth of Frostfang -> Frostfang
    29167,  # Spark -> Incinerator
    29168,  # The Bard -> The Minstrel
    29169,  # Dawn -> Sunrise
    29170,  # The Colossus -> The Juggernaut
    29171,  # Carcharias -> Kamohoali'i Kotaki
    29172,  # Leaf of Kudzu -> Kudzu
    29173,  # The Energizer -> The Moot
    29174,  # Chaos Gun -> Quip
    29175,  # The Hunter -> The Predator
    29176,  # Storm -> Meteorlogicus
    29177,  # The Chosen -> The Flameseeker Prophecies
    29178,  # The Lover -> The Dreamer
    29179,  # Rage -> Frenzy
    29180,  # The Legend -> The Bifrost
    29181,  # Zap -> Bolt
    29182,  # Rodgort's Flame -> Rodgort
    29183,  # Venom -> Kraitkin
    29184,  # Howl -> Howler
    29185,  # Dusk -> Twilight
})

MAX_UNIVERSE = 160  # live count ~120-170 depending on the sales metric; stays under one fetch_listings page


def weekly_sales(it):
    """Completed sales/week: units bought off this item's sell listings.

    This is the "nonzero weekly sales" metric docs/RESEARCH.md's market
    anatomy scan used (sell_sold specifically, not the sum of both sides):
    a sale is a buyer taking a listing, which is exactly what dw2.py's
    sell_sold counts. buy_sold is entry-side flow and feeds velocity()
    instead; it is not part of the universe screen.
    """
    return it.get("7d_sell_sold") or 0


def in_universe(it, exclude=EXCLUDED_IDS):
    """One dw2 snapshot row's membership: 500g+, sold at least once this
    week, not on the curated exclusion list."""
    item_id = it.get("id")
    if not item_id or item_id in exclude:
        return False
    if (it.get("sell_price") or 0) < tags.LUXURY_MIN_PRICE:
        return False
    return weekly_sales(it) > 0


def universe(snapshot, exclude=EXCLUDED_IDS, cap=MAX_UNIVERSE):
    """The luxury desk's tracked rows, most liquid first, capped at `cap`.

    Ranking by weekly sales before truncating means a screen-defeating patch
    (a sudden crop of new 500g+ items) truncates to the most liquid names
    first rather than growing the daily book collection without bound.
    """
    rows = [it for it in snapshot if in_universe(it, exclude)]
    rows.sort(key=weekly_sales, reverse=True)
    return rows[:cap]


# --- velocity ----------------------------------------------------------

def velocity(it):
    """(entry, exit) fills/day: patient-bid fill rate, relist fill rate.

    Blends the 7-day and 1-month dw2 rolling aggregates into a per-day rate
    each, then averages them: 7d is the freshest reading, but at this
    tier's 1-5-a-week pace a single heavy or dead day swings it hard, so it
    does not stand alone. Entry reads buy_sold (fills against resting buy
    orders - the side our patient bid sits on); exit reads sell_sold
    (fills against resting sell listings - our relist side), following
    dw2.py's field semantics.
    """
    def blend(w7, m1):
        return ((w7 or 0) / 7 + (m1 or 0) / 30) / 2

    entry = blend(it.get("7d_buy_sold"), it.get("1m_buy_sold"))
    exit_ = blend(it.get("7d_sell_sold"), it.get("1m_sell_sold"))
    return entry, exit_


def book_diff_fills(prev_book, cur_book):
    """Best-effort fills implied by one day of book-depth change, each side.

    Resting quantity on a side falls two ways: a fill (removed by a trade)
    or a cancellation (withdrawn by its owner); depth alone cannot tell
    them apart, so a same-day drop is an upper bound on fills, not a count.
    It exists to sanity-check velocity()'s dw2-derived estimate once our
    own collected book history accumulates, not to replace it.
    """
    def side_drop(prev_side, cur_side):
        before = sum(q for _, q in (prev_side or []))
        after = sum(q for _, q in (cur_side or []))
        return max(0, before - after)

    return (
        side_drop(prev_book.get("buys"), cur_book.get("buys")),
        side_drop(prev_book.get("sells"), cur_book.get("sells")),
    )


def book_diff_velocity(daily_books):
    """{item_id: (entry/day, exit/day)} implied by day-over-day depth drops.

    Empty until at least two days of history exist. daily_books is
    [(date_iso, {item_id: book}), ...], oldest first, as accumulated by
    main()'s daily snapshots under invest/luxury/books/.
    """
    if len(daily_books) < 2:
        return {}
    buy_sum, sell_sum, n = {}, {}, {}
    for (_, prev), (_, cur) in zip(daily_books, daily_books[1:]):
        for item_id in set(prev) | set(cur):
            b, s = book_diff_fills(prev.get(item_id) or {}, cur.get(item_id) or {})
            buy_sum[item_id] = buy_sum.get(item_id, 0) + b
            sell_sum[item_id] = sell_sum.get(item_id, 0) + s
            n[item_id] = n.get(item_id, 0) + 1
    return {i: (buy_sum[i] / n[i], sell_sum[i] / n[i]) for i in n}


# --- placement and sizing ------------------------------------------------

WAIT_TOLERANCE_DAYS = 14.0  # queue tolerance for the gap walk; a slice of the 21-day patience budget
MAX_WALK_PCT = 0.05  # luxury books gap harder than the flip shortlist; allow more room off the touch
MIN_MARGIN_PCT = 0.10  # S4: round trips net 10-25% after tax; below 10% is not this strategy
# S4: spreads run 25-40%. A live scan turned up a real median of 23%, a
# plausible tail out to the 40-65% area, then a break into books of two or
# three listings total (a 92g bid against a 750g ask on Ancestral Guard,
# 2026-08-08) - RESEARCH.md's "illiquidity mistaken for price" failure mode,
# not a real edge. The cap sits above the plausible tail so a genuinely wide
# but real spread still clears it, and drops the break instead of ranking
# it first.
MAX_MARGIN_PCT = 0.75

PATIENCE_DAYS = 21  # S4: reprice or withdraw by rule past three weeks unfilled, not mood
MAX_UNITS_PER_ITEM = 3  # S4 sizing: 1-3 units, diversify across 10-20 items instead of concentrating
MAX_EXIT_DAYS = 30  # docs/INVESTING.md risk rule 2's exit horizon for this tier


def size_qty(exit_flow_per_day, capture=CAPTURE, max_exit_days=MAX_EXIT_DAYS,
             max_units=MAX_UNITS_PER_ITEM):
    """Largest lot this item's own exit flow could clear inside the horizon,
    capped by the per-item diversification limit (risk rule 2 + S4 sizing).
    """
    if not exit_flow_per_day or exit_flow_per_day <= 0:
        return 0
    flow_cap = int(capture * exit_flow_per_day * max_exit_days)
    return max(0, min(flow_cap, max_units))


def _buy_price(buys, entry_flow, min_price):
    """Patient bid: gap-walked when we have a fill-rate estimate to walk
    against, best bid + 1c otherwise. A zero entry_flow is not rare here -
    plenty of luxury goods only ever move by patient listings that someone
    instant-buys, so buy_sold reads zero even on a healthy item - and with
    no rate to divide by, joining the current best bid is the honest
    placement rather than a guess at how deep to walk.
    """
    if not buys:
        return None
    if entry_flow and entry_flow > 0:
        return book.choose_buy_price(buys, entry_flow, WAIT_TOLERANCE_DAYS, min_price)
    return buys[0][0] + 1


def _sell_price(sells, exit_flow, max_price):
    """Relist ask: mirrors _buy_price on the sell side."""
    if not sells:
        return None
    if exit_flow and exit_flow > 0:
        return book.choose_sell_price(sells, exit_flow, WAIT_TOLERANCE_DAYS, max_price)
    return sells[0][0] - 1


def place(it, item_book, entry_flow, exit_flow):
    """One luxury pick: patient bid, relist ask, margin and sizing, or None
    when the book is empty, the margin is too thin (or implausibly wide -
    see MAX_MARGIN_PCT), or no size clears the exit-liquidity floor.
    """
    buys, sells = item_book.get("buys"), item_book.get("sells")
    if not buys or not sells:
        return None
    best_bid, best_ask = buys[0][0], sells[0][0]

    buy_at = _buy_price(buys, entry_flow, best_bid * (1 - MAX_WALK_PCT))
    sell_at = _sell_price(sells, exit_flow, best_ask * (1 + MAX_WALK_PCT))
    if not buy_at or not sell_at:
        return None

    margin = int((sell_at - 1) * (1 - TAX)) - buy_at
    if margin <= 0:
        return None
    margin_pct = margin / buy_at
    if margin_pct < MIN_MARGIN_PCT or margin_pct > MAX_MARGIN_PCT:
        return None

    qty = size_qty(exit_flow)
    if qty < 1:
        return None

    # Same linear model as scorers.flip: round_trip_days is the qty-scaled
    # total (matching the flip table's "Trip" column), but ev_week is the
    # per-unit rate and so does not depend on lot size (capital recycles).
    round_trip_days = ev_week = None
    if entry_flow and exit_flow and entry_flow > 0 and exit_flow > 0:
        per_unit_days = 1 / (CAPTURE * entry_flow) + 1 / (CAPTURE * exit_flow)
        round_trip_days = round(qty * per_unit_days, 1)
        ev_week = round(margin / per_unit_days * 7, 1)

    return {
        "id": it["id"],
        "name": it.get("name") or f"item {it['id']}",
        "rarity": it.get("rarity") or "",
        "strategy": STRATEGY,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "buy_at": buy_at,
        "sell_at": sell_at,
        "margin": margin,
        "margin_pct": round(margin_pct, 4),
        "qty": qty,
        "capital": qty * buy_at,
        "entry_flow_week": round(entry_flow * 7, 2),
        "exit_flow_week": round(exit_flow * 7, 2),
        "round_trip_days": round_trip_days,
        "ev_week": ev_week,
    }


def patience_verdict(days_open, filled_qty, patience_days=PATIENCE_DAYS):
    """("waiting"|"reprice", text): S4's rule for a stale resting order.

    A patient bid earns its name by not chasing every day, so only an
    order the market never reached inside the patience window is flagged.
    The rule fires on the unfilled case specifically: a partial fill
    already proved the price works, so the remainder keeps waiting instead
    of resetting on a technicality.
    """
    if filled_qty and filled_qty > 0:
        return "waiting", f"partially filled ({filled_qty:g} of the lot); keep waiting"
    if days_open < patience_days:
        return "waiting", f"day {days_open} of {patience_days} before repricing"
    return "reprice", f"{days_open}d unfilled, past the {patience_days}d patience rule"


def build_picks(universe_rows, books, top_n=None):
    """Every priceable universe row, gap-walked, margin-checked and sized.

    Sorted by margin_pct: this tier is a spread-capture strategy first, so
    the deepest discount to the ask leads, unlike flip's EV/day ranking
    which suits its much faster round trips.
    """
    picks = []
    for it in universe_rows:
        b = books.get(it.get("id"))
        if not b:
            continue
        entry_flow, exit_flow = velocity(it)
        p = place(it, b, entry_flow, exit_flow)
        if p:
            picks.append(p)
    picks.sort(key=lambda p: p["margin_pct"], reverse=True)
    return picks[:top_n] if top_n else picks


# --- collection and the artifact -----------------------------------------

LUXURY_KEY = "invest/luxury/latest.json.gz"
BOOKS_PREFIX = "invest/luxury/books/"
BOOK_HISTORY_DAYS = 21  # enough for the patience window and a first book-diff cross-check


def load_recent_books(store, days=BOOK_HISTORY_DAYS):
    """[(date_iso, {item_id: book}), ...] for the last `days` stored
    snapshots, oldest first, or [] before the first run has ever collected
    one.
    """
    out = []
    for key in store.list_keys(BOOKS_PREFIX)[-days:]:
        doc = store.get_json_gz(key)
        if not doc:
            continue
        books = {int(k): v for k, v in (doc.get("books") or {}).items()}
        out.append((doc.get("date"), books))
    return out


def main():
    import os
    from datetime import datetime, timezone

    from collector import dw2, gw2api
    from collector.s3store import Store

    store = Store(os.environ["S3_BUCKET"])
    snap = dw2.fetch_snapshot()
    uni = universe(snap)
    ids = [it["id"] for it in uni]
    books = gw2api.fetch_listings(ids) if ids else {}

    today = datetime.now(timezone.utc).date()
    # One JSON object/day under its own prefix, the same shape as
    # collector/compact.py's dated keys; at ~120 ids this is a few KB/day,
    # kilobytes/year against the 25GB bucket quota, so it is never pruned.
    store.put_json_gz(
        f"{BOOKS_PREFIX}{today.isoformat()}.json.gz",
        {"date": today.isoformat(), "books": {str(k): v for k, v in books.items()}},
    )

    history = load_recent_books(store, BOOK_HISTORY_DAYS)
    diffs = book_diff_velocity(history)

    picks = build_picks(uni, books)
    artifact = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "through": today.isoformat(),
        "universe_n": len(uni),
        "books_fetched": len(books),
        "book_history_days": len(history),
        "book_diff_coverage": len(diffs),
        "picks": picks,
    }
    store.put_json_gz(LUXURY_KEY, artifact)
    print(
        f"luxury desk: {len(uni)} universe ids, {len(books)} books fetched, "
        f"{len(picks)} picks, {len(diffs)} ids with a book-diff cross-check "
        f"-> s3://{store.bucket}/{LUXURY_KEY}"
    )


if __name__ == "__main__":
    main()
