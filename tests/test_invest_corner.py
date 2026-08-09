from invest import bankroll, corner
from scorers import flip


def _row(**over):
    r = {
        "id": 1,
        "name": "Corner Item",
        "rarity": "Exotic",
        "sell_price": 20_000,
        "sell_quantity": 50,
        "vendor_value": 100,
        "7d_sell_sold": 70,
        "1m_sell_sold": 300,
        "7d_sell_listed": 7,
        "1m_sell_listed": 30,
        "12m_sell_price_max": 60_000,
    }
    r.update(over)
    return r


def _book(sells=None, buys=None):
    return {
        "sells": sells if sells is not None else [
            [20_000, 10], [25_000, 10], [30_000, 10], [150_000, 100],
        ],
        "buys": buys if buys is not None else [[18_000, 40]],
    }


# --- rates ----------------------------------------------------------------


def test_demand_and_refill_blend_the_seven_day_and_one_month_counts():
    assert corner.demand(_row()) == 10.0  # (70/7 + 300/30) / 2
    assert corner.refill(_row()) == 1.0


def test_race_is_our_capture_share_of_demand_against_refill():
    assert corner.race_term(_row()) == 2.5  # 0.25 x 10 / 1


# --- stage 1 ---------------------------------------------------------------


def test_zero_demand_is_dropped_in_the_prefilter_before_any_division():
    assert corner.is_candidate(_row(**{"7d_sell_sold": 0, "1m_sell_sold": 0})) is False


def test_prefilter_drops_thin_evidence_vendor_floors_and_unswallowable_books():
    assert corner.is_candidate(_row(**{"1m_sell_sold": 19})) is False
    assert corner.is_candidate(_row(sell_price=9_999)) is False
    assert corner.is_candidate(_row(vendor_value=19_000)) is False  # 17,000 net <= 19,000
    assert corner.is_candidate(_row(sell_quantity=200)) is False  # 400g of book
    assert corner.is_candidate(_row()) is True


def test_prefilter_drops_a_book_too_deep_to_unload_inside_the_horizon():
    # 100 units against 2.5/day of capture is 40 days; 300 is 120.
    assert corner.is_candidate(_row(sell_quantity=100, sell_price=10_000)) is True
    assert corner.is_candidate(_row(sell_quantity=300, sell_price=10_000)) is False


def test_prefilter_ranks_by_race_and_truncates_to_one_listings_page():
    slow = _row(id=2, **{"7d_sell_listed": 70, "1m_sell_listed": 300})
    rows = corner.prefilter([slow, _row(id=1)])
    assert [r["id"] for r in rows] == [1, 2]
    assert len(corner.prefilter([_row(id=i) for i in range(1, 400)])) == corner.UNIVERSE_CAP


# --- the ladder walk -------------------------------------------------------


def test_walk_stops_at_the_break_even_and_never_climbs_the_fantasy_ladder():
    # The regression this whole ceiling exists for: one 150,000c ask resting
    # above a 60,000c yearly high must never become the relist price.
    p = corner.plan(_row(), _book(), None, None)
    assert p["qty"] == 30
    assert p["avg_cost"] == 25_000
    assert p["relist_at"] == 59_999
    assert p["ceiling"] == 60_000
    assert p["ceiling_source"] == "traded high"


def test_walk_takes_every_level_strictly_under_the_break_even():
    assert corner.walk([[10, 3], [20, 4], [30, 5]], 25) == (7, 10 * 3 + 20 * 4)
    assert corner.walk([], 100) == (0, 0)
    assert corner.walk([[100, 5]], 100) == (0, 0)


def test_score_is_the_minimum_of_race_and_ceiling_multiple():
    p = corner.plan(_row(), _book(), None, None)
    assert p["race"] == 2.5
    assert p["mult"] == 2.04
    assert p["score"] == 2.04  # bound by the ceiling multiple, not the race


def test_the_structural_ceiling_wins_when_it_is_cheaper_than_the_traded_high():
    p = corner.plan(_row(), _book(), 59_000, "forge")
    assert p["ceiling"] == 59_000
    assert p["ceiling_source"] == "forge"


def test_the_relist_undercuts_the_cheapest_ask_left_resting_in_the_book():
    # 52,000c and 58,000c both sit between the 51,000c break-even and the
    # 60,000c ceiling. Relisting at 57,999 would leave a 52,000c ask ahead
    # of us and the position undercut from day one.
    p = corner.plan(_row(), _book(sells=[[20_000, 10], [52_000, 5], [58_000, 5]]),
                    None, None)
    assert p["relist_at"] == 51_999
    assert p["qty"] == 10


# --- the rejection branch --------------------------------------------------


def test_no_score_when_no_ceiling_evidence_exists():
    out = corner.plan(_row(**{"12m_sell_price_max": 0}), _book(), None, None)
    assert out == {"id": 1, "name": "Corner Item", "reason": "no ceiling evidence"}


def test_no_score_when_the_ceiling_sits_below_the_current_price():
    out = corner.plan(_row(**{"12m_sell_price_max": 15_000}), _book(), None, None)
    assert out["reason"] == "already above the alternative route"


def test_no_score_when_every_listing_already_sits_above_the_break_even():
    out = corner.plan(_row(), _book(sells=[[55_000, 5]]), None, None)
    assert out["reason"] == "already at the ceiling"


def test_no_score_when_the_relist_leaves_no_room_after_tax():
    out = corner.plan(_row(), _book(sells=[[50_000, 10], [52_000, 5]]), None, None)
    assert out["reason"] == "no room under the ceiling"


def test_no_score_when_the_book_exceeds_the_ninety_day_unload_horizon_and_is_not_sized_down():
    # Priced so the lot stays well under the swallow cap: this row must be
    # rejected on the exit, which is the constraint a corner cannot size
    # its way out of, not on capital.
    out = corner.plan(_row(), _book(sells=[[2_000, 300]]), None, None)
    assert out["reason"] == "book is 120 days of unload"
    assert "qty" not in out  # sized down is exactly what a corner cannot do


def test_the_unload_horizon_binds_at_the_boundary_not_a_day_late():
    # 225 units against 2.5/day of capture is exactly 90 days.
    assert corner.plan(_row(), _book(sells=[[2_000, 225]]), None, None)["qty"] == 225
    out = corner.plan(_row(), _book(sells=[[2_000, 226]]), None, None)
    assert out["reason"].startswith("book is 90 days of unload")


def test_no_score_when_the_real_ladder_costs_more_than_the_swallow_cap():
    # The prefilter bounds cost at the touch, which is a strict lower bound:
    # 200 units at the 20,000c touch is 400g and would already fail stage 1,
    # but a steep ladder is the modal shape for a pick, so the walked cost
    # has to be re-tested here.
    out = corner.plan(_row(), _book(sells=[[20_000, 10], [30_000, 100]]), None, None)
    assert out["reason"] == "swallow cost 320g over the 300g cap"
    assert "qty" not in out


def test_a_ladder_costing_exactly_the_swallow_cap_still_passes():
    p = corner.plan(_row(), _book(sells=[[30_000, 100]]), None, None)
    assert p["cost"] == corner.MAX_SWALLOW
    assert p["qty"] == 100


def test_producible_ceiling_requires_a_two_times_multiple_and_traded_high_requires_one_point_two():
    # Same ladder, same 55,000c ceiling, a 1.87x multiple either way.
    producible = corner.plan(_row(), _book(), 55_000, "forge")
    assert producible["reason"] == "multiple 1.87x under the 2.00x floor for a forge ceiling"
    empirical = corner.plan(_row(**{"12m_sell_price_max": 55_000}), _book(), None, None)
    assert empirical["mult"] == 1.87
    assert empirical["score"] == 1.87


def test_no_score_when_the_score_falls_under_the_minimum():
    slow = _row(**{"7d_sell_listed": 700, "1m_sell_listed": 3000})  # refill 100/day
    out = corner.plan(slow, _book(), None, None)
    assert out["reason"].startswith("score 0.03 under the 1.00 minimum")


def test_the_score_gate_admits_exactly_break_even_and_rejects_just_under():
    # refill 2.5/day gives race 1.00 exactly, which is break-even on the
    # race axis and the lowest score this screen will publish.
    at = corner.plan(_row(**{"7d_sell_listed": 17.5, "1m_sell_listed": 75}),
                     _book(), None, None)
    assert at["score"] == 1.0
    under = corner.plan(_row(**{"7d_sell_listed": 17.6, "1m_sell_listed": 75}),
                        _book(), None, None)
    assert under["reason"].startswith("score 1.00 under the 1.00 minimum")


def test_the_multiple_gate_admits_exactly_its_floor():
    # A 24,000c relist net of tax is 20,400c against a 17,000c average:
    # 1.20x exactly, the floor a traded-high ceiling has to clear.
    row = _row(**{"12m_sell_price_max": 24_001})
    p = corner.plan(row, _book(sells=[[17_000, 10]]), None, None)
    assert p["mult"] == 1.2
    assert p["relist_at"] == 24_000
    under = corner.plan(row, _book(sells=[[20_000, 10]]), None, None)
    assert under["reason"].endswith("floor for a traded high ceiling")


def test_rejected_rows_carry_a_reason_and_never_a_score():
    out = corner.plan(_row(**{"12m_sell_price_max": 0}), _book(), None, None)
    assert set(out) == {"id", "name", "reason"}


def test_a_row_with_no_observed_resupply_is_unscoreable_rather_than_top_ranked():
    # Flooring an unobserved refill would hand the row the maximum race and
    # so waive the race half of min(race, mult), and its MIN_SCORE gate,
    # for exactly the rows with no evidence behind them.
    row = _row(**{"7d_sell_listed": 0, "1m_sell_listed": 0})
    assert corner.refill_unobserved(row) is True
    out = corner.plan(row, _book(), None, None)
    assert out["reason"] == "no observed re-supply to race against"
    assert "score" not in out


def test_a_measured_but_tiny_refill_is_floored_and_the_race_still_caps():
    # One listing in the month is evidence, unlike none at all: the rate is
    # floored so the division stays sane and the race caps rather than
    # running to infinity.
    row = _row(**{"7d_sell_listed": 0, "1m_sell_listed": 1})
    assert corner.refill(row) == corner.REFILL_FLOOR
    p = corner.plan(row, _book(), None, None)
    assert p["race"] == corner.RACE_CAP  # never infinity
    assert p["score"] == 2.04  # still bound by the ceiling multiple


def test_plan_rejects_zero_demand_instead_of_dividing_by_it():
    out = corner.plan(_row(**{"7d_sell_sold": 0, "1m_sell_sold": 0}), _book(), None, None)
    assert out["reason"] == "no measurable demand"


def test_a_structural_ceiling_of_zero_binds_and_never_falls_back_to_the_traded_high():
    # 24 real archive recipes have a non-self ingredient list of nothing but
    # catalysts, which are priced at zero, so their ceiling is 0 copper.
    # That is the strictest bound there is, not a missing one, and reading
    # it as absence would promote the looser traded high in its place.
    out = corner.plan(_row(), _book(), 0, "merchant")
    assert out["reason"] == "already above the alternative route"
    assert "score" not in out


# --- the unload schedule ---------------------------------------------------


def test_unload_schedule_tranches_by_week_and_sets_the_abort_tripwire():
    s = corner.unload_schedule(78, 26.8, 29_400)
    assert s["per_day"] == 6.7
    assert s["days"] == 12
    assert s["weeks"] == [{"week": 1, "units": 47}, {"week": 2, "units": 31}]
    assert s["abort_qty"] == 47  # one week of our own planned sales
    assert s["horizon_days"] == corner.MAX_EXIT_DAYS


def test_unload_schedule_is_none_without_demand_or_a_lot():
    assert corner.unload_schedule(10, 0, 100) is None
    assert corner.unload_schedule(0, 10, 100) is None


def test_every_pick_carries_its_unload_schedule_computed_before_any_buy():
    p = corner.plan(_row(), _book(), None, None)
    assert p["unload"]["qty"] == p["qty"]
    assert p["unload"]["relist_at"] == p["relist_at"]
    assert p["unload_days"] == 12.0


# --- the bail-out estimate -------------------------------------------------


def test_bail_out_values_only_the_units_the_buy_book_really_covers():
    # 20 units of a 30-unit lot bought at 25,000c avg: 10 at 24,000c and 10
    # at 20,000c is 440,000c gross, 374,000c net of the 15% cut, 18,700c a
    # unit against a 25,000c cost. The tax term is a quarter of the answer,
    # so it is asserted exactly rather than by sign.
    pct, coverage = corner.bail_out([[24_000, 10], [20_000, 10]], 30, 25_000)
    assert pct == -0.252
    assert coverage == 0.667


def test_bail_out_refuses_to_extrapolate_one_resting_bid_across_a_whole_lot():
    # The failure this guard exists for: one unit resting above our cost
    # would otherwise value the other 99 at that same bid and print a
    # POSITIVE bail-out for a lot the buy side cannot absorb at all.
    pct, coverage = corner.bail_out([[24_000, 1]], 100, 20_000)
    assert pct is None
    assert coverage == 0.01


def test_bail_out_is_reported_but_never_gates_a_pick():
    p = corner.plan(_row(), _book(buys=[[18_000, 40]]), None, None)
    assert p["bail_out_pct"] < 0  # dumping a swept lot always loses; not a gate
    assert p["bail_out_coverage"] == 1.0
    thin = corner.plan(_row(), _book(buys=[]), None, None)
    assert thin["bail_out_pct"] is None
    assert thin["bail_out_coverage"] is None
    assert thin["score"] == 2.04  # an empty buy book is what makes a corner possible


# --- assembly --------------------------------------------------------------


def test_build_picks_sorts_by_score_and_splits_the_rejections_out():
    # Two scoring picks, the weaker one first in, so the ordering is really
    # observed: report.track.roll opens only the first
    # MAX_CONCURRENT_CORNERS of this list, so an ascending sort would paper
    # trade the worst corners on the board every day.
    weak = _row(id=1, **{"7d_sell_listed": 14, "1m_sell_listed": 60})  # race 1.25
    strong = _row(id=2)  # race 2.5, so the ceiling multiple binds at 2.04
    bad = _row(id=3, name="No Ceiling", **{"12m_sell_price_max": 0})
    books = {i: _book() for i in (1, 2, 3)}
    picks, rejected = corner.build_picks([weak, strong, bad], books, {})
    assert [p["id"] for p in picks] == [2, 1]
    assert [p["score"] for p in picks] == [2.04, 1.25]
    assert [r["id"] for r in rejected] == [3]
    assert rejected[0]["reason"] == "no ceiling evidence"


def test_a_broken_conversion_graph_produces_no_ceiling_evidence_not_a_ladder_top():
    # The single most dangerous failure mode in this milestone: with no
    # ceiling of any kind the screen must refuse to score, never fall back
    # to the top of the ladder it just walked.
    rows = [_row(id=i, **{"12m_sell_price_max": 0}) for i in (1, 2, 3)]
    picks, rejected = corner.build_picks(rows, {i: _book() for i in (1, 2, 3)}, {})
    assert picks == []
    assert {r["reason"] for r in rejected} == {"no ceiling evidence"}


def test_a_missing_book_is_treated_as_a_book_with_nothing_under_the_ceiling():
    picks, rejected = corner.build_picks([_row()], {}, {})
    assert picks == []
    assert rejected[0]["reason"] == "already at the ceiling"


# --- conventions -----------------------------------------------------------


def test_strategy_tag_matches_a_bankroll_caps_key():
    assert corner.STRATEGY in bankroll.CAPS


def test_unload_share_matches_scorers_flip_capture():
    # Numerically the same, and deliberately named differently: in a flip
    # 0.25 is our share of a contested flow, in a corner it is a demand
    # haircut for the raised price, since we are the only seller.
    assert corner.UNLOAD_SHARE == flip.CAPTURE


def test_tax_matches_scorers_flip():
    assert corner.TAX == flip.TAX


def test_corner_fields_widen_the_snapshot_without_dropping_the_radar_columns():
    from collector import dw2

    fields = corner._corner_fields(dw2)
    assert fields.startswith(dw2.SNAPSHOT_FIELDS)
    for column in ("1d_sell_listed", "7d_sell_listed", "1m_sell_listed",
                   "12m_sell_price_max"):
        assert column in fields
