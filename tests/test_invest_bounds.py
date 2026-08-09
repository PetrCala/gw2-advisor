from pathlib import Path

from invest import bounds
from scorers import flip

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "recipes_mini.json"

# [buy_price, buy_qty, sell_price, sell_qty], the gw2api.fetch_prices shape.
PRICES = {
    2001: [100, 5, 120, 5],
    2002: [200, 5, 240, 5],
    2003: [1000, 5, 1200, 5],
    2004: [50, 5, 60, 5],
    1001: [500, 5, 600, 5],
    1002: [400, 5, 450, 5],
    1005: [900, 5, 1000, 5],
    1006: [4000, 5, 4400, 5],
    1011: [10_000, 5, 11_000, 5],
    29185: [1_000_000, 5, 1_100_000, 5],
}


def graph():
    return bounds.load_graph(FIXTURE)


def _row(**over):
    r = {"id": 2001, "name": "Fodder", "sell_price": 1000, "vendor_value": 500}
    r.update(over)
    return r


# --- the graph -------------------------------------------------------------


def test_load_graph_drops_disciplines_that_are_not_repeatable_routes():
    g = graph()
    assert g["n_recipes"] == 10  # 11 rows, the Achievement one dropped
    assert g["skipped"] == {"discipline": 1}
    assert 1009 not in g["by_output"]


def test_load_graph_matches_double_click_discipline_case_insensitively():
    g = graph()
    assert g["by_output"][1003][0]["source"] == "double-click"
    assert g["by_output"][1004][0]["source"] == "double-click"


def test_load_graph_reads_the_output_range_as_the_count_bounds():
    r = graph()["by_output"][1005][0]
    assert (r["count_min"], r["count_max"]) == (40, 200)
    assert graph()["by_output"][1001][0]["count_min"] == 2


def test_load_graph_indexes_recipes_by_ingredient_too():
    consumers = {r["output_id"] for r in graph()["by_ingredient"][2001]}
    assert consumers == {1001, 1002, 1005, 1008}


def test_load_graph_returns_an_empty_graph_when_the_archive_is_unreadable():
    g = bounds.load_graph(FIXTURE.parent / "does_not_exist.json")
    assert g["n_recipes"] == 0
    assert g["by_output"] == {} and g["by_ingredient"] == {}
    assert g["skipped"] == {"archive_unreadable": 1}


def test_the_real_archive_parses_into_a_usable_graph():
    g = bounds.load_graph()
    assert g["n_recipes"] > 7000
    assert len(g["by_output"]) > 1000


# --- the ceiling -----------------------------------------------------------


def test_ceiling_is_the_cheapest_route_per_unit_at_the_queue_front():
    # 10 x (100 + 1) + 5 x (200 + 1) = 2015 for 2 units.
    assert bounds.ceiling(1001, PRICES, graph()) == (1007, "forge")
    assert bounds.ceiling(1002, PRICES, graph()) == (303, "merchant")
    assert bounds.ceiling(1003, PRICES, graph()) == (250, "double-click")


def test_ceiling_prices_catalysts_at_zero_and_uses_the_output_range_maximum():
    # 250 x 101 = 25,250 for 200 - 1 net units; the Philosopher's Stone is free.
    assert bounds.ceiling(1005, PRICES, graph()) == (126, "forge")
    # A catalyst-only extra ingredient leaves the cost untouched: 4 x 201.
    assert bounds.ceiling(1006, PRICES, graph()) == (804, "forge")


def test_ceiling_is_none_when_an_ingredient_cannot_be_priced():
    assert bounds.ceiling(1008, PRICES, graph()) == (None, None)
    # A merchant route paid in a currency (a negative archive pseudo-id).
    assert bounds.ceiling(1010, PRICES, graph()) == (None, None)


def test_ceiling_is_none_without_any_route_at_all():
    assert bounds.ceiling(4242, PRICES, graph()) == (None, None)


def test_ceiling_is_none_on_an_empty_graph_so_callers_must_fail_closed():
    assert bounds.ceiling(1001, PRICES, {}) == (None, None)


def test_ingredient_ids_scopes_the_price_fetch_and_skips_catalysts():
    ids = bounds.ingredient_ids([1005, 1006], graph())
    assert ids == [2001, 2002]  # 20796 is a catalyst, priced at zero, never fetched


# --- floors ----------------------------------------------------------------


def test_at_vendor_floor_matches_the_radar_formula_it_replaces():
    assert bounds.at_vendor_floor(100, 90) is True  # 85 <= 90
    assert bounds.at_vendor_floor(100, 50) is False  # 85 > 50
    assert bounds.at_vendor_floor(None, 90) is False
    assert bounds.at_vendor_floor(100, None) is False


def test_vendor_floor_reads_the_snapshot_row():
    assert bounds.vendor_floor(_row()) == 500
    assert bounds.vendor_floor(_row(vendor_value=0)) is None
    assert bounds.vendor_floor({}) is None


def test_conversion_floor_is_the_best_fodder_bid_net_of_tax():
    # Merchant route: 400 x 1 x 0.85 = 340 over the 3 units it eats.
    assert bounds.conversion_floor(2001, PRICES, graph()) == (113, "merchant")


def test_conversion_floor_excludes_stochastic_and_catalyst_recipes():
    # 2002 feeds three routes: the catalyst-bearing one (which alone would
    # pay 850/unit), the gen-1 precursor forge (8,500/unit) and one honest
    # route that loses money. Every usable one is excluded, so no floor.
    assert bounds.conversion_floor(2002, PRICES, graph()) == (None, None)
    # 2004's only consumer yields a 10-25 range: an expectation is not a floor.
    assert bounds.conversion_floor(2004, PRICES, graph()) == (None, None)


def test_conversion_floor_ignores_a_route_that_hands_the_item_back():
    assert bounds.conversion_floor(1005, PRICES, graph()) == (None, None)


# --- coverage --------------------------------------------------------------


def test_floor_coverage_uses_net_of_tax_price_against_gross_vendor_value():
    # A vendor sale carries no cut; a dump into the book does. 500 / 850.
    assert round(bounds.floor_coverage(500, 1000), 4) == 0.5882


def test_floor_coverage_is_none_without_a_floor_or_a_price():
    assert bounds.floor_coverage(None, 1000) is None
    assert bounds.floor_coverage(500, 0) is None
    assert bounds.floor_coverage(500, None) is None


def test_coverage_clamps_at_or_below_zero_rather_than_returning_a_negative_percent():
    assert bounds.floor_coverage(-4000, 1000) == 0.0


def test_structural_floor_takes_the_higher_of_vendor_and_conversion_and_tags_provenance():
    out = bounds.structural_floor(2001, _row(vendor_value=50), PRICES, graph())
    assert out["floor"] == 113
    assert out["source"] == "conversion merchant"
    out = bounds.structural_floor(2001, _row(vendor_value=500), PRICES, graph())
    assert out["floor"] == 500
    assert out["source"] == "vendor"


def test_structural_floor_reports_coverage_and_distance_as_complements():
    out = bounds.structural_floor(2001, _row(), PRICES, graph())
    assert out["coverage"] == 0.5882
    assert out["distance"] == 0.4118


def test_structural_floor_is_none_without_a_vendor_value_or_a_priceable_route():
    out = bounds.structural_floor(2002, _row(id=2002, vendor_value=0), PRICES, graph())
    assert out["floor"] is None
    assert out["source"] == "none"
    assert out["coverage"] is None and out["distance"] is None


def test_structural_floor_without_a_graph_uses_the_vendor_leg_only():
    out = bounds.structural_floor(2001, _row(vendor_value=50), PRICES)
    assert out["floor"] == 50
    assert out["source"] == "vendor"


def test_structural_floor_never_reports_a_negative_distance_below_the_vendor_price():
    # The scorers.flip data-glitch case: the market price sits under the
    # vendor buy-back, so coverage runs past 1 and distance goes negative
    # rather than the floor being clamped away.
    out = bounds.structural_floor(2001, _row(sell_price=100, vendor_value=500), PRICES)
    assert out["coverage"] > 1
    assert out["floor"] == 500


# --- conventions -----------------------------------------------------------


def test_tax_matches_scorers_flip():
    assert bounds.TAX == flip.TAX
