from datetime import date

from invest import craft


def _chain(**over):
    c = {
        "key": "test",
        "name": "Test Chain",
        "output_id": 99,
        "inputs": ((1, 2), (2, 1)),
    }
    c.update(over)
    return c


# --- cost and spread ---------------------------------------------------------


def test_cost_of_sums_inputs_one_above_the_bid():
    prices = {1: (100, 110), 2: (50, 60)}
    # 2 units of item 1 at 101 each, 1 unit of item 2 at 51
    assert craft.cost_of(_chain(), prices) == 2 * 101 + 51


def test_cost_of_is_none_without_a_two_sided_market():
    assert craft.cost_of(_chain(), {1: (100, 110), 2: (0, 60)}) is None
    assert craft.cost_of(_chain(), {1: (100, 110), 2: (50, 0)}) is None
    assert craft.cost_of(_chain(), {1: (100, 110)}) is None


def test_spread_of_a_live_chain():
    prices = {1: (100, 110), 2: (50, 60), 99: (1000, 1100)}
    r = craft.spread_of(_chain(), prices)
    cost = 2 * 101 + 51
    revenue = round(1099 * 0.85)
    assert r["cost"] == cost
    assert r["revenue"] == revenue
    assert r["spread"] == round((revenue - cost) / cost, 4)
    assert r["dead"] is False


def test_spread_of_a_dead_chain_under_the_tax_hurdle():
    # revenue barely above cost: spread is positive but under the 15% hurdle
    prices = {1: (100, 110), 2: (50, 60), 99: (300, 310)}
    r = craft.spread_of(_chain(), prices)
    assert 0 < r["spread"] < craft.TAX
    assert r["dead"] is True


def test_spread_of_is_none_without_a_priced_output():
    r = craft.spread_of(_chain(), {1: (100, 110), 2: (50, 60)})
    assert r["cost"] is not None
    assert r["revenue"] is None and r["spread"] is None and r["dead"] is None


def test_spread_of_is_none_without_a_priced_input():
    r = craft.spread_of(_chain(), {1: (100, 110), 99: (1000, 1100)})
    assert r["cost"] is None
    assert r["revenue"] is None and r["spread"] is None and r["dead"] is None


def test_spread_of_a_chain_with_no_output_never_prices_a_spread():
    r = craft.spread_of(_chain(output_id=None), {1: (100, 110), 2: (50, 60)})
    assert r["cost"] is not None
    assert r["output_id"] is None
    assert r["revenue"] is None and r["spread"] is None and r["dead"] is None


# --- the daily point ----------------------------------------------------------


def test_build_point_covers_every_chain():
    point = craft.build_point({}, date(2026, 8, 8))
    assert point["d"] == "2026-08-08"
    assert [c["key"] for c in point["chains"]] == [c["key"] for c in craft.CHAINS]


def test_append_replaces_the_same_day_and_keeps_order():
    series = {"points": [{"d": "2026-08-06", "chains": []}, {"d": "2026-08-07", "chains": []}]}
    out = craft.append(series, {"d": "2026-08-07", "chains": [1]})
    assert [p["d"] for p in out["points"]] == ["2026-08-06", "2026-08-07"]
    assert out["points"][-1]["chains"] == [1]


def test_append_caps_the_series_length():
    series = {"points": [{"d": f"2026-01-{i:02d}"} for i in range(1, 11)]}
    out = craft.append(series, {"d": "2026-02-01"}, cap=5)
    assert len(out["points"]) == 5
    assert out["points"][-1]["d"] == "2026-02-01"
    assert out["points"][0]["d"] == "2026-01-07"


def test_append_starts_from_nothing():
    out = craft.append({}, {"d": "2026-08-08"})
    assert [p["d"] for p in out["points"]] == ["2026-08-08"]


# --- the real chain data -------------------------------------------------------


def test_chain_keys_and_priced_output_ids_are_unique():
    keys = [c["key"] for c in craft.CHAINS]
    assert len(keys) == len(set(keys))
    outputs = [c["output_id"] for c in craft.CHAINS if c["output_id"] is not None]
    assert len(outputs) == len(set(outputs))


def test_charged_quartz_has_no_single_tradable_output():
    cq = next(c for c in craft.CHAINS if c["key"] == "charged_quartz")
    assert cq["output_id"] is None


def test_all_ids_covers_every_input_and_output():
    ids = craft.all_ids()
    for c in craft.CHAINS:
        for item_id, _ in c["inputs"]:
            assert item_id in ids
        if c["output_id"] is not None:
            assert c["output_id"] in ids
