from datetime import date, timedelta

import pytest

from invest import index


def mk(iso):
    return index.month_key(iso)


def axis(first, n):
    d0 = date.fromisoformat(first)
    return [(d0 + timedelta(days=k)).isoformat() for k in range(n)]


# --- month helpers -------------------------------------------------------


def test_month_key_and_str_round_trip():
    assert index.month_str(mk("2020-01-15")) == "2020-01"
    assert mk("2020-01-31") + 1 == mk("2020-02-01")
    assert mk("2019-12-31") + 1 == mk("2020-01-01")


# --- weight capping ------------------------------------------------------


def test_weights_without_a_binding_cap_are_proportional():
    w = index.cap_weights({1: 30, 2: 30, 3: 40}, cap=0.5)
    assert w[3] == pytest.approx(0.4)
    assert sum(w.values()) == pytest.approx(1.0)


def test_excess_above_the_cap_spreads_over_the_rest():
    w = index.cap_weights({1: 80, 2: 10, 3: 10}, cap=0.5)
    assert w[1] == pytest.approx(0.5)
    assert w[2] == pytest.approx(0.25)
    assert w[3] == pytest.approx(0.25)
    assert sum(w.values()) == pytest.approx(1.0)


def test_capping_one_item_can_push_another_over_iteratively():
    w = index.cap_weights({1: 70, 2: 25, 3: 5}, cap=0.4)
    assert w[1] == pytest.approx(0.4)
    assert w[2] == pytest.approx(0.4)  # 0.25 grows past the cap, gets capped too
    assert w[3] == pytest.approx(0.2)


def test_an_infeasible_cap_falls_back_to_equal_weights():
    w = index.cap_weights({1: 90, 2: 5, 3: 5}, cap=0.05)  # 3 x 5% < 100%
    assert all(v == pytest.approx(1 / 3) for v in w.values())


def test_empty_or_zero_weights_yield_nothing():
    assert index.cap_weights({}) == {}
    assert index.cap_weights({1: 0.0}) == {}


# --- constituent selection -----------------------------------------------


def test_selection_ranks_by_prior_month_turnover_and_gates_on_coverage():
    feb = mk("2020-02-01")
    jan = feb - 1
    turn = {1: {jan: 500}, 2: {jan: 900}, 3: {jan: 700}, 4: {jan: 1000}}
    cover = {1: {jan: 31}, 2: {jan: 31}, 3: {jan: 5}, 4: {jan: 31}}
    got = index.select_constituents(turn, cover, feb, top_n=2, min_days=10)
    assert set(got) == {4, 2}  # 3 fails coverage, 1 loses the ranking
    assert got[4] == 1000


def test_selection_ignores_items_with_no_prior_month_turnover():
    feb = mk("2020-02-01")
    turn = {1: {feb: 500}}  # turnover in the month itself, not the prior one
    cover = {1: {feb: 29}}
    assert index.select_constituents(turn, cover, feb) == {}


# --- chaining ------------------------------------------------------------


def synthetic_market():
    """Three items over Jan-Mar 2020; feb is the first eligible month."""
    days = axis("2020-01-01", 91)
    jan, feb = mk("2020-01-01"), mk("2020-02-01")
    n = len(days)

    def series(fill):
        return [fill] * n

    mids = {1: series(100.0), 2: series(100.0), 3: series(100.0)}
    turn = {
        1: {jan: 100, feb: 100},
        2: {jan: 100, feb: 300},
        3: {jan: 100, feb: 100},
    }
    cover = {i: {jan: 31, feb: 29} for i in (1, 2, 3)}
    return days, mids, turn, cover


def test_flat_prices_chain_to_a_flat_index_from_the_first_eligible_month():
    days, mids, turn, cover = synthetic_market()
    out = index.chain_daily(days, mids, turn, cover, min_eligible=3, weight_cap=1.0)
    assert out["dates"][0] == "2020-02-01"
    assert out["levels"][0] == 100.0
    assert out["levels"][-1] == pytest.approx(100.0)
    assert [m["m"] for m in out["months"]] == ["2020-02", "2020-03"]
    assert all(m["n"] == 3 for m in out["months"])


def test_march_moves_are_weighted_by_february_turnover_with_renormalization():
    days, mids, turn, cover = synthetic_market()
    i_mar2 = days.index("2020-03-02")
    for t in range(i_mar2, len(days)):
        mids[1][t] = 110.0  # +10% at weight 100/500
        mids[2][t] = 105.0  # +5% at weight 300/500
    mids[3] = [100.0] * i_mar2 + [None] * (len(days) - i_mar2)  # dark from Mar 2
    out = index.chain_daily(days, mids, turn, cover, min_eligible=3, weight_cap=1.0)
    # weights renormalize to 0.25/0.75 over the two priced items
    assert out["levels"][-1] == pytest.approx(0.25 * 110 + 0.75 * 105, rel=1e-6)


def test_a_day_with_most_weight_dark_carries_the_level():
    days, mids, turn, cover = synthetic_market()
    i = days.index("2020-02-15")
    mids[1][i] = None
    mids[2][i] = None  # only item 3 (a third of the weight) prices that day
    mids[3][i] = 500.0  # and it spikes; must not move the index alone
    out = index.chain_daily(days, mids, turn, cover, min_eligible=3, weight_cap=1.0)
    assert out["levels"][days.index("2020-02-15") - 31] == pytest.approx(100.0)
    # the spike day is skipped, and so is the rebound relative the day after
    assert out["levels"][-1] == pytest.approx(100.0)


def test_glitch_relatives_are_clipped():
    days, mids, turn, cover = synthetic_market()
    i = days.index("2020-02-10")
    mids[1][i] = 100_000.0  # a 1000x data glitch for one day
    for t in range(i + 1, len(days)):
        mids[1][t] = 100.0
    out = index.chain_daily(days, mids, turn, cover, min_eligible=3, weight_cap=1.0)
    lo, hi = index.RELATIVE_CLIP
    up = (2 + hi) / 3  # equal thirds by feb turnover, one leg clipped to 10x
    down = (2 + lo) / 3
    assert out["levels"][i - 31] == pytest.approx(100 * up, rel=1e-6)
    assert out["levels"][i - 30] == pytest.approx(100 * up * down, rel=1e-6)


def test_no_eligible_month_returns_none():
    days = axis("2020-01-01", 60)
    assert index.chain_daily(days, {}, {}, {}, min_eligible=3) is None


# --- trailing returns ----------------------------------------------------


def test_trailing_return_uses_the_first_point_inside_the_window():
    dates = axis("2020-01-01", 100)
    values = list(range(100, 200))
    r = index.trailing_return(dates, values, 30, dates[-1])
    assert r == pytest.approx(values[-1] / values[len(values) - 31] - 1, abs=1e-4)


def test_trailing_return_without_a_base_or_data_is_none():
    assert index.trailing_return([], [], 30, "2020-01-01") is None
    dates = ["2020-01-01"]
    assert index.trailing_return(dates, [100], 30, "2020-01-01") is None
