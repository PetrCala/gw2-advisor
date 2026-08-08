from datetime import date

from account import pnl
from invest import nav


def gathered(**over):
    g = {
        "coins": 1_000_000,
        "delivery_coins": 50_000,
        "open_buy_capital": 200_000,
        "open_buy_orders": 2,
        "open_sell_orders": 1,
        "held": {
            24295: 10,  # t6 fine mat
            36038: 100,  # festival bag
            424242: 3,  # unknown item, priced -> other
            777: 5,  # unknown item, no price row -> unpriced
        },
    }
    g.update(over)
    return g


PRICES = {
    24295: [2400, 9, 2600, 9],
    36038: [400, 9, 450, 9],
    424242: [10_000, 9, 12_000, 9],
}

D = date(2026, 8, 8)


def test_point_totals_and_class_breakdown_add_up():
    p = nav.build_point(gathered(), PRICES, D)
    t6 = pnl.net_of_fees(2400 * 10)
    festival = pnl.net_of_fees(400 * 100)
    other = pnl.net_of_fees(10_000 * 3)
    liquid = 1_000_000 + 50_000 + 200_000
    assert p["d"] == "2026-08-08"
    assert p["liquid"] == liquid
    assert p["stock_floor"] == t6 + festival + other
    assert p["floor"] == liquid + t6 + festival + other
    assert p["classes"]["t6"] == t6
    assert p["classes"]["festival"] == festival
    assert p["classes"]["other"] == other
    assert p["classes"]["luxury"] == 0
    assert p["items"] == 4
    assert p["unpriced"] == 1
    mark = sum(
        pnl.net_of_fees(PRICES[i][2] * q)
        for i, q in gathered()["held"].items()
        if i in PRICES
    )
    assert p["mark"] == liquid + mark


def test_luxury_is_classified_by_the_sell_price():
    g = gathered(held={555: 1})
    prices = {555: [5_500_000, 1, 6_000_000, 1]}
    p = nav.build_point(g, prices, D)
    assert p["classes"]["luxury"] == pnl.net_of_fees(5_500_000)


def test_append_replaces_the_same_day_and_keeps_order():
    series = {"points": [{"d": "2026-08-06", "floor": 1}, {"d": "2026-08-07", "floor": 2}]}
    out = nav.append(series, {"d": "2026-08-07", "floor": 99})
    assert [p["d"] for p in out["points"]] == ["2026-08-06", "2026-08-07"]
    assert out["points"][-1]["floor"] == 99


def test_append_caps_the_series_length():
    series = {"points": [{"d": f"2026-01-{i:02d}", "floor": i} for i in range(1, 11)]}
    out = nav.append(series, {"d": "2026-02-01", "floor": 11}, cap=5)
    assert len(out["points"]) == 5
    assert out["points"][-1]["d"] == "2026-02-01"
    assert out["points"][0]["d"] == "2026-01-07"


def test_append_starts_from_nothing():
    out = nav.append({}, {"d": "2026-08-08", "floor": 1})
    assert [p["d"] for p in out["points"]] == ["2026-08-08"]
