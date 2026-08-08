from report import invest


def nav_series():
    def point(d, floor, mark=None):
        return {
            "d": d,
            "floor": floor,
            "mark": mark or floor,
            "liquid": floor // 2,
            "stock_floor": floor - floor // 2,
            "items": 12,
            "classes": {"t6": floor - floor // 2, "festival": 0, "staples": 0,
                        "luxury": 0, "other": 0},
        }

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


def test_render_with_no_artifacts_is_a_complete_empty_state_page():
    payload = invest.build_payload(None, None, None)
    html = invest.render(payload)
    assert "no benchmark artifact yet" in html
    assert "No NAV series yet" in html
    assert "No craft-carry artifact yet" in html
    for placeholder in ("__GENERATED__", "__META__", "__RETURNS__", "__CAPS__",
                        "__EXPOSURE__", "__CONSTITUENTS__", "__CRAFT__", "__DATA__"):
        assert placeholder not in html


def test_render_with_artifacts_carries_series_and_tables():
    payload = invest.build_payload(bench(), nav_series(), 2_200_000, craft_series())
    html = invest.render(payload)
    assert "Glob of Ectoplasm" in html
    assert "Portfolio NAV" in html
    assert payload["bench"]["index"]["start"] in html
    assert "+10.0%" in html  # the index 30d return from the artifact
    assert payload["nav"]["levels"][-1] == 110.0
    assert "Deldrimor Steel Ingot" in html
    assert "Charged Quartz Crystal" in html


# --- craft carry ---------------------------------------------------------


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
