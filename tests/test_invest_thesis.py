from datetime import date

from invest import bankroll, thesis

TODAY = date(2026, 8, 9)


def _entry(**over):
    e = {
        "key": "voe-q3-relic-charms",
        "id": 89141,
        "name": "Symbol of Enhancement",
        "direction": "long",
        "catalyst": "VoE's Q3 quarterly drop ships 6 crafted relics.",
        "catalyst_date": "2026-09-15",
        "entry_ceiling": 450_000,
        "target": 650_000,
        "invalidation": "Patch ships without the relic recipes.",
        "invalidation_date": "2026-10-01",
        "size": 900_000,
        "opened": "2026-08-09",
        "status": None,
    }
    e.update(over)
    return e


def _avoid(**over):
    e = {
        "key": "avoid-example",
        "id": 12345,
        "name": "Something to Stay Away From",
        "direction": "avoid",
        "catalyst": "A faucet is coming.",
        "catalyst_date": "2026-09-15",
        "invalidation": "The faucet gets reverted.",
        "invalidation_date": "2026-10-01",
        "opened": "2026-08-09",
    }
    e.update(over)
    return e


# --- load_theses -----------------------------------------------------------


def test_empty_registry_yields_no_theses_and_no_error(tmp_path):
    path = tmp_path / "theses.yaml"
    path.write_text("theses: []\n", encoding="utf-8")
    assert thesis.load_theses(path) == []


def test_missing_file_yields_no_theses_and_no_error(tmp_path):
    assert thesis.load_theses(tmp_path / "nope.yaml") == []


def test_a_malformed_entry_is_skipped_not_fatal(tmp_path, capsys):
    path = tmp_path / "theses.yaml"
    path.write_text(
        "theses:\n"
        "  - key: broken\n"
        "    id: 1\n"
        "    name: Missing Fields\n"
        "    direction: long\n",
        encoding="utf-8",
    )
    out = thesis.load_theses(path)
    assert out == []
    assert "broken" in capsys.readouterr().out


def test_a_valid_entry_alongside_a_malformed_one_still_loads(tmp_path):
    import yaml

    path = tmp_path / "theses.yaml"
    path.write_text(
        yaml.dump({"theses": [{"key": "bad", "direction": "long"}, _entry()]}),
        encoding="utf-8",
    )
    out = thesis.load_theses(path)
    assert [e["key"] for e in out] == ["voe-q3-relic-charms"]


def test_direction_must_be_long_or_avoid(tmp_path):
    import yaml

    path = tmp_path / "theses.yaml"
    path.write_text(yaml.dump({"theses": [_entry(direction="short")]}), encoding="utf-8")
    assert thesis.load_theses(path) == []


def test_avoid_direction_does_not_need_long_only_fields(tmp_path):
    import yaml

    path = tmp_path / "theses.yaml"
    path.write_text(yaml.dump({"theses": [_avoid()]}), encoding="utf-8")
    out = thesis.load_theses(path)
    assert [e["key"] for e in out] == ["avoid-example"]


# --- bucket inference --------------------------------------------------


def test_watching_becomes_active_when_price_at_or_under_entry_ceiling():
    row = thesis.enrich(_entry(), TODAY, price=500_000)
    assert row["bucket"] == "watching"
    row = thesis.enrich(_entry(), TODAY, price=450_000)
    assert row["bucket"] == "active"
    row = thesis.enrich(_entry(), TODAY, price=400_000)
    assert row["bucket"] == "active"


def test_bucket_flips_to_catalyst_passed_after_catalyst_date_with_target_unhit():
    row = thesis.enrich(_entry(), date(2026, 9, 16), price=500_000)
    assert row["bucket"] == "catalyst_passed"


def test_bucket_flips_to_invalidated_after_invalidation_date():
    row = thesis.enrich(_entry(), date(2026, 10, 2), price=400_000)
    assert row["bucket"] == "invalidated"


def test_status_closed_overrides_every_other_bucket():
    row = thesis.enrich(_entry(status="closed"), TODAY, price=400_000)
    assert row["bucket"] == "closed"


def test_status_active_forces_active_even_above_ceiling():
    row = thesis.enrich(_entry(status="active"), TODAY, price=999_999)
    assert row["bucket"] == "active"


def test_avoid_direction_never_gets_sized():
    row = thesis.enrich(_avoid(status="active"), TODAY, price=100)
    assert row["qty"] is None
    assert row["capital"] is None


def test_watching_entries_are_never_sized():
    row = thesis.enrich(_entry(), TODAY, price=500_000)
    assert row["bucket"] == "watching"
    assert row["qty"] is None
    assert row["capital"] is None


# --- distances and countdowns -----------------------------------------


def test_distance_to_ceiling_and_target_are_relative_to_live_price():
    row = thesis.enrich(_entry(), TODAY, price=450_000)
    assert row["dist_to_ceiling"] == 0.0
    assert row["dist_to_target"] == round(450_000 / 650_000 - 1, 4)


def test_distances_are_none_without_a_live_price():
    row = thesis.enrich(_entry(), TODAY, price=None)
    assert row["dist_to_ceiling"] is None
    assert row["dist_to_target"] is None


def test_days_to_catalyst_and_invalidation_count_down():
    row = thesis.enrich(_entry(), TODAY, price=None)
    assert row["days_to_catalyst"] == (date(2026, 9, 15) - TODAY).days
    assert row["days_to_invalidation"] == (date(2026, 10, 1) - TODAY).days


# --- sizing --------------------------------------------------------------


def test_active_entry_sizes_qty_from_capital_over_price():
    buys = [[449_000, 100]]
    row = thesis.enrich(_entry(), TODAY, price=450_000, buys=buys)
    assert row["bucket"] == "active"
    assert row["qty"] == 900_000 // 450_000  # size // price
    assert row["capital"] == row["qty"] * 450_000


def test_active_entry_qty_is_bounded_by_the_exit_floor():
    thin_buys = [[1, 1]]  # almost nothing to dump into
    row = thesis.enrich(_entry(size=450_000_000), TODAY, price=450_000, buys=thin_buys)
    assert row["bucket"] == "active"
    assert row["qty"] is None  # no lot size at all clears the exit floor
    assert row["capital"] is None


def test_active_entry_without_a_book_still_sizes_from_capital_alone():
    row = thesis.enrich(_entry(), TODAY, price=450_000, buys=None)
    assert row["qty"] == 900_000 // 450_000


# --- enrich_all ------------------------------------------------------------


def test_enrich_all_reads_price_and_books_by_item_id():
    rows = [_entry(), _avoid()]
    fresh = {89141: 450_000, 12345: 999}
    out = thesis.enrich_all(rows, TODAY, fresh)
    by_key = {r["key"]: r for r in out}
    assert by_key["voe-q3-relic-charms"]["bucket"] == "active"
    assert by_key["avoid-example"]["qty"] is None


def test_enrich_all_tolerates_missing_price_and_books():
    out = thesis.enrich_all([_entry()], TODAY, {})
    assert out[0]["live_price"] is None
    assert out[0]["bucket"] == "watching"


# --- module constants -------------------------------------------------


def test_strategy_tag_matches_the_bankroll_cap_table():
    assert thesis.STRATEGY in bankroll.CAPS


def test_horizon_is_a_positive_number_of_days():
    assert thesis.THESIS_HORIZON_DAYS > 0
