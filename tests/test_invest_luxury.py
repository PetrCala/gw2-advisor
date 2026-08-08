from invest import bankroll, luxury
from scorers import flip


def _row(**over):
    r = {
        "id": 1001, "name": "Ghastly Grinning Shield", "rarity": "Ascended",
        "sell_price": 60_000_000,  # 6000g
        "7d_buy_sold": 7, "7d_sell_sold": 7,
        "1m_buy_sold": 30, "1m_sell_sold": 30,
    }
    r.update(over)
    return r


def _book(**over):
    # ~34% spread, in the middle of S4's documented 25-40% band: wide
    # enough to clear the 15% tax with room for a positive margin.
    b = {
        "buys": [[5_900_000, 1], [5_700_000, 2], [5_400_000, 3]],
        "sells": [[7_900_000, 1], [8_200_000, 2], [8_600_000, 3]],
    }
    b.update(over)
    return b


# --- shared constants ------------------------------------------------------


def test_tax_and_capture_stay_in_sync_with_the_flip_scorer():
    assert luxury.TAX == flip.TAX
    assert luxury.CAPTURE == flip.CAPTURE


def test_strategy_tag_matches_the_bankroll_cap_table():
    assert luxury.STRATEGY in bankroll.CAPS


# --- universe ----------------------------------------------------------


def test_universe_needs_price_and_weekly_sales():
    rows = [
        _row(id=1, sell_price=luxury.tags.LUXURY_MIN_PRICE, **{"7d_sell_sold": 1}),
        _row(id=2, sell_price=luxury.tags.LUXURY_MIN_PRICE - 1, **{"7d_sell_sold": 1}),
        _row(id=3, sell_price=luxury.tags.LUXURY_MIN_PRICE, **{"7d_sell_sold": 0}),
    ]
    assert [it["id"] for it in luxury.universe(rows)] == [1]


def test_universe_drops_curated_exclusions_even_when_priced_in():
    excluded_id = next(iter(luxury.EXCLUDED_IDS))
    rows = [_row(id=excluded_id, sell_price=10_000_000, **{"7d_sell_sold": 5})]
    assert luxury.universe(rows) == []


def test_universe_ignores_buy_side_only_activity():
    # nonzero buy_sold, zero sell_sold: never actually sold this week
    rows = [_row(id=1, **{"7d_buy_sold": 20, "7d_sell_sold": 0})]
    assert luxury.universe(rows) == []


def test_universe_ranks_by_weekly_sales_and_caps():
    rows = [_row(id=i, **{"7d_sell_sold": i}) for i in range(1, 6)]
    out = luxury.universe(rows, cap=3)
    assert [it["id"] for it in out] == [5, 4, 3]


def test_a_row_missing_an_id_is_never_in_universe():
    assert luxury.in_universe({"sell_price": 10_000_000, "7d_sell_sold": 5}) is False


# --- velocity ------------------------------------------------------------


def test_velocity_blends_the_weekly_and_monthly_windows():
    it = _row(**{"7d_buy_sold": 7, "1m_buy_sold": 30, "7d_sell_sold": 0, "1m_sell_sold": 60})
    entry, exit_ = luxury.velocity(it)
    assert entry == (7 / 7 + 30 / 30) / 2 == 1.0
    assert round(exit_, 4) == round((0 / 7 + 60 / 30) / 2, 4)


def test_velocity_is_zero_with_no_data_at_all():
    assert luxury.velocity({}) == (0.0, 0.0)


def test_velocity_does_not_snap_to_zero_on_one_dead_week():
    # dead this week, active over the last month: smoothed, not zeroed
    it = _row(**{"7d_sell_sold": 0, "1m_sell_sold": 12})
    _, exit_ = luxury.velocity(it)
    assert exit_ > 0


# --- book-diff cross-check -------------------------------------------------


def test_book_diff_fills_reads_the_drop_on_each_side():
    prev = {"buys": [[100, 5], [90, 5]], "sells": [[110, 4], [120, 6]]}
    cur = {"buys": [[100, 3], [90, 5]], "sells": [[110, 4], [120, 4]]}
    assert luxury.book_diff_fills(prev, cur) == (2, 2)


def test_book_diff_fills_floors_at_zero_when_depth_grows():
    prev = {"buys": [[100, 1]], "sells": [[110, 1]]}
    cur = {"buys": [[100, 5]], "sells": [[110, 1]]}
    assert luxury.book_diff_fills(prev, cur) == (0, 0)


def test_book_diff_fills_treats_a_missing_side_as_empty():
    assert luxury.book_diff_fills({}, {"buys": [[100, 2]]}) == (0, 0)


def test_book_diff_velocity_needs_at_least_two_days():
    assert luxury.book_diff_velocity([]) == {}
    assert luxury.book_diff_velocity([("2026-08-01", {1: _book()})]) == {}


def test_book_diff_velocity_averages_over_the_tracked_days():
    day0 = {1: {"buys": [[100, 6]], "sells": [[110, 6]]}}
    day1 = {1: {"buys": [[100, 4]], "sells": [[110, 5]]}}
    day2 = {1: {"buys": [[100, 2]], "sells": [[110, 4]]}}
    out = luxury.book_diff_velocity([("d0", day0), ("d1", day1), ("d2", day2)])
    assert out[1] == (2.0, 1.0)


def test_book_diff_velocity_handles_an_id_only_seen_on_one_day():
    day0 = {1: {"buys": [[100, 5]], "sells": [[110, 5]]}}
    day1 = {1: {"buys": [[100, 3]], "sells": [[110, 5]]}, 2: {"buys": [[50, 2]], "sells": [[60, 2]]}}
    out = luxury.book_diff_velocity([("d0", day0), ("d1", day1)])
    assert out[1] == (2.0, 0.0)
    assert out[2] == (0.0, 0.0)


# --- sizing ----------------------------------------------------------------


def test_size_qty_is_bounded_by_flow_and_horizon():
    # 0.5/day * 0.25 capture * 30 days = 3.75 -> 3
    assert luxury.size_qty(0.5) == 3


def test_size_qty_hard_caps_at_the_per_item_unit_limit():
    assert luxury.size_qty(100.0) == luxury.MAX_UNITS_PER_ITEM


def test_size_qty_is_zero_with_no_exit_flow():
    assert luxury.size_qty(0) == 0
    assert luxury.size_qty(None) == 0


# --- placement ---------------------------------------------------------


def test_place_gap_walks_both_legs_and_sizes_the_lot():
    it = _row()
    b = _book()
    p = luxury.place(it, b, *luxury.velocity(it))
    assert p is not None
    # walked past the front of book: a lower buy than best+1c, a higher
    # sell than best-1c, both still inside the level-2 gap that stopped them
    assert p["buy_at"] == 5_700_001
    assert p["sell_at"] == 8_199_999
    assert p["margin"] == 1_269_997
    assert round(p["margin_pct"], 3) == 0.223
    assert p["qty"] == luxury.MAX_UNITS_PER_ITEM  # flow easily clears the unit cap
    assert p["strategy"] == luxury.STRATEGY
    assert p["capital"] == p["qty"] * p["buy_at"]
    assert p["round_trip_days"] == p["qty"] * 8.0  # qty-scaled, like flip's Trip column
    assert p["ev_week"] > 0  # per-unit rate: independent of qty, unlike round_trip_days


def test_place_is_none_on_an_empty_book_side():
    it = _row()
    empty = {"buys": [], "sells": _book()["sells"]}
    assert luxury.place(it, empty, *luxury.velocity(it)) is None


def test_place_is_none_when_the_margin_is_too_thin():
    it = _row()
    tight = {"buys": [[6_190_000, 5]], "sells": [[6_200_000, 5]]}
    assert luxury.place(it, tight, *luxury.velocity(it)) is None


def test_place_is_none_when_the_book_is_too_thin_to_trust():
    # a 92g bid against a 750g ask on two listings: illiquidity, not a spread
    it = _row(sell_price=7_500_000)
    thin = {"buys": [[920_000, 1]], "sells": [[7_500_000, 2]]}
    assert luxury.place(it, thin, *luxury.velocity(it)) is None


def test_place_falls_back_to_best_price_with_no_flow_evidence():
    it = _row(**{"7d_buy_sold": 0, "1m_buy_sold": 0})
    b = _book()
    p = luxury.place(it, b, 0.0, 0.5)
    assert p is not None
    assert p["buy_at"] == b["buys"][0][0] + 1


def test_place_is_none_when_no_size_clears_the_exit_horizon():
    it = _row()
    p = luxury.place(it, _book(), entry_flow=1.0, exit_flow=0.0)
    assert p is None


def test_place_leaves_the_pace_fields_blank_without_both_flows():
    it = _row()
    p = luxury.place(it, _book(), entry_flow=0.0, exit_flow=0.5)
    assert p is not None
    assert p["round_trip_days"] is None
    assert p["ev_week"] is None


# --- patience ------------------------------------------------------------


def test_patience_verdict_waits_out_the_window():
    bucket, _ = luxury.patience_verdict(5, 0)
    assert bucket == "waiting"


def test_patience_verdict_flags_a_stale_unfilled_order():
    bucket, text = luxury.patience_verdict(luxury.PATIENCE_DAYS, 0)
    assert bucket == "reprice"
    assert "21d" in text or str(luxury.PATIENCE_DAYS) in text


def test_patience_verdict_keeps_waiting_on_a_partial_fill_past_the_window():
    bucket, _ = luxury.patience_verdict(luxury.PATIENCE_DAYS + 5, 1)
    assert bucket == "waiting"


# --- build_picks -----------------------------------------------------------


def test_build_picks_skips_rows_with_no_book():
    rows = [_row(id=1), _row(id=2)]
    books = {1: _book()}
    picks = luxury.build_picks(rows, books)
    assert [p["id"] for p in picks] == [1]


def test_build_picks_sorts_by_margin_pct_descending():
    narrower = _book(sells=[[7_500_000, 5]])  # thinner margin, still above the 10% floor
    rows = [_row(id=1), _row(id=2)]
    books = {1: narrower, 2: _book()}  # id 2 keeps the ~34%-spread default
    picks = luxury.build_picks(rows, books)
    assert [p["id"] for p in picks] == [2, 1]
    assert picks[0]["margin_pct"] > picks[1]["margin_pct"]


def test_build_picks_respects_top_n():
    rows = [_row(id=i) for i in range(1, 5)]
    books = {i: _book() for i in range(1, 5)}
    assert len(luxury.build_picks(rows, books, top_n=2)) == 2


# --- load_recent_books (FakeStore) ------------------------------------


class FakeStore:
    """collector.s3store.Store's read surface, in memory."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def list_keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def get_json_gz(self, key):
        return self.objects.get(key)

    def put_json_gz(self, key, payload):
        self.objects[key] = payload


def test_load_recent_books_reads_the_stored_prefix_in_order():
    store = FakeStore({
        f"{luxury.BOOKS_PREFIX}2026-08-01.json.gz": {
            "date": "2026-08-01", "books": {"1": _book()}
        },
        f"{luxury.BOOKS_PREFIX}2026-08-02.json.gz": {
            "date": "2026-08-02", "books": {"1": _book()}
        },
    })
    out = luxury.load_recent_books(store)
    assert [d for d, _ in out] == ["2026-08-01", "2026-08-02"]
    assert out[0][1][1] == _book()  # string key coerced back to int


def test_load_recent_books_is_empty_before_the_first_collection():
    assert luxury.load_recent_books(FakeStore()) == []


def test_load_recent_books_respects_the_days_window():
    store = FakeStore({
        f"{luxury.BOOKS_PREFIX}2026-08-{d:02d}.json.gz": {
            "date": f"2026-08-{d:02d}", "books": {}
        }
        for d in range(1, 10)
    })
    out = luxury.load_recent_books(store, days=3)
    assert [d for d, _ in out] == ["2026-08-07", "2026-08-08", "2026-08-09"]
