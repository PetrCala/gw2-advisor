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


# --- structural floors (S7) ----------------------------------------------


def test_build_point_without_floors_emits_the_legacy_keys_only():
    p = nav.build_point(gathered(), PRICES, D)
    for key in ("structural_classes", "mark_classes", "stock_structural",
                "structural_items"):
        assert key not in p


def test_build_point_buckets_structural_floors_by_class():
    floors = {24295: 1000, 424242: 4000}  # t6 fine mat, unknown -> other
    p = nav.build_point(gathered(), PRICES, D, floors)
    assert p["structural_classes"]["t6"] == 1000 * 10
    assert p["structural_classes"]["other"] == 4000 * 3
    assert p["stock_structural"] == 1000 * 10 + 4000 * 3
    assert p["structural_items"] == 2


def test_a_class_with_no_computable_floor_is_absent_rather_than_zero():
    # "Unmeasured" and "measured at zero" are different statements and the
    # page renders them differently, so an unmeasured class carries no key.
    # invest.bounds can never produce a zero floor anyway: vendor_floor
    # returns None at vendor_value 0 and conversion_floor drops a route
    # worth nothing, so a stored zero could only ever mean unmeasured.
    p = nav.build_point(gathered(), PRICES, D, {24295: 1000})
    assert p["structural_classes"] == {"t6": 10_000}
    assert "festival" not in p["structural_classes"]


def test_build_point_carries_the_ask_net_of_tax_as_the_coverage_denominator():
    # Structural coverage is measured against the price net of tax
    # (docs/INVESTING.md rule 7, invest.bounds.floor_coverage,
    # report.track's Floor column), never against the liquidation value:
    # on a wide spread the two differ by the whole spread, and the same
    # position would then read two coverages on two pages of one site.
    p = nav.build_point(gathered(), PRICES, D, {})
    assert p["mark_classes"]["t6"] == pnl.net_of_fees(2600 * 10)
    assert p["mark_classes"]["t6"] > p["classes"]["t6"]
    assert sum(p["mark_classes"].values()) == p["mark"] - p["liquid"]


def test_build_point_with_an_empty_floor_map_emits_the_totals_at_zero():
    p = nav.build_point(gathered(), PRICES, D, {})
    assert p["stock_structural"] == 0
    assert p["structural_items"] == 0
    assert p["structural_classes"] == {}


def test_structural_floors_ignore_held_items_with_no_price_row():
    # 777 is held but unpriced, so it never reaches a class bucket at all.
    p = nav.build_point(gathered(), PRICES, D, {777: 9_999})
    assert p["stock_structural"] == 0


def test_structural_floors_map_held_ids_through_the_snapshot(monkeypatch):
    from collector import dw2

    monkeypatch.setattr(dw2, "fetch_snapshot", lambda *a, **k: [
        {"id": 24295, "sell_price": 2600, "vendor_value": 500},
        {"id": 424242, "sell_price": 12_000, "vendor_value": 0},
    ])
    out = nav._structural_floors([24295, 424242, 777], PRICES)
    # 424242 has no vendor value and no conversion route, 777 has no row.
    assert out == {24295: 500}


def test_structural_floors_degrade_to_none_when_the_snapshot_call_fails(monkeypatch):
    # The one series in this repo that cannot be backfilled: a datawars2
    # timeout must cost today's coverage number, never today's point.
    from collector import dw2

    def boom(*a, **k):
        raise RuntimeError("datawars2 timed out")

    monkeypatch.setattr(dw2, "fetch_snapshot", boom)
    assert nav._structural_floors([24295], PRICES) is None
