from report import invest


def nav_series(floors=False):
    def point(d, floor, mark=None):
        p = {
            "d": d,
            "floor": floor,
            "mark": mark or floor,
            "liquid": floor // 2,
            "stock_floor": floor - floor // 2,
            "items": 12,
            "classes": {"t6": floor - floor // 2, "festival": 0, "staples": 0,
                        "luxury": 0, "other": 0},
        }
        if floors:
            stock = floor - floor // 2
            p["floor_classes"] = {"t6": stock // 4, "festival": 0, "staples": 0,
                                  "luxury": 0, "other": 0}
            p["stock_floor_structural"] = stock // 4
            p["floor_items"] = 6
        return p

    return {"points": [point("2026-08-01", 1_000_000), point("2026-08-08", 1_100_000)]}


def bench():
    dates = [f"2026-07-{d:02d}" for d in range(1, 32)] + ["2026-08-01"]
    values = [100.0 + i for i in range(len(dates))]
    series = {"dates": dates, "values": values,
              "returns": {"30d": 0.1, "90d": None, "365d": 0.2, "all": 0.3}}
    return {
        "generated": "2026-08-04T01:40:00Z",
        "history_through": "2026-08-01",
        "params": {"top_n": 500},
        "index": {**series, "start": "2019-07-01", "months": [{"m": "2019-07", "n": 500}]},
        "ecto": series,
        "gems": None,
        "constituents": [
            {"id": 19721, "name": "Glob of Ectoplasm", "weight": 0.05,
             "turnover_g_day": 164_000}
        ],
    }


def _chain(key, name, output_id, cost, revenue=None, spread=None, dead=None):
    return {"key": key, "name": name, "output_id": output_id, "cost": cost,
            "revenue": revenue, "spread": spread, "dead": dead}


def craft_series():
    live = _chain("mithrillium", "Lump of Mithrillium -> Deldrimor Steel Ingot",
                   46738, 19277, 25492, 0.3224, False)
    dead = _chain("mithrillium", "Lump of Mithrillium -> Deldrimor Steel Ingot",
                   46738, 19000, 19500, 0.026, True)
    quartz = _chain("charged_quartz", "Charged Quartz Crystal", None, 850)
    return {
        "points": [
            {"d": "2026-08-07", "chains": [live, quartz]},
            {"d": "2026-08-08", "chains": [dead, quartz]},
        ]
    }


def luxury_doc():
    return {
        "generated": "2026-08-08T03:50:00Z",
        "through": "2026-08-08",
        "universe_n": 121,
        "picks": [
            {
                "id": 82044, "name": "Chak Infusion", "rarity": "Ascended",
                "strategy": "luxury desk", "best_bid": 7_400_000, "best_ask": 7_900_000,
                "buy_at": 7_450_001, "sell_at": 7_799_999, "margin": 200_000,
                "margin_pct": 0.2233, "qty": 2, "capital": 14_900_002,
                "entry_flow_week": 1.5, "exit_flow_week": 1.0,
                "round_trip_days": 16.0, "ev_week": 87_500.0,
            }
        ],
    }


def test_nav_series_is_published_rebased_with_shares_only():
    pub = invest.nav_public(nav_series())
    assert pub["levels"][0] == 100.0
    assert pub["levels"][-1] == 110.0
    assert pub["liquid_share"] == 0.5
    comp = {c["cls"]: c for c in pub["composition"]}
    assert comp["t6"]["stock_share"] == 1.0
    assert comp["luxury"]["stock_share"] == 0.0
    # nothing that looks like absolute copper survives into the payload
    assert "floor" not in pub
    assert isinstance(pub["points"], int)


def test_empty_nav_publishes_nothing():
    assert invest.nav_public(None) is None
    assert invest.nav_public({"points": []}) is None


def test_exposure_uses_the_bankroll_when_configured():
    rows = invest.exposure_rows(nav_series(), bankroll_copper=2_200_000)
    by = {r["cls"]: r for r in rows}
    assert by["t6"]["stock_share"] == 1.0
    assert by["t6"]["bankroll_share"] == 0.25  # 550k of 2.2m
    rows = invest.exposure_rows(nav_series(), bankroll_copper=None)
    assert all(r["bankroll_share"] is None for r in rows)


def test_nav_public_carries_no_absolute_gold():
    pub = invest.nav_public(nav_series(floors=True))
    # A privacy regression test worth having explicitly: every published
    # value is a date, a rebased level, a count or a ratio. Nothing on this
    # page may carry copper.
    for key in ("floor", "mark", "liquid", "stock_floor", "classes",
                "floor_classes", "stock_floor_structural"):
        assert key not in pub
    assert pub["floor_coverage"] == 0.25
    assert pub["floor_items"] == 6


def test_nav_public_leaves_floor_coverage_none_on_points_stored_before_s7():
    pub = invest.nav_public(nav_series())
    assert pub["floor_coverage"] is None
    assert pub["floor_items"] is None


def test_exposure_rows_render_floor_coverage_as_a_ratio_never_a_gold_level():
    rows = invest.exposure_rows(nav_series(floors=True), bankroll_copper=2_200_000)
    by = {r["cls"]: r for r in rows}
    assert by["t6"]["floor_coverage"] == 0.25
    assert by["festival"]["floor_coverage"] is None  # never measured, not zero
    assert all(abs(r["floor_coverage"] or 0) <= 1 for r in rows)


def test_exposure_rows_leave_floor_coverage_none_without_the_new_keys():
    rows = invest.exposure_rows(nav_series(), bankroll_copper=None)
    assert all(r["floor_coverage"] is None for r in rows)


def test_exposure_table_renders_coverage_and_the_liquidation_mark_wording():
    payload = invest.build_payload(None, nav_series(floors=True), None)
    html = invest.exposure_table(payload)
    assert "Floor coverage" in html
    assert "25.0%" in html
    assert "n/a" in html  # a class with no computable floor
    assert "above the liquidation mark" in html
    assert "above the floor" not in html


def test_coverage_cell_never_prints_a_negative_or_a_bare_zero():
    assert invest.coverage(None) == "n/a"
    assert invest.coverage(1.4) == "at floor"
    assert invest.coverage(1.0) == "at floor"
    assert invest.coverage(0.32) == "32.0%"


def test_luxury_public_passes_through_picks_and_meta():
    pub = invest.luxury_public(luxury_doc())
    assert pub["universe_n"] == 121
    assert pub["through"] == "2026-08-08"
    assert pub["picks"][0]["id"] == 82044


def test_luxury_public_is_none_without_picks():
    assert invest.luxury_public(None) is None
    assert invest.luxury_public({"picks": []}) is None


def test_render_with_no_artifacts_is_a_complete_empty_state_page():
    payload = invest.build_payload(None, None, None)
    html = invest.render(payload)
    assert "no benchmark artifact yet" in html
    assert "No NAV series yet" in html
    assert "No craft-carry artifact yet" in html
    assert "No luxury desk artifact yet" in html
    assert "No thesis registry entries yet" in html
    assert "No release event-study artifact yet" in html
    assert "No market radar artifact yet" in html
    assert "No corner screen artifact yet" in html
    for placeholder in ("__GENERATED__", "__META__", "__RETURNS__", "__CAPS__",
                        "__EXPOSURE__", "__CONSTITUENTS__", "__CRAFT__",
                        "__LUXURY__", "__THESIS__", "__RELEASES__", "__RADAR__",
                        "__CORNERS__", "__DATA__"):
        assert placeholder not in html


def test_render_with_all_new_artifacts_carries_their_tables():
    payload = invest.build_payload(
        None, None, None, theses=thesis_doc(), releases_doc=release_doc(),
        radar_doc=radar_doc(),
    )
    html = invest.render(payload)
    assert "Symbol of Enhancement" in html
    assert "T6 fine mats" in html
    assert "Vendor Floor Mat" in html


def test_render_with_artifacts_carries_series_and_tables():
    payload = invest.build_payload(
        bench(), nav_series(), 2_200_000, craft_series(), luxury_doc()
    )
    html = invest.render(payload)
    assert "Glob of Ectoplasm" in html
    assert "Portfolio NAV" in html
    assert payload["bench"]["index"]["start"] in html
    assert "+10.0%" in html  # the index 30d return from the artifact
    assert payload["nav"]["levels"][-1] == 110.0
    assert "Deldrimor Steel Ingot" in html
    assert "Charged Quartz Crystal" in html
    assert "Chak Infusion" in html
    assert "121" in html  # universe size in the luxury desk meta line


# --- craft carry -----------------------------------------------------------


def test_craft_rows_reads_the_latest_point_and_tallies_trailing_live_days():
    rows = invest.craft_rows(craft_series())
    by_key = {r["key"]: r for r in rows}
    mith = by_key["mithrillium"]
    assert mith["dead"] is True  # the latest point, not the first
    assert mith["tracked_days"] == 2
    assert mith["live_days"] == 1  # only the first day cleared the hurdle
    quartz = by_key["charged_quartz"]
    assert quartz["output_id"] is None
    assert quartz["tracked_days"] == 0 and quartz["live_days"] == 0


def test_craft_rows_windows_to_the_trailing_period():
    rows = invest.craft_rows(craft_series(), window=1)
    mith = next(r for r in rows if r["key"] == "mithrillium")
    assert mith["tracked_days"] == 1
    assert mith["live_days"] == 0  # the windowed-out day was the live one


def test_craft_rows_is_empty_without_an_artifact():
    assert invest.craft_rows(None) == []
    assert invest.craft_rows({"points": []}) == []


def test_craft_status_labels_every_case():
    live = _chain("a", "A", 1, 100, 200, 0.3, False)
    dead = _chain("a", "A", 1, 100, 105, 0.05, True)
    unpriced = _chain("a", "A", 1, 100)
    no_output = _chain("a", "A", None, 100)
    assert invest.craft_status(live) == ("live", "live")
    assert invest.craft_status(dead) == ("dead", "dead")
    assert invest.craft_status(unpriced) == ("unpriced", "na")
    assert invest.craft_status(no_output) == ("no tradable output", "na")


def test_craft_table_shows_cost_revenue_and_status():
    payload = invest.build_payload(None, None, None, craft_series())
    html = invest.craft_table(payload)
    assert "carry-dead" in html  # latest mithrillium reading is dead
    assert "1/2" in html  # live days over tracked days
    assert "no tradable output" in html  # charged quartz has none


# --- luxury desk -------------------------------------------------------


def test_luxury_table_shows_placements_and_meta():
    payload = invest.build_payload(None, None, None, None, luxury_doc())
    html = invest.luxury_table(payload)
    assert "Chak Infusion" in html
    assert "22.3%" in html  # margin_pct rendered as a share


# --- thesis registry -----------------------------------------------------


def thesis_doc():
    return [
        {
            "key": "voe-q3-relic-charms", "id": 89141, "name": "Symbol of Enhancement",
            "direction": "long", "bucket": "active",
            "dist_to_ceiling": -0.05, "dist_to_target": -0.3,
            "days_to_catalyst": 20, "days_to_invalidation": 40,
        },
        {
            "key": "avoid-example", "id": 12345, "name": "Something to Avoid",
            "direction": "avoid", "bucket": "watching",
            "days_to_catalyst": 10, "days_to_invalidation": 30,
        },
    ]


def test_thesis_table_shows_long_and_avoid_entries():
    payload = invest.build_payload(None, None, None, theses=thesis_doc())
    html = invest.thesis_table(payload)
    assert "Symbol of Enhancement" in html
    assert "Something to Avoid" in html
    assert "thesis-avoid" in html


def test_thesis_table_is_empty_state_without_entries():
    payload = invest.build_payload(None, None, None, theses=[])
    html = invest.thesis_table(payload)
    assert "No thesis registry entries yet" in html


# --- release event study -------------------------------------------------


def release_doc():
    return {
        "generated": "2026-08-10T01:40:00Z",
        "through": "2026-08-10",
        "classes": [
            {
                "class": "t6",
                "cycles": [{"label": "End of Dragons", "ret": 0.2, "items_priced": 4, "items_total": 8}],
                "hit_rate": 1.0,
                "confidence": "low",
                "coverage": {"launches_priced": 1, "launches_total": 6, "ids_total": 8},
            },
            {
                "class": "staples",
                "cycles": [],
                "hit_rate": None,
                "confidence": "low",
                "coverage": {"launches_priced": 0, "launches_total": 6, "ids_total": 9},
            },
        ],
    }


def test_release_table_shows_both_classes_and_coverage():
    payload = invest.build_payload(None, None, None, releases_doc=release_doc())
    html = invest.release_table(payload)
    assert "T6 fine mats" in html and "staples" in html
    assert "1 of 6" in html
    assert "0 of 6" in html


def test_release_table_is_empty_state_without_an_artifact():
    payload = invest.build_payload(None, None, None, releases_doc=None)
    html = invest.release_table(payload)
    assert "No release event-study artifact yet" in html


# --- market radar ----------------------------------------------------------


def radar_doc(history_days=90):
    return {
        "generated": "2026-08-10T01:40:00Z",
        "through": "2026-08-10",
        "history_days": history_days,
        "abnormal": [{"id": 1, "z": {"price": 5.2}, "flags": ["price"]}],
        "pumps": [42],
        "sinks": [{"id": 7, "name": "Vendor Floor Mat", "sell_price": 8, "flow_7d_avg": 150.0}],
    }


def test_radar_table_shows_all_three_screens():
    payload = invest.build_payload(None, None, None, radar_doc=radar_doc())
    html = invest.radar_table(payload)
    assert "Abnormal activity" in html
    assert "Pump filter" in html
    assert "Vendor Floor Mat" in html


def test_radar_table_is_empty_state_without_an_artifact():
    payload = invest.build_payload(None, None, None, radar_doc=None)
    html = invest.radar_table(payload)
    assert "No market radar artifact yet" in html


def test_radar_table_shows_burn_in_message_below_min_obs():
    payload = invest.build_payload(None, None, None, radar_doc=radar_doc(history_days=2))
    html = invest.radar_table(payload)
    assert "Still collecting the rolling baseline" in html


# --- corner screen ---------------------------------------------------------


def corner_doc(picks=None, rejected=None):
    return {
        "generated": "2026-08-10T04:20:00Z",
        "through": "2026-08-10",
        "candidates": 75,
        "recipes": 7514,
        "picks": picks if picks is not None else [
            {
                "id": 901, "name": "Corner Item", "rarity": "Exotic",
                "strategy": "corners", "sell_price": 20_000, "qty": 30,
                "cost": 750_000, "avg_cost": 25_000, "relist_at": 59_999,
                "ceiling": 60_000, "ceiling_source": "traded high",
                "margin": 25_999, "mult": 2.04, "demand_per_day": 10.0,
                "refill_per_day": 1.0, "refill_unobserved": False,
                "race": 2.5, "score": 2.04, "unload_days": 12.0,
                "bail_out_pct": -0.28, "unload": {"days": 12},
            }
        ],
        "rejected": rejected if rejected is not None else [
            {"id": 902, "name": "Deep Book", "reason": "book is 248 days of unload"}
        ],
    }


def test_corner_table_shows_the_pick_and_its_bounding_columns():
    payload = invest.build_payload(None, None, None, corner_doc=corner_doc())
    html = invest.corner_table(payload)
    assert "Corner Item" in html
    assert "traded high" in html
    assert "2.04" in html
    assert "12d" in html
    assert "per-corner cap" in html  # no bankroll figure, just the reminder


def test_corner_table_always_lists_the_near_misses_with_their_reasons():
    payload = invest.build_payload(None, None, None, corner_doc=corner_doc())
    html = invest.corner_table(payload)
    assert "Screened, not actionable (1)" in html
    assert "book is 248 days of unload" in html


def test_corner_table_says_an_empty_screen_is_the_normal_output():
    payload = invest.build_payload(None, None, None, corner_doc=corner_doc(picks=[]))
    html = invest.corner_table(payload)
    assert "Nothing clears the screen today" in html
    assert "Deep Book" in html  # the near-miss list is what keeps it legible


def test_corner_section_renders_an_explicit_empty_state_without_an_artifact():
    payload = invest.build_payload(None, None, None, corner_doc=None)
    html = invest.corner_table(payload)
    assert "No corner screen artifact yet" in html


def test_corner_section_publishes_no_bankroll_derived_figure():
    # A configured bankroll must change nothing about this section: the
    # page carries a reminder to check the swallow cost against the reader's
    # own cap, and never a capital figure or a within-cap flag, either of
    # which would bracket a share of BANKROLL_G beside a public gold cost.
    doc = corner_doc()
    with_bankroll = invest.corner_table(
        invest.build_payload(None, None, 50_000_000, corner_doc=doc)
    )
    without = invest.corner_table(invest.build_payload(None, None, None, corner_doc=doc))
    assert with_bankroll == without
    for key in doc["picks"][0]:
        assert "cap" not in key and "bankroll" not in key
