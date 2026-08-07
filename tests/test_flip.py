from scorers import flip

BASE = {
    "id": 1,
    "name": "Test Item",
    "rarity": "Fine",
    "vendor_value": 0,
    "buy_price": 1000,
    "sell_price": 1400,
    "buy_quantity": 5000,
    "sell_quantity": 5000,
    "1d_buy_sold": 1000,
    "1d_sell_sold": 1000,
    "7d_buy_sold": 7000,
    "7d_sell_sold": 7000,
    "7d_buy_price_avg": 1000,
    "7d_sell_price_avg": 1400,
    "7d_sell_price_min": 1350,
    "7d_sell_price_max": 1450,
    "1m_buy_sold": 30000,
    "1m_sell_sold": 30000,
}


def test_margin_math():
    s = flip.score_item(dict(BASE))
    assert s["buy_at"] == 1001
    assert s["sell_at"] == 1399
    assert s["margin"] == int(1399 * (1 - flip.TAX)) - 1001


def test_negative_margin_filtered():
    # 0.85 * 1149 = 976 net, below the 1001 cost
    assert flip.score_item(dict(BASE, sell_price=1150)) is None


def test_low_flow_filtered():
    assert flip.score_item(dict(BASE, **{"7d_buy_sold": 70})) is None


def test_vendor_floor_filtered():
    assert flip.score_item(dict(BASE, vendor_value=1400)) is None


def test_qty_respects_capital_cap():
    s = flip.score_item(dict(BASE))
    assert s["capital"] <= flip.CAPITAL_PER_ITEM
    assert s["round_trip_days"] <= flip.MAX_ROUND_TRIP_DAYS + 1e-9


def test_ev_day_is_margin_over_cycle_time():
    s = flip.score_item(dict(BASE))
    per_unit = 2 / (flip.CAPTURE * 1000)
    assert abs(s["ev_day"] - s["margin"] / per_unit) < 1e-6


def test_confidence_high_on_stable_item():
    assert flip.score_item(dict(BASE))["confidence"] == "high"


def test_confidence_drops_on_flow_spike():
    # Yesterday traded 5x the weekly average: one check fails.
    s = flip.score_item(dict(BASE, **{"1d_buy_sold": 5000}))
    assert s["confidence"] == "medium"


def test_score_all_ranks_by_ev_day():
    a = dict(BASE, id=1)
    b = dict(BASE, id=2, sell_price=1600, **{"7d_sell_price_avg": 1600})
    ranked = flip.score_all([a, b])
    assert [s["id"] for s in ranked] == [2, 1]
    assert ranked[0]["ev_day"] > ranked[1]["ev_day"]


BOOK = {
    "buys": [[1000, 100], [990, 50], [980, 5000]],
    "sells": [[1400, 100], [1410, 50], [1420, 5000]],
}


def test_rescore_prices_into_gaps():
    pick = flip.score_item(dict(BASE))
    r = flip.rescore(pick, BOOK, (5.0, 5.0))
    # 1000/day flow with 0.25-day budget tolerates 250 queued: past the
    # 100 and the 50 on each side, into the gap above/below the 5000 wall.
    assert r["buy_at"] == 981
    assert r["sell_at"] == 1419
    assert r["outbid_day"] == 5.0
    assert r["round_trip_days"] <= flip.MAX_ROUND_TRIP_DAYS + 1e-9
    assert r["exit_pct"] < 0  # dumping into the buy book always loses


def test_rescore_reports_the_touch_alongside_our_prices():
    pick = flip.score_item(dict(BASE))
    r = flip.rescore(pick, BOOK, None)
    assert (r["best_bid"], r["best_ask"]) == (1000, 1400)
    assert r["buy_at"] <= r["best_bid"] + 1 and r["sell_at"] >= r["best_ask"] - 1


# Thin crowd at the touch under a wide gap, as Guild Flame Ram Blueprint
# looked on 2026-08-07: the wait budget alone walks the listing 70% above a
# market that trades at 1400, and the margin is then pure fiction.
GAPPED_BOOK = {
    "buys": [[1000, 100], [990, 50], [980, 5000]],
    "sells": [[1400, 30], [1402, 5], [1560, 80], [1700, 300], [2400, 50_000]],
}


def test_rescore_keeps_the_listing_beside_the_touch():
    pick = flip.score_item(dict(BASE))
    r = flip.rescore(pick, GAPPED_BOOK, None)
    assert r["sell_at"] <= 1400 * (1 + flip.MAX_WALK_PCT)
    assert r["sell_at"] == 1401  # 1c under the 1402 level, not up in the gap
    # Unbounded, the walk lands at 2399 and claims a margin over 1000c.
    assert r["margin"] < 250


def test_rescore_drops_penny_wars():
    pick = flip.score_item(dict(BASE))
    assert flip.rescore(pick, BOOK, (100.0, 100.0)) is None


def test_rescore_without_rates_keeps_item():
    pick = flip.score_item(dict(BASE))
    r = flip.rescore(pick, BOOK, None)
    assert r is not None
    assert r["outbid_day"] is None


def test_rescore_empty_book_side_drops_item():
    pick = flip.score_item(dict(BASE))
    assert flip.rescore(pick, {"buys": [], "sells": BOOK["sells"]}, None) is None


def test_rescore_attaches_exit_prices_and_verdict():
    pick = flip.score_item(dict(BASE))
    r = flip.rescore(pick, BOOK, (5.0, 5.0))
    # break-even recovers buy_at after fees; bail recovers the dump value.
    assert int((r["break_even_sell"] - 1) * (1 - flip.TAX)) >= r["buy_at"]
    assert int((r["break_even_sell"] - 2) * (1 - flip.TAX)) < r["buy_at"]
    # A healthy margin, high confidence, calm reprice rates and an exit
    # floor inside the caution threshold reads as act now.
    assert r["bucket"] == "act_now"
    assert "act now" in r["verdict"]
    assert r["bail_price"] < r["break_even_sell"]
    assert r["act"] > 0


def test_rescore_act_score_prioritizes_bucket_over_ev_day():
    pick = flip.score_item(dict(BASE))
    healthy = flip.rescore(pick, BOOK, (5.0, 5.0))
    # Same book and flow, but an elevated (not yet penny-war) reprice rate
    # drops it to caution even though nothing about the price or margin
    # changed.
    caution = flip.rescore(pick, BOOK, (40.0, 40.0))
    assert healthy["bucket"] == "act_now"
    assert caution["bucket"] == "caution"
    assert healthy["act"] > caution["act"]
