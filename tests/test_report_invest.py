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
    for placeholder in ("__GENERATED__", "__META__", "__RETURNS__", "__CAPS__",
                        "__EXPOSURE__", "__CONSTITUENTS__", "__DATA__"):
        assert placeholder not in html


def test_render_with_artifacts_carries_series_and_tables():
    payload = invest.build_payload(bench(), nav_series(), 2_200_000)
    html = invest.render(payload)
    assert "Glob of Ectoplasm" in html
    assert "Portfolio NAV" in html
    assert payload["bench"]["index"]["start"] in html
    assert "+10.0%" in html  # the index 30d return from the artifact
    assert payload["nav"]["levels"][-1] == 110.0
