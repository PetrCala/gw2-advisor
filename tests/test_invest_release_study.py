from datetime import date, timedelta

from invest import release_study, tags
from season import cycles, events


# --- release_windows / release_returns (season/cycles.py) ------------------


def test_release_windows_produces_fixed_offset_dates_no_wrap_math():
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases)
    b0, b1, s0, s1 = wins["End of Dragons"]
    assert b0 == date(2021, 11, 30)  # -90d
    assert b1 == date(2022, 1, 29)  # -30d
    assert s0 == date(2022, 2, 21)  # -7d
    assert s1 == date(2022, 3, 7)  # +7d


def test_release_windows_supports_custom_offsets():
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases, buy_offsets=(-10, -5), sell_offsets=(0, 3))
    b0, b1, s0, s1 = wins["End of Dragons"]
    assert (b0, b1) == (date(2022, 2, 18), date(2022, 2, 23))
    assert (s0, s1) == (date(2022, 2, 28), date(2022, 3, 3))


def test_release_returns_drops_missing_windows_without_guessing():
    releases = [("2022-02-28", "End of Dragons"), ("2023-08-22", "Secrets of the Obscure")]
    wins = cycles.release_windows(releases)
    series = {}
    b0, b1, s0, s1 = wins["End of Dragons"]
    d = b0
    while d <= b1:
        series[d.isoformat()] = 100.0
        d += timedelta(days=1)
    d = s0
    while d <= s1:
        series[d.isoformat()] = 150.0
        d += timedelta(days=1)
    # Secrets of the Obscure's windows are left entirely unpriced.
    out = cycles.release_returns(series, wins)
    assert [r["label"] for r in out] == ["End of Dragons"]
    assert out[0]["ret"] == round(150 * 0.85 / 100 - 1, 3)


def test_release_returns_excludes_a_launch_still_in_progress():
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases)
    series = {}
    b0, b1, s0, s1 = wins["End of Dragons"]
    d = b0
    while d <= s1:
        series[d.isoformat()] = 100.0
        d += timedelta(days=1)
    assert cycles.release_returns(series, wins, through=date(2022, 3, 1)) == []
    assert len(cycles.release_returns(series, wins, through=date(2022, 3, 8))) == 1


# --- class_cycles / build_classes (invest/release_study.py) ----------------


def _priced_series(win, buy_price, sell_price):
    b0, b1, s0, s1 = win
    series = {}
    d = b0
    while d <= b1:
        series[d.isoformat()] = buy_price
        d += timedelta(days=1)
    d = s0
    while d <= s1:
        series[d.isoformat()] = sell_price
        d += timedelta(days=1)
    return series


def test_class_cycles_reports_items_priced_coverage_alongside_return():
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases)
    win = wins["End of Dragons"]
    series_by_id = {
        1: _priced_series(win, 100.0, 150.0),
        2: _priced_series(win, 200.0, 300.0),
        3: {},  # never priced at all
    }
    out = release_study.class_cycles([1, 2, 3], series_by_id, wins)
    assert out[0]["items_priced"] == 2
    assert out[0]["items_total"] == 3
    assert out[0]["ret"] == round(150 * 0.85 / 100 - 1, 3)  # median of two equal returns


def test_class_cycles_drops_a_launch_no_id_priced():
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases)
    out = release_study.class_cycles([1, 2], {1: {}, 2: {}}, wins)
    assert out == []


def test_both_classes_render_even_below_min_cycles_gate():
    # With only two ids and one priced launch, n is far below
    # season.score.MIN_CYCLES, but both class rows still emit.
    releases = [("2022-02-28", "End of Dragons")]
    wins = cycles.release_windows(releases)
    win = wins["End of Dragons"]
    t6_id = next(iter(tags.T6))
    series_by_id = {t6_id: _priced_series(win, 100.0, 150.0)}
    for other in tags.T6:
        series_by_id.setdefault(other, {})
    for s in tags.STAPLES:
        series_by_id[s] = {}
    rows = []
    for cls, ids in {"t6": sorted(tags.T6), "staples": sorted(tags.STAPLES)}.items():
        cyc = release_study.class_cycles(ids, series_by_id, wins)
        rows.append({"class": cls, "cycles": cyc})
    assert {r["class"] for r in rows} == {"t6", "staples"}
    staples_row = next(r for r in rows if r["class"] == "staples")
    assert staples_row["cycles"] == []  # zero cycles, row still present


def test_class_ids_cover_t6_and_staples_only():
    assert set(release_study.CLASS_IDS) == {"t6", "staples"}
    assert set(release_study.CLASS_IDS["t6"]) == tags.T6
    assert set(release_study.CLASS_IDS["staples"]) == tags.STAPLES


# --- season/events.next_release ---------------------------------------


def test_next_release_returns_earliest_upcoming_launch():
    d0 = date(2022, 1, 1)
    d1 = date(2024, 1, 1)
    got = events.next_release(d0, d1)
    assert got == (date(2022, 2, 28), "End of Dragons")


def test_next_release_is_none_with_nothing_in_range():
    assert events.next_release(date(2030, 1, 1), date(2030, 2, 1)) is None
