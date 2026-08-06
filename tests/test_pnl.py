from account import pnl

# One item, 10c best buy / 20c best sell, deep enough not to walk.
BOOKS = {77: {"buys": [[10, 1000]], "sells": [[20, 1000]]}}

BUY = {"item_id": 77, "price": 10, "quantity": 100, "purchased": "2026-08-06T12:00:00.000Z"}
SELL = {"item_id": 77, "price": 20, "quantity": 100, "purchased": "2026-08-06T13:00:00.000Z"}


def test_window_filters_on_purchased():
    rows = [
        {"purchased": "2026-08-06T09:00:00.000Z"},
        {"purchased": "2026-08-06T12:30:00.000Z"},
        {"purchased": "2026-08-06T14:00:00.000Z"},
    ]
    kept = pnl.in_window(rows, "2026-08-06T10:00:00.000Z", "2026-08-06T13:00:00.000Z")
    assert [r["purchased"] for r in kept] == ["2026-08-06T12:30:00.000Z"]


def test_open_transactions_are_skipped():
    assert pnl.in_window([{"purchased": None}], "2026-08-06T00:00:00.000Z") == []


def test_fees_take_fifteen_percent():
    assert pnl.net_of_fees(10_000) == 8_500


def test_buy_only_window_is_cash_negative_but_value_neutral():
    r = pnl.compute([BUY], [], BOOKS)
    # 100 units at 10c left the wallet.
    assert r["cash"] == -1000
    # Dumping them straight back recovers 85% of 1000c: a 150c round-trip loss.
    assert r["net_floor"] == -150
    # Listed at 20c they are worth 1700c net, so the flip is up 700c if it lands.
    assert r["net_mark"] == 700


def test_sell_only_window_nets_the_cut_minus_stock_given_up():
    r = pnl.compute([], [SELL], BOOKS)
    assert r["sold_gross"] == 2000
    assert r["fees"] == 300
    assert r["cash"] == 1700
    # Stock left the account, so the floor mark subtracts its dump value.
    assert r["stock_delta_floor"] == -850
    assert r["net_floor"] == 850


def test_round_trip_realises_the_margin():
    r = pnl.compute([BUY], [SELL], BOOKS)
    assert r["cash"] == 700
    # Bought and sold the same 100 units, so stock nets out.
    assert r["stock_delta_floor"] == 0
    assert r["stock_delta_mark"] == 0
    assert r["net_floor"] == r["net_mark"] == 700


def test_totals_group_by_item():
    coin, units = pnl.totals([BUY, BUY])
    assert coin == 2000
    assert units == {77: 200}


def test_unpriced_item_contributes_nothing():
    r = pnl.compute([{**BUY, "item_id": 999}], [], BOOKS)
    assert r["cash"] == -1000
    assert r["stock_delta_floor"] == 0


def test_gold_formats_both_signs():
    assert pnl.gold(0) == "0g 00s 00c"
    assert pnl.gold(123456) == "12g 34s 56c"
    assert pnl.gold(-123456) == "-12g 34s 56c"
