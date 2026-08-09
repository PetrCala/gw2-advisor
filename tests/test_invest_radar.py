from invest import radar


def _row(**over):
    r = {
        "id": 1, "name": "Test Item", "sell_price": 100, "buy_price": 90,
        "buy_quantity": 50, "sell_quantity": 50,
        "1d_buy_sold": 5, "1d_sell_sold": 5,
        "1d_buy_listed": 2, "1d_sell_listed": 2,
        "1d_buy_delisted": 1, "1d_sell_delisted": 1,
        "vendor_value": 10, "7d_sell_sold": 700,
    }
    r.update(over)
    return r


def _point(d, **items):
    return {"d": d, "items": items}


# --- history ---------------------------------------------------------------


def test_build_point_sums_both_sides_and_computes_imbalance():
    out = radar.build_point([_row(id=1)])
    row = out["1"]
    assert row["sold"] == 10
    assert row["listed"] == 4
    assert row["delisted"] == 2
    assert row["depth"] == 100
    assert row["imbalance"] == 0.0


def test_build_point_skips_rows_without_an_id():
    assert radar.build_point([{"name": "no id"}]) == {}


def test_build_point_imbalance_is_none_with_no_depth():
    out = radar.build_point([_row(id=1, buy_quantity=0, sell_quantity=0)])
    assert out["1"]["imbalance"] is None


def test_history_append_replaces_same_day_rerun_and_caps_length():
    history = {}
    for d in range(1, 5):
        history = radar.append(history, _point(f"2026-08-{d:02d}"), cap=3)
    assert [p["d"] for p in history["points"]] == ["2026-08-02", "2026-08-03", "2026-08-04"]
    history = radar.append(history, _point("2026-08-04", x=1), cap=3)
    assert len(history["points"]) == 3
    assert history["points"][-1] == _point("2026-08-04", x=1)


# --- zscore ------------------------------------------------------------


def test_zscore_is_none_below_min_observations():
    assert radar.zscore([1, 2, 3], 10, min_obs=5) is None


def test_zscore_is_none_when_stdev_is_zero():
    assert radar.zscore([5, 5, 5, 5, 5], 10, min_obs=5) is None


def test_zscore_computes_the_standard_score():
    vals = [1, 2, 3, 4, 5]
    assert radar.zscore(vals, 8, min_obs=5) == (8 - 3) / (sum((v - 3) ** 2 for v in vals) / 5) ** 0.5


def test_zscore_drops_none_values_from_the_baseline():
    assert radar.zscore([1, None, 2, None, 3, 4, 5], 8, min_obs=5) == radar.zscore(
        [1, 2, 3, 4, 5], 8, min_obs=5
    )


# --- abnormal-activity radar --------------------------------------------


def test_price_zscore_uses_log_return_not_raw_level():
    # a mildly wobbling baseline (a small nonzero return every day) makes a
    # real jump register on the daily return, not on the price level itself
    prices = [100, 101, 99, 102, 98, 101]
    points = [_point(f"2026-08-{d:02d}", **{"1": {"price": p}}) for d, p in zip(range(1, 7), prices)]
    points.append(_point("2026-08-07", **{"1": {"price": 300}}))  # today: a real jump
    rows = radar.radar_rows(points, threshold=1.0, min_obs=3)
    assert rows and rows[0]["id"] == 1
    assert "price" in rows[0]["flags"]


def test_radar_rows_needs_at_least_two_points():
    assert radar.radar_rows([]) == []
    assert radar.radar_rows([_point("2026-08-01")]) == []


def test_radar_rows_flags_a_sold_count_outlier():
    counts = [10, 12, 9, 11, 10]
    base = [
        _point(f"2026-08-{d:02d}", **{"1": {"sold": c, "price": None}})
        for d, c in zip(range(1, 6), counts)
    ]
    today = _point("2026-08-06", **{"1": {"sold": 500, "price": None}})
    rows = radar.radar_rows(base + [today], threshold=3.0, min_obs=5)
    assert any(r["id"] == 1 and "sold" in r["flags"] for r in rows)


def test_radar_rows_is_quiet_on_a_stable_series():
    base = [
        _point(f"2026-08-{d:02d}", **{"1": {"sold": 10, "price": 100}})
        for d in range(1, 7)
    ]
    assert radar.radar_rows(base, threshold=4.0, min_obs=5) == []


# --- pump filter ---------------------------------------------------------


def test_pump_flag_requires_four_of_five_up_days_and_low_float():
    up_series = [{"price": p, "depth": 50} for p in (100, 104, 108, 113, 118, 123)]
    assert radar.pump_flag(up_series) is True


def test_pump_flag_is_false_with_healthy_depth():
    up_series = [{"price": p, "depth": 5000} for p in (100, 104, 108, 113, 118, 123)]
    assert radar.pump_flag(up_series) is False


def test_pump_flag_is_false_without_enough_up_days():
    flat_series = [{"price": 100, "depth": 50} for _ in range(6)]
    assert radar.pump_flag(flat_series) is False


def test_pump_flag_needs_six_points():
    assert radar.pump_flag([{"price": 100, "depth": 1}] * 5) is False


def test_pump_candidates_scans_the_trailing_six_points():
    tail = [
        _point(f"2026-08-{d:02d}", **{"1": {"price": p, "depth": 50}})
        for d, p in zip(range(1, 7), (100, 104, 108, 113, 118, 123))
    ]
    assert radar.pump_candidates(tail) == [1]


# --- sink-anticipation watchlist -----------------------------------------


def test_sink_watchlist_filters_vendor_floor_and_flow_threshold():
    rows = [
        _row(id=1, sell_price=10, vendor_value=10, **{"7d_sell_sold": 700}),  # pinned, flows
        _row(id=2, sell_price=100, vendor_value=10, **{"7d_sell_sold": 700}),  # not pinned
        _row(id=3, sell_price=10, vendor_value=10, **{"7d_sell_sold": 7}),  # pinned, too thin
    ]
    out = radar.sink_watchlist(rows, min_flow=100)
    assert [r["id"] for r in out] == [1]


def test_sink_watchlist_sorts_by_flow_descending():
    rows = [
        _row(id=1, sell_price=8, vendor_value=10, **{"7d_sell_sold": 700}),
        _row(id=2, sell_price=8, vendor_value=10, **{"7d_sell_sold": 1400}),
    ]
    out = radar.sink_watchlist(rows, min_flow=100)
    assert [r["id"] for r in out] == [2, 1]


def test_sink_watchlist_skips_rows_missing_price_or_vendor_value():
    rows = [_row(id=1, sell_price=None), _row(id=2, vendor_value=None)]
    assert radar.sink_watchlist(rows) == []
